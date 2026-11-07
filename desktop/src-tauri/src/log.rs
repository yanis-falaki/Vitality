//! The recent-decisions log (`capture.log`): one line per capture decision, the last 500 kept.
//!
//! `DecisionLog::open(path)` reads the lines already there (trimming the file to the last 500);
//! `append(at, text)` appends "2026-11-07T20:02:03Z<TAB>text" to the file (one small write, not
//! a rewrite) and trims the file to the last 500 lines only once it passes 1000; `recent(n)`
//! gives the newest `n`, oldest first.

use std::collections::VecDeque;
use std::io::Write;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};

use crate::paths::write_atomic;

pub const KEEP: usize = 500;
/// The file is trimmed back to `KEEP` lines when it holds more than this.
pub const TRIM_AT: usize = 1000;

pub struct DecisionLog {
    path: PathBuf,
    lines: VecDeque<String>,
    /// Lines in the file now.
    in_file: usize,
}

impl DecisionLog {
    pub fn open(path: &Path) -> DecisionLog {
        let text = std::fs::read_to_string(path).unwrap_or_default();
        let mut lines: VecDeque<String> = text.lines().map(str::to_string).collect();
        let in_file = lines.len();
        while lines.len() > KEEP {
            lines.pop_front();
        }
        let mut log = DecisionLog { path: path.to_path_buf(), lines, in_file };
        if in_file > KEEP {
            log.rewrite();
        }
        log
    }

    pub fn append(&mut self, at: DateTime<Utc>, text: &str) {
        let line = format!("{}\t{}", at.format("%Y-%m-%dT%H:%M:%SZ"), text.replace(['\n', '\t'], " "));
        self.lines.push_back(line.clone());
        while self.lines.len() > KEEP {
            self.lines.pop_front();
        }
        if self.in_file + 1 > TRIM_AT {
            self.rewrite();
            return;
        }
        let written = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .and_then(|mut f| f.write_all(format!("{line}\n").as_bytes()));
        match written {
            Ok(()) => self.in_file += 1,
            Err(e) => eprintln!("vitality-client: cannot write {}: {e}", self.path.display()),
        }
    }

    pub fn recent(&self, n: usize) -> Vec<String> {
        self.lines.iter().skip(self.lines.len().saturating_sub(n)).cloned().collect()
    }

    /// Replaces the file with the lines kept.
    fn rewrite(&mut self) {
        let mut body = self.lines.iter().map(String::as_str).collect::<Vec<_>>().join("\n");
        body.push('\n');
        match write_atomic(&self.path, body.as_bytes()) {
            Ok(()) => self.in_file = self.lines.len(),
            Err(e) => eprintln!("vitality-client: cannot write {}: {e}", self.path.display()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn at() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 11, 7, 20, 2, 3).unwrap()
    }

    fn file_lines(path: &Path) -> usize {
        std::fs::read_to_string(path).unwrap().lines().count()
    }

    #[test]
    fn keeps_the_last_500_lines_across_restarts() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("capture.log");
        let mut log = DecisionLog::open(&path);
        for i in 0..510 {
            log.append(at(), &format!("R2 shot {i}"));
        }
        assert_eq!(log.recent(1000).len(), KEEP);
        let again = DecisionLog::open(&path);
        assert_eq!(again.recent(1000).len(), KEEP);
        assert_eq!(again.recent(2), ["2026-11-07T20:02:03Z\tR2 shot 508", "2026-11-07T20:02:03Z\tR2 shot 509"]);
        assert_eq!(file_lines(&path), KEEP, "trimmed on start");
    }

    #[test]
    fn appends_without_rewriting_and_trims_past_1000_lines() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("capture.log");
        let mut log = DecisionLog::open(&path);
        for i in 0..TRIM_AT {
            log.append(at(), &format!("line {i}"));
        }
        assert_eq!(file_lines(&path), TRIM_AT, "appended, not rewritten");
        log.append(at(), "one more");
        assert_eq!(file_lines(&path), KEEP, "trimmed back");
        assert_eq!(log.recent(1)[0], "2026-11-07T20:02:03Z\tone more");
        log.append(at(), "and another");
        assert_eq!(file_lines(&path), KEEP + 1);
    }
}
