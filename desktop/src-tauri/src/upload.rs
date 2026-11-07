//! Uploads finished interactions to the server, oldest first, in the protocol's request format
//! (`protocol/vitality_protocol/routes.py`): `POST /interactions`, multipart, part
//! `interaction` (the `interaction.json` text) and one part `shots` per file, named by its file.
//!
//! What each reply does:
//!   201, 200            → `uploaded` marker, go on with the next
//!   403                 → keep pending, state "rejected", retry every 5 min
//!   400, 409, 413, 422  → `failed` marker for that interaction only (never retried; counted and
//!                         shown in Status), go on with the next
//!   no connection       → the server is unreachable: state "offline", wait 5 s doubling to 5 min
//!   anything else, a timeout → that interaction waits with its own backoff (5 s doubling to
//!                         5 min) while newer ones go on; state "offline"
//! Among the interactions that are due, the oldest goes first. A request may take 60 s plus 1 s
//! per MiB of body (at most 15 min); connecting at most 10 s. Retries are safe: the server answers
//! 200 for an interaction it already has. A folder whose `interaction.json` cannot be read is
//! rebuilt from its shots (`Store::repair`), not failed.
//!
//! `Uploader::start(store, server, device, timings)` runs it as a tokio task; `wake()` (a new
//! interaction finished) tries at once; `status()` is its state for Status and the tray.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

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
    pub connect_timeout: Duration,
    /// A request may take `request_base + request_per_mib × MiB`, at most `request_max`.
    pub request_base: Duration,
    pub request_per_mib: Duration,
    pub request_max: Duration,
}

impl Default for UploadTimings {
    fn default() -> Self {
        UploadTimings {
            backoff_start: Duration::from_secs(5),
            backoff_max: Duration::from_secs(300),
            rejected_retry: Duration::from_secs(300),
            idle_poll: Duration::from_secs(60),
            connect_timeout: Duration::from_secs(10),
            request_base: Duration::from_secs(60),
            request_per_mib: Duration::from_secs(1),
            request_max: Duration::from_secs(15 * 60),
        }
    }
}

impl UploadTimings {
    /// How long a request with a body of `bytes` may take.
    pub fn request_timeout(&self, bytes: u64) -> Duration {
        let mib = bytes.div_ceil(1024 * 1024).min(1 << 20) as u32;
        (self.request_base + self.request_per_mib * mib).min(self.request_max)
    }
}

pub type Reporter = Arc<dyn Fn(String) + Send + Sync>;

/// What the last real upload said about this device: passed the server's device check or
/// not (403), for which server, and when.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Acceptance {
    pub server: String,
    pub accepted: bool,
    pub at: chrono::DateTime<Utc>,
}

#[derive(Clone)]
pub struct Uploader {
    status: Arc<Mutex<UploadStatus>>,
    /// Told what happened, in plain words (the decisions log).
    reporter: Arc<Mutex<Option<Reporter>>>,
    acceptance: Arc<Mutex<Option<Acceptance>>>,
    server: Arc<Mutex<Option<String>>>,
    wake: Arc<Notify>,
}

/// What one attempt came to.
#[derive(Debug, PartialEq)]
enum Outcome {
    Uploaded,
    /// Refused for good (400, 409, 413, 422).
    Failed(u16, String),
    Rejected,
    /// The server could not be reached at all: wait for it.
    Unreachable(String),
    /// This interaction did not go through (a 5xx, a timeout…): retry it later.
    Retry(String),
    /// The folder cannot be read as it is: rebuild it.
    Unreadable(String),
}

/// One interaction's own wait after a retryable failure.
struct Retry {
    due: Instant,
    backoff: Duration,
}

impl Uploader {
    /// Starts uploading (on the current tokio runtime). `server` is the base URL
    /// ("http://vitality-librarian:8100"); `None` or blank disables upload. `device` is the name
    /// shown when the server refuses this device.
    pub fn start(store: Arc<Store>, server: Option<String>, device: String, timings: UploadTimings) -> Uploader {
        let server = server.map(|s| s.trim().trim_end_matches('/').to_string()).filter(|s| !s.is_empty());
        let initial = if server.is_some() { UploadState::Ok } else { UploadState::Disabled };
        let uploader = Uploader {
            server: Arc::new(Mutex::new(server.clone())),
            reporter: Arc::default(),
            acceptance: Arc::default(),
            status: Arc::new(Mutex::new(UploadStatus {
                state: initial,
                pending: store.pending().len() as u32,
                last_upload: None,
                failed: store.failed_count(),
                last_failure: None,
            })),
            wake: Arc::new(Notify::new()),
        };
        let u = uploader.clone();
        tokio::spawn(async move { u.run(store, device, timings).await });
        uploader
    }

    pub fn status(&self) -> UploadStatus {
        self.status.lock().unwrap().clone()
    }

    /// Try now (a new interaction is ready, or the network may be back).
    pub fn wake(&self) {
        self.wake.notify_one();
    }

    /// Where to tell what happened: each upload, each refusal, and when the server goes away or
    /// comes back.
    pub fn set_reporter(&self, reporter: Reporter) {
        *self.reporter.lock().unwrap() = Some(reporter);
    }

    /// The last upload's word on this device, if any.
    pub fn acceptance(&self) -> Option<Acceptance> {
        self.acceptance.lock().unwrap().clone()
    }

    fn accepted(&self, server: &str, accepted: bool) {
        *self.acceptance.lock().unwrap() = Some(Acceptance { server: server.to_string(), accepted, at: Utc::now() });
    }

    fn report(&self, line: String) {
        if let Some(r) = self.reporter.lock().unwrap().clone() {
            r(line);
        }
    }

    /// Uploads to another server from now on (`None`: no upload).
    pub fn set_server(&self, server: Option<String>) {
        let server = server.map(|s| s.trim().trim_end_matches('/').to_string()).filter(|s| !s.is_empty());
        let state = if server.is_some() { UploadState::Ok } else { UploadState::Disabled };
        *self.server.lock().unwrap() = server;
        self.set(|s| s.state = state);
        self.wake();
    }

    async fn run(&self, store: Arc<Store>, device: String, t: UploadTimings) {
        let client = match reqwest::Client::builder().connect_timeout(t.connect_timeout).build() {
            Ok(c) => c,
            Err(e) => {
                eprintln!("vitality-client: cannot make an HTTP client: {e}");
                return;
            }
        };
        let mut backoff = t.backoff_start;
        let mut retries: HashMap<PathBuf, Retry> = HashMap::new();
        loop {
            let pending = store.pending();
            retries.retain(|f, _| pending.contains(f));
            self.set(|s| {
                s.pending = pending.len() as u32;
                s.failed = store.failed_count();
            });
            let server = self.server.lock().unwrap().clone();
            let Some(server) = server.as_deref() else {
                self.set(|s| s.state = UploadState::Disabled);
                self.wake.notified().await;
                continue;
            };
            let mut wait = t.idle_poll;
            let (mut rejected, mut unreachable) = (false, false);
            let now = Instant::now();
            for folder in pending {
                if retries.get(&folder).is_some_and(|r| r.due > now) {
                    continue; // waiting its own turn; newer ones go on
                }
                match upload_one(&client, server, &folder, &t).await {
                    Outcome::Uploaded => {
                        self.accepted(server, true);
                        mark(&store, &folder, UPLOADED);
                        retries.remove(&folder);
                        self.report(format!("Uploaded · {}", folder_name(&store, &folder)));
                        backoff = t.backoff_start;
                        self.set(|s| {
                            s.pending = s.pending.saturating_sub(1);
                            s.last_upload = Some(Utc::now());
                        });
                    }
                    Outcome::Failed(code, why) => {
                        self.accepted(server, true); // past the device check: refused for its content
                        let name = folder_name(&store, &folder);
                        eprintln!("vitality-client: {name} was refused for good ({code}): {why}");
                        mark(&store, &folder, FAILED);
                        let what = match code {
                            413 => "too large for the server",
                            409 => "stored differently on the server",
                            _ => "refused as invalid",
                        };
                        self.report(format!("Upload refused for good — {what} · {name}"));
                        self.set(|s| {
                            s.pending = s.pending.saturating_sub(1);
                            s.failed += 1;
                            s.last_failure = Some(format!("{name}: {what} ({code})"));
                        });
                    }
                    Outcome::Unreadable(why) => {
                        eprintln!("vitality-client: {} is unreadable ({why}); rebuilding it from its shots", folder.display());
                        if let Err(e) = store.repair(&folder) {
                            eprintln!("vitality-client: cannot rebuild {}: {e}", folder.display());
                            retries.insert(folder.clone(), Retry { due: Instant::now() + t.backoff_max, backoff: t.backoff_max });
                        }
                    }
                    Outcome::Rejected => {
                        self.accepted(server, false);
                        rejected = true;
                        wait = t.rejected_retry;
                        break;
                    }
                    Outcome::Unreachable(why) => {
                        if self.status().state != UploadState::Offline {
                            eprintln!("vitality-client: the server is unreachable ({why}); retrying");
                        }
                        unreachable = true;
                        wait = backoff;
                        backoff = (backoff * 2).min(t.backoff_max);
                        break;
                    }
                    Outcome::Retry(why) => {
                        let r = retries.entry(folder.clone()).or_insert(Retry { due: now, backoff: t.backoff_start / 2 });
                        r.backoff = (r.backoff * 2).clamp(t.backoff_start, t.backoff_max);
                        r.due = Instant::now() + r.backoff;
                        eprintln!("vitality-client: {} did not go through ({why}); again in {:?}", folder.display(), r.backoff);
                    }
                }
            }
            let now = Instant::now();
            let state = if rejected {
                UploadState::Rejected { device: device.clone() }
            } else if unreachable || !retries.is_empty() {
                UploadState::Offline
            } else {
                UploadState::Ok
            };
            let before = self.status().state;
            if state != before {
                match &state {
                    UploadState::Rejected { device } => self.report(format!("Upload refused — the server does not accept this device (\"{device}\")")),
                    UploadState::Offline => self.report("Server unreachable — uploads will be retried".into()),
                    UploadState::Ok if before != UploadState::Disabled => self.report("Server reachable again — uploading".into()),
                    _ => {}
                }
            }
            self.set(|s| s.state = state);
            if let Some(next) = retries.values().map(|r| r.due).min() {
                wait = wait.min(next.saturating_duration_since(now));
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

fn folder_name(store: &Store, folder: &Path) -> String {
    folder.strip_prefix(store.root()).unwrap_or(folder).to_string_lossy().into_owned()
}

fn mark(store: &Store, folder: &Path, marker: &str) {
    if let Err(e) = store.mark(folder, marker) {
        eprintln!("vitality-client: cannot mark {} {marker}: {e}", folder.display());
    }
}

/// The multipart form for one interaction folder, and its size.
async fn form(folder: &Path) -> Result<(reqwest::multipart::Form, u64), String> {
    let json = tokio::fs::read(folder.join(INTERACTION_FILE)).await.map_err(|e| format!("cannot read {INTERACTION_FILE}: {e}"))?;
    let interaction: InteractionJson = serde_json::from_slice(&json).map_err(|e| format!("{INTERACTION_FILE} is invalid: {e}"))?;
    let text = String::from_utf8(json).map_err(|_| format!("{INTERACTION_FILE} is not UTF-8"))?;
    let mut size = text.len() as u64;
    let mut form = reqwest::multipart::Form::new().text("interaction", text);
    for shot in interaction.shots {
        let bytes = tokio::fs::read(folder.join(&shot.file)).await.map_err(|e| format!("cannot read {}: {e}", shot.file))?;
        if bytes.len() as u64 != shot.bytes {
            return Err(format!("{} is {} bytes, not {}", shot.file, bytes.len(), shot.bytes));
        }
        size += bytes.len() as u64;
        let part = reqwest::multipart::Part::bytes(bytes).file_name(shot.file).mime_str("image/webp").map_err(|e| e.to_string())?;
        form = form.part("shots", part);
    }
    Ok((form, size))
}

async fn upload_one(client: &reqwest::Client, server: &str, folder: &Path, t: &UploadTimings) -> Outcome {
    let (form, size) = match form(folder).await {
        Ok(f) => f,
        Err(why) => return Outcome::Unreadable(why),
    };
    let reply = client.post(format!("{server}{INTERACTIONS_PATH}")).timeout(t.request_timeout(size)).multipart(form).send().await;
    match reply {
        Err(e) if e.is_connect() => Outcome::Unreachable(e.to_string()),
        Err(e) => Outcome::Retry(e.to_string()),
        Ok(r) => match r.status().as_u16() {
            200 | 201 => Outcome::Uploaded,
            403 => Outcome::Rejected,
            code @ (400 | 409 | 413 | 422) => Outcome::Failed(code, r.text().await.unwrap_or_default()),
            code => Outcome::Retry(format!("the server answered {code}")),
        },
    }
}

/// This machine's name, which Tailscale normally uses as the device's name.
pub fn device_name() -> String {
    std::fs::read_to_string("/proc/sys/kernel/hostname").map(|s| s.trim().to_string()).unwrap_or_else(|_| "this device".into())
}

#[cfg(test)]
pub(crate) mod tests;
