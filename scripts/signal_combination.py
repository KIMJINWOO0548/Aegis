"""Offline-only signed evidence experiment; never imported by the application."""

from collections import deque
from datetime import timedelta
from math import isfinite, sqrt
from statistics import mean, median

from aegis.evaluation import RANGES


WINDOWS_MS = (50, 100, 200)


class SignedWindowEvidence:
    def __init__(self, device_id):
        self.device_id = device_id
        self._last_at = None
        self._serial = 0
        self._reset()

    def _reset(self):
        self._history = deque(maxlen=256)
        self._points = deque(maxlen=256)
        self._flags = {window: 0 for window in WINDOWS_MS}
        self._state = self._anchor = None

    def accept(self, rows, *, continuous=True):
        if not rows:
            self._reset()
            return []
        at = max(row.observedAtUtc for row in rows)
        if self._last_at is not None and at <= self._last_at:
            return []
        gap = self._last_at is not None and (at - self._last_at).total_seconds() > 1.5
        self._last_at = at
        by_key = {str(row.signalKey): row for row in rows}
        state = (rows[0].modeId, rows[0].deviceStateVersion)
        if (len(rows) != len(RANGES) or set(by_key) != set(RANGES)
                or any(row.deviceId != self.device_id or row.observedAtUtc != at
                       or (row.modeId, row.deviceStateVersion) != state for row in rows)
                or any(row.quality != "GOOD" or row.value is None or not isfinite(row.value)
                       or not RANGES[key][0] <= row.value <= RANGES[key][1]
                       or (key == "cycleCount" and not row.value.is_integer()) for key, row in by_key.items())):
            self._reset()
            return []
        setpoint = by_key["setpointFlowLpm"].value
        if (gap or not continuous or (self._state is not None and self._state != state)
                or (self._anchor is not None and abs(setpoint - self._anchor) >= .5)):
            self._reset()
        self._state = state
        if self._anchor is None:
            self._anchor = setpoint
        while self._history and at - self._history[0][0] > timedelta(seconds=1):
            self._history.popleft()
        ready = len(self._history) >= 5 and self._history[-1][0] - self._history[0][0] >= timedelta(milliseconds=200)
        error = by_key["actualFlowLpm"].value - setpoint
        baseline = median(point[1] for point in self._history) if self._history else error
        mad = median(abs(point[1] - baseline) for point in self._history) if self._history else 0.0
        # Each point carries the baseline available BEFORE it entered any candidate window.
        self._points.append((at, error, baseline, mad, ready, by_key["actualFlowLpm"].observationId))
        self._history.append((at, error))
        candidates = []
        for window in WINDOWS_MS:
            points = [point for point in self._points if at - point[0] <= timedelta(milliseconds=window)]
            intervals = [(right[0] - left[0]).total_seconds() * 1000 for left, right in zip(points, points[1:])]
            span = (at - points[0][0]).total_seconds() * 1000
            if len(points) < 3 or not points[0][4] or span < .8 * window or max(intervals, default=0) > window / 2:
                self._flags[window] = 0
                continue
            base, scale = points[0][2], max(.1, 1.4826 * points[0][3])
            average = mean(point[1] for point in points) - base
            threshold = max(.5, 4 * scale / sqrt(len(points)))
            sign = (1 if average > 0 else -1) if abs(average) > threshold else 0
            if sign and sign != self._flags[window]:
                self._serial += 1
                candidates.append({"id": f"signed-{self._serial}", "channel": f"SIGNED_{window}MS",
                    "disposition": "UNRESOLVED", "emittedAtUtc": at, "windowStartAtUtc": points[0][0],
                    "sign": sign, "sampleCount": len(points), "spanMs": span,
                    "maxSampleIntervalMs": max(intervals), "baselineErrorLpm": base,
                    "residualMadLpm": points[0][3], "thresholdLpm": threshold, "meanShiftLpm": average,
                    "rawErrorMinLpm": min(point[1] for point in points),
                    "rawErrorMaxLpm": max(point[1] for point in points),
                    "observationIds": [point[5] for point in points]})
            self._flags[window] = sign
        return candidates


def group_evidence(candidates):
    """Navigation only: never delete candidates or use group count as false-alarm rate."""
    groups = []
    for row in sorted(candidates, key=lambda item: (item["emittedAtUtc"], item["id"])):
        at = row["emittedAtUtc"]
        if (not groups or at - groups[-1]["lastAtUtc"] > timedelta(milliseconds=100)
                or at - groups[-1]["startAtUtc"] > timedelta(seconds=1)):
            groups.append({"startAtUtc": at, "lastAtUtc": at, "childIds": []})
        groups[-1]["lastAtUtc"] = at
        groups[-1]["childIds"].append(row["id"])
    return groups
