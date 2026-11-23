"""Bounded offline input suitability for an owned, archived synthetic flow stream."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import sqlite3
from statistics import mean, median
import time

from aegis.archive_sessions import _format_time
from aegis.raw_history import MAX_PAYLOAD_BYTES, RawQueryError, _batch, _timestamp
from aegis.stored_records import QueryError, _Budget, _raw_schema, _reader, _regular


SAMPLE_LIMIT = 2048
MAX_TOTAL_PAYLOAD_BYTES = 8 * 1024 * 1024
MIN_VALID_PAIRS = 60
MIN_VALID_RATIO = 0.90
MIN_MODE_DURATION = timedelta(seconds=60)
EVAL_EMBARGO = timedelta(seconds=300)
MIN_FORECAST_SEGMENT = 20


@dataclass(frozen=True)
class _Sample:
    receipt: datetime
    observation: datetime
    mode: str
    setpoint: float | None
    actual: float | None

    @property
    def residual(self) -> float | None:
        if self.setpoint is None or self.actual is None:
            return None
        return self.actual - self.setpoint


def _flow_signals(original: dict) -> dict[str, float]:
    return {reading["signalKey"]: reading["value"] for reading in original["signals"]
            if reading["signalKey"] in ("setpointFlowLpm", "actualFlowLpm")
            and reading["quality"] == "GOOD" and reading["unit"] == "L/min" and reading["value"] is not None}


def _read_samples(source: dict, device_id: str) -> tuple[list[_Sample], bool]:
    budget = _Budget()
    budget.deadline = min(budget.deadline, time.monotonic() + 3)
    samples = []
    total_payload = 0
    try:
        source = dict(source, physical=_regular(source["path"]))
        with _reader(source, budget) as connection:
            _raw_schema(connection)
            cursor = connection.execute("""SELECT device_id,run_id,sequence,observed_at_utc,received_at_utc,
                    equipment_id,module_id,CASE WHEN length(CAST(payload_json AS BLOB)) <= ? THEN payload_json END
                FROM raw_batches NOT INDEXED WHERE device_id=? ORDER BY rowid DESC LIMIT ?""",
                (MAX_PAYLOAD_BYTES, device_id, SAMPLE_LIMIT + 1))
            for row in cursor:
                budget.check()
                if len(samples) == SAMPLE_LIMIT:
                    return samples, True
                if row[-1] is None:
                    raise QueryError("QUERY_LIMIT")
                total_payload += len(row[-1].encode("utf-8"))
                if total_payload > MAX_TOTAL_PAYLOAD_BYTES:
                    raise QueryError("QUERY_LIMIT")
                observed, original, receipt, _, _ = _batch(row, device_id, row[1])
                values = _flow_signals(original)
                samples.append(_Sample(_timestamp(receipt), observed, original["modeId"],
                                       values.get("setpointFlowLpm"), values.get("actualFlowLpm")))
    except (RawQueryError, ValueError) as error:
        raise QueryError("QUERY_LIMIT" if isinstance(error, RawQueryError) and error.status == 413
                         else "SOURCE_INVALID") from None
    except sqlite3.Error:
        raise QueryError("QUERY_LIMIT" if budget.exhausted else "SOURCE_UNAVAILABLE") from None
    return samples, False


def _forecast_comparison(mode: str, pairs: list[tuple[_Sample, float]]) -> dict | None:
    # Evaluate a fixed setpoint and a training-only baseline bias on the same raw
    # targets. Labels are unavailable; this is a forecast-error check only.
    ordered = sorted(pairs, key=lambda item: item[0].receipt)
    if len(ordered) < 2 * MIN_FORECAST_SEGMENT:
        return None
    cutoff = ordered[3 * len(ordered) // 4][0].receipt
    train = [residual for sample, residual in ordered if sample.receipt < cutoff - EVAL_EMBARGO]
    test = [residual for sample, residual in ordered if sample.receipt >= cutoff]
    if min(len(train), len(test)) < MIN_FORECAST_SEGMENT:
        return None
    center = median(train)
    return {"modeId": mode, "trainRows": len(train), "testRows": len(test),
            "setpointMaeLpm": mean(abs(value) for value in test),
            "biasAdjustedMaeLpm": mean(abs(value - center) for value in test)}


def _candidate_reason(stable: list[_Sample], valid: list[tuple[_Sample, float]]) -> str:
    if not stable:
        return "STABLE_MODE_REQUIRED"
    if len(valid) < MIN_VALID_PAIRS:
        return "TOO_FEW_VALID_PAIRS"
    if len(valid) / len(stable) < MIN_VALID_RATIO:
        return "VALID_PAIR_RATIO_LOW"
    observed = [sample.observation for sample, _residual in valid]
    if max(observed) - min(observed) < MIN_MODE_DURATION:
        return "INSUFFICIENT_MODE_DURATION"
    return "SUPPORTED_INPUT"


def analyze_archived_flow(session_id: str, device_id: str, source: dict) -> dict:
    samples, truncated = _read_samples(source, device_id)
    modes: dict[str, list[_Sample]] = {}
    valid: dict[str, list[tuple[_Sample, float]]] = {}
    for sample in samples:
        modes.setdefault(sample.mode, []).append(sample)
        residual = sample.residual
        if residual is not None:
            valid.setdefault(sample.mode, []).append((sample, residual))

    summaries, comparisons = [], []
    for mode, mode_samples in sorted(modes.items()):
        pairs = valid.get(mode, [])
        residuals = [value for _, value in pairs]
        center = median(residuals) if residuals else None
        summaries.append({"modeId": mode, "samples": len(mode_samples), "validPairs": len(pairs),
                          "medianResidualLpm": center, "medianAbsoluteDeviationLpm": (
                              median(abs(value - center) for value in residuals) if center is not None else None)})
        forecast = _forecast_comparison(mode, pairs)
        if forecast is not None:
            comparisons.append(forecast)
    valid_pairs = sum(len(group) for group in valid.values())
    reason = _candidate_reason(modes.get("PRODUCTION", []), valid.get("PRODUCTION", []))
    supported = reason == "SUPPORTED_INPUT"
    receipts = [sample.receipt for sample in samples]
    return {
        "schemaVersion": "flow-analysis-1", "sessionId": session_id, "deviceId": device_id,
        "source": "ARCHIVED_SYNTHETIC", "sampleLimit": SAMPLE_LIMIT, "scannedRows": len(samples),
        "validPairs": valid_pairs, "invalidPairs": len(samples) - valid_pairs,
        "zeroFlowRows": sum(sample.actual == 0 for sample in samples), "truncated": truncated,
        "firstReceivedAtUtc": _format_time(min(receipts)) if receipts else None,
        "lastReceivedAtUtc": _format_time(max(receipts)) if receipts else None,
        "labelEvidence": "NOT_PROVIDED", "modeSummaries": summaries,
        "candidates": [{"modelId": model_id, "status": "INPUT_SUFFICIENT" if supported else "INSUFFICIENT_INPUT",
                        "reasonCode": reason}
                       for model_id in ("rule-ewma-flow", "research-cusum-flow")],
        "recommendation": {
            "status": "RECOMMENDATION_ONLY" if supported else "INSUFFICIENT_INPUT",
            "modelId": "rule-ewma-flow" if supported else None,
            "reasonCode": "EXISTING_RULE_HAS_COMPATIBLE_INPUT" if supported else "INSUFFICIENT_FLOW_EVIDENCE",
        },
        "forecastComparison": comparisons,
    }
