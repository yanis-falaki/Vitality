//! Uploads finished interactions to the server, oldest first, in the protocol's request format
//! (`protocol/vitality_protocol/routes.py`): `POST /interactions`, multipart, part
//! `interaction` (the `interaction.json` text) and one part `shots` per file, named by its file.
//!
//! What each reply does (as `routes.py` documents):
//!   201, 200          → `uploaded` marker, go on with the next
//!   403               → keep pending, state "rejected", retry every 5 min
//!   409, 413, 422     → `failed` marker (never retried), go on with the next
//!   anything else, a timeout, no connection → keep pending, state "offline", retry after
//!                       5 s, doubling up to 5 min
//! Retries are safe: the server answers 200 for an interaction it already has.
//!
//! `Uploader::start(store, server, device, timings)` runs it as a tokio task; `wake()` (a new
//! interaction finished) tries at once; `status()` is its state for Status and the tray.

use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::Utc;
use tokio::sync::Notify;

use crate::store::{InteractionJson, Store, FAILED, INTERACTION_FILE, UPLOADED};
use crate::types::{UploadState, UploadStatus};

pub const INTERACTIONS_PATH: &str = "/interactions";

#[derive(Debug, Clone)]
pub struct UploadTimings {
    pub backoff_start: Duration,
    pub backoff_max: Duration,
    pub rejected_retry: Duration,
    /// How often to look for pending interactions with nothing else to do.
    pub idle_poll: Duration,
    pub request_timeout: Duration,
}

impl Default for UploadTimings {
    fn default() -> Self {
        UploadTimings {
            backoff_start: Duration::from_secs(5),
            backoff_max: Duration::from_secs(300),
            rejected_retry: Duration::from_secs(300),
            idle_poll: Duration::from_secs(60),
            request_timeout: Duration::from_secs(120),
        }
    }
}

#[derive(Clone)]
pub struct Uploader {
    status: Arc<Mutex<UploadStatus>>,
    wake: Arc<Notify>,
}

/// What one attempt came to.
#[derive(Debug, PartialEq)]
enum Outcome {
    Uploaded,
    Failed(String),
    Rejected,
    Offline(String),
}

impl Uploader {
    /// Starts uploading (on the current tokio runtime). `server` is the base URL
    /// ("http://vitality-librarian:8100"); `None` or blank disables upload. `device` is the name
    /// shown when the server refuses this device.
    pub fn start(store: Arc<Store>, server: Option<String>, device: String, timings: UploadTimings) -> Uploader {
        let server = server.map(|s| s.trim().trim_end_matches('/').to_string()).filter(|s| !s.is_empty());
        let initial = if server.is_some() { UploadState::Ok } else { UploadState::Disabled };
        let uploader = Uploader {
            status: Arc::new(Mutex::new(UploadStatus { state: initial, pending: store.pending().len() as u32, last_upload: None })),
            wake: Arc::new(Notify::new()),
        };
        let u = uploader.clone();
        tokio::spawn(async move { u.run(store, server, device, timings).await });
        uploader
    }

    pub fn status(&self) -> UploadStatus {
        self.status.lock().unwrap().clone()
    }

    /// Try now (a new interaction is ready, or the network may be back).
    pub fn wake(&self) {
        self.wake.notify_one();
    }

    async fn run(&self, store: Arc<Store>, server: Option<String>, device: String, t: UploadTimings) {
        let client = match reqwest::Client::builder().timeout(t.request_timeout).build() {
            Ok(c) => c,
            Err(e) => {
                eprintln!("vitality-client: cannot make an HTTP client: {e}");
                return;
            }
        };
        let mut backoff = t.backoff_start;
        loop {
            let pending = store.pending();
            self.set(|s| s.pending = pending.len() as u32);
            let Some(server) = server.as_deref() else {
                self.wake.notified().await;
                continue;
            };
            let mut wait = t.idle_poll;
            for folder in pending {
                match upload_one(&client, server, &folder).await {
                    Outcome::Uploaded => {
                        mark(&store, &folder, UPLOADED);
                        backoff = t.backoff_start;
                        self.set(|s| {
                            s.state = UploadState::Ok;
                            s.pending = s.pending.saturating_sub(1);
                            s.last_upload = Some(Utc::now());
                        });
                    }
                    Outcome::Failed(why) => {
                        eprintln!("vitality-client: {} was refused for good: {why}", folder.display());
                        mark(&store, &folder, FAILED);
                        self.set(|s| s.pending = s.pending.saturating_sub(1));
                    }
                    Outcome::Rejected => {
                        self.set(|s| s.state = UploadState::Rejected { device: device.clone() });
                        wait = t.rejected_retry;
                        break;
                    }
                    Outcome::Offline(why) => {
                        if self.status().state != UploadState::Offline {
                            eprintln!("vitality-client: the server is unreachable ({why}); retrying");
                        }
                        self.set(|s| s.state = UploadState::Offline);
                        wait = backoff;
                        backoff = (backoff * 2).min(t.backoff_max);
                        break;
                    }
                }
            }
            tokio::select! {
                _ = tokio::time::sleep(wait) => {}
                _ = self.wake.notified() => {}
            }
        }
    }

    fn set(&self, f: impl FnOnce(&mut UploadStatus)) {
        f(&mut self.status.lock().unwrap());
    }
}

fn mark(store: &Store, folder: &Path, marker: &str) {
    if let Err(e) = store.mark(folder, marker) {
        eprintln!("vitality-client: cannot mark {} {marker}: {e}", folder.display());
    }
}

/// The multipart form for one interaction folder.
async fn form(folder: &Path) -> Result<reqwest::multipart::Form, String> {
    let json = tokio::fs::read(folder.join(INTERACTION_FILE)).await.map_err(|e| format!("cannot read {INTERACTION_FILE}: {e}"))?;
    let interaction: InteractionJson = serde_json::from_slice(&json).map_err(|e| format!("{INTERACTION_FILE} is invalid: {e}"))?;
    let text = String::from_utf8(json).map_err(|_| format!("{INTERACTION_FILE} is not UTF-8"))?;
    let mut form = reqwest::multipart::Form::new().text("interaction", text);
    for shot in interaction.shots {
        let bytes = tokio::fs::read(folder.join(&shot.file)).await.map_err(|e| format!("cannot read {}: {e}", shot.file))?;
        let part = reqwest::multipart::Part::bytes(bytes).file_name(shot.file).mime_str("image/webp").map_err(|e| e.to_string())?;
        form = form.part("shots", part);
    }
    Ok(form)
}

async fn upload_one(client: &reqwest::Client, server: &str, folder: &Path) -> Outcome {
    let form = match form(folder).await {
        Ok(f) => f,
        // A folder that cannot be sent as it is would fail the same way every time.
        Err(why) => return Outcome::Failed(why),
    };
    let reply = client.post(format!("{server}{INTERACTIONS_PATH}")).multipart(form).send().await;
    match reply {
        Err(e) => Outcome::Offline(e.to_string()),
        Ok(r) => match r.status().as_u16() {
            200 | 201 => Outcome::Uploaded,
            403 => Outcome::Rejected,
            code @ (409 | 413 | 422) => Outcome::Failed(format!("{code} {}", r.text().await.unwrap_or_default())),
            code => Outcome::Offline(format!("the server answered {code}")),
        },
    }
}

/// This machine's name, which Tailscale normally uses as the device's name.
pub fn device_name() -> String {
    std::fs::read_to_string("/proc/sys/kernel/hostname").map(|s| s.trim().to_string()).unwrap_or_else(|_| "this device".into())
}

#[cfg(test)]
pub(crate) mod tests;
