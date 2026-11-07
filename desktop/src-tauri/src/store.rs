//! Interactions on disk, in the protocol's layout (`protocol/vitality_protocol/layout.py`):
//!
//! ```text
//! captures/2026-11-07/17-00-00/     one interaction (UTC; "-2", "-3"… if the name is taken)
//!   0000.webp  0012.webp  0012-2.webp   shots, by whole seconds since the start
//!   .shots.jsonl                    the shots so far (removed once interaction.json exists)
//!   interaction.json                written last: present = complete
//!   uploaded | failed               empty markers written by the uploader
//! ```
//!
//! `Store::begin` opens an interaction (`InteractionHandle`); `add_shot` writes a WebP at once
//! and notes it in `.shots.jsonl`; `finish` writes `interaction.json` (fields exactly those of
//! `messages.py`'s `Interaction`; `screen_name` only when given). `finish_interrupted` completes
//! folders a crash left without `interaction.json`. `pending`, `mark`, `today`, `disk_bytes` and
//! `free_up_space` (deletes only uploaded folders) serve the uploader and Status.

use std::collections::HashSet;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use chrono::{DateTime, FixedOffset, Local, NaiveDate, Utc};
use image::RgbImage;
use serde::{Deserialize, Serialize};

use crate::paths::write_atomic;
use crate::policy::Trigger;
pub use crate::types::TodayCounts;

pub const INTERACTION_FILE: &str = "interaction.json";
pub const UPLOADED: &str = "uploaded";
pub const FAILED: &str = "failed";
const PARTIAL: &str = ".shots.jsonl";

/// A shot as `interaction.json` lists it (the protocol's `Shot`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ShotJson {
    pub file: String,
    pub ts: String,
    pub trigger: String,
    pub width: u32,
    pub height: u32,
    pub bytes: u64,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub screen_name: Option<String>,
}

/// The content of `interaction.json` (the protocol's `Interaction`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InteractionJson {
    pub name: String,
    pub source: String,
    pub start: String,
    pub end: String,
    pub local_start: String,
    pub tz: String,
    pub shots: Vec<ShotJson>,
}

/// The first line of `.shots.jsonl`: what `interaction.json` needs besides the shots.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct PartialHeader {
    name: String,
    start: String,
    local_start: String,
    tz: String,
}

/// `2026-11-07T17:00:00.449Z`: milliseconds (truncated), `Z` for UTC.
pub fn format_utc(t: DateTime<Utc>) -> String {
    t.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string()
}

/// `2026-11-07T10:00:00.449-07:00` (`Z` when the offset is zero).
pub fn format_local(t: DateTime<FixedOffset>) -> String {
    if t.offset().local_minus_utc() == 0 {
        return format_utc(t.with_timezone(&Utc));
    }
    t.format("%Y-%m-%dT%H:%M:%S%.3f%:z").to_string()
}

/// The machine's IANA time zone: `$TZ`, else the target of `/etc/localtime`, else
/// `/etc/timezone`, else "UTC".
pub fn local_tz_name() -> String {
    fn after_zoneinfo(p: &str) -> Option<String> {
        p.split_once("zoneinfo/").map(|(_, name)| name.to_string()).filter(|n| !n.is_empty())
    }
    if let Ok(tz) = std::env::var("TZ") {
        let tz = tz.trim_start_matches(':');
        if let Some(name) = after_zoneinfo(tz) {
            return name;
        }
        if !tz.is_empty() && !tz.starts_with('/') && tz.contains('/') || tz == "UTC" {
            return tz.to_string();
        }
    }
    if let Ok(target) = fs::read_link("/etc/localtime") {
        if let Some(name) = after_zoneinfo(&target.to_string_lossy()) {
            return name;
        }
    }
    if let Ok(name) = fs::read_to_string("/etc/timezone") {
        let name = name.trim();
        if !name.is_empty() {
            return name.to_string();
        }
    }
    "UTC".to_string()
}

pub struct Store {
    root: PathBuf,
    quality: f32,
}

/// What `free_up_space` deleted.
#[allow(dead_code)] // Settings' Free up space (milestone 3)
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Freed {
    pub interactions: u32,
    pub bytes: u64,
}

impl Store {
    /// The store under `root` (the captures folder), writing WebP at `quality` (0–100).
    pub fn new(root: &Path, quality: f32) -> Store {
        Store { root: root.to_path_buf(), quality }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Opens a new interaction that began at `start` (the first input, or the first passive
    /// shot); `local_start` is `start` in local time, `tz` its IANA name.
    pub fn begin(&self, start: DateTime<Utc>, offset: FixedOffset, tz: &str) -> io::Result<InteractionHandle> {
        let day = start.format("%Y-%m-%d").to_string();
        let base = start.format("%H-%M-%S").to_string();
        let day_dir = self.root.join(&day);
        fs::create_dir_all(&day_dir)?;
        let mut n = 1;
        let (time, folder) = loop {
            let time = if n == 1 { base.clone() } else { format!("{base}-{n}") };
            let folder = day_dir.join(&time);
            match fs::create_dir(&folder) {
                Ok(()) => break (time, folder),
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => n += 1,
                Err(e) => return Err(e),
            }
        };
        let header = PartialHeader {
            name: format!("{day}/{time}"),
            start: format_utc(start),
            local_start: format_local(start.with_timezone(&offset)),
            tz: tz.to_string(),
        };
        append_line(&folder.join(PARTIAL), &header)?;
        Ok(InteractionHandle { folder, header, start, shots: vec![], quality: self.quality })
    }

    /// Completes every folder left without a readable `interaction.json` (a crash or a power
    /// loss), from its shots: the notes in `.shots.jsonl`, checked against the files, or else
    /// the shot files themselves. Folders without any shot are removed. Returns the folders
    /// completed.
    pub fn finish_interrupted(&self) -> io::Result<Vec<PathBuf>> {
        let mut done = vec![];
        for folder in self.folders()? {
            if folder.join(UPLOADED).exists() || folder.join(FAILED).exists() {
                continue;
            }
            if readable(&folder) {
                let _ = fs::remove_file(folder.join(PARTIAL)); // a crash between the two writes
                continue;
            }
            if self.repair(&folder)?.is_some() {
                done.push(folder);
            }
        }
        Ok(done)
    }

    /// Rebuilds `folder`'s `interaction.json` from its notes and shots; `None` (and the folder
    /// removed) when it holds no usable shot.
    pub fn repair(&self, folder: &Path) -> io::Result<Option<PathBuf>> {
        match recover(&self.root, folder)? {
            Some(json) => {
                write_atomic(&folder.join(INTERACTION_FILE), &serde_json::to_vec_pretty(&json)?)?;
                let _ = fs::remove_file(folder.join(PARTIAL));
                Ok(Some(folder.to_path_buf()))
            }
            None => {
                fs::remove_dir_all(folder)?;
                Ok(None)
            }
        }
    }

    /// Interactions marked `failed` (refused for good by the server).
    pub fn failed_count(&self) -> u32 {
        self.folders().unwrap_or_default().iter().filter(|f| f.join(FAILED).exists()).count() as u32
    }

    /// Complete interactions not yet uploaded or failed, oldest first.
    pub fn pending(&self) -> Vec<PathBuf> {
        self.folders()
            .unwrap_or_default()
            .into_iter()
            .filter(|f| f.join(INTERACTION_FILE).exists() && !f.join(UPLOADED).exists() && !f.join(FAILED).exists())
            .collect()
    }

    /// Writes the `uploaded` or `failed` marker.
    pub fn mark(&self, folder: &Path, marker: &str) -> io::Result<()> {
        fs::write(folder.join(marker), b"")
    }

    /// Interactions (complete) and their shots whose local start falls on `day`.
    pub fn day_counts(&self, day: NaiveDate) -> TodayCounts {
        let mut counts = TodayCounts::default();
        // Folders are UTC days: the local day spans at most the UTC day before and after.
        for d in [day.pred_opt(), Some(day), day.succ_opt()].into_iter().flatten() {
            let Ok(entries) = fs::read_dir(self.root.join(d.format("%Y-%m-%d").to_string())) else { continue };
            for entry in entries.flatten() {
                let Ok(bytes) = fs::read(entry.path().join(INTERACTION_FILE)) else { continue };
                let Ok(json) = serde_json::from_slice::<InteractionJson>(&bytes) else { continue };
                let local = DateTime::parse_from_rfc3339(&json.local_start).map(|t| t.date_naive());
                if local == Ok(day) {
                    counts.interactions += 1;
                    counts.shots += json.shots.len() as u32;
                }
            }
        }
        counts
    }

    pub fn today(&self) -> TodayCounts {
        self.day_counts(Local::now().date_naive())
    }

    /// Bytes used by all captures.
    pub fn disk_bytes(&self) -> u64 {
        dir_bytes(&self.root)
    }

    /// Deletes the local copies of uploaded interactions (never pending or failed ones).
    #[allow(dead_code)] // Settings' Free up space (milestone 3)
    pub fn free_up_space(&self) -> io::Result<Freed> {
        let mut freed = Freed::default();
        for folder in self.folders()? {
            if folder.join(UPLOADED).exists() {
                freed.bytes += dir_bytes(&folder);
                fs::remove_dir_all(&folder)?;
                freed.interactions += 1;
            }
        }
        for day in fs::read_dir(&self.root)?.flatten() {
            let _ = fs::remove_dir(day.path()); // only succeeds when empty
        }
        Ok(freed)
    }

    /// Every interaction folder, oldest first (names sort by time).
    fn folders(&self) -> io::Result<Vec<PathBuf>> {
        let mut out = vec![];
        let days = match fs::read_dir(&self.root) {
            Ok(d) => d,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(out),
            Err(e) => return Err(e),
        };
        for day in days.flatten() {
            if !is_day(&day.file_name().to_string_lossy()) || !day.path().is_dir() {
                continue;
            }
            for time in fs::read_dir(day.path())?.flatten() {
                if is_time(&time.file_name().to_string_lossy()) && time.path().is_dir() {
                    out.push(time.path());
                }
            }
        }
        out.sort_by_key(|p| sort_key(&self.root, p));
        Ok(out)
    }
}

/// An open interaction.
pub struct InteractionHandle {
    folder: PathBuf,
    header: PartialHeader,
    start: DateTime<Utc>,
    shots: Vec<ShotJson>,
    quality: f32,
}

impl InteractionHandle {
    pub fn name(&self) -> &str {
        &self.header.name
    }

    #[allow(dead_code)] // for tests
    pub fn folder(&self) -> &Path {
        &self.folder
    }

    pub fn shot_count(&self) -> usize {
        self.shots.len()
    }

    /// The bytes of its shots so far.
    pub fn bytes(&self) -> u64 {
        self.shots.iter().map(|s| s.bytes).sum()
    }

    /// Writes one shot (WebP) taken at `ts`; returns its file name.
    pub fn add_shot(&mut self, screen_name: Option<&str>, image: &RgbImage, ts: DateTime<Utc>, trigger: Trigger) -> io::Result<String> {
        let offset = (ts - self.start).num_seconds().max(0) as u64;
        let taken: HashSet<&str> = self.shots.iter().map(|s| s.file.as_str()).collect();
        let file = first_free(&format!("{offset:04}"), ".webp", &taken);
        let webp = webp::Encoder::from_rgb(image.as_raw(), image.width(), image.height())
            .encode_simple(false, self.quality)
            .map_err(|e| io::Error::other(format!("cannot encode a {}×{} shot as WebP: {e:?}", image.width(), image.height())))?;
        write_atomic(&self.folder.join(&file), &webp)?;
        let shot = ShotJson {
            file: file.clone(),
            ts: format_utc(ts),
            trigger: trigger_name(trigger).to_string(),
            width: image.width(),
            height: image.height(),
            bytes: webp.len() as u64,
            screen_name: screen_name.map(str::to_string),
        };
        append_line(&self.folder.join(PARTIAL), &shot)?;
        self.shots.push(shot);
        Ok(file)
    }

    /// Writes `interaction.json`; returns the folder, or `None` (and removes the folder) if
    /// no shot was taken.
    pub fn finish(self) -> io::Result<Option<PathBuf>> {
        if self.shots.is_empty() {
            fs::remove_dir_all(&self.folder)?;
            return Ok(None);
        }
        let json = assemble(&self.header, self.shots);
        write_atomic(&self.folder.join(INTERACTION_FILE), &serde_json::to_vec_pretty(&json)?)?;
        let _ = fs::remove_file(self.folder.join(PARTIAL));
        Ok(Some(self.folder))
    }
}

pub fn trigger_name(t: Trigger) -> &'static str {
    match t {
        Trigger::Activity => "activity",
        Trigger::Passive => "passive",
    }
}

fn assemble(header: &PartialHeader, mut shots: Vec<ShotJson>) -> InteractionJson {
    shots.sort_by(|a, b| (&a.ts, file_key(&a.file)).cmp(&(&b.ts, file_key(&b.file))));
    InteractionJson {
        name: header.name.clone(),
        source: "screen".into(),
        start: header.start.clone(),
        end: shots.last().map(|s| s.ts.clone()).unwrap_or_else(|| header.start.clone()),
        local_start: header.local_start.clone(),
        tz: header.tz.clone(),
        shots,
    }
}

/// The interaction a crashed folder holds, or `None` if it has no shot.
fn recover(root: &Path, folder: &Path) -> io::Result<Option<InteractionJson>> {
    let mut files: Vec<String> = fs::read_dir(folder)?
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| is_shot_file(n))
        .collect();
    files.sort();
    if files.is_empty() {
        return Ok(None);
    }
    let (header, mut shots) = read_partial(&folder.join(PARTIAL)).unwrap_or_else(|| (header_from_name(root, folder), vec![]));
    // Notes whose file is missing, cut short or not a WebP are dropped; files without a
    // (good) note are described from the file itself.
    shots.retain(|s| files.contains(&s.file) && is_webp_of(&folder.join(&s.file), s.bytes));
    let trigger = shots.first().map(|s| s.trigger.clone()).unwrap_or_else(|| "activity".into());
    let start = DateTime::parse_from_rfc3339(&header.start).map(|t| t.with_timezone(&Utc)).unwrap_or_else(|_| Utc::now());
    for file in &files {
        if shots.iter().any(|s| &s.file == file) {
            continue;
        }
        let bytes = fs::read(folder.join(file))?;
        if bytes.len() < 12 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WEBP" {
            continue;
        }
        let Some(features) = webp::BitstreamFeatures::new(&bytes) else { continue };
        let secs: i64 = file.chars().take_while(|c| c.is_ascii_digit()).collect::<String>().parse().unwrap_or(0);
        shots.push(ShotJson {
            file: file.clone(),
            ts: format_utc(start + chrono::Duration::seconds(secs)),
            trigger: trigger.clone(),
            width: features.width(),
            height: features.height(),
            bytes: bytes.len() as u64,
            screen_name: None,
        });
    }
    if shots.is_empty() {
        return Ok(None);
    }
    Ok(Some(assemble(&header, shots)))
}

/// Whether `folder`'s `interaction.json` exists and parses.
fn readable(folder: &Path) -> bool {
    fs::read(folder.join(INTERACTION_FILE)).ok().and_then(|b| serde_json::from_slice::<InteractionJson>(&b).ok()).is_some()
}

/// Whether `path` is a WebP of exactly `bytes` bytes.
fn is_webp_of(path: &Path, bytes: u64) -> bool {
    use std::io::Read;
    let Ok(meta) = fs::metadata(path) else { return false };
    let mut head = [0u8; 12];
    meta.len() == bytes
        && fs::File::open(path).and_then(|mut f| f.read_exact(&mut head)).is_ok()
        && &head[0..4] == b"RIFF"
        && &head[8..12] == b"WEBP"
}

fn read_partial(path: &Path) -> Option<(PartialHeader, Vec<ShotJson>)> {
    let text = fs::read_to_string(path).ok()?;
    let mut lines = text.lines();
    let header: PartialHeader = serde_json::from_str(lines.next()?).ok()?;
    // A line cut short by the crash is skipped.
    let shots = lines.filter_map(|l| serde_json::from_str(l).ok()).collect();
    Some((header, shots))
}

/// A header from the folder's name alone (start to the second, today's zone).
fn header_from_name(root: &Path, folder: &Path) -> PartialHeader {
    let name = folder.strip_prefix(root).unwrap_or(folder).to_string_lossy().replace('\\', "/");
    let base: String = name.chars().take(19).collect(); // "2026-11-07/17-00-00"
    let start = chrono::NaiveDateTime::parse_from_str(&base, "%Y-%m-%d/%H-%M-%S")
        .map(|t| t.and_utc())
        .unwrap_or_else(|_| Utc::now());
    let offset = *Local::now().offset();
    PartialHeader { name, start: format_utc(start), local_start: format_local(start.with_timezone(&offset)), tz: local_tz_name() }
}

fn append_line(path: &Path, value: &impl Serialize) -> io::Result<()> {
    use std::io::Write;
    let mut f = fs::OpenOptions::new().create(true).append(true).open(path)?;
    let mut line = serde_json::to_vec(value)?;
    line.push(b'\n');
    f.write_all(&line)?;
    f.sync_data()
}

fn first_free(base: &str, ext: &str, taken: &HashSet<&str>) -> String {
    let mut candidate = format!("{base}{ext}");
    let mut n = 1;
    while taken.contains(candidate.as_str()) {
        n += 1;
        candidate = format!("{base}-{n}{ext}");
    }
    candidate
}

/// "0003.webp" → (3, 1), "0003-2.webp" → (3, 2): the order shots were taken in.
fn file_key(file: &str) -> (u64, u32) {
    let stem = file.trim_end_matches(".webp");
    let (digits, n) = stem.split_once('-').unwrap_or((stem, "1"));
    (digits.parse().unwrap_or(0), n.parse().unwrap_or(1))
}

fn is_day(s: &str) -> bool {
    NaiveDate::parse_from_str(s, "%Y-%m-%d").is_ok() && s.len() == 10
}

fn is_time(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() >= 8
        && b[..8].iter().enumerate().all(|(i, c)| if i == 2 || i == 5 { *c == b'-' } else { c.is_ascii_digit() })
        && (b.len() == 8 || (b[8] == b'-' && s[9..].parse::<u32>().is_ok_and(|n| n >= 2)))
}

fn is_shot_file(s: &str) -> bool {
    let Some(stem) = s.strip_suffix(".webp") else { return false };
    let (digits, suffix) = stem.split_once('-').map_or((stem, None), |(d, n)| (d, Some(n)));
    digits.len() >= 4 && digits.bytes().all(|c| c.is_ascii_digit()) && suffix.is_none_or(|n| n.parse::<u32>().is_ok_and(|n| n >= 2))
}

/// Sorts "17-00-00-10" after "17-00-00-2".
fn sort_key(root: &Path, p: &Path) -> (String, u32) {
    let name = p.strip_prefix(root).unwrap_or(p).to_string_lossy().into_owned();
    let base: String = name.chars().take(19).collect();
    let n = name.get(20..).and_then(|s| s.parse().ok()).unwrap_or(1);
    (base, n)
}

fn dir_bytes(path: &Path) -> u64 {
    let Ok(entries) = fs::read_dir(path) else { return 0 };
    entries
        .flatten()
        .map(|e| match e.metadata() {
            Ok(m) if m.is_dir() => dir_bytes(&e.path()),
            Ok(m) => m.len(),
            Err(_) => 0,
        })
        .sum()
}

#[cfg(test)]
mod tests;
