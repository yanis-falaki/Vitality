//! The runtime: wires input, the screens and the clock to the policy, and carries
//! out its decisions with the store and the uploader.
//!
//! `Runtime::start(parts)` (inside the tokio runtime) finishes interrupted interactions, starts
//! the uploader, reopens the screens and spawns the loop; it returns the shared `Core`, which
//! the interface's commands, the tray, and the control socket use:
//! `status()`, `pause(how)`, `resume()`, `quit()`, `recent_decisions(n)`, `subscribe()`
//! (a notice whenever the status changes), `notify_changed()`, `forget_screen(id)`.
//!
//! The loop: every input goes to `Policy::on_input`; the owner's pause goes to
//! `on_stop`/`on_resume`; a tick (250 ms) passes the screens' thumbs to `on_tick`, expires a
//! timed pause, and tells the policy when the machine slept (`on_slept`: the wall clock jumped
//! further than the monotonic one, which stops during a suspend). Lock is not watched. Every decision is carried out and written to the
//! decisions log. Shots are encoded off the loop's thread.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use chrono::{DateTime, Local, Offset, Utc};
use tokio::sync::{broadcast, mpsc, oneshot};

use crate::activity::InputSource;
use crate::log::DecisionLog;
use crate::paths::Paths;
use crate::policy::{Decision, InteractionKind, Policy, PolicySettings, PolicyState, Rule};
use crate::screens::feed::FeedFactory;
use crate::screens::portal::Portal;
use crate::screens::Screens;
use crate::state::{PauseFor, PauseState};
use crate::store::{local_tz_name, InteractionHandle, Store};
use crate::types::{Check, CaptureView, DecisionLine, DiskView, ScreenId, ScreenState, StatusPage, StatusView, StopReason, TodayCounts};
use crate::upload::{device_name, UploadTimings, Uploader};

/// Everything the runtime is made of; the app passes the real ones, tests fakes.
pub struct Parts {
    pub paths: Paths,
    pub policy: PolicySettings,
    pub webp_quality: f32,
    pub server: Option<String>,
    pub upload_timings: UploadTimings,
    pub input: Arc<dyn InputSource>,
    pub portal: Arc<dyn Portal>,
    pub feeds: Arc<dyn FeedFactory>,
    pub tick: Duration,
}

enum Request {
    Pause(PauseFor, oneshot::Sender<()>),
    Resume(oneshot::Sender<()>),
    Quit(oneshot::Sender<()>),
    ForgetScreen(ScreenId),
}

/// The running core, shared by the interface, the tray and the control socket.
pub struct Core {
    pub screens: Arc<Screens>,
    pub store: Arc<Store>,
    pub uploader: Uploader,
    input: Arc<dyn InputSource>,
    requests: mpsc::Sender<Request>,
    snapshot: Mutex<StatusView>,
    changed: broadcast::Sender<()>,
    log: Mutex<DecisionLog>,
    checks: Mutex<Vec<Check>>,
    details: Mutex<Option<(Instant, TodayCounts, DiskView)>>,
    /// Free space under this many bytes is a warning.
    pub disk_warning: Mutex<u64>,
    /// Shows the window (set by the app; the control socket's `show`).
    pub on_show: Mutex<Option<Box<dyn Fn() + Send + Sync>>>,
}

impl Core {
    pub fn status(&self) -> StatusView {
        self.snapshot.lock().unwrap().clone()
    }

    pub async fn pause(&self, how: PauseFor) -> StatusView {
        self.ask(|tx| Request::Pause(how, tx)).await;
        self.status()
    }

    pub async fn resume(&self) -> StatusView {
        self.ask(Request::Resume).await;
        self.status()
    }

    /// Ends an open interaction (finished and uploaded later) and stops capture for good.
    pub async fn quit(&self) {
        self.ask(Request::Quit).await;
    }

    pub fn forget_screen(&self, id: &ScreenId) {
        let _ = self.requests.try_send(Request::ForgetScreen(id.clone()));
        self.notify_changed();
    }

    /// The newest `n` decisions, oldest first.
    pub fn recent_decisions(&self, n: usize) -> Vec<DecisionLine> {
        self.log.lock().unwrap().recent(n).iter().filter_map(|l| parse_decision(l)).collect()
    }

    /// The health checks, as last run (`set_checks`).
    pub fn checks(&self) -> Vec<Check> {
        self.checks.lock().unwrap().clone()
    }

    #[allow(dead_code)] // the health checks (milestone 3) set them
    pub fn set_checks(&self, checks: Vec<Check>) {
        let changed = *self.checks.lock().unwrap() != checks;
        *self.checks.lock().unwrap() = checks;
        if changed {
            self.notify_changed();
        }
    }

    /// Everything the Status page shows. Today's counts and disk use are recounted at most
    /// every 15 s (they walk the captures folder).
    pub fn status_page(&self) -> StatusPage {
        let (today, disk) = {
            let mut cache = self.details.lock().unwrap();
            match &*cache {
                Some((at, today, disk)) if at.elapsed() < Duration::from_secs(15) => (*today, disk.clone()),
                _ => {
                    let today = self.store.today();
                    let disk = self.disk();
                    *cache = Some((Instant::now(), today, disk.clone()));
                    (today, disk)
                }
            }
        };
        StatusPage { status: self.status(), today, disk, checks: self.checks(), recent_decisions: self.recent_decisions(50) }
    }

    /// Drop the cached counts (an interaction just ended, or space was freed).
    pub fn recount(&self) {
        *self.details.lock().unwrap() = None;
    }

    fn disk(&self) -> DiskView {
        let free = crate::paths::free_bytes(self.store.root()).unwrap_or(0);
        DiskView { captures_bytes: self.store.disk_bytes(), free_bytes: free, low: free < *self.disk_warning.lock().unwrap() }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<()> {
        self.changed.subscribe()
    }

    pub fn notify_changed(&self) {
        let _ = self.changed.send(());
    }

    pub fn show_window(&self) {
        if let Some(show) = self.on_show.lock().unwrap().as_ref() {
            show();
        }
    }

    async fn ask(&self, make: impl FnOnce(oneshot::Sender<()>) -> Request) {
        let (tx, rx) = oneshot::channel();
        if self.requests.send(make(tx)).await.is_ok() {
            let _ = rx.await;
        }
    }

    fn publish(&self, view: StatusView) {
        let mut snap = self.snapshot.lock().unwrap();
        if *snap != view {
            *snap = view;
            drop(snap);
            self.notify_changed();
        }
    }
}

pub struct Runtime {
    core: Arc<Core>,
    policy: Policy,
    open: Option<InteractionHandle>,
    pause: PauseState,
    pause_path: std::path::PathBuf,
    /// The last tick, by both clocks (to see a suspend).
    last_tick: Option<(Instant, DateTime<Utc>)>,
    quit: bool,
    /// Decisions from `on_stop`, carried out by the next `finish_stop`.
    pending_stop: Vec<Decision>,
}

impl Runtime {
    pub fn start(parts: Parts) -> Arc<Core> {
        let store = Arc::new(Store::new(&parts.paths.captures, parts.webp_quality));
        match store.finish_interrupted() {
            Ok(done) if !done.is_empty() => eprintln!("vitality-client: finished {} interrupted interaction(s)", done.len()),
            Ok(_) => {}
            Err(e) => eprintln!("vitality-client: cannot finish interrupted interactions: {e}"),
        }
        let uploader = Uploader::start(store.clone(), parts.server.clone(), device_name(), parts.upload_timings.clone());
        let screens = Screens::new(&parts.paths.screens, parts.portal.clone(), parts.feeds.clone(), parts.policy.threshold);
        let (requests, rx) = mpsc::channel(16);
        let pause = PauseState::load(&parts.paths.state);
        let core = Arc::new(Core {
            screens: screens.clone(),
            store,
            uploader,
            input: parts.input.clone(),
            requests,
            snapshot: Mutex::new(blank_status()),
            changed: broadcast::channel(16).0,
            log: Mutex::new(DecisionLog::open(&parts.paths.log)),
            checks: Mutex::new(vec![]),
            details: Mutex::new(None),
            disk_warning: Mutex::new(5 * 1024 * 1024 * 1024),
            on_show: Mutex::new(None),
        });
        let mut runtime = Runtime {
            core: core.clone(),
            policy: Policy::new(parts.policy.clone()),
            open: None,
            pause,
            pause_path: parts.paths.state.clone(),
            last_tick: None,
            quit: false,
            pending_stop: vec![],
        };
        runtime.update_stop(Instant::now());
        runtime.publish();
        let inputs = parts.input.subscribe();
        let tick = parts.tick;
        tokio::spawn(async move { screens.open_all().await });
        tokio::spawn(async move { runtime.run(inputs, rx, tick).await });
        core
    }

    async fn run(
        mut self,
        mut inputs: mpsc::Receiver<Instant>,
        mut requests: mpsc::Receiver<Request>,
        tick: Duration,
    ) {
        let mut ticks = tokio::time::interval(tick);
        ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                Some(at) = inputs.recv() => {
                    let ds = self.policy.on_input(at);
                    self.execute(ds).await;
                }
                Some(request) = requests.recv() => self.handle(request).await,
                _ = ticks.tick() => {
                    let now = Instant::now();
                    let wall = Utc::now();
                    if let Some((then, then_wall)) = self.last_tick.replace((now, wall)) {
                        if slept(wall - then_wall, now - then) {
                            self.log("the machine slept");
                            let ds = self.policy.on_slept(now);
                            self.execute(ds).await;
                        }
                    }
                    if self.pause.paused && !self.pause.active(Utc::now()) {
                        self.set_pause(PauseState::default());
                        self.update_stop(now);
                    }
                    self.core.screens.track_duplicates(now);
                    self.core.screens.reopen_due(now);
                    let thumbs = self.core.screens.thumbs();
                    let ds = self.policy.on_tick(now, &thumbs);
                    self.execute(ds).await;
                }
            }
            self.publish();
        }
    }

    async fn handle(&mut self, request: Request) {
        let now = Instant::now();
        match request {
            Request::Pause(how, done) => {
                let p = PauseState::starting(how, Utc::now());
                self.log(&match p.paused_until {
                    Some(u) => format!("paused until {}", u.with_timezone(&Local).format("%H:%M")),
                    None => "paused".into(),
                });
                self.set_pause(p);
                self.update_stop(now);
                self.finish_stop().await;
                self.publish();
                let _ = done.send(());
            }
            Request::Resume(done) => {
                if self.pause.paused {
                    self.log("resumed");
                }
                self.set_pause(PauseState::default());
                self.update_stop(now);
                self.publish();
                let _ = done.send(());
            }
            Request::Quit(done) => {
                self.quit = true;
                self.update_stop(now);
                self.finish_stop().await;
                self.publish();
                let _ = done.send(());
            }
            Request::ForgetScreen(id) => self.policy.forget_screen(&id),
        }
    }

    fn set_pause(&mut self, p: PauseState) {
        p.save(&self.pause_path);
        self.pause = p;
    }

    /// The reason capture must stop now, if any.
    fn stop_reason(&self) -> Option<StopReason> {
        if self.quit {
            Some(StopReason::Quit)
        } else if self.pause.active(Utc::now()) {
            Some(StopReason::Paused)
        } else {
            None
        }
    }

    /// Tells the policy to stop or resume as the reasons say. An interaction it ends is
    /// finished by the `finish_stop` that follows.
    fn update_stop(&mut self, now: Instant) {
        let state = self.policy.state();
        match self.stop_reason() {
            Some(why) if state.stopped_by != Some(why) => {
                let ds = self.policy.on_stop(now, why);
                self.pending_stop.extend(ds);
            }
            None if state.stopped_by.is_some() => {
                self.policy.on_resume(now);
            }
            _ => {}
        }
    }

    async fn finish_stop(&mut self) {
        let ds = std::mem::take(&mut self.pending_stop);
        self.execute(ds).await;
    }

    async fn execute(&mut self, decisions: Vec<Decision>) {
        let mut queue: std::collections::VecDeque<Decision> = decisions.into();
        while let Some(d) = queue.pop_front() {
            match d {
                Decision::StartInteraction { kind } => {
                    if self.open.is_some() {
                        self.end(Rule::R8).await; // never happens: the policy ends first
                    }
                    let now = Utc::now();
                    let offset = Local::now().offset().fix();
                    let tz = local_tz_name(); // per interaction: the zone may change (travel)
                    match self.core.store.begin(now, offset, &tz) {
                        Ok(h) => {
                            self.log(&format!("start {} interaction {}", kind_name(kind), h.name()));
                            self.open = Some(h);
                        }
                        Err(e) => self.log(&format!("error: cannot start an interaction: {e}")),
                    }
                }
                Decision::Shot { screens, trigger, rule } => {
                    let Some(mut handle) = self.open.take() else {
                        screens.iter().for_each(|id| self.policy.forget_screen(id));
                        continue;
                    };
                    let order = self.core.screens.ids();
                    let mut taken = vec![];
                    for id in order.iter().filter(|id| screens.contains(id)) {
                        let label = self.core.screens.statuses().into_iter().find(|s| &s.id == id).map(|s| s.name).unwrap_or_default();
                        let Some(latest) = self.core.screens.latest(id) else {
                            self.policy.forget_screen(id); // not shot: its change is still to come
                            continue;
                        };
                        let name = self.core.screens.shot_name(id);
                        let written = tokio::task::spawn_blocking(move || {
                            let r = handle.add_shot(name.as_deref(), &latest.image, Utc::now(), trigger);
                            (handle, r)
                        })
                        .await;
                        let (h, result) = match written {
                            Ok(done) => done,
                            Err(e) => {
                                // The interaction is lost from memory; its folder is finished at
                                // the next start.
                                self.log(&format!("error: writing a shot of {label} failed: {e}"));
                                return;
                            }
                        };
                        handle = h;
                        match result {
                            Ok(file) => taken.push(format!("{label} ({file})")),
                            Err(e) => {
                                self.policy.forget_screen(id);
                                self.log(&format!("error: cannot write a shot of {label}: {e}"));
                            }
                        }
                    }
                    let (bytes, count) = (handle.bytes(), handle.shot_count() as u32);
                    self.open = Some(handle);
                    self.log(&format!("{rule:?} shot {} · {}", store_trigger(trigger), taken.join(", ")));
                    // R9: past the size budget, the policy splits the interaction.
                    let split = self.policy.on_written(Instant::now(), bytes, count);
                    for d in split.into_iter().rev() {
                        queue.push_front(d);
                    }
                }
                Decision::EndInteraction { rule } => self.end(rule).await,
                Decision::Skipped { rule, why } => self.log(&format!("{rule:?} skipped: {why}")),
            }
        }
    }

    async fn end(&mut self, rule: Rule) {
        let Some(handle) = self.open.take() else { return };
        let name = handle.name().to_string();
        let (shots, bytes) = (handle.shot_count(), handle.bytes());
        match tokio::task::spawn_blocking(move || handle.finish()).await {
            Ok(Ok(Some(_))) => {
                self.core.recount();
                self.log(&format!("{rule:?} end {name} · {shots} shots, {:.1} MiB", bytes as f64 / 1048576.0));
                self.core.uploader.wake();
            }
            Ok(Ok(None)) => self.log(&format!("{rule:?} end {name} · no shots, nothing kept")),
            Ok(Err(e)) => self.log(&format!("error: cannot finish {name}: {e}")),
            Err(e) => self.log(&format!("error: finishing {name} failed: {e}")),
        }
    }

    fn log(&self, text: &str) {
        self.core.log.lock().unwrap().append(Utc::now(), text);
    }

    fn publish(&self) {
        let view = status_view(&self.policy.state(), &self.pause, &self.core);
        self.core.publish(view);
    }
}

/// Whether the machine slept between two ticks: the wall clock moved over 30 s further than
/// the monotonic clock (which stands still during a suspend).
fn slept(wall: chrono::TimeDelta, monotonic: Duration) -> bool {
    wall.to_std().is_ok_and(|w| w.saturating_sub(monotonic) > Duration::from_secs(30))
}

/// "2026-11-07T20:02:03Z<TAB>text" as a `DecisionLine`.
fn parse_decision(line: &str) -> Option<DecisionLine> {
    let (at, text) = line.split_once('\t')?;
    let at = DateTime::parse_from_rfc3339(at).ok()?.with_timezone(&Utc);
    Some(DecisionLine { at, text: text.to_string() })
}

fn kind_name(k: InteractionKind) -> &'static str {
    match k {
        InteractionKind::Active => "active",
        InteractionKind::Passive => "passive",
    }
}

fn store_trigger(t: crate::policy::Trigger) -> &'static str {
    crate::store::trigger_name(t)
}

/// `at` as wall-clock time, to the second (so the status only changes when it means to).
fn wall(at: Instant) -> DateTime<Utc> {
    let now_i = Instant::now();
    let now = Utc::now();
    let t = if at <= now_i { now - (now_i - at) } else { now + (at - now_i) };
    DateTime::from_timestamp(t.timestamp(), 0).unwrap_or(t)
}

fn blank_status() -> StatusView {
    StatusView {
        capture: CaptureView { mode: crate::types::CaptureMode::Idle, since: None, shots: 0, next_check_at: None, stopped_by: None },
        pause: Default::default(),
        upload: crate::types::UploadStatus { state: crate::types::UploadState::Ok, pending: 0, last_upload: None, failed: 0, last_failure: None },
        screens: 0,
        screens_live: 0,
        screens_attention: vec![],
        input: "ok".into(),
    }
}

fn status_view(state: &PolicyState, pause: &PauseState, core: &Core) -> StatusView {
    let statuses = core.screens.statuses();
    StatusView {
        capture: CaptureView {
            mode: state.mode,
            since: state.since.map(wall),
            shots: state.shots,
            next_check_at: state.next_check_at.map(wall),
            stopped_by: state.stopped_by,
        },
        pause: pause.view(Utc::now()),
        upload: core.uploader.status(),
        screens: statuses.len() as u32,
        screens_live: statuses.iter().filter(|s| s.state == ScreenState::Live).count() as u32,
        screens_attention: core.screens.needing_attention(),
        input: core.input.accessible().as_str().into(),
    }
}

#[cfg(test)]
pub(crate) mod tests;
