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
        end_after_idle: Duration::from_millis(300),
        max_gap: Duration::from_secs(2),
        passive_every: Duration::from_secs(60),
        ..PolicySettings::default()
    }
}

async fn rig_in(dir: tempfile::TempDir) -> Rig {
    rig_with(dir, quick()).await
}

async fn rig_with(dir: tempfile::TempDir, policy: PolicySettings) -> Rig {
    rig_full(dir, policy, Arc::new(Duration::default), Duration::from_millis(20)).await
}

/// A rig with its own suspend clock and tick.
async fn rig_full(dir: tempfile::TempDir, policy: PolicySettings, suspend_clock: SuspendClock, tick: Duration) -> Rig {
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
        tick,
        suspend_clock,
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
    r.frame(1, 200); // the screen changes before the closing shot
    eventually("finished", || !r.complete().is_empty()).await;
    eventually("uploaded", || r.complete().first().is_some_and(|f| f.join(UPLOADED).exists())).await;
    let folder = &r.complete()[0];
    let j = json(folder);
    let shots = j["shots"].as_array().unwrap();
    assert_eq!(shots.len(), 2, "the R1 shot and the closing shot: {j}");
    assert!(shots.iter().all(|s| s["trigger"] == "activity" && s.get("screen_name").is_none()));
    assert_eq!(r.server.names().len(), 1);
    let log = r.core.recent_decisions(50).iter().map(|d| d.text.clone()).collect::<Vec<_>>().join("\n");
    assert!(log.contains("Screenshot — you started using the computer") && log.contains("Closing screenshot — idle for 0.3 s; interaction ended"), "{log}");
    assert!(!log.contains("R1") && !log.contains("R3"), "no rule codes in the log: {log}");
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
    assert_eq!(names, ["Main", "Screen 2", "Main", "Screen 2"], "the start and the closing shot, each of every screen");
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
    PauseState { paused: true, paused_until: Some(soon), ..Default::default() }.save(&dir.path().join("state.json"));
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
        suspend_clock: Arc::new(Duration::default),
    });
    BareCore { dir, core }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_full_interaction_is_split_and_every_part_is_uploaded() {
    // R6 end to end: a tiny budget, so the second shot of a busy interaction (R2) passes it.
    let dir = tempfile::tempdir().unwrap();
    let r = rig_with(dir, PolicySettings { max_interaction_bytes: 1, max_gap: Duration::from_millis(400), ..quick() }).await;
    r.add_screen(40).await;
    tokio::time::sleep(Duration::from_millis(60)).await;
    r.input();
    eventually("active", || r.core.status().capture.mode == CaptureMode::Active).await;
    r.frame(1, 200); // a busy interaction
    for _ in 0..8 {
        tokio::time::sleep(Duration::from_millis(100)).await;
        r.input(); // still busy
    }
    eventually("two interactions uploaded", || {
        let c = r.complete();
        c.len() >= 2 && c.iter().all(|f| f.join(UPLOADED).exists())
    })
    .await;
    let log = r.core.recent_decisions(50).iter().map(|d| d.text.clone()).collect::<Vec<_>>().join("\n");
    assert!(log.contains("Interaction split — size limit reached") && log.contains("continuing in a new interaction"), "{log}");
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
    assert!(page.recent_decisions.iter().any(|d| d.text.starts_with("Screenshot — you started using the computer")), "{:?}", page.recent_decisions);
    assert!(page.recent_decisions.len() <= 50);
}

#[test]
fn the_suspend_clock_counts_only_suspends() {
    assert!(!slept(Duration::from_secs(10)));
    assert!(slept(Duration::from_secs(7200)));
    let a = suspended_time();
    std::thread::sleep(Duration::from_millis(20));
    assert!(suspended_time().saturating_sub(a) < Duration::from_secs(1), "not suspended while running");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn after_a_suspend_the_waking_input_starts_a_fresh_interaction() {
    // I-2: the monotonic clock did not move; only the suspend clock did. The key press that
    // wakes the machine arrives before any tick.
    let suspended = Arc::new(std::sync::Mutex::new(Duration::ZERO));
    let clock: SuspendClock = {
        let s = suspended.clone();
        Arc::new(move || *s.lock().unwrap())
    };
    let slow = PolicySettings { end_after_idle: Duration::from_secs(30), ..quick() };
    let r = rig_full(tempfile::tempdir().unwrap(), slow, clock, Duration::from_millis(50)).await;
    r.add_screen(40).await;
    tokio::time::sleep(Duration::from_millis(120)).await;
    r.input();
    eventually("active", || r.core.status().capture.mode == CaptureMode::Active).await;
    *suspended.lock().unwrap() += Duration::from_secs(2 * 3600);
    r.input();
    eventually("the old one finished", || !r.complete().is_empty()).await;
    eventually("a new one open", || r.folders().len() == 2).await;
    let lines = || r.core.recent_decisions(20).iter().map(|d| d.text.clone()).collect::<Vec<_>>();
    eventually("the new one's first screenshot logged", || lines().iter().filter(|l| l.contains("you started using the computer")).count() == 2).await;
    let log = lines();
    let i = log.iter().position(|l| l.contains("the computer was asleep for 120 min")).expect("logged");
    assert!(log[i].starts_with("Interaction ended"), "{log:?}");
    let fresh = log.iter().rposition(|l| l.contains("you started using the computer")).unwrap();
    assert!(fresh > i, "the old one closes before the new one starts: {log:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn setup_done_is_remembered_through_pauses() {
    let r = rig().await;
    assert!(!r.core.status().setup_done, "a first run");
    r.core.finish_setup().await;
    r.core.pause(PauseFor::Minutes(5)).await;
    r.core.resume().await;
    assert!(r.core.status().setup_done);
    assert!(PauseState::load(&r.paths.state).setup_done, "kept in state.json");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn checks_are_absent_until_the_first_run_and_only_pass_or_fail_changes_notify() {
    use crate::types::samples_page::check;
    let r = rig().await;
    assert!(r.core.checks().is_none());
    let mut changes = r.core.subscribe();
    r.core.set_checks(vec![check("disk_space", true, false, "Disk space", "96.0 GB free", None)]);
    assert!(changes.try_recv().is_ok());
    while changes.try_recv().is_ok() {}
    r.core.set_checks(vec![check("disk_space", true, false, "Disk space", "95.9 GB free", None)]);
    assert!(changes.try_recv().is_err(), "a detail moving is not a change");
    assert_eq!(r.core.checks().unwrap()[0].detail, "95.9 GB free");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn new_settings_apply_at_once() {
    let r = rig().await;
    let s = crate::settings::Settings { server: "".into(), disk_warning_gb: 1e6, ..Default::default() };
    r.core.apply_settings(&s);
    eventually("upload off", || r.core.status().upload.state == crate::types::UploadState::Disabled).await;
    assert!(r.core.status_page().disk.low, "the new warning level counts");
    eventually("logged", || r.core.recent_decisions(10).iter().any(|d| d.text == "Settings changed")).await;
}
