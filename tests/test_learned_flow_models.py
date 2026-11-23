from dataclasses import replace
from datetime import timedelta
import hashlib
import json

from aegis.archive import RawArchive
from aegis.flow_analysis import _Sample, _forecast_samples, _holdout, analyze_archived_flow
from test_archive import AT, RUN, snapshot
from test_archive_sessions_api import DEVICE, SESSION


def test_four_hz_archived_flow_learns_command_effect_beyond_train_bias(tmp_path):
    marker = hashlib.sha256(DEVICE.encode()).hexdigest()[:12]
    path = tmp_path / f"module-{DEVICE}-{marker}.sqlite3"
    seconds = 16 * 60

    # Seed the RAW_V1 writer directly: the archive-analysis test requires SQLite,
    # but has no dependency on the production event loop or a loopback socket.
    writer = RawArchive(path)
    writer._open()
    try:
        for second in range(seconds):
            rows = []
            setpoint = 12 + second % 11
            valve = 40 + 2 * (second % 13)
            for tick in range(4):
                sequence = 4 * second + tick
                observed = AT + timedelta(milliseconds=250 * sequence)
                actual = setpoint + 2 + 0.2 * (setpoint - 17) + 0.4 * (valve - 50)
                # Add brief train-only spikes to test robustness without changing
                # the held-out process response defined by the fixture.
                if second < 400 and second % 67 == 0:
                    actual += 25
                record = snapshot(sequence, device=DEVICE, value=actual)
                record["observedAtUtc"] = observed.isoformat()
                record["signals"].extend([
                    {"signalKey": "setpointFlowLpm", "quality": "GOOD", "unit": "L/min", "value": setpoint},
                    {"signalKey": "valveCommandPct", "quality": "GOOD", "unit": "%", "value": valve},
                ])
                rows.append((DEVICE, RUN, sequence, record["observedAtUtc"],
                             json.dumps(record, sort_keys=True, separators=(",", ":"))))
            writer._append(rows, (AT + timedelta(seconds=second + 1)).isoformat(), None, None, None)
    finally:
        writer._close_connection()
    before = path.read_bytes()
    result = analyze_archived_flow(SESSION, DEVICE, {
        "storeId": DEVICE, "kind": "RAW_V1", "path": path, "cleanPortable": True,
    })
    assert result["scannedRows"] == 4 * seconds
    report = result["learnedForecastReport"]
    assert report["scope"] == "ARCHIVE_FORECAST_ERROR_ONLY"
    assert report["labelEvidence"] == "NOT_PROVIDED"
    assert report["status"] == "HOLDOUT_EVALUATED"
    assert report["sampling"] == {"intervalSeconds": 1, "lookbackSeconds": 1200,
                                  "rawRowsExamined": 4 * seconds, "selectedRows": seconds}
    comparison, = report["evaluations"]
    assert comparison["runId"] == RUN
    assert comparison["featureKeys"] == ["setpointFlowLpm", "valveCommandPct"]
    assert comparison["trainRows"] > 400
    assert comparison["testRows"] > 200
    assert comparison["preferredModelId"] == "learned-flow-ridge"
    models = {value["modelId"]: value for value in comparison["models"]}
    assert (models["learned-flow-ridge"]["maeLpm"]
            < models["learned-flow-bias"]["maeLpm"] * 0.2)
    assert models["learned-flow-ridge"]["improvementPercent"] > 80
    assert result["recommendation"]["status"] == "RECOMMENDATION_ONLY"
    assert path.read_bytes() == before


def test_forecast_sampling_rejects_future_observation_and_keeps_runs_and_modes_apart():
    second_run = "9eb48cb0-5132-4720-90db-26a66e3cc485"
    one = _Sample(AT + timedelta(milliseconds=100), AT, "PRODUCTION", RUN, 10, 11, 40)
    same_second_later = replace(one, receipt=AT + timedelta(milliseconds=300),
                                observation=AT + timedelta(milliseconds=200), actual=99)
    future = replace(one, observation=AT + timedelta(seconds=5), actual=90)
    other_run = replace(one, run=second_run, actual=12)
    other_mode = replace(one, mode="SETUP", actual=10)
    samples = _forecast_samples([same_second_later, future, other_run, other_mode, one])
    assert samples == {(RUN, "PRODUCTION"): [(one, 1)], (RUN, "SETUP"): [(other_mode, 0)],
                       (second_run, "PRODUCTION"): [(other_run, 2)]}
    assert _holdout(samples[RUN, "PRODUCTION"]) is None
