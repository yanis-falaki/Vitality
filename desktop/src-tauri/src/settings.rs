//! The owner's settings, read from `client.toml` (missing or invalid → the defaults).
//! Milestone 1 only reads them; the Settings page that writes them comes later.
//!
//! ```toml
//! server = "http://vitality-librarian:8100"   # "" keeps captures on this machine
//! webp_quality = 85
//! start_gap_s = 2.0     settle_s = 2.0     max_gap_s = 10.0
//! change_fraction = 0.005   change_levels = 16
//! passive_every_s = 45.0    passive_quiet_checks = 3
//! max_interaction_mib = 40
//! ```
//!
//! Unknown keys (e.g. the old client's `upload_server`) are ignored, with a notice on stderr.

use std::path::Path;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::policy::{PolicySettings, Threshold};

pub const DEFAULT_SERVER: &str = "http://vitality-librarian:8100";

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
            Ok(s) if s.valid() => s,
            Ok(_) => {
                eprintln!("vitality-client: {} has out-of-range values; using the defaults", path.display());
                Settings::default()
            }
            Err(e) => {
                eprintln!("vitality-client: {} is invalid ({e}); using the defaults", path.display());
                Settings::default()
            }
        }
    }

    fn valid(&self) -> bool {
        let durations = [self.start_gap_s, self.settle_s, self.max_gap_s, self.passive_every_s];
        // Positive and at most a day (`Duration::from_secs_f64` panics on huge values).
        durations.iter().all(|d| d.is_finite() && *d > 0.0 && *d <= 86_400.0)
            && (1..=45).contains(&self.max_interaction_mib)
            && (0.0..=100.0).contains(&self.webp_quality)
            && (0.0..1.0).contains(&self.change_fraction)
            && self.passive_quiet_checks > 0
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
        Some(self.server.trim().to_string()).filter(|s| !s.is_empty())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
