from vitality_client.policy import CapturePolicy, SnapshotRequest

def fresh():
    """Return a P(**kw) factory whose policies draw ids i1, i2, i3 from a fresh counter."""
    ids = iter(["i1", "i2", "i3"])
    return lambda **kw: CapturePolicy(2, 10, ids.__next__, **kw)


def test_idle_activity_emits_before():
    P = fresh()
    p = P(); assert p.on_activity(100.0) == [SnapshotRequest("before", "i1", 100.0)]; assert p.state == "active"


def test_settle_emits_after_then_idle():
    P = fresh()
    p = P(); p.on_activity(100.0); p.on_activity(101.0)
    assert p.on_tick(102.9) == []
    assert p.on_tick(103.0) == [SnapshotRequest("after", "i1", 103.0)]
    assert p.state == "idle"


def test_continuous_activity_forces_after_every_max_gap():
    P = fresh()
    p = P(); out = []
    for t in range(0, 25): out += p.on_activity(100.0 + t)      # one event per second, never settles
    assert [(r.kind, r.ts) for r in out] == [("before", 100.0), ("after", 110.0), ("after", 120.0)]
    assert {r.interaction_id for r in out} == {"i1"}


def test_next_deadline_tracks_settle_and_gap():
    P = fresh()
    p = P(); p.on_activity(100.0); assert p.next_deadline() == 102.0
    assert P().next_deadline() is None


def test_pause_while_active_emits_final_after_and_ignores_activity():
    P = fresh()
    p = P(); p.on_activity(100.0)
    assert p.pause(101.0) == [SnapshotRequest("after", "i1", 101.0)]
    assert p.on_activity(102.0) == [] and p.on_tick(200.0) == [] and p.state == "paused"


def test_pause_while_idle_emits_nothing():
    P = fresh()
    assert P().pause(100.0) == []


def test_timed_pause_resumes():
    P = fresh()
    p = P(); p.pause(100.0, until=160.0)
    assert p.next_deadline() == 160.0
    p.on_tick(160.0); assert p.state == "idle"


def test_expired_timed_pause_from_previous_run_resumes():
    P = fresh()
    p = P(paused=True, paused_until=50.0); p.on_tick(100.0); assert p.state == "idle"


def test_untimed_pause_from_previous_run_stays_paused():
    P = fresh()
    p = P(paused=True); assert p.on_activity(100.0) == [] and p.state == "paused"


def test_resume_returns_to_idle():
    P = fresh()
    p = P(); p.pause(100.0); p.resume(); assert p.state == "idle"


def test_clock_backwards_does_not_emit_or_stick():          # Review Focus 2
    P = fresh()
    p = P(); p.on_activity(100.0)
    assert p.on_tick(50.0) == []
    assert p.on_tick(102.0) == [SnapshotRequest("after", "i1", 102.0)]


def test_forward_jump_after_suspend_closes_interaction_once():   # Review Focus 2
    P = fresh()
    p = P(); p.on_activity(100.0)
    assert [r.kind for r in p.on_tick(5000.0)] == ["after"]
    assert p.on_tick(5001.0) == []


def test_pause_while_paused_updates_deadline_and_emits_nothing():
    P = fresh()
    p = P(); p.pause(100.0, until=160.0)
    assert p.pause(110.0, until=200.0) == []
    assert p.paused_until == 200.0 and p.state == "paused"


def test_new_interaction_after_resume_gets_fresh_id():
    P = fresh()
    p = P(); p.on_activity(100.0); p.pause(101.0); p.resume()
    assert p.on_activity(200.0) == [SnapshotRequest("before", "i2", 200.0)]
