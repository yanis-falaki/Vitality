//! The rules R1–R8 as `Policy`'s event handlers (the types are in `mod.rs`).
//!
//! R1 any input while no interaction is open starts an active one, with a shot of every screen.
//! R2 after `end_after_idle` without input, a closing shot, and the interaction ends at once.
//! R3 while input goes on, a shot at least every `max_gap`.
//! R4 a shot holds only screens changed since their own last shot; none changed → no shot (the
//!    R2 end still happens). R1 shots include every screen.
//! R5 while idle, every `passive_every`, a passive shot of the changed screens.
//! R6 passive shots form one passive interaction; it ends on input (R1 then starts an active
//!    one), after `passive_quiet_checks` checks without change, or when capture stops.
//! R7 paused: an open interaction ends; nothing is captured, no checks run.
//! R8 an interaction whose shots reach `max_interaction_bytes` (or `max_interaction_shots`)
//!    ends, and one of the same kind starts at once with a shot of every screen.
//!
//! After a suspend (`on_slept`, told by the runtime) an open interaction ends at once (R2
//! active, R6 passive) with no shot: the time asleep is not part of it.

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
                // R6: input ends the passive interaction; R1 starts an active one.
                let mut out = vec![Decision::EndInteraction { rule: Rule::R6 }];
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
            Mode::Active { .. } => Rule::R2,
            Mode::Passive { .. } => Rule::R6,
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
                // R5: a passive check; the first changed one starts a passive interaction (R6).
                let screens = self.changed_screens();
                if screens.is_empty() {
                    self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                    return vec![skipped(Rule::R5, "passive check: no screen changed")];
                }
                self.mode = Mode::Passive { since: now, shots: 1, next_check: now + s.passive_every, quiet: 0 };
                vec![
                    Decision::StartInteraction { kind: InteractionKind::Passive },
                    self.shoot(screens, Trigger::Passive, Rule::R5),
                ]
            }
            Mode::Idle { .. } => vec![],
            Mode::Passive { since, shots, next_check, quiet } if now >= next_check => {
                let screens = self.changed_screens();
                let next_check = now + s.passive_every;
                if !screens.is_empty() {
                    self.mode = Mode::Passive { since, shots: shots + 1, next_check, quiet: 0 };
                    return vec![self.shoot(screens, Trigger::Passive, Rule::R5)];
                }
                let quiet = quiet + 1;
                if quiet >= s.passive_quiet_checks {
                    self.mode = Mode::Idle { next_check: Some(next_check) };
                    return vec![
                        skipped(Rule::R5, "passive check: no screen changed"),
                        Decision::EndInteraction { rule: Rule::R6 },
                    ];
                }
                self.mode = Mode::Passive { since, shots, next_check, quiet };
                vec![skipped(Rule::R5, &format!("passive check: no screen changed ({quiet} of {})", s.passive_quiet_checks))]
            }
            Mode::Passive { .. } => vec![],
            Mode::Active { since, shots, last_shot_at } => {
                let last_input = self.last_input.unwrap_or(since);
                if now.duration_since(last_input) >= s.end_after_idle {
                    // R2: idle long enough: the closing shot (R4 decides which screens), then the end.
                    let screens = self.changed_screens();
                    let closing = if screens.is_empty() {
                        skipped(Rule::R2, "closing shot: no screen changed (R4)")
                    } else {
                        self.shoot(screens, Trigger::Activity, Rule::R2)
                    };
                    self.mode = Mode::Idle { next_check: Some(now + s.passive_every) };
                    return vec![closing, Decision::EndInteraction { rule: Rule::R2 }];
                }
                if now.duration_since(last_shot_at) < s.max_gap {
                    return vec![];
                }
                // R3: still busy: a shot at least every `max_gap`.
                let screens = self.changed_screens();
                if screens.is_empty() {
                    self.mode = Mode::Active { since, shots, last_shot_at: now };
                    return vec![skipped(Rule::R3, "no screen changed (R4)")];
                }
                self.mode = Mode::Active { since, shots: shots + 1, last_shot_at: now };
                vec![self.shoot(screens, Trigger::Activity, Rule::R3)]
            }
        }
    }

    /// The open interaction now holds `bytes` in `shots` shots (R8: past the budget it is split).
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
            Mode::Active { since, shots, last_shot_at } => {
                (*since, *shots, *last_shot_at) = (now, first, now);
            }
            Mode::Passive { since, shots, quiet, .. } => {
                (*since, *shots, *quiet) = (now, first, 0);
            }
            _ => {}
        }
        let shot = if screens.is_empty() {
            skipped(Rule::R8, "no screen has a frame")
        } else {
            self.shoot(screens, trigger, Rule::R8)
        };
        vec![Decision::EndInteraction { rule: Rule::R8 }, Decision::StartInteraction { kind }, shot]
    }

    /// Capture must stop (R7, or quitting). Ends an open interaction.
    pub fn on_stop(&mut self, _now: Instant, why: StopReason) -> Vec<Decision> {
        let open = matches!(self.mode, Mode::Active { .. } | Mode::Passive { .. });
        self.mode = Mode::Stopped { why };
        if open {
            vec![Decision::EndInteraction { rule: Rule::R7 }]
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
        self.mode = Mode::Active { since: now, shots, last_shot_at: now };
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
