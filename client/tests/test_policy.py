from vitality_client.daemon.policy import CapturePolicy, InteractionEnd, InteractionRef, Snapshot


def P(**kw):
    return CapturePolicy(2, 10, **kw)


R = InteractionRef(100.0)


def test_idle_activity_starts_interaction():
    p = P(); assert p.on_activity(100.0) == [Snapshot(R, "activity", 100.0)]; assert p.state == "active"


def test_settle_emits_final_snapshot_and_end():
    p = P(); p.on_activity(100.0); p.on_activity(101.0)
    assert p.on_tick(102.9) == []
    assert p.on_tick(103.0) == [Snapshot(R, "activity", 103.0), InteractionEnd(R, 103.0)]
    assert p.state == "idle"


def test_continuous_activity_snapshots_every_max_gap():
    p = P(); out = []
    for t in range(0, 25): out += p.on_activity(100.0 + t)      # one event per second, never settles
    assert [a.ts for a in out] == [100.0, 110.0, 120.0]
    assert all(isinstance(a, Snapshot) and a.interaction == R for a in out)


def test_next_deadline_tracks_settle_and_gap():
    p = P(); p.on_activity(100.0); assert p.next_deadline() == 102.0
    assert P().next_deadline() is None


def test_pause_while_active_ends_interaction():
    p = P(); p.on_activity(100.0)
    assert p.pause(101.0) == [Snapshot(R, "activity", 101.0), InteractionEnd(R, 101.0)]
    assert p.on_activity(102.0) == [] and p.on_tick(200.0) == [] and p.state == "paused"


def test_pause_right_after_settle_does_not_end_twice():
    p = P(); p.on_activity(100.0)
    out = p.pause(103.0)
    assert out == [Snapshot(R, "activity", 103.0), InteractionEnd(R, 103.0)]


def test_pause_while_idle_emits_nothing():
    assert P().pause(100.0) == []


def test_manual_while_active_joins_open_interaction():
    p = P(); p.on_activity(100.0)
    assert p.manual(101.5) == [Snapshot(R, "manual", 101.5)]
    assert p.next_deadline() == 102.0          # timers unchanged
    assert p.state == "active"


def test_manual_while_idle_is_one_shot_interaction():
    r = InteractionRef(50.0)
    p = P()
    assert p.manual(50.0) == [Snapshot(r, "manual", 50.0), InteractionEnd(r, 50.0)]
    assert p.state == "idle" and p.next_deadline() is None


def test_manual_while_paused_is_one_shot_interaction():                     # Review Focus 5
    p = P(paused=True); r = InteractionRef(50.0)
    assert p.manual(50.0) == [Snapshot(r, "manual", 50.0), InteractionEnd(r, 50.0)]
    assert p.state == "paused"


def test_timed_pause_resumes():
    p = P(); p.pause(100.0, until=160.0)
    assert p.next_deadline() == 160.0
    p.on_tick(160.0); assert p.state == "idle"


def test_expired_timed_pause_from_previous_run_resumes():
    p = P(paused=True, paused_until=50.0); p.on_tick(100.0); assert p.state == "idle"


def test_untimed_pause_from_previous_run_stays_paused():
    p = P(paused=True); assert p.on_activity(100.0) == [] and p.state == "paused"


def test_resume_returns_to_idle():
    p = P(); p.pause(100.0); p.resume(); assert p.state == "idle"


def test_clock_backwards_does_not_emit_or_stick():          # Review Focus 2
    p = P(); p.on_activity(100.0)
    assert p.on_tick(50.0) == []
    assert p.on_tick(102.0) == [Snapshot(R, "activity", 102.0), InteractionEnd(R, 102.0)]


def test_forward_jump_after_suspend_closes_interaction_once():   # Review Focus 2
    p = P(); p.on_activity(100.0)
    assert p.on_tick(5000.0) == [Snapshot(R, "activity", 5000.0), InteractionEnd(R, 5000.0)]
    assert p.on_tick(5001.0) == []


def test_pause_while_paused_updates_deadline_and_emits_nothing():
    p = P(); p.pause(100.0, until=160.0)
    assert p.pause(110.0, until=200.0) == []
    assert p.paused_until == 200.0 and p.state == "paused"


def test_new_interaction_after_resume_has_fresh_ref():
    p = P(); p.on_activity(100.0); p.pause(101.0); p.resume()
    assert p.on_activity(200.0) == [Snapshot(InteractionRef(200.0), "activity", 200.0)]


def test_pause_on_max_gap_boundary_emits_single_final_pair():
    p = P()
    for t in range(100, 110): p.on_activity(float(t))   # 1/s; never settles
    assert p.pause(110.0) == [Snapshot(R, "activity", 110.0), InteractionEnd(R, 110.0)]
    assert p.state == "paused"


def test_max_gap_fires_via_tick_and_stays_active():
    p = P()
    for t in range(100, 110): p.on_activity(float(t))   # 1/s; never settles
    assert p.on_tick(110.0) == [Snapshot(R, "activity", 110.0)]
    assert p.state == "active"


def test_settle_and_max_gap_due_together_emit_single_pair_and_idle():
    p = P(); p.on_activity(100.0)
    assert p.on_tick(120.0) == [Snapshot(R, "activity", 120.0), InteractionEnd(R, 120.0)]
    assert p.state == "idle"


def test_backwards_clock_rebases_deadlines():
    p = P(); p.on_activity(100.0)
    assert p.on_tick(50.0) == []
    deadline = p.next_deadline()
    assert deadline is not None and deadline >= 50.0
    out = []
    for t in range(51, 63): out += p.on_activity(float(t))
    assert [a.ts for a in out] == [60.0]
    assert all(isinstance(a, Snapshot) and a.interaction == R for a in out)


def test_pause_with_backwards_clock_emits_exactly_one_final_pair():
    p = P(); p.on_activity(100.0)
    assert p.pause(50.0) == [Snapshot(R, "activity", 50.0), InteractionEnd(R, 50.0)]
    assert p.state == "paused"
