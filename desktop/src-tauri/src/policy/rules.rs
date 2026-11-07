//! The rules R1–R8 as `Policy`'s event handlers (the types are in `mod.rs`).
//!
//! R1 input after ≥ start_gap without input starts an active interaction, shot of every screen.
//! R2 settle shot `settle` after the last input.  R3 while input goes on, a shot every `max_gap`.
//! R4 a shot holds only screens changed since their own last shot; none changed → skipped.
//! R5 no input within `start_gap` after the settle shot → the interaction ends.
//! R6 while idle, every `passive_every`, a passive shot of the changed screens.
//! R7 passive shots form one passive interaction; it ends on input, after
//!    `passive_quiet_checks` checks without change, or when capture stops.
//! R8 paused: an open interaction ends; nothing is captured, no checks run.
//!
//! After a system sleep (a tick more than 30 s after the one before) an open interaction ends
//! at once (R5 active, R7 passive) with no shot: the gap is not part of it.
//! R9 an interaction whose shots reach `max_interaction_bytes` (or `max_interaction_shots`)
//!    ends, and one of the same kind starts at once with a shot of every screen.
//!
//! In idle every input starts an interaction: an interaction only ends after `settle` +
//! `start_gap` without input, so R1's gap has always passed by then (and after a resume the
//! owner is plainly active).

use std::collections::BTreeMap;
use std::time::Instant;

use super::change::changed;
use super::{Decision, InteractionKind, Mode, Policy, Rule, ScreenId, StopReason, Thumb, Trigger};

/// Ticks come every 250 ms; a gap longer than this means the machine slept.
const SLEEP_GAP: std::time::Duration = std::time::Duration::from_secs(30);

impl Policy {
    /// Keyboard or mouse input at `now`.
    pub fn on_input(&mut self, now: Instant) -> Vec<Decision> {
        let mut out = self.after_sleep(now);
        self.last_input = Some(now);
        out.extend(self.input_decisions(now));
        out
    }

    /// The machine slept (the runtime sees the wall clock jump while the monotonic clock,
    /// which stops during a suspend, did not): an open interaction ends at once, without a shot.
    pub fn on_slept(&mut self, now: Instant) -> Vec<Decision> {
        self.end_after_gap(now, true)
    }

    /// An open interaction ends, without a shot, when the machine slept since the last tick.
    fn after_sleep(&mut self, now: Instant) -> Vec<Decision> {
        let slept = self.last_tick.is_some_and(|t| now.saturating_duration_since(t) > SLEEP_GAP);
        self.end_after_gap(now, slept)
    }

    fn end_after_gap(&mut self, now: Instant, slept: bool) -> Vec<Decision> {
        let rule = match self.mode {
            Mode::Active { .. } => Rule::R5,
            Mode::Passive { .. } => Rule::R7,
            _ => return vec![],
        };
        if !slept {
            return vec![];
        }
        self.mode = Mode::Idle { next_check: Some(now + self.settings.passive_every) };
        vec![Decision::EndInteraction { rule }]
    }

    fn input_decisions(&mut self, now: Instant) -> Vec<Decision> {
        match &mut self.mode {
            Mode::Stopped { .. } => vec![],
            Mode::Active { settled_at, .. } => {
                *settled_at = None; // R5: input after the settle shot keeps the interaction going
                vec![]
            }
            Mode::Idle { .. } => self.start_active(now),
            Mode::Passive { .. } => {
                // R7: input ends the passive interaction; R1 starts an active one.
                let mut out = vec![Decision::EndInteraction { rule: Rule::R7 }];
                out.extend(self.start_active(now));
                out
            }
        }
    }

    /// The 250 ms tick, with each screen's newest thumb (screens without a frame are absent).
    pub fn on_tick(&mut self, now: Instant, thumbs: &BTreeMap<ScreenId, Thumb>) -> Vec<Decision> {
        self.current = thumbs.clone();
        let woke = self.after_sleep(now);
        self.last_tick = Some(now);
        if !woke.is_empty() {
            return woke;
        }
        let s = self.settings.clone();
        match self.mode.clone() {
            Mode::Stopped { .. } => vec![],
            Mode::Idle { next_check: None } => {
                self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                vec![]
            }
            Mode::Idle { next_check: Some(due) } if now >= due => {
                // R6: a passive check; the first changed one starts a passive interaction (R7).
                let screens = self.changed_screens();
                if screens.is_empty() {
                    self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                    return vec![skipped(Rule::R6, "passive check: no screen changed")];
                }
                self.mode = Mode::Passive { since: now, shots: 1, next_check: now + s.passive_every, quiet: 0 };
                vec![
                    Decision::StartInteraction { kind: InteractionKind::Passive },
                    self.shoot(screens, Trigger::Passive, Rule::R6),
                ]
            }
            Mode::Idle { .. } => vec![],
            Mode::Passive { since, shots, next_check, quiet } if now >= next_check => {
                let screens = self.changed_screens();
                let next_check = now + s.passive_every;
                if !screens.is_empty() {
                    self.mode = Mode::Passive { since, shots: shots + 1, next_check, quiet: 0 };
                    return vec![self.shoot(screens, Trigger::Passive, Rule::R6)];
                }
                let quiet = quiet + 1;
                if quiet >= s.passive_quiet_checks {
                    self.mode = Mode::Idle { next_check: Some(next_check) };
                    return vec![
                        skipped(Rule::R6, "passive check: no screen changed"),
                        Decision::EndInteraction { rule: Rule::R7 },
                    ];
                }
                self.mode = Mode::Passive { since, shots, next_check, quiet };
                vec![skipped(Rule::R6, &format!("passive check: no screen changed ({quiet} of {})", s.passive_quiet_checks))]
            }
            Mode::Passive { .. } => vec![],
            Mode::Active { since, shots, last_shot_at, settled_at } => {
                let last_input = self.last_input.unwrap_or(since);
                if let Some(settled) = settled_at {
                    if now.duration_since(settled) >= s.start_gap {
                        // R5: no input after the settle shot.
                        self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                        return vec![Decision::EndInteraction { rule: Rule::R5 }];
                    }
                    return vec![];
                }
                let rule = if now.duration_since(last_input) >= s.settle {
                    Rule::R2
                } else if now.duration_since(last_shot_at) >= s.max_gap {
                    Rule::R3
                } else {
                    return vec![];
                };
                let screens = self.changed_screens();
                let settled_at = if rule == Rule::R2 { Some(now) } else { None };
                if screens.is_empty() {
                    self.mode = Mode::Active { since, shots, last_shot_at: now, settled_at };
                    return vec![skipped(rule, "no screen changed (R4)")];
                }
                self.mode = Mode::Active { since, shots: shots + 1, last_shot_at: now, settled_at };
                vec![self.shoot(screens, Trigger::Activity, rule)]
            }
        }
    }

    /// The open interaction now holds `bytes` in `shots` shots (R9: past the budget it is split).
    pub fn on_written(&mut self, now: Instant, bytes: u64, shots: u32) -> Vec<Decision> {
        let s = &self.settings;
        // An interaction of one shot cannot be split any further.
        if shots <= 1 || (bytes < s.max_interaction_bytes && shots < s.max_interaction_shots) {
            return vec![];
        }
        let (kind, trigger) = match self.mode {
            Mode::Active { .. } => (InteractionKind::Active, Trigger::Activity),
            Mode::Passive { .. } => (InteractionKind::Passive, Trigger::Passive),
            _ => return vec![],
        };
        let screens: Vec<ScreenId> = self.current.keys().cloned().collect();
        let first = u32::from(!screens.is_empty());
        match &mut self.mode {
            Mode::Active { since, shots, last_shot_at, .. } => {
                (*since, *shots, *last_shot_at) = (now, first, now);
            }
            Mode::Passive { since, shots, quiet, .. } => {
                (*since, *shots, *quiet) = (now, first, 0);
            }
            _ => {}
        }
        let shot = if screens.is_empty() {
            skipped(Rule::R9, "no screen has a frame")
        } else {
            self.shoot(screens, trigger, Rule::R9)
        };
        vec![Decision::EndInteraction { rule: Rule::R9 }, Decision::StartInteraction { kind }, shot]
    }

    /// Capture must stop (R8, or quitting). Ends an open interaction.
    pub fn on_stop(&mut self, _now: Instant, why: StopReason) -> Vec<Decision> {
        let open = matches!(self.mode, Mode::Active { .. } | Mode::Passive { .. });
        self.mode = Mode::Stopped { why };
        if open {
            vec![Decision::EndInteraction { rule: Rule::R8 }]
        } else {
            vec![]
        }
    }

    /// Nothing stops capture any more: idle, with the next passive check one period away.
    pub fn on_resume(&mut self, now: Instant) -> Vec<Decision> {
        if matches!(self.mode, Mode::Stopped { .. }) {
            self.mode = Mode::Idle { next_check: Some(now + self.settings.passive_every) };
        }
        vec![]
    }

    /// R1: start an active interaction with a shot of every screen.
    fn start_active(&mut self, now: Instant) -> Vec<Decision> {
        let screens: Vec<ScreenId> = self.current.keys().cloned().collect();
        let shots = u32::from(!screens.is_empty());
        self.mode = Mode::Active { since: now, shots, last_shot_at: now, settled_at: None };
        let shot = if screens.is_empty() {
            skipped(Rule::R1, "no screen has a frame")
        } else {
            self.shoot(screens, Trigger::Activity, Rule::R1)
        };
        vec![Decision::StartInteraction { kind: InteractionKind::Active }, shot]
    }

    /// R4: the screens whose newest thumb differs from their thumb at their own last shot
    /// (a screen never shot counts as changed).
    fn changed_screens(&self) -> Vec<ScreenId> {
        let t = &self.settings.threshold;
        self.current
            .iter()
            .filter(|(id, now)| self.last_shot.get(*id).is_none_or(|last| changed(last, now, t)))
            .map(|(id, _)| id.clone())
            .collect()
    }

    /// A shot of `screens`, remembering their thumbs as their last shot.
    fn shoot(&mut self, screens: Vec<ScreenId>, trigger: Trigger, rule: Rule) -> Decision {
        for id in &screens {
            if let Some(t) = self.current.get(id) {
                self.last_shot.insert(id.clone(), t.clone());
            }
        }
        Decision::Shot { screens, trigger, rule }
    }
}

fn skipped(rule: Rule, why: &str) -> Decision {
    Decision::Skipped { rule, why: why.to_string() }
}
