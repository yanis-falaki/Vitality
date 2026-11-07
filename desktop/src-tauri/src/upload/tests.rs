//! The uploader against an in-process fake server (axum) and a store in a temporary folder.

use std::collections::VecDeque;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::extract::{Multipart, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use chrono::{FixedOffset, TimeZone};
use image::{Rgb, RgbImage};

use super::*;
use crate::policy::Trigger;

/// One part of a received request.
#[derive(Debug, Clone)]
pub struct ReceivedPart {
    pub name: String,
    pub file_name: Option<String>,
    pub content_type: Option<String>,
    pub bytes: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct Received {
    /// The status the fake answered.
    pub status: u16,
    pub content_length: Option<String>,
    pub transfer_encoding: bool,
    pub parts: Vec<ReceivedPart>,
}

impl Received {
    pub fn interaction(&self) -> serde_json::Value {
        let part = self.parts.iter().find(|p| p.name == "interaction").expect("no interaction part");
        serde_json::from_slice(&part.bytes).unwrap()
    }
    pub fn name(&self) -> String {
        self.interaction()["name"].as_str().unwrap().to_string()
    }
}

/// Answers each request with the next scripted status (then `then`), recording what came.
#[derive(Clone, Default)]
pub struct FakeServer {
    /// Interactions (by name) answered with a fixed status, whatever the script says.
    pub fixed: Arc<Mutex<std::collections::HashMap<String, u16>>>,
    /// Interactions (by name) whose reply never comes (a stalled upload).
    pub stall: Arc<Mutex<std::collections::HashSet<String>>>,
    pub script: Arc<Mutex<VecDeque<u16>>>,
    pub then: Arc<Mutex<u16>>,
    pub received: Arc<Mutex<Vec<Received>>>,
}

impl FakeServer {
    /// Serves on 127.0.0.1; returns its base URL.
    pub async fn serve(&self) -> String {
        *self.then.lock().unwrap() = 201;
        let app = axum::Router::new().route("/interactions", post(handle)).with_state(self.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        format!("http://{addr}")
    }

    pub fn names(&self) -> Vec<String> {
        self.received.lock().unwrap().iter().map(Received::name).collect()
    }
}

async fn handle(State(server): State<FakeServer>, headers: HeaderMap, mut multipart: Multipart) -> (StatusCode, String) {
    let mut parts = vec![];
    while let Some(field) = multipart.next_field().await.unwrap() {
        parts.push(ReceivedPart {
            name: field.name().unwrap_or_default().to_string(),
            file_name: field.file_name().map(str::to_string),
            content_type: field.content_type().map(str::to_string),
            bytes: field.bytes().await.unwrap().to_vec(),
        });
    }
    let name = parts
        .iter()
        .find(|p| p.name == "interaction")
        .and_then(|p| serde_json::from_slice::<serde_json::Value>(&p.bytes).ok())
        .and_then(|j| j["name"].as_str().map(str::to_string))
        .unwrap_or_default();
    if server.stall.lock().unwrap().contains(&name) {
        tokio::time::sleep(Duration::from_secs(30)).await;
    }
    let fixed = server.fixed.lock().unwrap().get(&name).copied();
    let status = fixed.unwrap_or_else(|| server.script.lock().unwrap().pop_front().unwrap_or(*server.then.lock().unwrap()));
    server.received.lock().unwrap().push(Received {
        status,
        content_length: headers.get("content-length").map(|v| v.to_str().unwrap().to_string()),
        transfer_encoding: headers.contains_key("transfer-encoding"),
        parts,
    });
    (StatusCode::from_u16(status).unwrap(), r#"{"ok":true,"stored":"new"}"#.to_string())
}

pub fn fast() -> UploadTimings {
    UploadTimings {
        backoff_start: Duration::from_millis(20),
        backoff_max: Duration::from_millis(80),
        rejected_retry: Duration::from_millis(60),
        idle_poll: Duration::from_millis(40),
        connect_timeout: Duration::from_secs(2),
        request_base: Duration::from_millis(300),
        request_per_mib: Duration::from_millis(100),
        request_max: Duration::from_secs(2),
    }
}

pub async fn eventually(what: &str, mut cond: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !cond() {
        assert!(Instant::now() < deadline, "timed out waiting for: {what}");
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

fn interaction(store: &Store, minute: u32, screens: &[Option<&str>]) -> std::path::PathBuf {
    let start = chrono::Utc.with_ymd_and_hms(2026, 11, 7, 17, minute, 0).unwrap();
    let mut h = store.begin(start, FixedOffset::west_opt(8 * 3600).unwrap(), "America/Vancouver").unwrap();
    for (i, name) in screens.iter().enumerate() {
        let img = RgbImage::from_pixel(32, 24, Rgb([i as u8 * 50, minute as u8, 9]));
        h.add_shot(*name, &img, start + chrono::Duration::milliseconds(1500), Trigger::Activity).unwrap();
    }
    h.finish().unwrap().unwrap()
}

fn setup() -> (tempfile::TempDir, Arc<Store>) {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::new(&dir.path().join("captures"), 80.0));
    (dir, store)
}

#[tokio::test]
async fn created_and_existing_are_marked_uploaded() {
    let (_d, store) = setup();
    let a = interaction(&store, 0, &[None]);
    let b = interaction(&store, 1, &[None]);
    let server = FakeServer::default();
    let url = server.serve().await;
    server.script.lock().unwrap().extend([201, 200]);
    let up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("both uploaded", || a.join(UPLOADED).exists() && b.join(UPLOADED).exists()).await;
    eventually("status says none pending", || up.status().pending == 0).await;
    assert_eq!(up.status().state, UploadState::Ok);
    assert!(up.status().last_upload.is_some());
    assert_eq!(server.names(), ["2026-11-07/17-00-00", "2026-11-07/17-01-00"]);
}

#[tokio::test]
async fn conflict_too_large_and_invalid_are_marked_failed_and_never_retried() {
    let (_d, store) = setup();
    let folders: Vec<_> = (0..4).map(|m| interaction(&store, m, &[None])).collect();
    let server = FakeServer::default();
    let url = server.serve().await;
    server.script.lock().unwrap().extend([409, 413, 422, 201]);
    let _up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("the last is uploaded", || folders[3].join(UPLOADED).exists()).await;
    for f in &folders[..3] {
        assert!(f.join(FAILED).exists() && !f.join(UPLOADED).exists());
    }
    tokio::time::sleep(Duration::from_millis(200)).await; // several idle polls
    assert_eq!(server.received.lock().unwrap().len(), 4, "failed ones are not sent again");
}

#[tokio::test]
async fn a_refused_device_stays_pending_and_is_retried() {
    let (_d, store) = setup();
    let a = interaction(&store, 0, &[None]);
    let server = FakeServer::default();
    let url = server.serve().await;
    server.script.lock().unwrap().extend([403, 403]);
    let up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("rejected", || up.status().state == UploadState::Rejected { device: "desk".into() }).await;
    assert!(!a.join(UPLOADED).exists() && !a.join(FAILED).exists());
    assert_eq!(up.status().pending, 1);
    eventually("accepted on a retry", || a.join(UPLOADED).exists()).await;
    eventually("state ok", || up.status().state == UploadState::Ok).await;
    assert_eq!(server.received.lock().unwrap().len(), 3);
}

#[tokio::test]
async fn hours_offline_then_back_uploads_everything_in_order_once() {
    // Review Focus 3: nothing listens for a while (the server is unreachable), then it is back.
    let (_d, store) = setup();
    let folders: Vec<_> = (0..5).map(|m| interaction(&store, m, &[None])).collect();
    let dead = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let dead_addr = dead.local_addr().unwrap();
    drop(dead);
    let up = Uploader::start(store.clone(), Some(format!("http://{dead_addr}")), "desk".into(), fast());
    eventually("offline", || up.status().state == UploadState::Offline).await;
    tokio::time::sleep(Duration::from_millis(200)).await; // several attempts
    assert_eq!(up.status().pending, 5);
    drop(up);
    let server = FakeServer::default();
    let url = server.serve().await;
    let up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("all uploaded", || folders.iter().all(|f| f.join(UPLOADED).exists())).await;
    let expected: Vec<String> = (0..5).map(|m| format!("2026-11-07/17-0{m}-00")).collect();
    assert_eq!(server.names(), expected, "in order, each once");
    eventually("ok", || up.status().state == UploadState::Ok && up.status().pending == 0).await;
}

#[tokio::test]
async fn a_failing_interaction_waits_its_turn_while_newer_ones_go_on() {
    // A 500 (or 507, 411…) for one interaction does not hold up the queue.
    let (_d, store) = setup();
    let folders: Vec<_> = (0..3).map(|m| interaction(&store, m, &[None])).collect();
    let server = FakeServer::default();
    let url = server.serve().await;
    server.fixed.lock().unwrap().insert("2026-11-07/17-00-00".into(), 500);
    let up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("the newer ones uploaded", || folders[1..].iter().all(|f| f.join(UPLOADED).exists())).await;
    assert!(!folders[0].join(UPLOADED).exists() && !folders[0].join(FAILED).exists());
    eventually("offline while it waits", || up.status().state == UploadState::Offline).await;
    server.fixed.lock().unwrap().clear(); // the server is fixed
    eventually("the old one too", || folders[0].join(UPLOADED).exists()).await;
    eventually("ok", || up.status().state == UploadState::Ok).await;
    let names = server.names();
    assert_eq!(names.iter().filter(|n| n.ends_with("17-01-00")).count(), 1, "{names:?}");
}

#[tokio::test]
async fn a_stalled_upload_times_out_and_does_not_block_newer_ones() {
    let (_d, store) = setup();
    let folders: Vec<_> = (0..3).map(|m| interaction(&store, m, &[None])).collect();
    let server = FakeServer::default();
    let url = server.serve().await;
    server.stall.lock().unwrap().insert("2026-11-07/17-00-00".into());
    let _up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("the newer ones uploaded", || folders[1..].iter().all(|f| f.join(UPLOADED).exists())).await;
    assert!(!folders[0].join(UPLOADED).exists());
    server.stall.lock().unwrap().clear();
    eventually("the stalled one on a later try", || folders[0].join(UPLOADED).exists()).await;
}

#[tokio::test]
async fn refusals_for_good_are_counted_and_the_last_one_shown() {
    let (_d, store) = setup();
    let folders: Vec<_> = (0..2).map(|m| interaction(&store, m, &[None])).collect();
    let server = FakeServer::default();
    let url = server.serve().await;
    server.script.lock().unwrap().extend([400, 413]);
    let up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("both failed", || folders.iter().all(|f| f.join(FAILED).exists())).await;
    eventually("counted", || up.status().failed == 2).await;
    let statuses: Vec<u16> = server.received.lock().unwrap().iter().map(|r| r.status).collect();
    assert_eq!(statuses, [400, 413]);
    assert_eq!(up.status().last_failure.as_deref(), Some("2026-11-07/17-01-00: too large for the server (413)"));
}

#[tokio::test]
async fn an_unreadable_interaction_json_is_rebuilt_and_uploaded() {
    let (_d, store) = setup();
    let folder = interaction(&store, 0, &[Some("Main"), Some("Side")]);
    std::fs::write(folder.join(INTERACTION_FILE), b"{\"name\": \"2026-11-0").unwrap(); // torn
    let server = FakeServer::default();
    let url = server.serve().await;
    let up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("uploaded", || folder.join(UPLOADED).exists()).await;
    assert_eq!(server.received.lock().unwrap()[0].parts.len(), 3);
    assert_eq!(up.status().failed, 0);
}

#[test]
fn the_request_timeout_grows_with_the_body() {
    let t = UploadTimings::default();
    assert_eq!(t.request_timeout(0), Duration::from_secs(60));
    assert_eq!(t.request_timeout(35 * 1024 * 1024), Duration::from_secs(95));
    assert_eq!(t.request_timeout(u64::MAX / 2), Duration::from_secs(900));
}

#[tokio::test]
async fn an_interrupted_interaction_is_finished_at_start_and_uploaded() {
    // Review Focus 2.
    let (_d, store) = setup();
    let start = chrono::Utc.with_ymd_and_hms(2026, 11, 7, 18, 0, 0).unwrap();
    let mut h = store.begin(start, FixedOffset::west_opt(8 * 3600).unwrap(), "America/Vancouver").unwrap();
    h.add_shot(None, &RgbImage::new(16, 16), start, Trigger::Activity).unwrap();
    let folder = h.folder().to_path_buf();
    drop(h); // the crash
    assert!(store.pending().is_empty());
    store.finish_interrupted().unwrap(); // what the runtime does at start
    let server = FakeServer::default();
    let url = server.serve().await;
    let _up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("uploaded", || folder.join(UPLOADED).exists()).await;
    assert_eq!(server.names(), ["2026-11-07/18-00-00"]);
}

#[tokio::test]
async fn without_a_server_nothing_leaves_the_machine() {
    let (_d, store) = setup();
    interaction(&store, 0, &[None]);
    let up = Uploader::start(store.clone(), Some("  ".into()), "desk".into(), fast());
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert_eq!(up.status().state, UploadState::Disabled);
    assert_eq!(up.status().pending, 1);
}

#[tokio::test]
async fn the_request_is_the_protocols_field_by_field() {
    let (_d, store) = setup();
    let folder = interaction(&store, 0, &[Some("Main"), Some("Side")]);
    let server = FakeServer::default();
    let url = server.serve().await;
    let _up = Uploader::start(store.clone(), Some(url), "desk".into(), fast());
    eventually("uploaded", || folder.join(UPLOADED).exists()).await;
    let r = server.received.lock().unwrap()[0].clone();
    assert!(r.content_length.is_some(), "the server answers 411 without a Content-Length");
    assert!(!r.transfer_encoding, "the server answers 411 to Transfer-Encoding");
    let names: Vec<&str> = r.parts.iter().map(|p| p.name.as_str()).collect();
    assert_eq!(names, ["interaction", "shots", "shots"]);
    let interaction = &r.parts[0];
    assert_eq!(interaction.file_name, None, "a form field, not a file");
    assert_eq!(interaction.bytes, std::fs::read(folder.join(INTERACTION_FILE)).unwrap());
    let json = r.interaction();
    for (part, shot) in r.parts[1..].iter().zip(json["shots"].as_array().unwrap()) {
        assert_eq!(part.file_name.as_deref(), shot["file"].as_str());
        assert_eq!(part.content_type.as_deref(), Some("image/webp"));
        assert_eq!(part.bytes.len() as u64, shot["bytes"].as_u64().unwrap());
        assert_eq!(&part.bytes[0..4], b"RIFF");
    }
    assert_eq!(r.parts[2].file_name.as_deref(), Some("0001-2.webp"));
    assert_python_accepts(&interaction.bytes);
}

#[tokio::test]
async fn a_one_screen_interaction_has_no_screen_name() {
    let (_d, store) = setup();
    let folder = interaction(&store, 0, &[None]);
    let json: serde_json::Value = serde_json::from_slice(&std::fs::read(folder.join(INTERACTION_FILE)).unwrap()).unwrap();
    assert!(json["shots"][0].get("screen_name").is_none());
    assert_python_accepts(&std::fs::read(folder.join(INTERACTION_FILE)).unwrap());
}

/// Checks `json` with the protocol's own pydantic model (and its name/start rule), through
/// `uv run` in `protocol/`. Skipped, with a note, where uv is not installed.
pub fn assert_python_accepts(json: &[u8]) {
    let protocol = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../protocol");
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("interaction.json");
    std::fs::write(&file, json).unwrap();
    let check = "import sys; from vitality_protocol.messages import Interaction; \
                 from vitality_protocol.layout import name_matches_start; \
                 i = Interaction.model_validate_json(open(sys.argv[1]).read()); \
                 assert name_matches_start(i.name, i.start), 'name does not match start'; print('ok')";
    let out = std::process::Command::new("uv")
        .args(["run", "--quiet", "--project"])
        .arg(&protocol)
        .args(["python", "-c", check])
        .arg(&file)
        .output();
    match out {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => eprintln!("skipped: uv is not installed"),
        Err(e) => panic!("cannot run uv: {e}"),
        Ok(o) => assert!(o.status.success(), "the protocol refuses it:\n{}", String::from_utf8_lossy(&o.stderr)),
    }
}
