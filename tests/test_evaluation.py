"""Public raw-observation examples; these are not independent model validation."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from aegis.contracts import Observation, VERSION


AT = datetime(2026, 10, 18, tzinfo=timezone.utc)
VALUES = {
    "setpointFlowLpm": (10.0, "L/min"), "actualFlowLpm": (10.0, "L/min"),
    "supplyPressureKpa": (250.0, "kPa"), "valveCommandPct": (50.0, "%"),
    "valveFeedbackPct": (50.0, "%"), "motorCurrentAmp": (2.0, "A"),
    "temperatureC": (25.0, "degC"), "cycleCount": (10.0, "count"),
}


def batch(second, error=0.0, *, change=None, mode="PRODUCTION"):
    rows = []
    for key, (value, unit) in VALUES.items():
        if key == "actualFlowLpm":
            value -= error
        fields = {"schemaVersion": VERSION, "observationId": f"{second}:{key}",
                  "deviceId": "EQ-SUPPLY-01", "signalKey": key, "value": value,
                  "unit": unit, "quality": "GOOD", "observedAtUtc": AT + timedelta(seconds=second),
                  "receivedAtUtc": AT + timedelta(seconds=second), "deviceStateVersion": 1,
                  "modeId": mode}
        if change and key == change[0]:
            fields.update(change[1])
        rows.append(Observation(**fields))
    return rows


def evaluator():
    from aegis.evaluation import Evaluator
    return Evaluator("EQ-SUPPLY-01")


def feed(engine, start, stop, error=0.0, **kwargs):
    for second in range(start, stop):
        engine.accept(batch(second, error, **kwargs))


def test_normal_requires_sixty_consecutive_samples_and_never_invents_a_percentile():
    engine = evaluator()
    feed(engine, 0, 59)
    assert engine.diagnostics()["evaluation"] == "INSUFFICIENT_DATA"
    assert engine.diagnostics()["sampleCount"] == 59
    assert engine.diagnostics()["flowErrorEwmaLpm"] is None
    engine.accept(batch(59))
    health = engine.health()
    assert health.observationEvaluation == "NORMAL"
    assert health.modelLifecycle == "ADVISORY"
    assert health.modelVersion == "ewma-flow-dev-1"
    assert health.normalReferencePercentile is None
    assert health.windowEndUtc == AT + timedelta(seconds=59)
    assert health.evidenceSignalKeys == ["actualFlowLpm", "setpointFlowLpm"]
    assert engine.diagnostics()["flowErrorEwmaLpm"] == 0.0
    assert engine.warnings() == []


def test_three_high_windows_open_one_incident_and_three_low_windows_resolve_it():
    engine = evaluator()
    feed(engine, 0, 119, error=2.0)
    assert engine.warnings() == []
    engine.accept(batch(119, error=2.0))
    warning = engine.warnings()[0]
    assert warning.state == "OPEN"
    assert engine.health().observationEvaluation == "ANOMALOUS"
    assert engine.timeline(warning.id).events[0].eventName == "EWMA_WARNING_RAISED"
    feed(engine, 120, 239)
    assert engine.warnings()[0].state == "OPEN"
    engine.accept(batch(239))
    assert engine.diagnostics()["flowErrorEwmaLpm"] == pytest.approx(0.9216)
    feed(engine, 240, 299)
    assert engine.warnings()[0].state == "OPEN"
    engine.accept(batch(299))
    assert engine.health().observationEvaluation == "NORMAL"
    assert engine.warnings()[0].id == warning.id
    assert engine.warnings()[0].state == "RESOLVED"
    assert engine.timeline(warning.id).events[-1].eventName == "ADVISORY_RECOVERY_CONFIRMED"
    assert engine.timeline("unknown") is None


def test_duplicate_and_out_of_order_batches_cannot_advance_warning_windows():
    engine = evaluator()
    feed(engine, 0, 90, error=2.0)
    for _ in range(100):
        engine.accept(batch(89, error=2.0))
        engine.accept(batch(60, error=2.0))
    assert engine.diagnostics()["sampleCount"] == 60
    assert engine.warnings() == []
    feed(engine, 90, 120, error=2.0)
    assert len(engine.warnings()) == 1


def test_gap_and_mode_change_restart_warmup_and_discard_the_old_score():
    engine = evaluator()
    feed(engine, 0, 60)
    engine.accept(batch(62))
    assert engine.diagnostics()["evaluation"] == "INSUFFICIENT_DATA"
    assert engine.diagnostics()["sampleCount"] == 1
    assert engine.diagnostics()["flowErrorEwmaLpm"] is None
    feed(engine, 63, 122)
    assert engine.health().observationEvaluation == "NORMAL"
    engine.accept(batch(122, mode="SETUP"))
    assert engine.diagnostics()["sampleCount"] == 1
    assert engine.health().observationEvaluation == "INSUFFICIENT_DATA"


def test_device_state_version_change_cannot_mix_operating_states_in_one_window():
    engine = evaluator()
    for second in range(60):
        engine.accept(batch(second))
    assert engine.health().observationEvaluation == "NORMAL"
    changed = batch(60)
    for row in changed:
        row.deviceStateVersion += 1
    engine.accept(changed)
    assert engine.health().observationEvaluation == "INSUFFICIENT_DATA"
    assert engine.diagnostics()["sampleCount"] == 1


@pytest.mark.parametrize("key,value", [
    ("actualFlowLpm", -0.1), ("setpointFlowLpm", 100.1),
    ("supplyPressureKpa", 2000.1), ("valveCommandPct", -0.1),
    ("valveFeedbackPct", 100.1), ("motorCurrentAmp", 100.1),
    ("temperatureC", -40.1), ("temperatureC", 200.1),
    ("cycleCount", -1.0), ("cycleCount", 1.5),
])
def test_source_good_out_of_range_values_are_bad_data_not_an_ml_warning(key, value):
    engine = evaluator()
    feed(engine, 0, 60)
    engine.accept(batch(60, change=(key, {"value": value})))
    assert engine.diagnostics()["evaluation"] == "INSUFFICIENT_DATA"
    assert engine.diagnostics()["sampleCount"] == 0
    assert engine.diagnostics()["flowErrorEwmaLpm"] is None
    assert engine.health().dataQuality == "BAD"
    assert engine.warnings() == []
    issue = engine.diagnostics()["issues"][0]
    assert issue["signalKey"] == key
    assert issue["reasonCode"] == "OUT_OF_RANGE"
    assert any("가" <= char <= "힣" for char in issue["message"])
    engine.accept(batch(61))
    assert engine.diagnostics()["sampleCount"] == 1


@pytest.mark.parametrize("quality", ["BAD", "MISSING", "STALE"])
def test_invalid_non_flow_quality_resets_analysis_and_preserves_the_open_warning(quality):
    engine = evaluator()
    feed(engine, 0, 120, error=2.0)
    warning_id = engine.warnings()[0].id
    engine.accept(batch(120, change=("temperatureC", {"value": None, "quality": quality})))
    assert engine.health().observationEvaluation == "INSUFFICIENT_DATA"
    assert engine.health().dataQuality == quality
    assert engine.diagnostics()["sampleCount"] == 0
    assert engine.diagnostics()["issues"][0]["reasonCode"] == quality
    assert engine.warnings()[0].id == warning_id
    assert engine.warnings()[0].state == "OPEN"


def test_missing_signal_cannot_be_counted_as_a_complete_sample():
    engine = evaluator()
    engine.accept(batch(0)[:-1])
    assert engine.diagnostics()["sampleCount"] == 0
    assert engine.health().dataQuality == "MISSING"
    assert engine.diagnostics()["issues"][0]["signalKey"] == "cycleCount"


def test_unavailable_cannot_report_normal_or_resolve_an_open_warning():
    engine = evaluator()
    feed(engine, 0, 120, error=2.0)
    engine.unavailable()
    assert engine.health().observationEvaluation == "UNAVAILABLE"
    assert engine.health().dataQuality == "STALE"
    assert engine.diagnostics()["flowErrorEwmaLpm"] is None
    assert engine.warnings()[0].state == "OPEN"
    engine.accept(batch(119))
    assert engine.health().observationEvaluation == "UNAVAILABLE"
    engine.accept(batch(120))
    assert engine.diagnostics()["sampleCount"] == 1
    assert engine.health().observationEvaluation == "INSUFFICIENT_DATA"


def test_samples_must_cover_fifty_nine_seconds_and_diagnostics_expose_only_public_inputs():
    engine = evaluator()
    for step in range(60):
        engine.accept(batch(step / 2))
    diagnostics = engine.diagnostics()
    assert diagnostics["evaluation"] == "INSUFFICIENT_DATA"
    assert diagnostics["flowErrorEwmaLpm"] is None
    assert set(diagnostics) == {"deviceId", "enabled", "modelVersion", "evaluation",
                                "sampleCount", "flowErrorEwmaLpm", "issues"}
    assert diagnostics["enabled"] is True
    assert "scenario" not in json.dumps(diagnostics).lower()


def test_progressive_raw_flow_error_is_detected_without_scenario_metadata():
    engine = evaluator()
    for second in range(209):
        engine.accept(batch(second, error=second * .03))
    assert engine.warnings() == []
    engine.accept(batch(209, error=6.27))
    assert engine.health().observationEvaluation == "ANOMALOUS"
    assert engine.warnings()[0].state == "OPEN"


def test_valid_subsecond_frames_update_live_time_without_displacing_the_one_hertz_window():
    engine = evaluator()
    feed(engine, 0, 60)
    engine.accept(batch(59.5))
    assert engine.health().observationEvaluation == "NORMAL"
    assert engine.diagnostics()["sampleCount"] == 60
    assert engine.diagnostics()["flowErrorEwmaLpm"] == 0.0
    assert engine.health().windowEndUtc == AT + timedelta(seconds=59.5)


@pytest.mark.parametrize("rate", [10, 100])
def test_high_rate_raw_input_builds_a_one_hertz_advisory_window(rate):
    engine = evaluator()
    for step in range(59 * rate + 1):
        engine.accept(batch(step / rate))
    assert engine.health().observationEvaluation == "NORMAL"
    assert engine.diagnostics()["sampleCount"] == 60
    assert engine.diagnostics()["flowErrorEwmaLpm"] == 0.0


@pytest.mark.parametrize("change", [
    ("temperatureC", {"quality": "BAD", "value": None}),
    ("actualFlowLpm", {"value": -5.0}),
])
def test_brief_invalid_transient_between_projection_samples_is_never_skipped(change):
    engine = evaluator()
    for step in range(591):
        engine.accept(batch(step / 10))
    engine.accept(batch(59.05, change=change))
    assert engine.health().observationEvaluation == "INSUFFICIENT_DATA"
    assert engine.health().dataQuality == "BAD"
    assert engine.diagnostics()["sampleCount"] == 0
    assert engine.diagnostics()["flowErrorEwmaLpm"] is None
    assert engine.diagnostics()["issues"][0]["signalKey"] == change[0]
    engine.accept(batch(59.1))
    assert engine.diagnostics()["sampleCount"] == 1
    assert engine.health().observationEvaluation == "INSUFFICIENT_DATA"


def test_high_windows_must_be_consecutive_and_threshold_is_strict():
    engine = evaluator()
    feed(engine, 0, 59, error=1.5)
    engine.accept(batch(59, error=1.5))
    feed(engine, 60, 120, error=1.5)
    assert engine.warnings() == []
    engine.unavailable()
    feed(engine, 120, 180, error=2.0)
    feed(engine, 180, 300)
    assert engine.warnings() == []
    feed(engine, 300, 389, error=10.0)
    assert engine.warnings() == []
    engine.accept(batch(389, error=10.0))
    assert len(engine.warnings()) == 1


def test_recovery_at_one_liter_requires_three_windows_after_invalid_data():
    engine = evaluator()
    feed(engine, 0, 120, error=2.0)
    warning_id = engine.warnings()[0].id
    engine.accept(batch(120, change=("temperatureC", {"quality": "BAD"})))
    feed(engine, 121, 240, error=1.0)
    assert engine.warnings()[0].state == "OPEN"
    engine.accept(batch(240, error=1.0))
    assert engine.warnings()[0].state == "RESOLVED"
    assert engine.warnings()[0].id == warning_id


def test_warning_and_timeline_snapshots_cannot_modify_the_evaluator():
    engine = evaluator()
    feed(engine, 0, 120, error=2.0)
    warning = engine.warnings()[0]
    warning.state = "RESOLVED"
    timeline = engine.timeline(warning.id)
    timeline.events[0].safeContext["flowErrorEwmaLpm"] = 999.0
    assert engine.warnings()[0].state == "OPEN"
    assert engine.timeline(warning.id).events[0].safeContext["flowErrorEwmaLpm"] == 2.0


def test_only_thirty_two_recent_incidents_and_their_timelines_are_retained():
    engine = evaluator()
    first_id = None
    for cycle in range(33):
        start = cycle * 240
        engine.unavailable()
        feed(engine, start, start + 120, error=2.0)
        if cycle == 0:
            first_id = engine.warnings()[0].id
        engine.unavailable()
        feed(engine, start + 120, start + 240)
    warnings = engine.warnings()
    assert len(warnings) == 32
    assert all(warning.state == "RESOLVED" for warning in warnings)
    assert engine.timeline(first_id) is None
    assert all(len(engine.timeline(warning.id).events) == 2 for warning in warnings)
