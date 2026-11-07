//! The session's lock and the system's sleep, from logind (system bus) and the screensaver
//! interface (session bus, where a desktop offers it).
//!
//! `SessionWatch::subscribe()` gives a stream of `SessionEvent`s, starting with the current
//! state (so a late subscriber misses nothing): `Locked { locked, by }` on every lock change,
//! `by` naming the signal that caused it (for the decisions log), and `Sleeping(bool)` from
//! logind's `PrepareForSleep`.
//!
//! Lock is decided by `LockTracker` (pure): locked on logind's `Lock` signal, `LockedHint=true`
//! or the screensaver turning active; unlocked on `Unlock`, `LockedHint=false` or the
//! screensaver turning inactive; and, because some lockers never send an unlock, while locked a
//! poll every 5 s unlocks when `LockedHint` is false and no screensaver is active.
//! A display that sleeps on its own (DPMS) is not visible portably and is not reported.
//! `LogindWatch` is the real one; `FakeSession` is for tests.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::StreamExt;
use tokio::sync::mpsc;
use zbus::zvariant::OwnedObjectPath;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionEvent {
    Locked { locked: bool, by: String },
    Sleeping(bool),
}

pub trait SessionWatch: Send + Sync {
    fn subscribe(&self) -> mpsc::Receiver<SessionEvent>;
}

/// What can change the lock state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockInput {
    LockSignal,
    UnlockSignal,
    LockedHint(bool),
    ScreenSaver(bool),
    /// The 5 s poll while locked: logind's `LockedHint` and the screensaver's state, if any.
    Poll { locked_hint: bool, screensaver: Option<bool> },
}

#[derive(Debug, Default)]
pub struct LockTracker {
    pub locked: bool,
}

impl LockTracker {
    /// The new state and what caused it, when `input` changes it.
    pub fn apply(&mut self, input: LockInput) -> Option<(bool, &'static str)> {
        let (to, by) = match input {
            LockInput::LockSignal => (true, "logind Lock"),
            LockInput::UnlockSignal => (false, "logind Unlock"),
            LockInput::LockedHint(v) => (v, "logind LockedHint"),
            LockInput::ScreenSaver(v) => (v, "ScreenSaver ActiveChanged"),
            LockInput::Poll { locked_hint, screensaver } => {
                if !self.locked || locked_hint || screensaver == Some(true) {
                    return None;
                }
                (false, "poll: LockedHint false and no screensaver active")
            }
        };
        if to == self.locked {
            return None;
        }
        self.locked = to;
        Some((to, by))
    }
}

/// Subscribers, each sent the current state when it subscribes.
#[derive(Default, Clone)]
struct Subscribers(Arc<Mutex<SubscribersInner>>);

#[derive(Default)]
struct SubscribersInner {
    txs: Vec<mpsc::Sender<SessionEvent>>,
    locked: Option<SessionEvent>,
    sleeping: Option<SessionEvent>,
}

impl Subscribers {
    fn add(&self) -> mpsc::Receiver<SessionEvent> {
        let (tx, rx) = mpsc::channel(16);
        let mut inner = self.0.lock().unwrap();
        for e in [&inner.locked, &inner.sleeping].into_iter().flatten() {
            let _ = tx.try_send(e.clone());
        }
        inner.txs.push(tx);
        rx
    }

    fn send(&self, event: SessionEvent) {
        let mut inner = self.0.lock().unwrap();
        match &event {
            SessionEvent::Locked { .. } => inner.locked = Some(event.clone()),
            SessionEvent::Sleeping(_) => inner.sleeping = Some(event.clone()),
        }
        inner.txs.retain(|tx| !matches!(tx.try_send(event.clone()), Err(mpsc::error::TrySendError::Closed(_))));
    }
}

/// Watches logind and the screensaver; `start()` must run inside the tokio runtime.
pub struct LogindWatch {
    subscribers: Subscribers,
}

impl LogindWatch {
    pub fn start() -> LogindWatch {
        let subscribers = Subscribers::default();
        let s = subscribers.clone();
        tokio::spawn(async move {
            if let Err(e) = watch(s).await {
                eprintln!("vitality-client: cannot watch the session's lock and sleep: {e}");
            }
        });
        LogindWatch { subscribers }
    }
}

impl SessionWatch for LogindWatch {
    fn subscribe(&self) -> mpsc::Receiver<SessionEvent> {
        self.subscribers.add()
    }
}

#[zbus::proxy(
    interface = "org.freedesktop.login1.Manager",
    default_service = "org.freedesktop.login1",
    default_path = "/org/freedesktop/login1"
)]
trait Manager {
    fn get_session(&self, session_id: &str) -> zbus::Result<OwnedObjectPath>;
    #[zbus(signal)]
    fn prepare_for_sleep(&self, start: bool) -> zbus::Result<()>;
}

#[zbus::proxy(
    interface = "org.freedesktop.login1.User",
    default_service = "org.freedesktop.login1",
    default_path = "/org/freedesktop/login1/user/self"
)]
trait User {
    #[zbus(property)]
    fn display(&self) -> zbus::Result<(String, OwnedObjectPath)>;
}

#[zbus::proxy(interface = "org.freedesktop.login1.Session", default_service = "org.freedesktop.login1")]
trait Session {
    #[zbus(property)]
    fn locked_hint(&self) -> zbus::Result<bool>;
    #[zbus(signal)]
    fn lock(&self) -> zbus::Result<()>;
    #[zbus(signal)]
    fn unlock(&self) -> zbus::Result<()>;
}

#[zbus::proxy(
    interface = "org.freedesktop.ScreenSaver",
    default_service = "org.freedesktop.ScreenSaver",
    default_path = "/org/freedesktop/ScreenSaver"
)]
trait ScreenSaver {
    fn get_active(&self) -> zbus::Result<bool>;
    #[zbus(signal)]
    fn active_changed(&self, active: bool) -> zbus::Result<()>;
}

/// The graphical session's object: the user's display session (right for a systemd user
/// service, which is outside any session), else `$XDG_SESSION_ID`.
async fn session_path(conn: &zbus::Connection, manager: &ManagerProxy<'_>) -> zbus::Result<OwnedObjectPath> {
    if let Ok((id, path)) = UserProxy::new(conn).await?.display().await {
        if !id.is_empty() {
            return Ok(path);
        }
    }
    let id = std::env::var("XDG_SESSION_ID").map_err(|_| zbus::Error::Failure("no graphical session found".into()))?;
    manager.get_session(&id).await
}

async fn watch(subscribers: Subscribers) -> zbus::Result<()> {
    let conn = zbus::Connection::system().await?;
    let manager = ManagerProxy::new(&conn).await?;
    let path = session_path(&conn, &manager).await?;
    let session = SessionProxy::builder(&conn).path(path)?.build().await?;
    let mut sleeps = manager.receive_prepare_for_sleep().await?;
    let mut hints = session.receive_locked_hint_changed().await;
    let mut locks = session.receive_lock().await?;
    let mut unlocks = session.receive_unlock().await?;
    // The screensaver is optional: many desktops (Hyprland, Sway) have none.
    let saver = match zbus::Connection::session().await {
        Ok(c) => ScreenSaverProxy::new(&c).await.ok(),
        Err(_) => None,
    };
    let mut saver_changes = match &saver {
        Some(s) => s.receive_active_changed().await.ok(),
        None => None,
    };
    let saver_active = async |s: &Option<ScreenSaverProxy<'_>>| match s {
        Some(s) => s.get_active().await.ok(),
        None => None,
    };

    let mut tracker = LockTracker::default();
    let emit = |tracker: &mut LockTracker, input: LockInput| {
        if let Some((locked, by)) = tracker.apply(input) {
            subscribers.send(SessionEvent::Locked { locked, by: by.to_string() });
        }
    };
    let hint = session.locked_hint().await.unwrap_or(false);
    let active = saver_active(&saver).await.unwrap_or(false);
    // The current state, so the first subscriber knows it even when nothing is locked.
    tracker.locked = hint || active;
    subscribers.send(SessionEvent::Locked { locked: tracker.locked, by: "at start".into() });
    let mut poll = tokio::time::interval(Duration::from_secs(5));
    poll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            Some(change) = hints.next() => {
                if let Ok(v) = change.get().await {
                    emit(&mut tracker, LockInput::LockedHint(v));
                }
            }
            Some(_) = locks.next() => emit(&mut tracker, LockInput::LockSignal),
            Some(_) = unlocks.next() => emit(&mut tracker, LockInput::UnlockSignal),
            Some(signal) = async {
                match saver_changes.as_mut() {
                    Some(s) => s.next().await,
                    None => std::future::pending().await,
                }
            } => {
                if let Ok(args) = signal.args() {
                    emit(&mut tracker, LockInput::ScreenSaver(args.active));
                }
            }
            Some(signal) = sleeps.next() => {
                if let Ok(args) = signal.args() {
                    subscribers.send(SessionEvent::Sleeping(args.start));
                }
            }
            _ = poll.tick() => {
                if tracker.locked {
                    let locked_hint = session.locked_hint().await.unwrap_or(false);
                    let screensaver = saver_active(&saver).await;
                    emit(&mut tracker, LockInput::Poll { locked_hint, screensaver });
                }
            }
        }
    }
}

/// A session for tests: `send(event)` reaches every subscriber.
#[cfg(test)]
#[derive(Default)]
pub struct FakeSession {
    subscribers: Subscribers,
}

#[cfg(test)]
impl FakeSession {
    pub fn send(&self, event: SessionEvent) {
        self.subscribers.send(event);
    }

    pub fn lock(&self, locked: bool) {
        self.send(SessionEvent::Locked { locked, by: "test".into() });
    }
}

#[cfg(test)]
impl SessionWatch for FakeSession {
    fn subscribe(&self) -> mpsc::Receiver<SessionEvent> {
        self.subscribers.add()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn events_reach_every_subscriber_and_closed_ones_are_dropped() {
        let fake = FakeSession::default();
        let mut a = fake.subscribe();
        let b = fake.subscribe();
        drop(b);
        fake.lock(true);
        fake.send(SessionEvent::Sleeping(true));
        assert_eq!(a.recv().await, Some(SessionEvent::Locked { locked: true, by: "test".into() }));
        assert_eq!(a.recv().await, Some(SessionEvent::Sleeping(true)));
        assert_eq!(fake.subscribers.0.lock().unwrap().txs.len(), 1);
    }

    #[tokio::test]
    async fn a_late_subscriber_gets_the_current_state_first() {
        // I6: the initial "locked" must not be lost when the runtime subscribes after it.
        let fake = FakeSession::default();
        fake.lock(true);
        fake.send(SessionEvent::Sleeping(false));
        let mut late = fake.subscribe();
        assert_eq!(late.recv().await, Some(SessionEvent::Locked { locked: true, by: "test".into() }));
        assert_eq!(late.recv().await, Some(SessionEvent::Sleeping(false)));
    }

    #[test]
    fn any_lock_source_locks_and_any_unlock_source_unlocks() {
        let mut t = LockTracker::default();
        assert_eq!(t.apply(LockInput::LockSignal), Some((true, "logind Lock")));
        assert_eq!(t.apply(LockInput::LockedHint(true)), None, "already locked");
        assert_eq!(t.apply(LockInput::UnlockSignal), Some((false, "logind Unlock")));
        assert_eq!(t.apply(LockInput::ScreenSaver(true)), Some((true, "ScreenSaver ActiveChanged")));
        assert_eq!(t.apply(LockInput::ScreenSaver(false)), Some((false, "ScreenSaver ActiveChanged")));
        assert_eq!(t.apply(LockInput::LockedHint(true)), Some((true, "logind LockedHint")));
        assert_eq!(t.apply(LockInput::LockedHint(false)), Some((false, "logind LockedHint")));
    }

    #[test]
    fn a_lock_signal_without_an_unlock_is_undone_by_the_poll_when_logind_says_unlocked() {
        let mut t = LockTracker::default();
        t.apply(LockInput::LockSignal);
        // The screensaver still active: stay locked.
        assert_eq!(t.apply(LockInput::Poll { locked_hint: false, screensaver: Some(true) }), None);
        assert_eq!(t.apply(LockInput::Poll { locked_hint: true, screensaver: None }), None);
        assert_eq!(
            t.apply(LockInput::Poll { locked_hint: false, screensaver: None }),
            Some((false, "poll: LockedHint false and no screensaver active"))
        );
        assert!(!t.locked);
        // While unlocked the poll does nothing.
        assert_eq!(t.apply(LockInput::Poll { locked_hint: false, screensaver: None }), None);
    }
}
