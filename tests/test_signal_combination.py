"""Experimental arithmetic checks use fixed traces, never held-out seeds."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_evaluation import AT, batch


def monitor():
    from scripts.signal_combination import SignedWindowEvidence
    return SignedWindowEvidence("EQ-SUPPLY-01")


def test_signed_accumulation_preserves_a_below_instant_threshold_pulse_and_never_backdates():
    engine = monitor()
    emitted = []
    for tick in range(200):
        error = 1.5 if 101 <= tick < 112 else (.7 if tick % 2 else -.7)
        rows = batch(tick / 100, error=error)
        before = deepcopy(rows)
        candidates = engine.accept(rows)
        assert rows == before
        assert all(item["emittedAtUtc"] == rows[0].observedAtUtc for item in candidates)
        emitted.extend(candidates)
    assert any(item["channel"] == "SIGNED_100MS" and item["emittedAtUtc"] < AT + timedelta(seconds=1.12)
               for item in emitted)
    assert all(item["disposition"] == "UNRESOLVED" for item in emitted)
    assert all(item["windowStartAtUtc"] < item["emittedAtUtc"] for item in emitted)


def test_one_sample_spike_is_not_manufactured_into_short_window_duration():
    engine = monitor()
    emitted = []
    for tick in range(200):
        emitted.extend(engine.accept(batch(tick / 100, error=3 if tick == 101 else 0)))
    assert emitted == []


@pytest.mark.parametrize("interruption", ["quality", "state", "setpoint", "gap", "sequence"])
def test_interruption_discards_partial_accumulation_and_requires_new_context(interruption):
    engine = monitor()
    for tick in range(100):
        engine.accept(batch(tick / 100))
    engine.accept(batch(1, error=1))
    rows = batch(3 if interruption == "gap" else 1.01, error=1,
                 mode="SETUP" if interruption == "state" else "PRODUCTION")
    if interruption == "quality":
        rows[1].quality, rows[1].value = "MISSING", None
    if interruption == "setpoint":
        rows[0].value = 12
    assert engine.accept(rows, continuous=interruption != "sequence") == []


def test_identical_observations_with_different_test_only_causes_cannot_change_outputs():
    answers = []
    for cause in ("real", "noise"):
        engine = monitor()
        answers.append([engine.accept(batch(tick / 100, error=2 if 100 <= tick < 115 else 0)) for tick in range(200)])
    assert answers[0] == answers[1]


def test_duplicate_and_old_input_do_not_create_more_evidence():
    engine = monitor()
    for tick in range(100):
        engine.accept(batch(tick / 100))
    assert engine.accept(batch(.99, error=3)) == []
    assert engine.accept(batch(.5, error=3)) == []


def test_groups_preserve_all_child_ids_and_cannot_chain_forever():
    from scripts.signal_combination import group_evidence
    records = [{"id": str(tick), "emittedAtUtc": AT + timedelta(milliseconds=50 * tick)} for tick in range(60)]
    groups = group_evidence(records)
    assert [key for group in groups for key in group["childIds"]] == [row["id"] for row in records]
    assert len(groups) == 3
    assert all((group["lastAtUtc"] - group["startAtUtc"]).total_seconds() <= 1 for group in groups)
