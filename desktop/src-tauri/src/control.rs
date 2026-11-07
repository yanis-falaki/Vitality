//! The control socket (`$XDG_RUNTIME_DIR/vitality/client.sock`) through which the CLI talks to
//! the running app: one JSON object per line each way.
//!
//! Requests: `{"cmd":"status"}`, `{"cmd":"pause","minutes":30}` (`minutes` left out = until
//! resumed), `{"cmd":"resume"}`, `{"cmd":"show"}` (open the window; a second start of the app
//! sends it), `{"cmd":"screens"}` (adds `"screens"`), `{"cmd":"checks"}` (adds `"checks"`, the
//! app's health checks as last run; both for `doctor`). Replies: `{"ok":true,"status":{…StatusView…}}` or `{"ok":false,"error":"…"}`.
//!
//! `serve(path, core)` binds the socket (only the owner may use it) and answers on the tokio
//! runtime; `request(path, req)` is the blocking client the CLI uses.

use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt};

use crate::runtime::Core;
use crate::state::PauseFor;
use crate::types::StatusView;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum ControlRequest {
    Status,
    Pause {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        minutes: Option<u32>,
    },
    Resume,
    Show,
    /// The screens (for `doctor`).
    Screens,
    /// The app's health checks as last run (for `doctor`; `checks` is absent before the first).
    Checks,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ControlReply {
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<StatusView>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub screens: Option<crate::types::ScreensView>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub checks: Option<Vec<crate::types::Check>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Binds the socket and answers requests until the app ends. Fails if another instance answers
/// on it already.
pub fn serve(path: &Path, core: Arc<Core>) -> io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    }
    if path.exists() {
        if UnixStream::connect(path).is_ok() {
            return Err(io::Error::new(io::ErrorKind::AddrInUse, "another vitality-client is running"));
        }
        std::fs::remove_file(path)?; // left by a crash
    }
    let listener = tokio::net::UnixListener::bind(path)?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    tokio::spawn(async move {
        loop {
            let Ok((stream, _)) = listener.accept().await else { continue };
            let core = core.clone();
            tokio::spawn(async move {
                let (read, mut write) = stream.into_split();
                let mut lines = tokio::io::BufReader::new(read).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    let reply = answer(&core, &line).await;
                    let mut text = serde_json::to_string(&reply).expect("serialisable");
                    text.push('\n');
                    if write.write_all(text.as_bytes()).await.is_err() {
                        break;
                    }
                }
            });
        }
    });
    Ok(())
}

async fn answer(core: &Core, line: &str) -> ControlReply {
    let request: ControlRequest = match serde_json::from_str(line) {
        Ok(r) => r,
        Err(e) => return ControlReply { ok: false, error: Some(format!("bad request: {e}")), ..ControlReply::default() },
    };
    let status = match request {
        ControlRequest::Status => core.status(),
        ControlRequest::Pause { minutes: Some(m) } => core.pause(PauseFor::Minutes(m)).await,
        ControlRequest::Pause { minutes: None } => core.pause(PauseFor::UntilResumed).await,
        ControlRequest::Resume => core.resume().await,
        ControlRequest::Show => {
            core.show_window();
            core.status()
        }
        ControlRequest::Screens => {
            return ControlReply { ok: true, status: Some(core.status()), screens: Some(core.screens.view()), ..ControlReply::default() };
        }
        ControlRequest::Checks => {
            return ControlReply { ok: true, status: Some(core.status()), checks: core.checks(), ..ControlReply::default() };
        }
    };
    ControlReply { ok: true, status: Some(status), ..ControlReply::default() }
}

/// Holds the one-instance lock (`client.lock` beside the socket) for as long as the returned
/// file lives; fails with `WouldBlock` while another instance holds it. Taken before the core
/// starts, so a second instance never touches the first one's open interaction.
pub fn lock_instance(socket: &Path) -> io::Result<std::fs::File> {
    use std::os::fd::AsRawFd;
    let path = socket.with_file_name("client.lock");
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    }
    let file = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(&path)?;
    // SAFETY: flock on a file descriptor we own.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(file)
}

/// Sends one request to the running app and returns its reply (blocking, 5 s at most).
pub fn request(path: &Path, request: &ControlRequest) -> io::Result<ControlReply> {
    let mut stream = UnixStream::connect(path)?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    let mut line = serde_json::to_string(request)?;
    line.push('\n');
    stream.write_all(line.as_bytes())?;
    let mut reply = String::new();
    BufReader::new(stream).read_line(&mut reply)?;
    serde_json::from_str(&reply).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requests_are_the_documented_json() {
        let json = |r: &ControlRequest| serde_json::to_string(r).unwrap();
        assert_eq!(json(&ControlRequest::Status), r#"{"cmd":"status"}"#);
        assert_eq!(json(&ControlRequest::Pause { minutes: Some(30) }), r#"{"cmd":"pause","minutes":30}"#);
        assert_eq!(json(&ControlRequest::Pause { minutes: None }), r#"{"cmd":"pause"}"#);
        assert_eq!(json(&ControlRequest::Resume), r#"{"cmd":"resume"}"#);
        let parsed: ControlRequest = serde_json::from_str(r#"{"cmd":"pause"}"#).unwrap();
        assert_eq!(parsed, ControlRequest::Pause { minutes: None });
    }

    // The round trip through a running core is in `crate::runtime::tests` style, below.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn round_trip_status_pause_resume_and_bad_requests() {
        let rig = crate::runtime::tests::bare_core().await;
        let path = rig.dir.path().join("run/vitality/client.sock");
        serve(&path, rig.core.clone()).unwrap();
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        let p = path.clone();
        let (status, paused, resumed, again) = tokio::task::spawn_blocking(move || {
            let status = request(&p, &ControlRequest::Status).unwrap();
            let paused = request(&p, &ControlRequest::Pause { minutes: Some(30) }).unwrap();
            let resumed = request(&p, &ControlRequest::Resume).unwrap();
            // A second instance would see the socket taken.
            (status, paused, resumed, UnixStream::connect(&p).is_ok())
        })
        .await
        .unwrap();
        assert!(status.ok && !status.status.unwrap().pause.paused);
        let paused = paused.status.unwrap();
        assert!(paused.pause.paused && paused.pause.until.is_some());
        assert!(!resumed.status.unwrap().pause.paused);
        assert!(again);
        assert_eq!(serve(&path, rig.core.clone()).unwrap_err().kind(), io::ErrorKind::AddrInUse);
        // A bad line gets an error reply, not a hang.
        let p = path.clone();
        let reply = tokio::task::spawn_blocking(move || {
            let mut s = UnixStream::connect(&p).unwrap();
            s.write_all(b"{\"cmd\":\"dance\"}\n").unwrap();
            let mut line = String::new();
            BufReader::new(s).read_line(&mut line).unwrap();
            line
        })
        .await
        .unwrap();
        assert!(reply.contains("\"ok\":false") && reply.contains("bad request"), "{reply}");
    }

    #[test]
    fn only_one_instance_holds_the_lock() {
        let dir = tempfile::tempdir().unwrap();
        let socket = dir.path().join("vitality/client.sock");
        let first = lock_instance(&socket).unwrap();
        assert_eq!(lock_instance(&socket).unwrap_err().kind(), io::ErrorKind::WouldBlock);
        drop(first);
        assert!(lock_instance(&socket).is_ok());
    }

    #[test]
    fn a_stale_socket_file_is_replaced_and_no_app_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.sock");
        assert!(request(&path, &ControlRequest::Status).is_err());
        std::os::unix::net::UnixListener::bind(&path).unwrap(); // bound and dropped: stale
        assert!(request(&path, &ControlRequest::Status).is_err());
    }
}
