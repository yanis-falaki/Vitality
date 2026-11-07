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
        rule("R1", "Start active", format!(
            "Any keyboard or mouse input after at least {} without input starts an active interaction, with a shot of every screen.",
            secs(p.start_gap))),
        rule("R2", "Settle", format!("{} after the last input, a shot.", secs(p.settle))),
        rule("R3", "Maximum gap", format!("While you are continuously active, a shot at least every {}.", secs(p.max_gap))),
        rule("R4", "Which screens", format!(
            "A shot includes a screen only if it changed since that screen's own last shot: more than {pct:.1}\u{a0}% of a small \
             grayscale copy (64 × 36) moved by more than {} levels. A shot in which no screen changed is skipped. \
             R1 shots include every screen.",
            p.threshold.levels)),
        rule("R5", "End active", format!(
            "After the R2 shot, if no input follows within {}, the interaction ends and is uploaded.", secs(p.start_gap))),
        rule("R6", "Passive check", format!(
            "While idle, every {}, each screen is compared with its last shot by R4's rule; if any changed, a shot of the \
             changed screens, marked passive.",
            secs(p.passive_every))),
        rule("R7", "Passive interaction", format!(
            "The first passive shot starts a passive interaction; every passive shot until it ends belongs to it. It ends on \
             any input (R1 then starts an active one), after {} checks in a row without change, or on pause. It holds only \
             passive shots.",
            p.passive_quiet_checks)),
        rule("R8", "Stop", "While paused, nothing is captured and no checks run; an open interaction ends.".into()),
        rule("R9", "Size budget", format!(
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
        assert_eq!(rules.iter().map(|r| r.id.as_str()).collect::<Vec<_>>(), ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]);
        assert!(rules[0].text.contains("at least 2 s without input"));
        assert!(rules[2].text.contains("every 10 s"));
        assert!(rules[3].text.contains("more than 0.5\u{a0}%") && rules[3].text.contains("16 levels"));
        assert!(rules[5].text.contains("every 45 s"));
        assert!(rules[6].text.contains("after 3 checks"));
        assert!(rules[8].text.contains("40 MiB"));
        let other = explain(&PolicySettings { settle: Duration::from_millis(2500), passive_every: Duration::from_secs(120), ..PolicySettings::default() });
        assert_eq!(other[1].text, "2.5 s after the last input, a shot.");
        assert!(other[5].text.contains("every 2 min"));
        assert!(rules.iter().all(|r| !r.text.contains("lock")));
    }
}
