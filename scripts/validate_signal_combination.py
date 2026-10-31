"""One frozen held-out experiment. Causes exist only here, never in detector inputs."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
from random import Random

from aegis.collector import SIGNAL_UNITS
from aegis.contracts import Observation, VERSION
from aegis.evaluation import Evaluator
from scripts.signal_combination import SignedWindowEvidence, WINDOWS_MS, group_evidence
from scripts.validate_signal_review import AT


ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("independent", "independent_pulse", "burst", "burst_pulse", "ar05", "ar09", "ar09_pulse", "artifact_counterpart")


def fixture(family, seed):
    """AR innovations preserve marginal sigma .9; counterpart changes cause labels only."""
    rng = Random(seed)
    rho = .5 if family == "ar05" else (.9 if family in {"ar09", "ar09_pulse", "artifact_counterpart"} else 0)
    previous = rng.gauss(0, .9) if rho else 0
    duration_ticks = (5, 10, 20)[(seed - 49001) % 3]
    for tick in range(400):
        if rho:
            previous = rho * previous + rng.gauss(0, .9 * sqrt(1 - rho * rho))
            noise = previous
        else:
            noise = rng.gauss(0, .12 if family.startswith("burst") else .9)
        if family.startswith("burst") and 200 <= tick < 300:
            noise += (1 if tick % 8 < 4 else -1) * .9 + rng.gauss(0, .5)
        injected = (family.endswith("_pulse") or family == "artifact_counterpart") and 223 <= tick < 223 + duration_ticks
        shift = (-1.5 if seed % 2 else 1.5) if injected else 0
        values = (10, 10 + noise + shift, 250, 50, 50, 2, 25, 10)
        at = AT + timedelta(milliseconds=10 * tick)
        rows = [Observation(schemaVersion=VERSION, observationId=f"sample:{tick}:{key}",
            deviceId="EQ-COMBINATION-01", signalKey=key, value=float(value), unit=unit,
            observedAtUtc=at, receivedAtUtc=at, quality="GOOD", deviceStateVersion=1, modeId="PRODUCTION")
            for (key, unit), value in zip(SIGNAL_UNITS.items(), values)]
        yield rows, bool(injected and family != "artifact_counterpart")


def summarize(candidates, truth_interval):
    times = [(row["emittedAtUtc"] - AT).total_seconds() for row in candidates]
    eligible = [at for at in times if at >= 1]
    start, stop = truth_interval or (0, 0)
    matches = [at for at in eligible if start <= at < stop]
    background = sum(not start <= at < stop for at in eligible)
    seconds = 3 - (stop - start)
    return {"candidateCount": len(candidates), "eligibleCandidateCount": len(eligible),
        "matchedOnset": bool(matches), "onsetDelayMs": (min(matches) - start) * 1000 if matches else None,
        "duplicateEmissionsInsideTruth": max(0, len(matches) - 1),
        "lateEmissionsWithin200Ms": sum(stop <= at < stop + .2 for at in eligible) if truth_interval else 0,
        "backgroundCandidateCount": background, "eligibleBackgroundSeconds": seconds,
        "backgroundCandidatesPerMinute": background * 60 / seconds}


def evaluate(family, seed):
    baseline, experiment = Evaluator("EQ-COMBINATION-01"), SignedWindowEvidence("EQ-COMBINATION-01")
    raw, signed, digest = {}, [], sha256()
    truth_ticks = []
    for tick, (rows, truth) in enumerate(fixture(family, seed)):
        wire = json.dumps([row.model_dump(mode="json") for row in rows], sort_keys=True, separators=(",", ":"))
        digest.update(wire.encode())
        baseline.accept(rows)
        signed.extend(experiment.accept(rows))
        assert wire == json.dumps([row.model_dump(mode="json") for row in rows], sort_keys=True, separators=(",", ":"))
        if truth:
            truth_ticks.append(tick)
        if tick % 10 == 0 or tick == 399:
            review = baseline.signal_review()
            for event in review["events"]:
                assert event["disposition"] == "UNRESOLVED"
                raw[event["eventId"]] = {"id": f"raw-{event['eventId']}", "channel": "RAW",
                    "emittedAtUtc": datetime.fromisoformat(event["startAtUtc"].replace("Z", "+00:00")),
                    "disposition": "UNRESOLVED"}
            assert len(raw) == review["eventCount"], "Bounded polling lost a raw candidate."
    interval = (truth_ticks[0] / 100, (truth_ticks[-1] + 1) / 100) if truth_ticks else None
    candidates = [*raw.values(), *signed]
    groups = group_evidence(candidates)
    grouped_ids = [key for group in groups for key in group["childIds"]]
    assert len(grouped_ids) == len(set(grouped_ids)) == len(candidates)
    assert set(grouped_ids) == {row["id"] for row in candidates}
    variants = {"raw": list(raw.values()), **{f"raw+{window}ms": [*raw.values(), *[row for row in signed
        if row["channel"] == f"SIGNED_{window}MS"]] for window in WINDOWS_MS}, "raw+all": candidates}
    return {"family": family, "seed": seed, "publicInputSha256": digest.hexdigest(),
        "truthIntervalSeconds": interval, "rawInputUnchanged": True, "allRawCandidatesPreserved": True,
        "metrics": {name: summarize(rows, interval) for name, rows in variants.items()},
        "navigationGroupCount": len(groups), "rawCandidates": list(raw.values()),
        "signedCandidates": signed, "groups": groups}


def gate(records):
    recovered = sum(row["truthIntervalSeconds"] is not None and not row["metrics"]["raw"]["matchedOnset"]
        and row["metrics"]["raw+all"]["matchedOnset"] for row in records)
    noise = {}
    for family in ("independent", "ar05", "ar09"):
        selected = [row for row in records if row["family"] == family]
        added = sum(row["metrics"]["raw+all"]["backgroundCandidateCount"] -
                    row["metrics"]["raw"]["backgroundCandidateCount"] for row in selected)
        seconds = sum(row["metrics"]["raw"]["eligibleBackgroundSeconds"] for row in selected)
        noise[family] = added * 60 / seconds
    burst = [row for row in records if row["family"] == "burst"]
    raw_count = sum(row["metrics"]["raw"]["eligibleCandidateCount"] for row in burst)
    added_burst = sum(row["metrics"]["raw+all"]["eligibleCandidateCount"] -
                      row["metrics"]["raw"]["eligibleCandidateCount"] for row in burst)
    checks = {"recoversAtLeastOneMissedPulse": recovered >= 1,
        "preservesEveryRawCandidate": all(row["allRawCandidatesPreserved"] for row in records),
        "addsAtMostOnePerMinuteInEachNoiseFamily": all(value <= 1 for value in noise.values()),
        "burstIncreaseAtMostTenPercent": added_burst <= .1 * raw_count}
    return {"passed": all(checks.values()), "checks": checks, "newlyRecoveredPulseCount": recovered,
        "addedBackgroundCandidatesPerMinute": noise, "baselineBurstCandidates": raw_count,
        "addedBurstCandidates": added_burst, "maximumAllowedAddedBurstCandidates": .1 * raw_count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evidence/signal-combination.json")
    args = parser.parse_args()
    registry_path = ROOT / "docs/evidence/signal-experiment-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entry = next(row for row in registry["experiments"] if row["id"] == "signed-windows-1")
    if entry["status"] != "reserved" or entry["tuningRuns"] != 0 or entry["heldOutSeeds"] != list(range(49001, 49021)):
        raise SystemExit("Reserved experiment has already run or changed. Do not tune/rerun on held-out seeds.")
    files = (Path(__file__), ROOT / "scripts/signal_combination.py", ROOT / "src/aegis/signal_review.py",
             ROOT / "src/aegis/evaluation.py", ROOT / entry["protocol"])
    entry.update(status="running", startedAtUtc=datetime.now(timezone.utc).isoformat(),
                 sourceSha256={str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in files})
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    records = []
    for family in FAMILIES:
        records.extend(evaluate(family, seed) for seed in entry["heldOutSeeds"])
        print(f"Evaluated {family}: 20 frozen cases", flush=True)
    by_key = {(row["family"], row["seed"]): row for row in records}
    for seed in entry["heldOutSeeds"]:
        left, right = by_key["ar09_pulse", seed], by_key["artifact_counterpart", seed]
        assert left["publicInputSha256"] == right["publicInputSha256"]
        assert left["rawCandidates"] == right["rawCandidates"] and left["signedCandidates"] == right["signedCandidates"]
    promotion = gate(records)
    result = {"generatedAtUtc": datetime.now(timezone.utc).isoformat(), "experiment": entry["id"],
        "scope": "Frozen offline synthetic development experiment; no equipment cause classification.",
        "tuningRuns": 0, "identicalObservationsRemainUnresolved": True,
        "promotionGate": promotion, "runtimePromoted": False, "sourceSha256": entry["sourceSha256"],
        "groupMeaning": "Navigation compression retaining every child, never a reduction in false alarms.",
        "records": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False,
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)) + "\n", encoding="utf-8")
    entry.update(status="completed", completedAtUtc=result["generatedAtUtc"],
        evidence=str(args.output.resolve().relative_to(ROOT)), promotionGatePassed=promotion["passed"], runtimePromoted=False)
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(promotion, indent=2), flush=True)


if __name__ == "__main__":
    main()
