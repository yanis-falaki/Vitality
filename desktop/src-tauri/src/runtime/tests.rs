//! The runtime end to end with fakes: input, portal, frames, and the fake server.

use std::path::PathBuf;
use std::sync::Arc;

use image::{Rgb, RgbImage};

use super::*;
use crate::activity::FakeInput;
use crate::screens::fakes::{FakeFeeds, FakePortal};
use crate::store::{INTERACTION_FILE, UPLOADED};
use crate::types::CaptureMode;
use crate::upload::tests::{eventually, fast, FakeServer};

struct Rig {
    _dir: tempfile::TempDir,
    paths: Paths,
    input: Arc<FakeInput>,
    feeds: Arc<FakeFeeds>,
    core: Arc<Core>,
    server: FakeServer,
}

fn quick() -> PolicySettings {
    PolicySettings {
        start_gap: Duration::from_millis(150),
        settle: Duration::from_millis(150),
        max_gap: Duration::from_secs(2),
        passive_every: Duration::from_secs(60),
        ..PolicySettings::default()
    }
}

async fn rig_in(dir: tempfile::TempDir) -> Rig {
    rig_with(dir, quick()).await
}

async fn rig_with(dir: tempfile::TempDir, policy: PolicySettings) -> Rig {
    let paths = Paths::under(dir.path());
    let server = FakeServer::default();
    let url = server.serve().await;
    let input = Arc::new(FakeInput::default());
    let feeds = Arc::new(FakeFeeds::default());
    let core = Runtime::start(Parts {
        paths: paths.clone(),
        policy,
        webp_quality: 75.0,
        server: Some(url),
        upload_timings: fast(),
        input: input.clone(),
        portal: Arc::new(FakePortal::default()),
        feeds: feeds.clone(),
        tick: Duration::from_millis(20),
    });
    eventually("the saved screens reopened", || core.screens.opened_all()).await;
    Rig { _dir: dir, paths, input, feeds, core, server }
}

async fn rig() -> Rig {
    rig_in(tempfile::tempdir().unwrap()).await
}

impl Rig {
    async fn add_screen(&self, level: u8) -> ScreenId {
        let id = self.core.screens.add().await.unwrap();
        let node = self.feeds.slots.lock().unwrap().len() as u32;
        self.feeds.push(node, frame(level));
        id
    }

    fn frame(&self, node: u32, level: u8) {
        self.feeds.push(node, frame(level));
    }

    fn folders(&self) -> Vec<PathBuf> {
        let mut out = vec![];
        for day in std::fs::read_dir(&self.paths.captures).into_iter().flatten().flatten() {
            for f in std::fs::read_dir(day.path()).unwrap().flatten() {
                out.push(f.path());
            }
        }
        out.sort();
        out
    }

    fn complete(&self) -> Vec<PathBuf> {
        self.folders().into_iter().filter(|f| f.join(INTERACTION_FILE).exists()).collect()
    }

    fn input(&self) {
        self.input.send(Instant::now());
    }
}

fn frame(level: u8) -> RgbImage {
    RgbImage::from_pixel(64, 36, Rgb([level, level, level]))
}

fn json(folder: &std::path::Path) -> serde_json::Value {
    serde_json::from_slice(&std::fs::read(folder.join(INTERACTION_FILE)).unwrap()).unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn input_becomes_an_interaction_folder_that_is_uploaded() {
    let r = rig().await;
    r.add_screen(40).await;
    tokio::time::sleep(Duration::from_millis(60)).await; // a tick sees the frame
    r.input();
    eventually("an active interaction", || r.core.status().capture.mode == CaptureMode::Active).await;
    r.frame(1, 200); // the screen changes before the settle shot
    eventually("finished", || !r.complete().is_empty()).await;
    eventually("uploaded", || r.complete().first().is_some_and(|f| f.join(UPLOADED).exists())).await;
    let folder = &r.complete()[0];
    let j = json(folder);
    let shots = j["shots"].as_array().unwrap();
    assert_eq!(shots.len(), 2, "the R1 shot and the settle shot: {j}");
    assert!(shots.iter().all(|s| s["trigger"] == "activity" && s.get("screen_name").is_none()));
    assert_eq!(r.server.names().len(), 1);
    let log = r.core.recent_decisions(50).iter().map(|d| d.text.clone()).collect::<Vec<_>>().join("\n");
    assert!(log.contains("start active interaction") && log.contains("R1 shot") && log.contains("R2 shot") && log.contains("R5 end"), "{log}");
    eventually("idle again", || r.core.status().capture.mode == CaptureMode::Idle).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn two_screens_write_their_names() {
    let r = rig().await;
    let a = r.add_screen(40).await;
    r.add_screen(90).await;
    r.core.screens.rename(&a, "Main").unwrap();
    tokio::time::sleep(Duration::from_millis(60)).await;
    r.input();
    eventually("finished", || !r.complete().is_empty()).await;
    let j = json(&r.complete()[0]);
    let names: Vec<&str> = j["shots"].as_array().unwrap().iter().map(|s| s["screen_name"].as_str().unwrap()).collect();
    assert_eq!(names, ["Main", "Screen 2"]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn pause_mid_interaction_ends_it_at_once_and_captures_nothing_until_resume() {
    // Review Focus 4 (the runtime's half; the policy's is in policy::tests).
    let r = rig().await;
    r.add_screen(40).await;
    tokio::time::sleep(Duration::from_millis(60)).await;
    for _ in 0..3 {
        r.input();
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert_eq!(r.core.status().capture.mode, CaptureMode::Active);
    let status = r.core.pause(PauseFor::UntilResumed).await;
    assert_eq!(status.capture.mode, CaptureMode::Stopped);
    assert!(status.pause.paused && status.pause.until.is_none());
    assert_eq!(r.complete().len(), 1, "the open interaction ended at once");
    assert_eq!(r.folders().len(), 1);
    // Input and a changing screen while paused: nothing.
    for level in [100u8, 150, 200, 250] {
        r.input();
        r.frame(1, level);
        tokio::time::sleep(Duration::from_millis(80)).await;
    }
    assert_eq!(r.folders().len(), 1, "nothing captured while paused");
    assert!(PauseState::load(&r.paths.state).paused, "the pause is saved");
    let status = r.core.resume().await;
    assert_eq!(status.capture.mode, CaptureMode::Idle);
    r.input();
    eventually("a new interaction after resume", || r.complete().len() == 2).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_saved_pause_holds_after_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    PauseState::starting(PauseFor::Minutes(30), Utc::now()).save(&dir.path().join("state.json"));
    let r = rig_in(dir).await;
    let s = r.core.status();
    assert_eq!((s.capture.mode, s.pause.paused), (CaptureMode::Stopped, true));
    assert!(s.pause.until.is_some());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_timed_pause_ends_by_itself() {
    let dir = tempfile::tempdir().unwrap();
    let soon = Utc::now() + chrono::Duration::milliseconds(400);
    PauseState { paused: true, paused_until: Some(soon) }.save(&dir.path().join("state.json"));
    let r = rig_in(dir).await;
    assert_eq!(r.core.status().capture.stopped_by, Some(StopReason::Paused));
    eventually("the pause ran out", || r.core.status().capture.mode == CaptureMode::Idle).await;
    assert!(!r.core.status().pause.paused);
    assert!(!PauseState::load(&r.paths.state).paused);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn quit_finishes_the_open_interaction() {
    let r = rig().await;
    r.add_screen(40).await;
    tokio::time::sleep(Duration::from_millis(60)).await;
    r.input();
    eventually("active", || r.core.status().capture.mode == CaptureMode::Active).await;
    r.core.quit().await;
    assert_eq!(r.complete().len(), 1);
    assert_eq!(r.core.status().capture.stopped_by, Some(StopReason::Quit));
}

/// A core with fakes and no screens, for other modules' tests (the control socket).
pub struct BareCore {
    pub dir: tempfile::TempDir,
    pub core: Arc<Core>,
}

pub async fn bare_core() -> BareCore {
    let dir = tempfile::tempdir().unwrap();
    let core = Runtime::start(Parts {
        paths: Paths::under(dir.path()),
        policy: quick(),
        webp_quality: 75.0,
        server: None,
        upload_timings: fast(),
        input: Arc::new(FakeInput::default()),
        portal: Arc::new(FakePortal::default()),
        feeds: Arc::new(FakeFeeds::default()),
        tick: Duration::from_millis(20),
    });
    BareCore { dir, core }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_full_interaction_is_split_and_every_part_is_uploaded() {
    // R9 end to end: a tiny budget, so the second shot of an interaction passes it.
    let dir = tempfile::tempdir().unwrap();
    let r = rig_with(dir, PolicySettings { max_interaction_bytes: 1, ..quick() }).await;
    r.add_screen(40).await;
    tokio::time::sleep(Duration::from_millis(60)).await;
    r.input();
    eventually("active", || r.core.status().capture.mode == CaptureMode::Active).await;
    r.frame(1, 200); // the settle shot has something to take
    eventually("two interactions uploaded", || {
        let c = r.complete();
        c.len() >= 2 && c.iter().all(|f| f.join(UPLOADED).exists())
    })
    .await;
    let log = r.core.recent_decisions(50).iter().map(|d| d.text.clone()).collect::<Vec<_>>().join("\n");
    assert!(log.contains("R9 end") && log.contains("R9 shot"), "{log}");
    let second = json(&r.complete()[1]);
    assert!(!second["shots"].as_array().unwrap().is_empty());
}


#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn the_status_page_counts_today_and_lists_decisions() {
    let r = rig().await;
    r.add_screen(40).await;
    tokio::time::sleep(Duration::from_millis(60)).await;
    r.input();
    eventually("finished", || !r.complete().is_empty()).await;
    eventually("counted", || r.core.status_page().today.interactions == 1).await;
    let page = r.core.status_page();
    assert!(page.today.shots >= 1);
    assert!(page.disk.captures_bytes > 0 && page.disk.free_bytes > 0);
    assert!(page.recent_decisions.iter().any(|d| d.text.starts_with("R1 shot")), "{:?}", page.recent_decisions);
    assert!(page.recent_decisions.len() <= 50);
}

#[test]
fn a_suspend_is_a_wall_clock_jump_the_monotonic_clock_did_not_make() {
    let wall = |s: i64| chrono::TimeDelta::seconds(s);
    assert!(!slept(wall(0), Duration::from_millis(250)));
    assert!(!slept(wall(20), Duration::from_secs(1)), "a small clock correction");
    assert!(slept(wall(7200), Duration::from_millis(250)), "two hours asleep");
    assert!(!slept(wall(-3600), Duration::from_millis(250)), "the clock set back");
}
