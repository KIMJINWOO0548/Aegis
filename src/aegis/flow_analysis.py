"""Bounded offline input suitability for an owned, archived synthetic flow stream."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import sqlite3
from statistics import mean, median
import time

from aegis.archive_sessions import _format_time
from aegis.evaluation import RANGES
from aegis.flow_models import fit_robust_flow_ridge
from aegis.raw_history import MAX_PAYLOAD_BYTES, RawQueryError, _batch, _timestamp
from aegis.stored_records import QueryError, _Budget, _raw_schema, _reader, _regular


SAMPLE_LIMIT = 12_000
MAX_TOTAL_PAYLOAD_BYTES = 12 * 1024 * 1024
FORECAST_LOOKBACK = timedelta(minutes=20)
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
    run: str
    setpoint: float | None
    actual: float | None
    valve_command: float | None

    @property
    def residual(self) -> float | None:
        if self.setpoint is None or self.actual is None:
            return None
        return self.actual - self.setpoint

    def command(self, key: str) -> float | None:
        value = self.setpoint if key == "setpointFlowLpm" else self.valve_command
        return value if value is not None and RANGES[key][0] <= value <= RANGES[key][1] else None


def _flow_signals(original: dict) -> dict[str, float]:
    units = {"setpointFlowLpm": "L/min", "actualFlowLpm": "L/min", "valveCommandPct": "%"}
    return {reading["signalKey"]: reading["value"] for reading in original["signals"]
            if reading["signalKey"] in units and reading["quality"] == "GOOD"
            and reading["unit"] == units[reading["signalKey"]] and reading["value"] is not None}


def _read_samples(source: dict, device_id: str) -> tuple[list[_Sample], bool]:
    budget = _Budget()
    budget.deadline = min(budget.deadline, time.monotonic() + 3)
    samples = []
    total_payload = 0
    latest_receipt = None
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
                received = _timestamp(row[4])
                latest_receipt = received if latest_receipt is None else max(received, latest_receipt)
                if received < latest_receipt - FORECAST_LOOKBACK:
                    return samples, True
                if row[-1] is None:
                    raise QueryError("QUERY_LIMIT")
                total_payload += len(row[-1].encode("utf-8"))
                if total_payload > MAX_TOTAL_PAYLOAD_BYTES:
                    raise QueryError("QUERY_LIMIT")
                observed, original, _, _, _ = _batch(row, device_id, row[1])
                values = _flow_signals(original)
                samples.append(_Sample(received, observed, original["modeId"], row[1],
                                       values.get("setpointFlowLpm"), values.get("actualFlowLpm"),
                                       values.get("valveCommandPct")))
    except (RawQueryError, ValueError) as error:
        raise QueryError("QUERY_LIMIT" if isinstance(error, RawQueryError) and error.status == 413
                         else "SOURCE_INVALID") from None
    except sqlite3.Error:
        raise QueryError("QUERY_LIMIT" if budget.exhausted else "SOURCE_UNAVAILABLE") from None
    return samples, False


def _forecast_samples(samples: list[_Sample]) -> dict[tuple[str, str], list[tuple[_Sample, float]]]:
    """Use first-received known data per second; runs and modes stay independent."""
    selected = set()
    by_run_mode = {}
    for sample in sorted(samples, key=lambda row: (row.receipt, row.observation)):
        valid_setpoint = sample.command("setpointFlowLpm") is not None
        if (sample.observation > sample.receipt or not valid_setpoint or sample.actual is None
                or not RANGES["actualFlowLpm"][0] <= sample.actual <= RANGES["actualFlowLpm"][1]):
            continue
        window = (sample.run, sample.mode, sample.observation.replace(microsecond=0))
        if window not in selected:
            selected.add(window)
            by_run_mode.setdefault(window[:2], []).append((sample, sample.residual))
    return by_run_mode


def _holdout(pairs):
    if len(pairs) < 2 * MIN_FORECAST_SEGMENT:
        return None
    cutoff = pairs[3 * len(pairs) // 4][0].receipt
    train = [pair for pair in pairs if pair[0].receipt < cutoff - EVAL_EMBARGO]
    test = [pair for pair in pairs if pair[0].receipt >= cutoff and pair[0].observation >= cutoff]
    if min(len(train), len(test)) < MIN_FORECAST_SEGMENT:
        return None
    return train, test


def _forecast_comparison(mode, train, test):
    center = median(value for _sample, value in train)
    return {"modeId": mode, "trainRows": len(train), "testRows": len(test),
            "setpointMaeLpm": mean(abs(value) for _sample, value in test),
            "biasAdjustedMaeLpm": mean(abs(value - center) for _sample, value in test)}


def _variable_commands(train):
    features = []
    for name in ("setpointFlowLpm", "valveCommandPct"):
        available = [value for sample, _residual in train if (value := sample.command(name)) is not None]
        if len(available) >= MIN_VALID_RATIO * len(train) and max(available) - min(available) > 1e-6:
            features.append(name)
    return features


def _compare_learned_forecasts(run, mode, train, test):
    features = _variable_commands(train)
    ridge_mae = None
    if features:
        matched_train = [pair for pair in train if all(pair[0].command(key) is not None for key in features)]
        matched_test = [pair for pair in test if all(pair[0].command(key) is not None for key in features)]
        if (len(matched_train) >= max(MIN_FORECAST_SEGMENT, MIN_VALID_RATIO * len(train))
                and len(matched_test) >= max(MIN_FORECAST_SEGMENT, MIN_VALID_RATIO * len(test))):
            train, test = matched_train, matched_test
            inputs = [tuple(sample.command(key) for key in features) for sample, _value in train]
            model = fit_robust_flow_ridge(inputs, [value for _sample, value in train])
            ridge_mae = mean(abs(value - model.predict_residual(tuple(sample.command(key) for key in features)))
                             for sample, value in test)
        else:
            features = []
    baseline = _forecast_comparison(mode, train, test)
    reference = baseline["setpointMaeLpm"]
    estimates = [("setpoint-flow-baseline", reference), ("learned-flow-bias", baseline["biasAdjustedMaeLpm"])]
    if ridge_mae is not None:
        estimates.append(("learned-flow-ridge", ridge_mae))
    best_mae = min(error for _identifier, error in estimates)
    tolerance = max(0.01, 0.01 * best_mae)
    preferred, preferred_mae = next((identifier, error) for identifier, error in estimates
                                    if error <= best_mae + tolerance)
    return {
        "modeId": mode, "runId": run, "trainRows": len(train), "testRows": len(test),
        "trainFromUtc": _format_time(train[0][0].receipt), "trainToUtc": _format_time(train[-1][0].receipt),
        "testFromUtc": _format_time(test[0][0].receipt), "testToUtc": _format_time(test[-1][0].receipt),
        "featureKeys": features,
        "models": [{"modelId": identifier, "maeLpm": error, "improvementLpm": reference - error,
                    "improvementPercent": 100 * (reference - error) / reference if reference > 0 else None}
                   for identifier, error in estimates],
        "preferredModelId": preferred,
        "reasonCode": "LOWEST_HELD_OUT_MAE" if preferred_mae == best_mae else "SIMPLEST_WITHIN_TOLERANCE",
    }


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

    summaries = []
    for mode, mode_samples in sorted(modes.items()):
        pairs = valid.get(mode, [])
        residuals = [value for _, value in pairs]
        center = median(residuals) if residuals else None
        summaries.append({"modeId": mode, "samples": len(mode_samples), "validPairs": len(pairs),
                          "medianResidualLpm": center, "medianAbsoluteDeviationLpm": (
                              median(abs(value - center) for value in residuals) if center is not None else None)})
    forecast_samples = _forecast_samples(samples)
    evaluations = []
    latest_forecasts = {}
    for (run, mode), pairs in forecast_samples.items():
        segments = _holdout(pairs)
        if segments is None:
            continue
        train, test = segments
        evaluation = _compare_learned_forecasts(run, mode, train, test)
        evaluations.append(evaluation)
        last_test_receipt = test[-1][0].receipt
        if mode not in latest_forecasts or last_test_receipt > latest_forecasts[mode][0]:
            latest_forecasts[mode] = (last_test_receipt, _forecast_comparison(mode, train, test))
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
        "forecastComparison": [value[1] for _, value in sorted(latest_forecasts.items())],
        "learnedForecastReport": {
            "schemaVersion": "learned-flow-forecast-1", "scope": "ARCHIVE_FORECAST_ERROR_ONLY",
            "labelEvidence": "NOT_PROVIDED", "embargoSeconds": int(EVAL_EMBARGO.total_seconds()),
            "status": "HOLDOUT_EVALUATED" if evaluations else "NO_COMPARABLE_HOLDOUT",
            "sampling": {"intervalSeconds": 1, "lookbackSeconds": int(FORECAST_LOOKBACK.total_seconds()),
                         "rawRowsExamined": len(samples), "selectedRows": sum(map(len, forecast_samples.values()))},
            "evaluations": sorted(evaluations, key=lambda row: (row["testToUtc"], row["runId"], row["modeId"]), reverse=True),
        },
    }
