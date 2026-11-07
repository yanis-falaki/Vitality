//! The rules in plain words with the current numbers, for the "How capture works" page: the
//! one source of that text (the interface does not repeat it). `explain(settings)`.

use std::time::Duration;

use super::PolicySettings;
use crate::types::RuleText;

fn secs(d: Duration) -> String {
    let s = d.as_secs_f64();
    if s >= 60.0 && s % 60.0 == 0.0 {
        let m = s / 60.0;
        format!("{m} min")
    } else if s.fract() == 0.0 {
        format!("{s} s")
    } else {
        format!("{s:.1} s")
    }
}

pub fn explain(p: &PolicySettings) -> Vec<RuleText> {
    let pct = p.threshold.fraction * 100.0;
    let mib = p.max_interaction_bytes / (1024 * 1024);
    let rule = |id: &str, title: &str, text: String| RuleText { id: id.into(), title: title.into(), text };
    vec![
        rule("R1", "Start", "Any keyboard or mouse input while no interaction is open starts an active interaction, with a \
             shot of every screen."
            .into()),
        rule("R2", "End", format!(
            "After {} without input, a closing shot (of the screens that changed, by R4), and the interaction ends and \
             is uploaded. If no screen changed, there is no closing shot, but the interaction still ends.",
            secs(p.end_after_idle))),
        rule("R3", "Maximum gap", format!("While you keep using the keyboard or mouse, a shot at least every {}.", secs(p.max_gap))),
        rule("R4", "Which screens", format!(
            "A shot includes a screen only if it changed since that screen's own last shot: more than {pct:.1}\u{a0}% of a \
             small grayscale copy (64 × 36) moved by more than {} levels. A shot in which no screen changed is skipped. \
             R1 shots include every screen.",
            p.threshold.levels)),
        rule("R5", "Passive check", format!(
            "While idle, every {}, each screen is compared with its last shot by R4's rule; if any changed, a shot of the \
             changed screens, marked passive.",
            secs(p.passive_every))),
        rule("R6", "Passive interaction", format!(
            "The first passive shot starts a passive interaction; every passive shot until it ends belongs to it. It ends on \
             any input (R1 then starts an active one), after {} checks in a row without change, or on pause. It holds only \
             passive shots.",
            p.passive_quiet_checks)),
        rule("R7", "Stop", "While paused, nothing is captured and no checks run; an open interaction ends. A locked \
             screen is not a pause: the lock screen is captured like any other. When the machine sleeps, an open \
             interaction ends as it wakes, with no shot for the time asleep."
            .into()),
        rule("R8", "Size budget", format!(
            "When an interaction's shots reach {mib} MiB (or {} shots), it ends and a new one of the same kind starts at \
             once with a shot of every screen, so every upload stays under the server's limit.",
            p.max_interaction_shots)),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_rules_carry_the_current_numbers() {
        let rules = explain(&PolicySettings::default());
        assert_eq!(rules.iter().map(|r| r.id.as_str()).collect::<Vec<_>>(), ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]);
        assert!(rules[1].text.starts_with("After 3 s without input"));
        assert!(rules[2].text.contains("every 10 s"));
        assert!(rules[3].text.contains("more than 0.5\u{a0}%") && rules[3].text.contains("16 levels"));
        assert!(rules[4].text.contains("every 45 s"));
        assert!(rules[5].text.contains("after 3 checks"));
        assert!(rules[6].text.contains("lock screen is captured"));
        assert!(rules[7].text.contains("40 MiB"));
        let other = explain(&PolicySettings { end_after_idle: Duration::from_millis(2500), passive_every: Duration::from_secs(120), ..PolicySettings::default() });
        assert!(other[1].text.starts_with("After 2.5 s without input"));
        assert!(other[4].text.contains("every 2 min"));
    }
}
