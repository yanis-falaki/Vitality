//! The recent-decisions log (`capture.log`): one line per capture decision, the last 500 kept.
//!
//! `DecisionLog::open(path)` reads the lines already there; `append(at, text)` adds
//! "2026-11-07T20:02:03Z<TAB>text" and rewrites the file (atomically) with the last 500;
//! `recent(n)` gives the newest `n`, oldest first.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};

use crate::paths::write_atomic;

pub const KEEP: usize = 500;

pub struct DecisionLog {
    path: PathBuf,
    lines: VecDeque<String>,
}

impl DecisionLog {
    pub fn open(path: &Path) -> DecisionLog {
        let text = std::fs::read_to_string(path).unwrap_or_default();
        let mut lines: VecDeque<String> = text.lines().map(str::to_string).collect();
        while lines.len() > KEEP {
            lines.pop_front();
        }
        DecisionLog { path: path.to_path_buf(), lines }
    }

    pub fn append(&mut self, at: DateTime<Utc>, text: &str) {
        self.lines.push_back(format!("{}\t{}", at.format("%Y-%m-%dT%H:%M:%SZ"), text.replace(['\n', '\t'], " ")));
        while self.lines.len() > KEEP {
            self.lines.pop_front();
        }
        let mut body = self.lines.iter().map(String::as_str).collect::<Vec<_>>().join("\n");
        body.push('\n');
        if let Err(e) = write_atomic(&self.path, body.as_bytes()) {
            eprintln!("vitality-client: cannot write {}: {e}", self.path.display());
        }
    }

    #[allow(dead_code)] // shown on Status (milestone 2)
    pub fn recent(&self, n: usize) -> Vec<String> {
        self.lines.iter().skip(self.lines.len().saturating_sub(n)).cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn keeps_the_last_500_lines_across_restarts() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("capture.log");
        let at = Utc.with_ymd_and_hms(2026, 11, 7, 20, 2, 3).unwrap();
        let mut log = DecisionLog::open(&path);
        for i in 0..510 {
            log.append(at, &format!("R2 shot {i}"));
        }
        let again = DecisionLog::open(&path);
        assert_eq!(again.recent(1000).len(), KEEP);
        assert_eq!(again.recent(2), ["2026-11-07T20:02:03Z\tR2 shot 508", "2026-11-07T20:02:03Z\tR2 shot 509"]);
        assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), KEEP);
    }
}
