//! The owner's settings in `client.toml`: the server, every number of the rules, image
//! quality, the disk warning level and start at login. Written by the Settings page.
//!
//! ```toml
//! server = "http://vitality-librarian:8100"   # "" keeps captures on this machine
//! webp_quality = 85
//! start_gap_s = 2.0     settle_s = 2.0     max_gap_s = 10.0
//! change_fraction = 0.005   change_levels = 16
//! passive_every_s = 45.0    passive_quiet_checks = 3
//! max_interaction_mib = 40
//! disk_warning_gb = 5.0
//! start_at_login = true
//! ```
//!
//! `Settings::load(path)`: missing → the defaults; unreadable or out of range → the defaults
//! (said on stderr); unknown keys (e.g. the old client's `upload_server`) are ignored with a
//! notice. `validate()` lists what is wrong, field by field; `save(path)` writes atomically.
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
    pub start_gap_s: f64,
    pub settle_s: f64,
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
            start_gap_s: p.start_gap.as_secs_f64(),
            settle_s: p.settle.as_secs_f64(),
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

impl Settings {
    pub fn load(path: &Path) -> Settings {
        let Ok(text) = std::fs::read_to_string(path) else { return Settings::default() };
        if let Ok(table) = text.parse::<toml::Table>() {
            let known = toml::Table::try_from(Settings::default()).unwrap_or_default();
            for key in table.keys().filter(|k| !known.contains_key(*k)) {
                eprintln!("vitality-client: {} has an unknown setting {key:?}; it is ignored", path.display());
            }
        }
        match toml::from_str::<Settings>(&text) {
            Ok(s) if s.validate().is_empty() => s,
            Ok(s) => {
                let what: Vec<String> = s.validate().into_iter().map(|e| format!("{}: {}", e.field, e.message)).collect();
                eprintln!("vitality-client: {} has out-of-range values ({}); using the defaults", path.display(), what.join("; "));
                Settings::default()
            }
            Err(e) => {
                eprintln!("vitality-client: {} is invalid ({e}); using the defaults", path.display());
                Settings::default()
            }
        }
    }

    /// Writes the settings (atomically); refuses invalid ones.
    pub fn save(&self, path: &Path) -> Result<(), String> {
        if !self.validate().is_empty() {
            return Err("the settings are not valid".into());
        }
        let text = toml::to_string_pretty(self).map_err(|e| e.to_string())?;
        let text = format!("# Vitality's desktop client: written by its Settings page.\n{text}");
        write_atomic(path, text.as_bytes()).map_err(|e| format!("cannot write {}: {e}", path.display()))
    }

    /// What is wrong, field by field (empty: valid).
    pub fn validate(&self) -> Vec<FieldError> {
        let mut errors = vec![];
        let mut bad = |field: &str, message: &str| errors.push(FieldError { field: field.into(), message: message.into() });
        let durations = [
            ("start_gap_s", self.start_gap_s),
            ("settle_s", self.settle_s),
            ("max_gap_s", self.max_gap_s),
            ("passive_every_s", self.passive_every_s),
        ];
        for (field, d) in durations {
            // Positive and at most a day (`Duration::from_secs_f64` panics on huge values).
            if !(d.is_finite() && d > 0.0 && d <= 86_400.0) {
                bad(field, "must be more than 0 s and at most a day");
            }
        }
        if self.max_gap_s.is_finite() && self.settle_s.is_finite() && self.max_gap_s < self.settle_s {
            bad("max_gap_s", "must be at least the settle time");
        }
        if !(0.0..=100.0).contains(&self.webp_quality) {
            bad("webp_quality", "must be from 0 to 100");
        }
        if !(self.change_fraction > 0.0 && self.change_fraction < 1.0) {
            bad("change_fraction", "must be more than 0 % and less than 100 %");
        }
        if self.passive_quiet_checks == 0 || self.passive_quiet_checks > 1000 {
            bad("passive_quiet_checks", "must be from 1 to 1000");
        }
        if !(1..=45).contains(&self.max_interaction_mib) {
            bad("max_interaction_mib", "must be from 1 to 45 MiB (the server takes at most 50 MiB)");
        }
        if !(self.disk_warning_gb.is_finite() && self.disk_warning_gb >= 0.0) {
            bad("disk_warning_gb", "must be 0 or more");
        }
        let server = self.server.trim();
        if !server.is_empty() && !(server.starts_with("http://") || server.starts_with("https://")) {
            bad("server", "must start with http:// or https:// (or be empty to keep captures here)");
        }
        errors
    }

    pub fn policy(&self) -> PolicySettings {
        PolicySettings {
            start_gap: Duration::from_secs_f64(self.start_gap_s),
            settle: Duration::from_secs_f64(self.settle_s),
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
    fn enabled(&self) -> Result<bool, String>;
    fn set(&self, on: bool) -> Result<(), String>;
}

/// `systemctl --user enable|disable vitality-client.service`.
pub struct SystemdUnit;

impl StartAtLogin for SystemdUnit {
    fn enabled(&self) -> Result<bool, String> {
        let out = std::process::Command::new("systemctl").args(["--user", "is-enabled", SERVICE]).output().map_err(missing)?;
        let state = String::from_utf8_lossy(&out.stdout).trim().to_string();
        match state.as_str() {
            "enabled" | "enabled-runtime" | "static" | "alias" => Ok(true),
            "disabled" | "masked" => Ok(false),
            "" | "not-found" => Err(format!("{SERVICE} is not installed (install the package, or start Vitality from your desktop's autostart)")),
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
    }

    impl StartAtLogin for FakeLogin {
        fn enabled(&self) -> Result<bool, String> {
            if self.missing {
                return Err("systemd is not available here".into());
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
    }

    #[test]
    fn defaults_and_a_partial_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        assert_eq!(Settings::load(&path), Settings::default());
        assert_eq!(Settings::default().policy(), PolicySettings::default());
        std::fs::write(&path, "server = \"\"\nsettle_s = 3.5\n").unwrap();
        let s = Settings::load(&path);
        assert_eq!(s.server(), None);
        assert_eq!(s.policy().settle, Duration::from_millis(3500));
        assert_eq!(s.webp_quality, 85.0);
        assert!(s.start_at_login);
    }

    #[test]
    fn round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("vitality/client.toml");
        let s = Settings { server: "http://box:8100".into(), settle_s: 3.0, max_interaction_mib: 30, start_at_login: false, disk_warning_gb: 2.5, ..Settings::default() };
        s.save(&path).unwrap();
        assert_eq!(Settings::load(&path), s);
        assert!(std::fs::read_to_string(&path).unwrap().starts_with("# Vitality"));
    }

    #[test]
    fn unknown_keys_of_the_old_client_are_ignored() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        std::fs::write(&path, "upload_server = \"http://x\"\nsettle_seconds = 3\nserver = \"http://box:8100\"\n").unwrap();
        assert_eq!(Settings::load(&path).server, "http://box:8100");
    }

    #[test]
    fn invalid_values_fall_back_to_the_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("client.toml");
        for bad in ["settle_s = 0", "max_gap_s = -1", "settle_s = 1e30", "webp_quality = 200", "max_interaction_mib = 60", "server = 3"] {
            std::fs::write(&path, bad).unwrap();
            assert_eq!(Settings::load(&path), Settings::default(), "{bad}");
        }
    }

    #[test]
    fn validation_names_each_field_and_refuses_zero_or_negative_durations() {
        let s = Settings {
            start_gap_s: 0.0,
            settle_s: -2.0,
            passive_every_s: f64::NAN,
            passive_quiet_checks: 0,
            change_fraction: 0.0,
            server: "vitality-librarian:8100".into(),
            ..Settings::default()
        };
        let fields: Vec<String> = s.validate().into_iter().map(|e| e.field).collect();
        assert_eq!(fields, ["start_gap_s", "settle_s", "passive_every_s", "change_fraction", "passive_quiet_checks", "server"]);
        assert!(s.save(std::path::Path::new("/nonexistent/x.toml")).is_err());
        let short = Settings { max_gap_s: 1.0, ..Settings::default() };
        assert_eq!(short.validate()[0].field, "max_gap_s");
        assert!(Settings::default().validate().is_empty());
        assert!(Settings { server: "".into(), ..Settings::default() }.validate().is_empty());
    }

    #[test]
    fn start_at_login_through_a_fake() {
        let login = FakeLogin::default();
        login.set(true).unwrap();
        assert!(login.enabled().unwrap());
        assert!(FakeLogin { missing: true, ..Default::default() }.set(true).unwrap_err().contains("systemd"));
    }
}
