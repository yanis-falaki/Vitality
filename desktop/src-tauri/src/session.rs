//! The session's lock and the system's sleep, from logind over the system D-Bus.
//!
//! `SessionWatch::subscribe()` gives a stream of `SessionEvent`s: `Locked(bool)` when the
//! session's `LockedHint` changes (and once at start), `Sleeping(bool)` from the manager's
//! `PrepareForSleep` signal. `LogindWatch` is the real one; `FakeSession` is for tests.
//! A display that sleeps on its own (DPMS) is not visible portably and is not reported.

use std::sync::{Arc, Mutex};

use futures_util::StreamExt;
use tokio::sync::mpsc;
use zbus::zvariant::OwnedObjectPath;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionEvent {
    Locked(bool),
    Sleeping(bool),
}

pub trait SessionWatch: Send + Sync {
    fn subscribe(&self) -> mpsc::Receiver<SessionEvent>;
}

#[derive(Default, Clone)]
struct Subscribers(Arc<Mutex<Vec<mpsc::Sender<SessionEvent>>>>);

impl Subscribers {
    fn add(&self) -> mpsc::Receiver<SessionEvent> {
        let (tx, rx) = mpsc::channel(16);
        self.0.lock().unwrap().push(tx);
        rx
    }

    fn send(&self, event: SessionEvent) {
        self.0.lock().unwrap().retain(|tx| !matches!(tx.try_send(event), Err(mpsc::error::TrySendError::Closed(_))));
    }
}

/// Watches logind; `start()` must run inside the tokio runtime.
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
    let mut locks = session.receive_locked_hint_changed().await;
    subscribers.send(SessionEvent::Locked(session.locked_hint().await.unwrap_or(false)));
    loop {
        tokio::select! {
            Some(change) = locks.next() => {
                if let Ok(locked) = change.get().await {
                    subscribers.send(SessionEvent::Locked(locked));
                }
            }
            Some(signal) = sleeps.next() => {
                if let Ok(args) = signal.args() {
                    subscribers.send(SessionEvent::Sleeping(args.start));
                }
            }
            else => return Ok(()),
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
        fake.send(SessionEvent::Locked(true));
        fake.send(SessionEvent::Sleeping(true));
        assert_eq!(a.recv().await, Some(SessionEvent::Locked(true)));
        assert_eq!(a.recv().await, Some(SessionEvent::Sleeping(true)));
        assert_eq!(fake.subscribers.0.lock().unwrap().len(), 1);
    }
}
