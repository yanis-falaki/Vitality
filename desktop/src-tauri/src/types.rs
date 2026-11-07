//! The values the core sends to the interface (Tauri command replies and events) and to the CLI.
//!
//! Plain serde structs, mirrored by hand in `ui/src/core.ts`. The test at the bottom serialises
//! one sample of each and checks them against `ui/src/fixtures.ts`, whose constants are typed with
//! the TypeScript mirrors, so `npm run typecheck` fails when the two drift apart.
//! (`UPDATE_FIXTURES=1 cargo test types` rewrites the fixtures after a deliberate change.)

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// A screen's identity, stable across restarts and renames ("s1a2b3c4").
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ScreenId(pub String);

impl std::fmt::Display for ScreenId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// Whether a screen is delivering frames.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScreenState {
    /// Its screen-share is open (frames may still be late: see `last_frame_age_s`).
    Live,
    /// The portal refused its saved permission: the owner has to pick it again.
    NeedsPick,
    /// Its screen-share is being opened (the portal's picker may be showing).
    Opening,
    /// Its stream ended or failed; it is being reopened from its saved permission.
    Stopped,
}

/// One screen as the Screens page shows it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScreenStatus {
    pub id: ScreenId,
    pub name: String,
    pub state: ScreenState,
    /// Seconds since its newest frame arrived; `None` before the first.
    pub last_frame_age_s: Option<f64>,
}

/// The reply of `screens_list`: the screens in the owner's order, and pairs that look identical.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScreensView {
    pub screens: Vec<ScreenStatus>,
    pub duplicates: Vec<(ScreenId, ScreenId)>,
}

/// What capture is doing, in the terms of the rules (§6).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureMode {
    Idle,
    Active,
    Passive,
    Stopped,
}

/// Why capture is stopped (R8), when it is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StopReason {
    Paused,
    Locked,
    Sleeping,
    Quit,
}

/// The capture state, with wall-clock times.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CaptureView {
    pub mode: CaptureMode,
    /// When the open interaction started (active or passive).
    pub since: Option<DateTime<Utc>>,
    /// Shots in the open interaction.
    pub shots: u32,
    /// When the next passive check runs (idle only).
    pub next_check_at: Option<DateTime<Utc>>,
    pub stopped_by: Option<StopReason>,
}

/// The pause the owner asked for; `until: None` while paused means until resumed.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct PauseView {
    pub paused: bool,
    pub until: Option<DateTime<Utc>>,
}

/// The uploader's state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum UploadState {
    /// Everything finished is uploaded, or uploading.
    Ok,
    /// The server could not be reached (or failed); retrying with growing waits.
    Offline,
    /// The server refused this device (403); `device` is the name to allow on the server.
    Rejected { device: String },
    /// No server is set: captures stay on this machine.
    Disabled,
}

/// The uploader's state with its numbers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UploadStatus {
    #[serde(flatten)]
    pub state: UploadState,
    /// Finished interactions not yet uploaded.
    pub pending: u32,
    pub last_upload: Option<DateTime<Utc>>,
    /// Interactions the server refused for good (marked `failed`: 400, 409, 413, 422).
    pub failed: u32,
    /// The latest such refusal since the app started ("2026-11-07/17-00-00: too large for the
    /// server (413)"): a warning on Status and the tray.
    pub last_failure: Option<String>,
}

/// The reply of `status` (the interface, the tray, `vitality-client status`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StatusView {
    pub capture: CaptureView,
    pub pause: PauseView,
    pub upload: UploadStatus,
    /// Screens set up, and how many of them are live.
    pub screens: u32,
    pub screens_live: u32,
    /// The screens that need the owner: to be picked again, or stopped.
    pub screens_attention: Vec<String>,
    /// Input devices readable ("ok", "no_devices", "permission_denied").
    pub input: String,
}

#[cfg(test)]
pub mod samples {
    //! One sample value of each type, shared by the fixture test and other tests.
    use super::*;
    use chrono::TimeZone;

    pub fn at(h: u32, m: u32, s: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 11, 7, h, m, s).unwrap()
    }

    pub fn screens() -> ScreensView {
        ScreensView {
            screens: vec![
                ScreenStatus { id: ScreenId("s1".into()), name: "Main".into(), state: ScreenState::Live, last_frame_age_s: Some(0.4) },
                ScreenStatus { id: ScreenId("s2".into()), name: "Screen 2".into(), state: ScreenState::NeedsPick, last_frame_age_s: None },
                ScreenStatus { id: ScreenId("s3".into()), name: "Screen 3".into(), state: ScreenState::Opening, last_frame_age_s: None },
                ScreenStatus { id: ScreenId("s4".into()), name: "Screen 4".into(), state: ScreenState::Stopped, last_frame_age_s: None },
            ],
            duplicates: vec![(ScreenId("s1".into()), ScreenId("s3".into()))],
        }
    }

    pub fn status() -> StatusView {
        StatusView {
            capture: CaptureView { mode: CaptureMode::Active, since: Some(at(20, 2, 0)), shots: 14, next_check_at: None, stopped_by: None },
            pause: PauseView { paused: false, until: None },
            upload: UploadStatus { state: UploadState::Ok, pending: 0, last_upload: Some(at(20, 1, 30)), failed: 0, last_failure: None },
            screens: 2,
            screens_live: 1,
            screens_attention: vec!["Screen 2".into()],
            input: "ok".into(),
        }
    }

    pub fn status_paused() -> StatusView {
        StatusView {
            capture: CaptureView { mode: CaptureMode::Stopped, since: None, shots: 0, next_check_at: None, stopped_by: Some(StopReason::Paused) },
            pause: PauseView { paused: true, until: Some(at(21, 0, 0)) },
            upload: UploadStatus {
                state: UploadState::Rejected { device: "desktop".into() },
                pending: 3,
                last_upload: None,
                failed: 1,
                last_failure: Some("2026-11-07/17-00-00: too large for the server (413)".into()),
            },
            screens: 2,
            screens_live: 2,
            screens_attention: vec![],
            input: "permission_denied".into(),
        }
    }

    pub fn status_idle() -> StatusView {
        StatusView {
            capture: CaptureView { mode: CaptureMode::Idle, since: None, shots: 0, next_check_at: Some(at(20, 3, 15)), stopped_by: None },
            pause: PauseView::default(),
            upload: UploadStatus { state: UploadState::Offline, pending: 1, last_upload: None, failed: 0, last_failure: None },
            screens: 0,
            screens_live: 0,
            screens_attention: vec![],
            input: "no_devices".into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::samples;
    use serde::Serialize;
    use std::path::PathBuf;

    /// (constant name, TypeScript type, value) for every fixture, in file order.
    fn fixtures() -> Vec<(&'static str, &'static str, serde_json::Value)> {
        fn v(x: impl Serialize) -> serde_json::Value {
            serde_json::to_value(x).unwrap()
        }
        vec![
            ("screens", "ScreensView", v(samples::screens())),
            ("statusActive", "StatusView", v(samples::status())),
            ("statusPaused", "StatusView", v(samples::status_paused())),
            ("statusIdle", "StatusView", v(samples::status_idle())),
        ]
    }

    fn render() -> String {
        let fixtures = fixtures();
        let mut types: Vec<&str> = fixtures.iter().map(|f| f.1).collect();
        types.sort();
        types.dedup();
        let mut out = String::from(
            "// Sample values of the types the core sends, written by the Rust test `types::tests`\n\
             // (`UPDATE_FIXTURES=1 cargo test types` rewrites it). Typed with core.ts's mirrors, so the\n\
             // type check fails when Rust and TypeScript drift apart. Also the fake core's data.\n",
        );
        out += &format!("import type {{ {} }} from \"./core\";\n", types.join(", "));
        for (name, ty, value) in fixtures {
            out += &format!("\nexport const {name}: {ty} = {};\n", serde_json::to_string_pretty(&value).unwrap());
        }
        out
    }

    #[test]
    fn typescript_fixtures_match_the_rust_types() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../ui/src/fixtures.ts");
        let expected = render();
        if std::env::var_os("UPDATE_FIXTURES").is_some() {
            std::fs::write(&path, &expected).unwrap();
        }
        let actual = std::fs::read_to_string(&path).unwrap_or_default();
        assert!(actual == expected, "ui/src/fixtures.ts is out of date: run UPDATE_FIXTURES=1 cargo test types, then npm run typecheck");
    }
}
