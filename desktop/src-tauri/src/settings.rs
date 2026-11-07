//! The owner's settings in `client.toml`: the server, every number of the rules, image
//! quality, the disk warning level and start at login. Written by the Settings page.
//!
//! ```toml
//! server = "http://vitality-librarian:8100"   # "" keeps captures on this machine
//! webp_quality = 85
//! end_after_idle_s = 3.0     max_gap_s = 10.0
//! change_fraction = 0.005   change_levels = 16
//! passive_every_s = 45.0    passive_quiet_checks = 3
//! max_interaction_mib = 40
//! disk_warning_gb = 5.0
//! start_at_login = true
//! ```
//!
//! `Settings::load(path)` / `load_full`: missing → the defaults; field by field, an invalid or
//! ill-typed value takes its default and the others are kept (each problem said once on stderr,
//! and listed for the Settings page); unknown keys (e.g. the old client's `upload_server`) are
//! ignored. `from_fields` checks values from the Settings page with the same rules, field by
//! field; `save(path)` writes atomically, changing only what changed.
//! `StartAtLogin` turns the systemd user service on or off (`SystemdUnit` is the real one).

use std::path::Path;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::paths::write_atomic;
use crate::policy::{PolicySettings, Threshold};

pub const DEFAULT_SERVER: &str = "http://vitality-librarian:8100";
pub const SERVICE: &str = "vitality-client.service";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub server: String,
    pub webp_quality: f32,
    pub end_after_idle_s: f64,
    pub max_gap_s: f64,
    pub change_fraction: f64,
    pub change_levels: u8,
    pub passive_every_s: f64,
    pub passive_quiet_checks: u32,
    pub max_interaction_mib: u32,
    pub disk_warning_gb: f64,
    pub start_at_login: bool,
}

/// One thing wrong with a setting, for the Settings page.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FieldError {
    pub field: String,
    pub message: String,
}

impl Default for Settings {
    fn default() -> Self {
        let p = PolicySettings::default();
        Settings {
            server: DEFAULT_SERVER.into(),
            webp_quality: 85.0,
            end_after_idle_s: p.end_after_idle.as_secs_f64(),
            max_gap_s: p.max_gap.as_secs_f64(),
            change_fraction: p.threshold.fraction,
            change_levels: p.threshold.levels,
            passive_every_s: p.passive_every.as_secs_f64(),
            passive_quiet_checks: p.passive_quiet_checks,
            max_interaction_mib: (p.max_interaction_bytes / (1024 * 1024)) as u32,
            disk_warning_gb: 5.0,
            start_at_login: true,
        }
    }
}

/// How each field is checked (the same rules for the file and for the Settings page).
enum Kind {
    Text,
    Bool,
    Number { int: bool, lo: f64, lo_in: bool, hi: f64, hi_in: bool },
}

struct Spec {
    name: &'static str,
    kind: Kind,
    message: &'static str,
}

const fn num(name: &'static str, lo: f64, lo_in: bool, hi: f64, hi_in: bool, message: &'static str) -> Spec {
    Spec { name, kind: Kind::Number { int: false, lo, lo_in, hi, hi_in }, message }
}

const fn int(name: &'static str, lo: f64, hi: f64, message: &'static str) -> Spec {
    Spec { name, kind: Kind::Number { int: true, lo, lo_in: true, hi, hi_in: true }, message }
}

const DAY: &str = "must be more than 0 s and at most a day";

const SPECS: &[Spec] = &[
    Spec { name: "server", kind: Kind::Text, message: "must be an http:// or https:// address with a host (or empty to keep captures here)" },
    num("webp_quality", 0.0, true, 100.0, true, "must be from 0 to 100"),
    num("end_after_idle_s", 0.0, false, 86_400.0, true, DAY),
    num("max_gap_s", 0.0, false, 86_400.0, true, DAY),
    num("change_fraction", 0.0, false, 1.0, false, "must be more than 0 % and less than 100 %"),
    int("change_levels", 1.0, 254.0, "must be a whole number from 1 to 254"),
    num("passive_every_s", 0.0, false, 86_400.0, true, DAY),
    int("passive_quiet_checks", 1.0, 1000.0, "must be a whole number from 1 to 1000"),
    int("max_interaction_mib", 1.0, 45.0, "must be a whole number from 1 to 45 MiB (the server takes at most 50 MiB)"),
    num("disk_warning_gb", 0.0, true, 10_000.0, true, "must be from 0 to 10000 GB"),
    Spec { name: "start_at_login", kind: Kind::Bool, message: "must be on or off" },
];

fn field_ok(spec: &Spec, v: &serde_json::Value) -> bool {
    match (&spec.kind, v) {
        (Kind::Bool, serde_json::Value::Bool(_)) => true,
        (Kind::Text, serde_json::Value::String(s)) => {
            let s = s.trim();
            s.is_empty()
                || ((s.starts_with("http://") || s.starts_with("https://"))
                    && reqwest::Url::parse(s).is_ok_and(|u| u.host_str().is_some_and(|h| !h.is_empty())))
        }
        (Kind::Number { int, lo, lo_in, hi, hi_in }, serde_json::Value::Number(n)) => {
            let Some(x) = n.as_f64() else { return false };
            x.is_finite()
                && (!int || x.fract() == 0.0)
                && (if *lo_in { x >= *lo } else { x > *lo })
                && (if *hi_in { x <= *hi } else { x < *hi })
        }
        _ => false,
    }
}

/// A warning on stderr, said once per process (the file is read again and again).
fn warn_once(message: String) {
    static SAID: std::sync::OnceLock<std::sync::Mutex<std::collections::HashSet<String>>> = std::sync::OnceLock::new();
    if SAID.get_or_init(Default::default).lock().unwrap().insert(message.clone()) {
        eprintln!("vitality-client: {message}");
    }
}

/// The settings read from the file, what was wrong in it, and the file's own table.
#[derive(Debug, Clone, Default)]
pub struct Loaded {
    pub settings: Settings,
    /// "change_levels = 300: must be a whole number from 1 to 254 (using 16)".
    pub problems: Vec<String>,
    pub raw: toml::Table,
}

impl Settings {
    /// The settings from `get(field)`, field by field: a missing field takes its default, an
    /// invalid one too (and is reported).
    pub fn from_fields(get: impl Fn(&str) -> Option<serde_json::Value>) -> (Settings, Vec<FieldError>) {
        let defaults = serde_json::to_value(Settings::default()).expect("serialisable");
        let mut map = defaults.as_object().cloned().unwrap_or_default();
        let mut errors = vec![];
        for spec in SPECS {
            let Some(v) = get(spec.name) else { continue };
            if field_ok(spec, &v) {
                map.insert(spec.name.into(), v);
            } else {
                errors.push(FieldError { field: spec.name.into(), message: spec.message.into() });
            }
        }
        let settings: Settings = serde_json::from_value(serde_json::Value::Object(map)).unwrap_or_default();
        (settings, errors)
    }

    /// The settings at `path` (missing → the defaults). Each invalid value takes its default,
    /// the others are kept; unknown keys are ignored. Problems are warned about once.
    pub fn load(path: &Path) -> Settings {
        Settings::load_full(path).settings
    }

    pub fn load_full(path: &Path) -> Loaded {
        let Ok(text) = std::fs::read_to_string(path) else { return Loaded::default() };
        let raw = match text.parse::<toml::Table>() {
            Ok(t) => t,
            Err(e) => {
                let problem = format!("{} is not valid TOML ({}); using the defaults", path.display(), e.message());
                warn_once(problem.clone());
                return Loaded { problems: vec![problem], ..Loaded::default() };
            }
        };
        for key in raw.keys().filter(|k| !SPECS.iter().any(|s| s.name == k.as_str())) {
            warn_once(format!("{} has an unknown setting {key:?}; it is ignored", path.display()));
        }
        let (settings, errors) = Settings::from_fields(|k| raw.get(k).and_then(|v| serde_json::to_value(v).ok()));
        let defaults = serde_json::to_value(Settings::default()).expect("serialisable");
        let problems: Vec<String> = errors
            .iter()
            .map(|e| {
                let had = raw.get(&e.field).map(|v| v.to_string()).unwrap_or_default();
                format!("{} = {had}: {} (using {})", e.field, e.message, defaults[&e.field])
            })
            .collect();
        for p in &problems {
            warn_once(format!("{}: {p}", path.display()));
        }
        Loaded { settings, problems, raw }
    }

    /// Writes the settings (atomically), keeping the file's own value for every field that was
    /// not changed (an invalid one the owner did not touch stays as written) and its other
    /// keys. A file that was not TOML at all is kept as `client.toml.bak`. Refuses invalid
    /// settings.
    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(e) = self.validate().first() {
            return Err(format!("{}: {}", e.field, e.message));
        }
        let before = Settings::load_full(path);
        if let Ok(text) = std::fs::read_to_string(path) {
            if text.parse::<toml::Table>().is_err() {
                let _ = std::fs::copy(path, path.with_extension("toml.bak"));
            }
        }
        let mut table = before.raw.clone();
        let new = toml::Table::try_from(self).map_err(|e| e.to_string())?;
        let old = toml::Table::try_from(&before.settings).map_err(|e| e.to_string())?;
        for (key, value) in new {
            let unchanged = old.get(&key) == Some(&value);
            if !(unchanged && table.contains_key(&key)) {
                table.insert(key, value);
            }
        }
        let text = toml::to_string_pretty(&table).map_err(|e| e.to_string())?;
        let text = format!("# Vitality's desktop client: written by its Settings page.\n{text}");
        write_atomic(path, text.as_bytes()).map_err(|e| format!("cannot write {}: {e}", path.display()))
    }

    /// What is wrong, field by field (empty: valid).
    pub fn validate(&self) -> Vec<FieldError> {
        let me = serde_json::to_value(self).expect("serialisable");
        Settings::from_fields(|k| me.get(k).cloned()).1
    }

    pub fn policy(&self) -> PolicySettings {
        PolicySettings {
            end_after_idle: Duration::from_secs_f64(self.end_after_idle_s),
            max_gap: Duration::from_secs_f64(self.max_gap_s),
            threshold: Threshold { fraction: self.change_fraction, levels: self.change_levels },
            passive_every: Duration::from_secs_f64(self.passive_every_s),
            passive_quiet_checks: self.passive_quiet_checks,
            max_interaction_bytes: self.max_interaction_mib as u64 * 1024 * 1024,
            ..PolicySettings::default()
        }
    }

    /// The server to upload to; `None` when blank.
    pub fn server(&self) -> Option<String> {
        Some(self.server.trim().trim_end_matches('/').to_string()).filter(|s| !s.is_empty())
    }

    pub fn disk_warning_bytes(&self) -> u64 {
        (self.disk_warning_gb * 1e9) as u64
    }
}

/// Starting the app at login: the systemd user service, on or off.
pub trait StartAtLogin: Send + Sync {
    /// Whether the service is enabled; `Err(NOT_INSTALLED…)` when it is not installed.
    fn enabled(&self) -> Result<bool, String>;
    fn set(&self, on: bool) -> Result<(), String>;
    /// Whether this session started `graphical-session.target` (which starts the service);
    /// `None` if unknown.
    fn session_target_active(&self) -> Option<bool>;
}

/// The start of `enabled()`'s error when the service is not installed.
pub const NOT_INSTALLED: &str = "the service is not installed";

/// `systemctl --user enable|disable vitality-client.service`.
pub struct SystemdUnit;

impl StartAtLogin for SystemdUnit {
    fn enabled(&self) -> Result<bool, String> {
        let out = std::process::Command::new("systemctl").args(["--user", "is-enabled", SERVICE]).output().map_err(missing)?;
        let state = String::from_utf8_lossy(&out.stdout).trim().to_string();
        let stderr = String::from_utf8_lossy(&out.stderr);
        match state.as_str() {
            "enabled" | "enabled-runtime" | "static" | "alias" | "linked" | "linked-runtime" | "generated" => Ok(true),
            "disabled" | "masked" | "masked-runtime" | "indirect" => Ok(false),
            _ if stderr.contains("Failed to connect to bus") || stderr.contains("No medium found") => {
                Err("the systemd user manager is not reachable from here".into())
            }
            "" | "not-found" => Err(format!("{NOT_INSTALLED} ({SERVICE}: install the package, or start Vitality from your desktop's autostart)")),
            other => Err(format!("{SERVICE} is {other}")),
        }
    }

    fn set(&self, on: bool) -> Result<(), String> {
        let verb = if on { "enable" } else { "disable" };
        let out = std::process::Command::new("systemctl").args(["--user", verb, SERVICE]).output().map_err(missing)?;
        if out.status.success() {
            Ok(())
        } else {
            Err(format!("systemctl --user {verb} {SERVICE} failed: {}", String::from_utf8_lossy(&out.stderr).trim()))
        }
    }

    fn session_target_active(&self) -> Option<bool> {
        SystemdUnit::is_active("graphical-session.target")
    }
}

impl SystemdUnit {
    fn is_active(unit: &str) -> Option<bool> {
        let out = std::process::Command::new("systemctl").args(["--user", "is-active", unit]).output().ok()?;
        match String::from_utf8_lossy(&out.stdout).trim() {
            "active" => Some(true),
            "inactive" | "failed" => Some(false),
            _ => None,
        }
    }
}

fn missing(e: std::io::Error) -> String {
    if e.kind() == std::io::ErrorKind::NotFound {
        "systemd is not available here: start Vitality from your desktop's autostart instead".into()
    } else {
        format!("cannot run systemctl: {e}")
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::sync::Mutex;

    /// Start at login for tests: remembers the state, or always fails (`missing`).
    #[derive(Default)]
    pub struct FakeLogin {
        pub on: Mutex<Option<bool>>,
        pub missing: bool,
        pub target: Option<bool>,
    }

    impl StartAtLogin for FakeLogin {
        fn enabled(&self) -> Result<bool, String> {
            if self.missing {
                return Err(format!("{NOT_INSTALLED} (fake)"));
            }
            Ok(self.on.lock().unwrap().unwrap_or(false))
        }
        fn set(&self, on: bool) -> Result<(), String> {
            if self.missing {
                return Err("systemd is not available here".into());
            }
            *self.on.lock().unwrap() = Some(on);
            Ok(())
        }
        fn session_target_active(&self) -> Option<bool> {
            self.target
        }
    }

    #[test]
    fn defaults_and_a_partial_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        assert_eq!(Settings::load(&path), Settings::default());
        assert_eq!(Settings::default().policy(), PolicySettings::default());
        std::fs::write(&path, "server = \"\"\nend_after_idle_s = 3.5\n").unwrap();
        let s = Settings::load(&path);
        assert_eq!(s.server(), None);
        assert_eq!(s.policy().end_after_idle, Duration::from_millis(3500));
        assert_eq!(s.webp_quality, 85.0);
        assert!(s.start_at_login);
    }

    #[test]
    fn round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("vitality/client.toml");
        let s = Settings { server: "http://box:8100".into(), end_after_idle_s: 3.0, max_interaction_mib: 30, start_at_login: false, disk_warning_gb: 2.5, ..Settings::default() };
        s.save(&path).unwrap();
        assert_eq!(Settings::load(&path), s);
        assert!(std::fs::read_to_string(&path).unwrap().starts_with("# Vitality"));
    }

    #[test]
    fn unknown_keys_of_the_old_client_are_ignored() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        std::fs::write(&path, "upload_server = \"http://x\"\nend_after_idle_seconds = 3\nserver = \"http://box:8100\"\n").unwrap();
        assert_eq!(Settings::load(&path).server, "http://box:8100");
    }

    #[test]
    fn an_invalid_value_falls_back_alone_and_the_rest_is_kept() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        for bad in ["end_after_idle_s = 0", "end_after_idle_s = 1e30", "webp_quality = 200", "max_interaction_mib = 60", "server = 3", "change_levels = 300",
                    "server = \"box:8100\"", "server = \"http://\"", "passive_quiet_checks = 2.5", "start_at_login = 1"] {
            std::fs::write(&path, format!("disk_warning_gb = 2.5\n{bad}\n")).unwrap();
            let loaded = Settings::load_full(&path);
            assert_eq!(loaded.settings, Settings { disk_warning_gb: 2.5, ..Settings::default() }, "{bad}");
            assert_eq!(loaded.problems.len(), 1, "{bad}: {:?}", loaded.problems);
        }
        std::fs::write(&path, "change_levels = 300\n").unwrap();
        assert_eq!(Settings::load_full(&path).problems, ["change_levels = 300: must be a whole number from 1 to 254 (using 16)"]);
        std::fs::write(&path, "not toml [").unwrap();
        assert_eq!(Settings::load_full(&path).problems.len(), 1);
    }

    #[test]
    fn save_changes_only_what_changed_and_keeps_the_rest_of_the_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        std::fs::write(&path, "upload_server = \"http://old\"\nchange_levels = 300\nserver = \"http://box:8100\"\n").unwrap();
        let loaded = Settings::load(&path);
        Settings { end_after_idle_s: 3.0, ..loaded.clone() }.save(&path).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        let table: toml::Table = text.parse().unwrap();
        assert_eq!(table["end_after_idle_s"].as_float(), Some(3.0));
        assert_eq!(table["change_levels"].as_integer(), Some(300), "an invalid value the owner did not touch stays");
        assert_eq!(table["server"].as_str(), Some("http://box:8100"));
        assert_eq!(table["upload_server"].as_str(), Some("http://old"), "unknown keys stay");
        // Editing the invalid field replaces it.
        Settings { change_levels: 20, ..Settings::load(&path) }.save(&path).unwrap();
        assert_eq!(Settings::load_full(&path).problems.len(), 0);
        assert_eq!(Settings::load(&path).change_levels, 20);
    }

    #[test]
    fn a_file_that_is_not_toml_is_kept_aside_on_save() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        std::fs::write(&path, "server = [broken").unwrap();
        Settings::default().save(&path).unwrap();
        assert_eq!(std::fs::read_to_string(path.with_extension("toml.bak")).unwrap(), "server = [broken");
        assert_eq!(Settings::load(&path), Settings::default());
    }

    #[test]
    fn page_values_are_checked_field_by_field_with_types() {
        let page = serde_json::json!({
            "server": "http://box:8100", "webp_quality": 80, "end_after_idle_s": null,
            "max_gap_s": 10, "change_fraction": 0.005, "change_levels": 0, "passive_every_s": 45,
            "passive_quiet_checks": 2.5, "max_interaction_mib": 40, "disk_warning_gb": 5, "start_at_login": true,
        });
        let (s, errors) = Settings::from_fields(|k| page.get(k).cloned());
        let fields: Vec<&str> = errors.iter().map(|e| e.field.as_str()).collect();
        assert_eq!(fields, ["end_after_idle_s", "change_levels", "passive_quiet_checks"], "empty, 0 and a fraction are errors");
        assert_eq!(s.webp_quality, 80.0);
        assert!(Settings::from_fields(|k| serde_json::to_value(Settings::default()).unwrap().get(k).cloned()).1.is_empty());
    }

    #[test]
    fn start_at_login_through_a_fake() {
        let login = FakeLogin::default();
        login.set(true).unwrap();
        assert!(login.enabled().unwrap());
        assert!(FakeLogin { missing: true, ..Default::default() }.enabled().unwrap_err().starts_with(NOT_INSTALLED));
    }
}
