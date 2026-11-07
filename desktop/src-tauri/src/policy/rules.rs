//! The rules R1–R6 as `Policy`'s event handlers (the types are in `mod.rs`). Every shot is a
//! shot of every screen; change detection only decides passive shots.
//!
//! R1 any input while no interaction is open starts an active one, with a shot.
//! R2 while active, a shot at least every `max_gap`.
//! R3 after `end_after_idle` without input, a closing shot, and the interaction ends.
//! R4 while idle, every `passive_every`, each screen is compared with its last shot (the change
//!    threshold); if any changed, a passive shot. The first starts a passive interaction; it
//!    ends on input (R1 then starts an active one) or after `passive_quiet_checks` checks in a
//!    row without change. Passive and active shots never mix.
//! R5 paused: an open interaction ends; nothing is captured, no checks run.
//! R6 an interaction whose shots reach `max_interaction_bytes` (or `max_interaction_shots`)
//!    ends, and one of the same kind starts at once with a shot.
//!
//! After a suspend (`on_slept`, told by the runtime) an open interaction ends at once (R3
//! active, R4 passive) with no shot: the time asleep is not part of it.

use std::collections::BTreeMap;
use std::time::Instant;

use super::change::changed;
use super::{Decision, InteractionKind, Mode, Policy, Rule, ScreenId, StopReason, Thumb, Trigger};

impl Policy {
    /// Keyboard or mouse input at `now`.
    pub fn on_input(&mut self, now: Instant) -> Vec<Decision> {
        self.last_input = Some(now);
        match &self.mode {
            Mode::Stopped { .. } | Mode::Active { .. } => vec![],
            Mode::Idle { .. } => self.start_active(now),
            Mode::Passive { .. } => {
                // R4: input ends the passive interaction; R1 starts an active one.
                let mut out = vec![Decision::EndInteraction { rule: Rule::Passive }];
                out.extend(self.start_active(now));
                out
            }
        }
    }

    /// The machine was suspended (the runtime measures it; the monotonic clock the policy runs
    /// on does not advance during a suspend): an open interaction ends at once, without a shot,
    /// and the next input starts a fresh one. Call it before the first event after waking.
    pub fn on_slept(&mut self, now: Instant) -> Vec<Decision> {
        let rule = match self.mode {
            Mode::Active { .. } => Rule::End,
            Mode::Passive { .. } => Rule::Passive,
            _ => return vec![],
        };
        self.mode = Mode::Idle { next_check: Some(now + self.settings.passive_every) };
        vec![Decision::EndInteraction { rule }]
    }

    /// The 250 ms tick, with each screen's newest thumb (screens without a frame are absent).
    pub fn on_tick(&mut self, now: Instant, thumbs: &BTreeMap<ScreenId, Thumb>) -> Vec<Decision> {
        self.current = thumbs.clone();
        let s = self.settings.clone();
        match self.mode.clone() {
            Mode::Stopped { .. } => vec![],
            Mode::Idle { next_check: None } => {
                self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                vec![]
            }
            Mode::Idle { next_check: Some(due) } if now >= due => {
                // R4: a passive check; a change starts a passive interaction.
                if !self.any_changed() {
                    self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                    return vec![skipped(Rule::Passive, "Checked idle screens — nothing changed")];
                }
                self.mode = Mode::Passive { since: now, shots: 1, next_check: now + s.passive_every, quiet: 0 };
                vec![Decision::StartInteraction { kind: InteractionKind::Passive }, self.shoot_all(Trigger::Passive, Rule::Passive)]
            }
            Mode::Idle { .. } => vec![],
            Mode::Passive { since, shots, next_check, quiet } if now >= next_check => {
                let next_check = now + s.passive_every;
                if self.any_changed() {
                    self.mode = Mode::Passive { since, shots: shots + 1, next_check, quiet: 0 };
                    return vec![self.shoot_all(Trigger::Passive, Rule::Passive)];
                }
                let quiet = quiet + 1;
                if quiet >= s.passive_quiet_checks {
                    self.mode = Mode::Idle { next_check: Some(next_check) };
                    return vec![skipped(Rule::Passive, "Checked idle screens — nothing changed"), Decision::EndInteraction { rule: Rule::Passive }];
                }
                self.mode = Mode::Passive { since, shots, next_check, quiet };
                vec![skipped(Rule::Passive, &format!("Checked idle screens — nothing changed ({quiet} of {})", s.passive_quiet_checks))]
            }
            Mode::Passive { .. } => vec![],
            Mode::Active { since, shots, last_shot_at } => {
                let last_input = self.last_input.unwrap_or(since);
                if now.duration_since(last_input) >= s.end_after_idle {
                    // R3: idle long enough: the closing shot, then the end.
                    let closing = self.shoot_all(Trigger::Activity, Rule::End);
                    self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                    return vec![closing, Decision::EndInteraction { rule: Rule::End }];
                }
                if now.duration_since(last_shot_at) < s.max_gap {
                    return vec![];
                }
                // R2: still busy: a shot at least every `max_gap`.
                self.mode = Mode::Active { since, shots: shots + 1, last_shot_at: now };
                vec![self.shoot_all(Trigger::Activity, Rule::Busy)]
            }
        }
    }

    /// The open interaction now holds `bytes` in `shots` shots (R6: past the budget it is split).
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
        let first = u32::from(!self.current.is_empty());
        match &mut self.mode {
            Mode::Active { since, shots, last_shot_at } => {
                (*since, *shots, *last_shot_at) = (now, first, now);
            }
            Mode::Passive { since, shots, quiet, .. } => {
                (*since, *shots, *quiet) = (now, first, 0);
            }
            _ => {}
        }
        let shot = self.shoot_all(trigger, Rule::SizeLimit);
        vec![Decision::EndInteraction { rule: Rule::SizeLimit }, Decision::StartInteraction { kind }, shot]
    }

    /// Capture must stop (R5, or quitting). Ends an open interaction.
    pub fn on_stop(&mut self, _now: Instant, why: StopReason) -> Vec<Decision> {
        let open = matches!(self.mode, Mode::Active { .. } | Mode::Passive { .. });
        self.mode = Mode::Stopped { why };
        if open {
            vec![Decision::EndInteraction { rule: Rule::Pause }]
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
        let shots = u32::from(!self.current.is_empty());
        self.mode = Mode::Active { since: now, shots, last_shot_at: now };
        vec![Decision::StartInteraction { kind: InteractionKind::Active }, self.shoot_all(Trigger::Activity, Rule::Start)]
    }

    /// R4's question: has any screen changed since its own last shot (by the change threshold)?
    /// A screen never shot counts as changed.
    fn any_changed(&self) -> bool {
        let t = &self.settings.threshold;
        self.current.iter().any(|(id, now)| self.last_shot.get(id).is_none_or(|last| changed(last, now, t)))
    }

    /// A shot of every screen that has a frame (none: the shot is skipped), remembering their
    /// thumbs as their last shot.
    fn shoot_all(&mut self, trigger: Trigger, rule: Rule) -> Decision {
        if self.current.is_empty() {
            return skipped(rule, "No screenshot — no screen has a picture yet");
        }
        let screens: Vec<ScreenId> = self.current.keys().cloned().collect();
        for (id, t) in &self.current {
            self.last_shot.insert(id.clone(), t.clone());
        }
        Decision::Shot { screens, trigger, rule }
    }
}

fn skipped(rule: Rule, why: &str) -> Decision {
    Decision::Skipped { rule, why: why.to_string() }
}
