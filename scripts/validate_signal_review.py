"""Seeded offline evidence, with test-only causes never passed to the evaluator."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
from random import Random
from statistics import mean

from aegis.collector import SIGNAL_UNITS
from aegis.contracts import Observation, VERSION
from aegis.evaluation import Evaluator


AT = datetime(2026, 10, 20, tzinfo=timezone.utc)
CASES = ("clean", "small_noise", "heavy_noise", "sensor_impulse", "real_pulse",
         "real_pulse_heavy_noise", "common_sensor_noise", "real_correlated_pulse",
         "burst_noise", "real_pulse_burst_noise", "sustained_step", "slow_drift", "setpoint_step")


def fixture(case, seed):
    """Only this harness knows the clean signal and injected causes."""
    rng = Random(seed)
    rate, seconds = (10, 210) if case == "slow_drift" else (100, 8)
    pulses = [(2.23, 2.33), (4.47, 4.57)]
    for tick in range(rate * seconds):
        second = tick / rate
        pulse = any(start <= second < stop - 1e-9 for start, stop in pulses)
        truth = 0.0
        noise = 0.0 if case in {"clean", "setpoint_step"} else rng.gauss(0, .9 if case in
            {"heavy_noise", "real_pulse_heavy_noise"} else .12)
        if case in {"burst_noise", "real_pulse_burst_noise"} and (2 <= second < 3 or 4.2 <= second < 5.2):
            noise += (1 if tick % 8 < 4 else -1) * .9 + rng.gauss(0, .5)
        if case in {"real_pulse", "real_correlated_pulse"} and pulse:
            truth = -4.0
        elif case == "real_pulse_heavy_noise" and pulse:
            truth = -1.5
        elif case == "real_pulse_burst_noise" and pulse:
            truth = -1.5
        elif case == "sustained_step" and second >= 2:
            truth = -3.0
        elif case == "slow_drift":
            truth = -min(8, second * .03)
        if case in {"sensor_impulse", "common_sensor_noise"} and pulse:
            noise -= 4.0
        setpoint = 12.0 if case == "setpoint_step" and second >= 2 else 10.0
        pressure = 220.0 if case in {"common_sensor_noise", "real_correlated_pulse"} and pulse else 250.0
        values = [setpoint, setpoint + (truth + noise), pressure, 50, 50, 2, 25, 10]
        rows = [Observation(schemaVersion=VERSION, observationId=f"sample:{tick}:{key}",
            deviceId="EQ-NOISE-01", signalKey=key, value=float(value), unit=unit, quality="GOOD",
            observedAtUtc=AT + timedelta(seconds=second), receivedAtUtc=AT + timedelta(seconds=second),
            deviceStateVersion=1, modeId="PRODUCTION") for (key, unit), value in zip(SIGNAL_UNITS.items(), values)]
        # Truth and noise remain separate objects from the public observations.
        yield rows, truth, second, rate, seconds


def evaluate(case, seed):
    engine = Evaluator("EQ-NOISE-01")
    raw_errors, trend_errors, events = [], [], {}
    digest = sha256()
    warming = 0
    for rows, truth, second, rate, seconds in fixture(case, seed):
        wire = json.dumps([row.model_dump(mode="json") for row in rows], sort_keys=True, separators=(",", ":"))
        digest.update(wire.encode())
        engine.accept(rows)  # No case, clean truth, seed or cause supplied here.
        assert wire == json.dumps([row.model_dump(mode="json") for row in rows], sort_keys=True, separators=(",", ":"))
        review = engine.signal_review()
        warming += review["state"] == "WARMING"
        if second >= 1:
            raw_errors.append((review["rawErrorLpm"] - truth) ** 2)
            trend_errors.append((review["trendErrorLpm"] - truth) ** 2)
        if review["events"]:
            event = review["events"][-1]
            assert event["disposition"] == "UNRESOLVED"
            events[event["eventId"]] = event
    intervals = [(2.23, 2.33), (4.47, 4.57)] if case in {
        "real_pulse", "real_pulse_heavy_noise", "real_correlated_pulse", "real_pulse_burst_noise"} else []
    if case == "sustained_step":
        intervals = [(2, 8)]
    onsets = [(datetime.fromisoformat(event["startAtUtc"].replace("Z", "+00:00")) - AT).total_seconds()
              for event in events.values()]
    matched = [any(start <= at < stop for at in onsets) for start, stop in intervals]
    onset_delays = [(min(at for at in onsets if start <= at < stop) - start) * 1000
                    for start, stop in intervals if any(start <= at < stop for at in onsets)]
    extra = sum(not any(start <= at < stop for start, stop in intervals) for at in onsets)
    raw_rmse, trend_rmse = sqrt(mean(raw_errors)), sqrt(mean(trend_errors))
    return {"case": case, "seed": seed, "rateHz": rate, "observedSeconds": seconds,
        "publicInputSha256": digest.hexdigest(), "rawInputUnchanged": True,
        "rawRmseLpm": raw_rmse, "trendRmseLpm": trend_rmse,
        "trendRmseReductionPercent": 100 * (1 - trend_rmse / raw_rmse) if raw_rmse else None,
        "truthEventCount": len(intervals), "matchedNewCandidateOnsets": sum(matched),
        "newCandidateRecall": sum(matched) / len(matched) if matched else None,
        "detectedOnsetDelayMs": onset_delays,
        "extraCandidateOnsetsPerMinute": extra * 60 / seconds,
        "candidateCount": review["eventCount"], "warmingSamples": warming,
        "existingEwmaWarningCount": len(engine.warnings()),
        "finalTrendErrorLpm": review["trendErrorLpm"], "events": list(events.values())}


def run():
    records = [evaluate(case, seed) for case in CASES for seed in (11, 97, 2027)]
    by_key = {(row["case"], row["seed"]): row for row in records}
    for first, second in (("sensor_impulse", "real_pulse"), ("common_sensor_noise", "real_correlated_pulse")):
        for seed in (11, 97, 2027):
            a, b = by_key[first, seed], by_key[second, seed]
            assert a["publicInputSha256"] == b["publicInputSha256"]
            assert a["events"] == b["events"], "Identical observations must not produce invented cause certainty."
    assert all(row["matchedNewCandidateOnsets"] == 2 for row in records if row["case"] == "real_pulse")
    assert all(row["candidateCount"] == 0 for row in records if row["case"] == "clean")
    assert all(row["existingEwmaWarningCount"] == 1 for row in records if row["case"] == "slow_drift")
    return {"scope": "Offline synthetic development probe, not equipment-calibrated cause classification.",
        "algorithmVersion": "flow-review-dev-1", "rawInputUnchanged": True,
        "identicalObservationsRemainUnresolved": True,
        "metrics": {"newCandidateRecall": "Truth events with a NEW candidate onset inside their interval; earlier open envelopes do not count.",
            "extraCandidateOnsetsPerMinute": "Review burden outside injected process-event intervals, not confirmed false alarm rate.",
            "trendRmseLpm": "Derived 5-point causal median versus test-only clean signal, after first second; lag is included."},
        "limitations": ["Fixed synthetic engineering constants, no industrial threshold validation.",
            "Heavy, burst or common-cause noise may hide true events or produce review candidates.",
            "No cause certainty from identical observations; all candidates remain UNRESOLVED.",
            "No automatic noise-only deletion, repair or equipment control.",
            "Duration below acquisition cadence cannot be established; look at raw timestamps and sample intervals."],
        "records": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/signal-review.json"))
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Saved {len(result['records'])} seeded cases to {args.output}")
    for case in CASES:
        rows = [row for row in result["records"] if row["case"] == case]
        print(f"{case}: candidates={sum(row['candidateCount'] for row in rows)}, "
              f"matched={sum(row['matchedNewCandidateOnsets'] for row in rows)}/{sum(row['truthEventCount'] for row in rows)}, "
              f"rawRMSE={mean(row['rawRmseLpm'] for row in rows):.4f}, "
              f"trendRMSE={mean(row['trendRmseLpm'] for row in rows):.4f}")


if __name__ == "__main__":
    main()
