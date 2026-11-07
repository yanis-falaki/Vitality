//! R1–R8 as timelines: inputs at given seconds, ticks every 250 ms, screens' thumbs as functions
//! of time; each test reads the decisions with their times.

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use super::*;

fn id(s: &str) -> ScreenId {
    ScreenId(s.to_string())
}

/// A screen's picture over time: a flat level that changes at the given seconds.
type Screen = Box<dyn Fn(f64) -> Thumb>;

/// Changes at each of `at` (the level steps by 40 each time).
fn changing_at(at: &'static [f64]) -> Screen {
    Box::new(move |t| Thumb::flat(40 + 40 * at.iter().filter(|&&c| t >= c).count() as u8 % 200))
}

/// Never changes.
fn still() -> Screen {
    Box::new(|_| Thumb::flat(100))
}

/// Changes every second (a video).
fn video() -> Screen {
    Box::new(|t| Thumb::flat(((t.floor() as u32 * 37) % 200) as u8 + 20))
}

struct Timeline {
    policy: Policy,
    t0: Instant,
    screens: Vec<(ScreenId, Screen)>,
    log: Vec<(f64, Decision)>,
    now: f64,
}

impl Timeline {
    fn new(screens: Vec<(&str, Screen)>) -> Timeline {
        let mut tl = Timeline {
            policy: Policy::new(PolicySettings::default()),
            t0: Instant::now(),
            screens: screens.into_iter().map(|(n, s)| (id(n), s)).collect(),
            log: vec![],
            now: 0.0,
        };
        tl.tick(); // the first tick at 0 s sets the first passive check
        tl
    }

    fn at(&self, t: f64) -> Instant {
        self.t0 + Duration::from_secs_f64(t)
    }

    fn thumbs(&self, t: f64) -> BTreeMap<ScreenId, Thumb> {
        self.screens.iter().map(|(id, s)| (id.clone(), s(t))).collect()
    }

    fn record(&mut self, ds: Vec<Decision>) {
        let now = self.now;
        self.log.extend(ds.into_iter().map(|d| (now, d)));
    }

    fn tick(&mut self) {
        let (now, thumbs) = (self.at(self.now), self.thumbs(self.now));
        let ds = self.policy.on_tick(now, &thumbs);
        self.record(ds);
    }

    /// Run until `until` seconds with input at each of `inputs` (sorted), ticking every 250 ms.
    fn run(&mut self, until: f64, inputs: &[f64]) -> &mut Self {
        let mut inputs = inputs.iter().peekable();
        while self.now < until {
            let next = self.now + 0.25;
            while let Some(&&i) = inputs.peek() {
                if i >= next {
                    break;
                }
                self.now = i.max(self.now);
                let ds = self.policy.on_input(self.at(i));
                self.record(ds);
                inputs.next();
            }
            self.now = next;
            self.tick();
        }
        self
    }

    /// Continuous input from `from` to `to`, ten events a second.
    fn burst(from: f64, to: f64) -> Vec<f64> {
        let n = ((to - from) * 10.0).round() as usize;
        (0..=n).map(|i| from + i as f64 * 0.1).collect()
    }

    fn stop(&mut self, why: StopReason) {
        let ds = self.policy.on_stop(self.at(self.now), why);
        self.record(ds);
    }

    fn resume(&mut self) {
        let ds = self.policy.on_resume(self.at(self.now));
        self.record(ds);
    }

    fn shots(&self) -> Vec<(f64, Vec<ScreenId>, Trigger, Rule)> {
        self.log
            .iter()
            .filter_map(|(t, d)| match d {
                Decision::Shot { screens, trigger, rule } => Some((*t, screens.clone(), *trigger, *rule)),
                _ => None,
            })
            .collect()
    }

    fn rules_of_shots(&self) -> Vec<Rule> {
        self.shots().into_iter().map(|s| s.3).collect()
    }

    fn starts(&self) -> Vec<(f64, InteractionKind)> {
        self.log
            .iter()
            .filter_map(|(t, d)| match d {
                Decision::StartInteraction { kind } => Some((*t, *kind)),
                _ => None,
            })
            .collect()
    }

    fn ends(&self) -> Vec<(f64, Rule)> {
        self.log
            .iter()
            .filter_map(|(t, d)| match d {
                Decision::EndInteraction { rule } => Some((*t, *rule)),
                _ => None,
            })
            .collect()
    }

    fn skipped(&self) -> Vec<(f64, Rule)> {
        self.log
            .iter()
            .filter_map(|(t, d)| match d {
                Decision::Skipped { rule, .. } => Some((*t, *rule)),
                _ => None,
            })
            .collect()
    }

    fn clear(&mut self) {
        self.log.clear();
    }
}

// --- R1 · start active ------------------------------------------------------------------------

#[test]
fn r1_first_input_after_idle_starts_with_a_shot_of_every_screen() {
    let mut tl = Timeline::new(vec![("a", still()), ("b", still())]);
    tl.run(5.0, &[]).run(5.5, &[5.1]);
    assert_eq!(tl.starts(), vec![(5.1, InteractionKind::Active)]);
    assert_eq!(tl.shots(), vec![(5.1, vec![id("a"), id("b")], Trigger::Activity, Rule::R1)]);
}

#[test]
fn r1_shot_includes_unchanged_screens() {
    // Both screens were shot before and have not changed since: R1 still takes both.
    let mut tl = Timeline::new(vec![("a", still()), ("b", still())]);
    tl.run(10.0, &[1.0]);
    tl.clear();
    tl.run(20.0, &[15.0]);
    assert_eq!(tl.shots()[0], (15.0, vec![id("a"), id("b")], Trigger::Activity, Rule::R1));
}

#[test]
fn r1_without_frames_starts_but_skips_the_shot() {
    let mut tl = Timeline::new(vec![]);
    tl.run(2.0, &[1.0]);
    assert_eq!(tl.starts(), vec![(1.0, InteractionKind::Active)]);
    assert!(tl.shots().is_empty());
    assert_eq!(tl.skipped(), vec![(1.0, Rule::R1)]);
}

// --- R2 · settle ------------------------------------------------------------------------------

#[test]
fn r2_settle_shot_two_seconds_after_the_last_input() {
    let mut tl = Timeline::new(vec![("a", changing_at(&[1.5]))]);
    tl.run(4.0, &[1.0, 1.2]);
    // Last input 1.2 s: the tick at 3.25 s is the first ≥ 2 s later.
    assert_eq!(tl.shots().iter().map(|s| (s.0, s.3)).collect::<Vec<_>>(), vec![(1.0, Rule::R1), (3.25, Rule::R2)]);
}

#[test]
fn r2_settle_shot_of_an_unchanged_screen_is_skipped() {
    let mut tl = Timeline::new(vec![("a", still())]);
    tl.run(4.0, &[1.0]);
    assert_eq!(tl.rules_of_shots(), vec![Rule::R1]);
    assert_eq!(tl.skipped(), vec![(3.0, Rule::R2)]);
}

// --- R3 · maximum gap -------------------------------------------------------------------------

#[test]
fn r3_a_shot_at_least_every_ten_seconds_in_a_long_burst() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(40.0, &Timeline::burst(1.0, 35.0));
    let shots = tl.shots();
    assert_eq!(shots[0].3, Rule::R1);
    let r3: Vec<f64> = shots.iter().filter(|s| s.3 == Rule::R3).map(|s| s.0).collect();
    assert_eq!(r3, vec![11.0, 21.0, 31.0]);
    assert_eq!(shots.last().unwrap().3, Rule::R2);
    assert_eq!(tl.starts().len(), 1, "one burst is one interaction");
}

#[test]
fn r3_gap_shot_skipped_when_nothing_changed() {
    let mut tl = Timeline::new(vec![("a", still())]);
    tl.run(13.0, &Timeline::burst(1.0, 12.5));
    assert_eq!(tl.rules_of_shots(), vec![Rule::R1]);
    assert_eq!(tl.skipped(), vec![(11.0, Rule::R3)]);
}

// --- R4 · which screens -----------------------------------------------------------------------

#[test]
fn r4_only_changed_screens_are_in_a_shot() {
    let mut tl = Timeline::new(vec![("a", changing_at(&[2.0])), ("b", still())]);
    tl.run(5.0, &[1.0, 1.5]);
    let shots = tl.shots();
    assert_eq!(shots[0].1, vec![id("a"), id("b")]);
    assert_eq!(shots[1], (3.5, vec![id("a")], Trigger::Activity, Rule::R2));
}

#[test]
fn r4_change_is_against_each_screens_own_last_shot() {
    // a changes at 2 s and is shot at 3.5 s; it does not change again, so the next shot (R3 in
    // a long burst) skips it, while b, which changed at 12 s and was not shot since 1 s, is in.
    let mut tl = Timeline::new(vec![("a", changing_at(&[2.0])), ("b", changing_at(&[12.0]))]);
    tl.run(3.6, &[1.0, 1.5]).run(30.0, &Timeline::burst(4.0, 25.0));
    let shots = tl.shots();
    let at_r3: Vec<_> = shots.iter().filter(|s| s.3 == Rule::R3).map(|s| s.1.clone()).collect();
    assert_eq!(at_r3, vec![vec![id("b")]]);
}

// --- R5 · end active --------------------------------------------------------------------------

#[test]
fn r5_ends_when_no_input_follows_the_settle_shot() {
    let mut tl = Timeline::new(vec![("a", still())]);
    tl.run(8.0, &[1.0]);
    assert_eq!(tl.ends(), vec![(5.0, Rule::R5)]);
    assert_eq!(tl.policy.state().mode, CaptureMode::Idle);
}

#[test]
fn r5_input_soon_after_the_settle_shot_continues_the_interaction() {
    let mut tl = Timeline::new(vec![("a", still())]);
    tl.run(12.0, &[1.0, 4.0]); // settle at 3 s, input at 4 s (< 2 s later)
    assert_eq!(tl.starts().len(), 1);
    assert_eq!(tl.ends(), vec![(8.0, Rule::R5)]); // settle again at 6 s, end at 8 s
}

#[test]
fn r5_then_input_starts_a_new_interaction() {
    let mut tl = Timeline::new(vec![("a", still())]);
    tl.run(10.0, &[1.0, 7.0]);
    assert_eq!(tl.starts().iter().map(|s| s.0).collect::<Vec<_>>(), vec![1.0, 7.0]);
}

// --- R6 · passive check -----------------------------------------------------------------------

#[test]
fn r6_idle_check_every_45_s_shoots_only_changed_screens() {
    let mut tl = Timeline::new(vec![("a", changing_at(&[30.0])), ("b", still())]);
    tl.run(10.0, &[1.0]); // shoot both, end at 5 s; next check at 50 s
    tl.clear();
    tl.run(60.0, &[]);
    assert_eq!(tl.starts(), vec![(50.0, InteractionKind::Passive)]);
    assert_eq!(tl.shots(), vec![(50.0, vec![id("a")], Trigger::Passive, Rule::R6)]);
}

#[test]
fn r6_check_without_change_is_skipped_and_starts_nothing() {
    let mut tl = Timeline::new(vec![("a", still())]);
    tl.run(10.0, &[1.0]);
    tl.clear();
    tl.run(100.0, &[]);
    assert!(tl.starts().is_empty());
    assert_eq!(tl.skipped(), vec![(50.0, Rule::R6), (95.0, Rule::R6)]);
}

// --- R7 · passive interaction -----------------------------------------------------------------

#[test]
fn r7_a_video_while_idle_is_one_passive_interaction() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(10.0, &[1.0]);
    tl.clear();
    tl.run(240.0, &[]);
    assert_eq!(tl.starts(), vec![(50.0, InteractionKind::Passive)]);
    assert!(tl.ends().is_empty());
    let times: Vec<f64> = tl.shots().iter().map(|s| s.0).collect();
    assert_eq!(times, vec![50.0, 95.0, 140.0, 185.0, 230.0]);
    assert!(tl.shots().iter().all(|s| s.2 == Trigger::Passive));
}

#[test]
fn r7_input_ends_the_passive_interaction_and_starts_an_active_one() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(10.0, &[1.0]);
    tl.clear();
    tl.run(72.0, &[60.0]);
    assert_eq!(tl.starts(), vec![(50.0, InteractionKind::Passive), (60.0, InteractionKind::Active)]);
    assert_eq!(tl.ends(), vec![(60.0, Rule::R7), (64.0, Rule::R5)]);
    // The order at 60 s: end the passive, start the active, then its R1 shot.
    let at60: Vec<&Decision> = tl.log.iter().filter(|(t, _)| *t == 60.0).map(|(_, d)| d).collect();
    assert!(matches!(at60[0], Decision::EndInteraction { rule: Rule::R7 }));
    assert!(matches!(at60[1], Decision::StartInteraction { kind: InteractionKind::Active }));
    assert!(matches!(at60[2], Decision::Shot { trigger: Trigger::Activity, rule: Rule::R1, .. }));
}

#[test]
fn r7_ends_after_three_quiet_checks() {
    // Changes at 30 s (shot at 50 s), then nothing: quiet at 95, 140, 185 → ends at 185 s.
    let mut tl = Timeline::new(vec![("a", changing_at(&[30.0]))]);
    tl.run(10.0, &[1.0]);
    tl.clear();
    tl.run(250.0, &[]);
    assert_eq!(tl.starts(), vec![(50.0, InteractionKind::Passive)]);
    assert_eq!(tl.ends(), vec![(185.0, Rule::R7)]);
    assert_eq!(tl.policy.state().mode, CaptureMode::Idle);
}

#[test]
fn r7_a_change_resets_the_quiet_count() {
    let mut tl = Timeline::new(vec![("a", changing_at(&[30.0, 120.0]))]);
    tl.run(10.0, &[1.0]);
    tl.clear();
    tl.run(320.0, &[]);
    // shots at 50 and 140; quiet at 95, then 185, 230, 275 → end at 275.
    assert_eq!(tl.shots().iter().map(|s| s.0).collect::<Vec<_>>(), vec![50.0, 140.0]);
    assert_eq!(tl.ends(), vec![(275.0, Rule::R7)]);
}

#[test]
fn r7_pause_ends_the_passive_interaction() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(10.0, &[1.0]).run(60.0, &[]);
    tl.clear();
    tl.stop(StopReason::Paused);
    assert_eq!(tl.ends(), vec![(60.0, Rule::R8)]);
}

#[test]
fn r7_never_mixes_active_and_passive_shots_in_one_interaction() {
    let mut tl = Timeline::new(vec![("a", video()), ("b", changing_at(&[100.0]))]);
    let mut inputs = Timeline::burst(1.0, 3.0);
    inputs.extend(Timeline::burst(150.0, 152.0));
    tl.run(400.0, &inputs);
    let mut open: Option<InteractionKind> = None;
    for (_, d) in &tl.log {
        match d {
            Decision::StartInteraction { kind } => {
                assert!(open.is_none(), "an interaction started while one was open");
                open = Some(*kind);
            }
            Decision::EndInteraction { .. } => open = None,
            Decision::Shot { trigger, .. } => {
                let expected = match open.expect("a shot outside an interaction") {
                    InteractionKind::Active => Trigger::Activity,
                    InteractionKind::Passive => Trigger::Passive,
                };
                assert_eq!(*trigger, expected);
            }
            Decision::Skipped { .. } => {}
        }
    }
}

// --- R8 · stop (Review Focus 4) ---------------------------------------------------------------

#[test]
fn r8_pause_ends_an_open_interaction_at_once_and_captures_nothing_until_resume() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(3.0, &Timeline::burst(1.0, 3.0));
    tl.clear();
    tl.stop(StopReason::Paused);
    assert_eq!(tl.ends(), vec![(3.0, Rule::R8)]);
    assert_eq!(tl.policy.state().mode, CaptureMode::Stopped);
    assert_eq!(tl.policy.state().stopped_by, Some(StopReason::Paused));
    tl.clear();
    // Input and a changing screen for minutes: nothing at all, not even passive checks.
    tl.run(300.0, &Timeline::burst(10.0, 20.0));
    assert!(tl.log.is_empty(), "decided while paused: {:?}", tl.log);
    tl.resume();
    tl.run(302.0, &[301.0]);
    assert_eq!(tl.starts(), vec![(301.0, InteractionKind::Active)]);
}

#[test]
fn r8_quit_stops_checks_too() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.stop(StopReason::Quit);
    tl.run(200.0, &[]);
    assert!(tl.log.is_empty());
}

// --- a suspend --------------------------------------------------------------------------------
// On Linux the monotonic clock (`Instant`) stands still during a suspend: the policy's time
// does not jump, the runtime tells it with `on_slept` before the first event after waking.

#[test]
fn a_suspend_ends_an_open_active_interaction_with_no_shot() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(1.5, &Timeline::burst(1.0, 1.4));
    assert_eq!(tl.policy.state().mode, CaptureMode::Active);
    tl.clear();
    let ds = tl.policy.on_slept(tl.at(1.5));
    assert_eq!(ds, vec![Decision::EndInteraction { rule: Rule::R5 }]);
    assert_eq!(tl.policy.state().mode, CaptureMode::Idle);
    tl.run(3.0, &[]);
    assert!(tl.shots().is_empty(), "no shot for the gap: {:?}", tl.log);
    assert!(tl.policy.on_slept(tl.at(3.0)).is_empty(), "nothing open any more");
}

#[test]
fn the_waking_input_starts_a_fresh_interaction_after_the_old_one_is_closed() {
    // A passive interaction is open when the lid closes; a key press wakes the machine.
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(10.0, &[1.0]).run(60.0, &[]);
    assert_eq!(tl.policy.state().mode, CaptureMode::Passive);
    tl.clear();
    let mut ds = tl.policy.on_slept(tl.at(60.0));
    ds.extend(tl.policy.on_input(tl.at(60.1)));
    assert_eq!(ds[0], Decision::EndInteraction { rule: Rule::R7 });
    assert_eq!(ds[1], Decision::StartInteraction { kind: InteractionKind::Active });
    assert!(matches!(&ds[2], Decision::Shot { rule: Rule::R1, trigger: Trigger::Activity, .. }));
    assert_eq!(ds.len(), 3, "one end, one start: {ds:?}");
}

#[test]
fn a_long_gap_between_ticks_alone_ends_nothing() {
    // A stalled loop (the clock running, no suspend) is not a sleep.
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(1.5, &Timeline::burst(1.0, 1.4));
    tl.clear();
    tl.now += 60.0;
    tl.run(tl.now + 3.0, &[]);
    assert_eq!(tl.ends().iter().map(|e| e.1).collect::<Vec<_>>(), vec![Rule::R5]);
    assert!(!tl.shots().is_empty() || !tl.skipped().is_empty(), "the rules run as usual");
}

// --- state ------------------------------------------------------------------------------------

#[test]
fn state_reports_the_open_interaction_and_the_next_check() {
    let mut tl = Timeline::new(vec![("a", video())]);
    assert_eq!(tl.policy.state().next_check_at, Some(tl.at(45.0)));
    tl.run(2.0, &[1.0]);
    let s = tl.policy.state();
    assert_eq!((s.mode, s.since, s.shots), (CaptureMode::Active, Some(tl.at(1.0)), 1));
}

#[test]
fn settings_defaults_are_the_specs() {
    let s = PolicySettings::default();
    assert_eq!(s.start_gap, Duration::from_secs(2));
    assert_eq!(s.settle, Duration::from_secs(2));
    assert_eq!(s.max_gap, Duration::from_secs(10));
    assert_eq!(s.passive_every, Duration::from_secs(45));
    assert_eq!(s.passive_quiet_checks, 3);
    assert_eq!(s.threshold, Threshold { fraction: 0.005, levels: 16 });
}

// --- R9 · size budget -------------------------------------------------------------------------

#[test]
fn r9_a_full_active_interaction_ends_and_a_new_one_starts_with_every_screen() {
    let mut tl = Timeline::new(vec![("a", video()), ("b", still())]);
    tl.run(3.0, &Timeline::burst(1.0, 3.0));
    let mib = 1024 * 1024;
    assert!(tl.policy.on_written(tl.at(3.0), 39 * mib, 10).is_empty());
    let ds = tl.policy.on_written(tl.at(3.0), 40 * mib, 11);
    assert_eq!(ds[0], Decision::EndInteraction { rule: Rule::R9 });
    assert_eq!(ds[1], Decision::StartInteraction { kind: InteractionKind::Active });
    assert_eq!(ds[2], Decision::Shot { screens: vec![id("a"), id("b")], trigger: Trigger::Activity, rule: Rule::R9 });
    let s = tl.policy.state();
    assert_eq!((s.mode, s.since, s.shots), (CaptureMode::Active, Some(tl.at(3.0)), 1));
    // The burst goes on in the new interaction, which ends by R5 as usual.
    tl.clear();
    tl.run(10.0, &Timeline::burst(3.1, 4.0));
    assert_eq!(tl.ends().last().map(|e| e.1), Some(Rule::R5));
    assert!(tl.starts().is_empty());
}

#[test]
fn r9_a_full_passive_interaction_continues_as_a_passive_one() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(10.0, &[1.0]).run(60.0, &[]);
    let ds = tl.policy.on_written(tl.at(60.0), 1, 900); // the shot count limit
    assert_eq!(ds[1], Decision::StartInteraction { kind: InteractionKind::Passive });
    assert!(matches!(&ds[2], Decision::Shot { trigger: Trigger::Passive, rule: Rule::R9, .. }));
    assert_eq!(tl.policy.state().mode, CaptureMode::Passive);
}

#[test]
fn r9_does_nothing_without_an_open_interaction() {
    let mut tl = Timeline::new(vec![("a", video())]);
    assert!(tl.policy.on_written(tl.at(0.0), u64::MAX, 5).is_empty());
}

#[test]
fn r9_never_splits_a_single_shot() {
    let mut tl = Timeline::new(vec![("a", video())]);
    tl.run(1.5, &[1.0]);
    assert!(tl.policy.on_written(tl.at(1.5), u64::MAX, 1).is_empty());
}
