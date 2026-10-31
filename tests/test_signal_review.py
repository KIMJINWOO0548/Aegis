"""Synthetic cause labels stay outside the public observation inputs."""

from copy import deepcopy
from datetime import timedelta
from random import Random
from statistics import mean

import pytest

from test_evaluation import AT, batch, evaluator


def review(engine):
    return engine.signal_review()


def warm(engine, rate=100):
    for tick in range(rate):
        engine.accept(batch(tick / rate))


@pytest.mark.parametrize("rate,start,length", [(100, 1.23, .1), (100, 1.99, .1), (10, 1.2, .1), (100, 1.23, .01)])
def test_valid_short_changes_between_one_hertz_samples_keep_raw_peak(rate, start, length):
    engine = evaluator()
    warm(engine, rate)
    original = []
    for tick in range(rate, 3 * rate):
        second = tick / rate
        rows = batch(second, error=4.0 if start <= second < start + length - 1e-8 else 0)
        original.append(deepcopy(rows))
        engine.accept(rows)
        assert rows == original[-1]
    result = review(engine)
    event = result["events"][0]
    assert result["state"] == "REVIEW_REQUIRED"
    assert event["disposition"] == "UNRESOLVED"
    assert event["rawErrorMinLpm"] == -4.0
    assert event["peakDeviationLpm"] == 4.0
    assert event["startAtUtc"] == (AT + timedelta(seconds=start)).isoformat().replace("+00:00", "Z")
    assert event["endReason"] == "RETURNED_TO_CONTEXT"
    assert event["sampleCount"] == max(1, round(length * rate))
    assert event["relatedSignalKeys"] == []
    assert engine.diagnostics()["flowErrorEwmaLpm"] is None
    assert engine.warnings() == []


def test_observationally_identical_noise_and_process_pulse_have_identical_unresolved_results():
    results = []
    for test_only_cause in ("sensor impulse", "real valve disturbance"):
        engine = evaluator()
        warm(engine)
        for tick in range(100, 220):
            engine.accept(batch(tick / 100, error=3.0 if 123 <= tick < 133 else 0))
        results.append(review(engine))
    assert results[0] == results[1]
    assert results[0]["events"][0]["disposition"] == "UNRESOLVED"


def test_conditioned_trend_reduces_seeded_small_noise_without_changing_raw_inputs():
    engine, rng = evaluator(), Random(713)
    raw, trend = [], []
    for tick in range(600):
        error = rng.gauss(0, .12)
        rows = batch(tick / 100, error=error)
        engine.accept(rows)
        result = review(engine)
        if tick >= 100:
            raw.append(error ** 2)
            trend.append(result["trendErrorLpm"] ** 2)
        assert rows[1].value == 10 - error
    assert mean(trend) < .65 * mean(raw)
    assert result["eventCount"] == 0
    assert result["state"] == "TRACKING"


def test_sustained_step_cannot_become_noise_as_the_rolling_baseline_moves():
    engine = evaluator()
    warm(engine)
    for tick in range(100, 600):
        engine.accept(batch(tick / 100, error=3))
    result = review(engine)
    assert result["eventCount"] == 1
    event = result["events"][0]
    assert event["closedAtUtc"] is None
    assert event["sampleCount"] == 500
    assert event["baselineErrorLpm"] == 0
    assert result["trendErrorLpm"] == -3
    assert event["disposition"] == "UNRESOLVED"


def test_return_after_sustained_step_does_not_open_false_event_and_later_change_still_registers():
    engine = evaluator()
    warm(engine)
    for tick in range(100, 900):
        engine.accept(batch(tick / 100, error=3 if tick < 600 else 0))
    result = review(engine)
    assert result["eventCount"] == 1
    assert result["events"][0]["endReason"] == "RETURNED_TO_CONTEXT"
    engine.accept(batch(9, error=4))
    result = review(engine)
    assert result["eventCount"] == 2
    assert result["events"][-1]["baselineErrorLpm"] == 0
    assert result["events"][-1]["peakDeviationLpm"] == 4


@pytest.mark.parametrize("change,reason", [
    (("actualFlowLpm", {"quality": "MISSING", "value": None}), "QUALITY"),
    (("temperatureC", {"quality": "BAD", "value": None}), "QUALITY"),
    (("actualFlowLpm", {"value": -1}), "QUALITY"),
    (("setpointFlowLpm", {"value": 12}), "SETPOINT_CHANGE"),
    (("actualFlowLpm", {"modeId": "SETUP"}), "QUALITY"),
])
def test_context_interruptions_do_not_report_process_recovery(change, reason):
    engine = evaluator()
    warm(engine)
    engine.accept(batch(1.0, error=3))
    engine.accept(batch(1.01, error=3, change=change))
    result = review(engine)
    assert result["events"][0]["endReason"] == reason
    assert result["trendErrorLpm"] in (None, -5)
    assert result["contextSampleCount"] <= 1
    assert result["state"] in ("UNAVAILABLE", "WARMING")


@pytest.mark.parametrize("kind,reason", [("gap", "GAP"), ("mode", "STATE_CHANGE"), ("version", "STATE_CHANGE"), ("unavailable", "SOURCE_UNAVAILABLE")])
def test_resets_keep_history_but_cannot_claim_a_fresh_stable_trend(kind, reason):
    engine = evaluator()
    warm(engine)
    engine.accept(batch(1.0, error=3))
    if kind == "unavailable":
        engine.unavailable()
    else:
        rows = batch(3.0 if kind == "gap" else 1.01, error=3, mode="SETUP" if kind == "mode" else "PRODUCTION")
        if kind == "version":
            for row in rows:
                row.deviceStateVersion += 1
        engine.accept(rows)
    result = review(engine)
    assert result["events"][0]["endReason"] == reason
    assert result["state"] in ("WARMING", "UNAVAILABLE")
    assert result["contextSampleCount"] <= 1


def test_supporting_sensor_changes_are_evidence_and_never_cause_confirmation():
    engine = evaluator()
    warm(engine)
    engine.accept(batch(1, error=3, change=("supplyPressureKpa", {"value": 220})))
    event = review(engine)["events"][0]
    assert event["relatedSignalKeys"] == ["supplyPressureKpa"]
    assert event["disposition"] == "UNRESOLVED"


def test_bounded_history_reports_evictions_and_snapshots_cannot_modify_analysis():
    engine = evaluator()
    warm(engine)
    for tick in range(100, 500):
        engine.accept(batch(tick / 100, error=3 if tick % 10 == 0 else 0))
    result = review(engine)
    assert result["eventCount"] == 40
    assert len(result["events"]) == 32
    assert result["omittedEventCount"] == 8
    result["events"][0]["disposition"] = "NOISE"
    assert review(engine)["events"][0]["disposition"] == "UNRESOLVED"


def test_gently_varying_setpoint_does_not_reset_every_frame_but_step_resets():
    engine = evaluator()
    for tick in range(300):
        rows = batch(tick / 100, change=("setpointFlowLpm", {"value": 10 + tick * .0001}))
        engine.accept(rows)
    assert review(engine)["contextSampleCount"] > 5
    engine.accept(batch(3, change=("setpointFlowLpm", {"value": 11})))
    assert review(engine)["state"] == "WARMING"
    assert review(engine)["contextReason"] == "SETPOINT_CHANGE"


def test_duplicate_and_old_frames_do_not_change_evidence_and_sparse_input_stays_warming():
    engine = evaluator()
    for second in range(10):
        engine.accept(batch(second))
    previous = review(engine)
    engine.accept(batch(9, error=4))
    engine.accept(batch(8, error=4))
    assert review(engine) == previous
    assert previous["state"] == "WARMING"


def test_slow_drift_does_not_disappear_from_setpoint_relative_trend_or_existing_warning():
    engine = evaluator()
    for second in range(210):
        for tenth in range(10):
            at = second + tenth / 10
            engine.accept(batch(at, error=min(8, at * .03)))
    assert review(engine)["trendErrorLpm"] < -6
    assert engine.health().observationEvaluation == "ANOMALOUS"
    assert engine.warnings()[0].state == "OPEN"


def test_excessive_sample_rate_exposes_insufficient_time_context_instead_of_assuming_ready():
    engine = evaluator()
    for tick in range(1500):
        engine.accept(batch(tick / 10000))
    result = review(engine)
    assert result["state"] == "WARMING"
    assert result["contextSampleCount"] == 256
    assert result["contextSpanMs"] < 200
    assert result["eventCount"] == 0
