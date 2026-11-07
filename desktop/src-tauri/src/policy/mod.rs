//! The capture policy: rules R1–R9 (spec §6) as a pure state machine. No I/O, no clock of its own.
//!
//! The runtime feeds it events and executes what it returns:
//! - `Policy::on_input(now)` for keyboard or mouse input,
//! - `Policy::on_tick(now, thumbs)` every 250 ms with each screen's newest thumb,
//! - `Policy::on_stop(now, why)` when capture must stop (pause, quit),
//! - `Policy::on_resume(now)` when nothing stops it any more,
//! - `Policy::on_written(now, bytes, shots)` after each shot, with the open interaction's size (R9).
//!
//! Each returns `Decision`s in order: start an interaction, take a shot of some screens, end the
//! interaction, or a skipped shot (for the decisions log). `Policy::state()` says what it is doing.
//! The rules themselves are in `rules.rs`; change detection in `change.rs`.

pub mod change;
pub mod explain;
mod rules;

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

pub use crate::types::{CaptureMode, ScreenId, StopReason};
pub use change::{Thumb, Threshold};

/// The numbers of the rules; every one is a setting. Defaults are the spec's.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PolicySettings {
    /// R1: input after at least this long without input starts an interaction; R5: the interaction
    /// ends when no input follows its settle shot within this long.
    pub start_gap: Duration,
    /// R2: a shot this long after the last input.
    pub settle: Duration,
    /// R3: while continuously active, a shot at least this often.
    pub max_gap: Duration,
    /// R4: what counts as a changed screen.
    pub threshold: Threshold,
    /// R6: while idle, a passive check this often.
    pub passive_every: Duration,
    /// R7: a passive interaction ends after this many checks in a row without change.
    pub passive_quiet_checks: u32,
    /// R9: an interaction whose shots reach this many bytes ends, and a new one starts.
    pub max_interaction_bytes: u64,
    /// R9: … or this many shots (the server takes at most 1000 files in one upload).
    pub max_interaction_shots: u32,
}

impl Default for PolicySettings {
    fn default() -> Self {
        PolicySettings {
            start_gap: Duration::from_secs(2),
            settle: Duration::from_secs(2),
            max_gap: Duration::from_secs(10),
            threshold: Threshold::default(),
            passive_every: Duration::from_secs(45),
            passive_quiet_checks: 3,
            max_interaction_bytes: 40 * 1024 * 1024,
            max_interaction_shots: 900,
        }
    }
}

/// The kind of an interaction: active (input) or passive (the screen changed while idle).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionKind {
    Active,
    Passive,
}

/// Why a shot was taken, as written in `interaction.json`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Trigger {
    Activity,
    Passive,
}

/// The rule behind a decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Rule {
    R1,
    R2,
    R3,
    R4,
    R5,
    R6,
    R7,
    R8,
    R9,
}

/// What the runtime should do.
#[derive(Debug, Clone, PartialEq)]
pub enum Decision {
    StartInteraction { kind: InteractionKind },
    /// A shot of these screens (in id order), into the open interaction.
    Shot { screens: Vec<ScreenId>, trigger: Trigger, rule: Rule },
    /// The open interaction ends (and is to be uploaded); `rule` says why.
    EndInteraction { rule: Rule },
    /// A shot that was due but not taken; `why` is for the decisions log.
    Skipped { rule: Rule, why: String },
}

/// What the policy is doing now, for Status and the tray.
#[derive(Debug, Clone, PartialEq)]
pub struct PolicyState {
    pub mode: CaptureMode,
    /// When the open interaction started.
    pub since: Option<Instant>,
    /// Shots in the open interaction.
    pub shots: u32,
    /// When the next passive check runs.
    pub next_check_at: Option<Instant>,
    pub stopped_by: Option<StopReason>,
}

/// The state machine. See the module comment and `rules.rs`.
#[derive(Debug)]
pub struct Policy {
    settings: PolicySettings,
    mode: Mode,
    last_input: Option<Instant>,
    /// Each screen's newest thumb, from the last tick.
    current: BTreeMap<ScreenId, Thumb>,
    /// Each screen's thumb when it was last shot (kept across interactions).
    last_shot: BTreeMap<ScreenId, Thumb>,
}

#[derive(Debug, Clone, PartialEq)]
enum Mode {
    /// No interaction; `next_check` is set on the first tick.
    Idle { next_check: Option<Instant> },
    Active {
        since: Instant,
        shots: u32,
        last_shot_at: Instant,
        /// When the settle shot (R2) for the current pause in input was due; `None` while input goes on.
        settled_at: Option<Instant>,
    },
    Passive { since: Instant, shots: u32, next_check: Instant, quiet: u32 },
    Stopped { why: StopReason },
}

impl Policy {
    pub fn new(settings: PolicySettings) -> Policy {
        Policy {
            settings,
            mode: Mode::Idle { next_check: None },
            last_input: None,
            current: BTreeMap::new(),
            last_shot: BTreeMap::new(),
        }
    }

    pub fn state(&self) -> PolicyState {
        let base = PolicyState { mode: CaptureMode::Idle, since: None, shots: 0, next_check_at: None, stopped_by: None };
        match self.mode {
            Mode::Idle { next_check } => PolicyState { next_check_at: next_check, ..base },
            Mode::Active { since, shots, .. } => PolicyState { mode: CaptureMode::Active, since: Some(since), shots, ..base },
            Mode::Passive { since, shots, next_check, .. } => {
                PolicyState { mode: CaptureMode::Passive, since: Some(since), shots, next_check_at: Some(next_check), ..base }
            }
            Mode::Stopped { why } => PolicyState { mode: CaptureMode::Stopped, stopped_by: Some(why), ..base },
        }
    }

    /// New numbers for the rules, from now on (the open interaction goes on).
    pub fn set_settings(&mut self, settings: PolicySettings) {
        self.settings = settings;
    }

    /// Forget a screen's last shot (it was removed, re-picked, or its shot could not be written),
    /// so it counts as changed next time.
    pub fn forget_screen(&mut self, id: &ScreenId) {
        self.current.remove(id);
        self.last_shot.remove(id);
    }
}

#[cfg(test)]
mod tests;
