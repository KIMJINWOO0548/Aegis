using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Aegis.SignalProcessing;

/// <summary>Descriptive raw flow evidence, without automatic cause classification.</summary>
public sealed class SignalReviewEngine
{
    private static readonly (string Key, string Unit, double Min, double Max)[] Signals =
    [
        ("setpointFlowLpm", "L/min", 0, 100), ("actualFlowLpm", "L/min", 0, 100),
        ("supplyPressureKpa", "kPa", 0, 2000), ("valveCommandPct", "%", 0, 100),
        ("valveFeedbackPct", "%", 0, 100), ("motorCurrentAmp", "A", 0, 100),
        ("temperatureC", "degC", -40, 200), ("cycleCount", "count", 0, double.MaxValue)
    ];
    private static readonly (string Key, double Floor)[] Related =
        [("supplyPressureKpa", 10), ("motorCurrentAmp", .2), ("valveFeedbackPct", 3)];
    private readonly object _gate = new();
    private readonly string _deviceId;
    private readonly Queue<Point> _history = new();
    private readonly Queue<double> _trend = new();
    private readonly List<FlowExcursion> _events = [];
    private readonly Queue<Guid> _retiredRuns = new();
    private Dictionary<string, (double Center, double Threshold)>? _reference;
    private FlowReviewSnapshot _result;
    private Frame? _last;
    private DateTimeOffset? _lastAt;
    private double? _setpoint;
    private long _count;
    private bool _active;

    public SignalReviewEngine(string deviceId)
    {
        if (deviceId is null || !Regex.IsMatch(deviceId, @"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\z"))
            throw new ArgumentException("Invalid device identity.", nameof(deviceId));
        _deviceId = deviceId;
        _result = new() { DeviceId = deviceId };
    }

    /// <summary>Validates a complete protocol 1.1 archive payload before applying it.</summary>
    /// <exception cref="JsonException">The payload is structurally invalid; analysis is reset.</exception>
    public void AcceptRawJson(string payloadJson)
    {
        lock (_gate)
        {
            Frame frame;
            try { frame = Parse(payloadJson); }
            catch (Exception error) when (error is JsonException or InvalidOperationException or FormatException or OverflowException or ArgumentException)
            {
                Reset("QUALITY", _lastAt);
                throw new JsonException("Invalid raw snapshot.", error);
            }
            if (_last is not null)
            {
                if (frame.Run == _last.Run && frame.Sequence <= _last.Sequence || _retiredRuns.Contains(frame.Run))
                    return;
                if (frame.Run != _last.Run)
                {
                    _retiredRuns.Enqueue(_last.Run);
                    if (_retiredRuns.Count > 32) _retiredRuns.Dequeue();
                    Reset("SOURCE_UNAVAILABLE", _lastAt);
                }
                else if (frame.At <= _last.At)
                {
                    Reset("GAP", _lastAt);
                    _last = _last with { Sequence = frame.Sequence };
                    return;
                }
                else if (frame.Sequence - _last.Sequence != 1 || (frame.At - _last.At).TotalSeconds > 1.5)
                    Reset("GAP", frame.At);
                if (frame.Run == _last.Run && (frame.Mode != _last.Mode || frame.State != _last.State))
                    Reset("STATE_CHANGE", frame.At);
            }
            _last = frame;
            if (Signals.Any(signal => !frame.Values.TryGetValue(signal.Key, out var value) || value is null ||
                    !frame.Good.Contains(signal.Key) || value < signal.Min || value > signal.Max ||
                    signal.Key == "cycleCount" && value != Math.Truncate(value.Value)))
            {
                Reset("QUALITY", frame.At);
                return;
            }
            Accept(frame);
        }
    }

    public FlowReviewSnapshot Snapshot()
    {
        lock (_gate)
            return _result with { EventCount = _count, OmittedEventCount = _count - _events.Count,
                Events = _events.Select(e => e with { RelatedSignalKeys = e.RelatedSignalKeys.ToArray() }).ToArray() };
    }

    public void MarkUnavailable()
    {
        lock (_gate) Reset("SOURCE_UNAVAILABLE", _lastAt);
    }

    private void Close(DateTimeOffset? at, string reason)
    {
        if (!_active) return;
        _events[^1] = _events[^1] with { ClosedAtUtc = at ?? _events[^1].LastAtUtc, EndReason = reason };
        _active = false;
        _reference = null;
    }

    private void Reset(string reason, DateTimeOffset? at)
    {
        Close(at, reason);
        _history.Clear();
        _trend.Clear();
        _lastAt = null;
        _setpoint = null;
        _result = new() { DeviceId = _deviceId, State = "UNAVAILABLE", ObservedAtUtc = at, ContextReason = reason };
    }

    private void Accept(Frame frame)
    {
        double Value(string key) => frame.Values[key]!.Value;
        var at = frame.At;
        var setpoint = Value("setpointFlowLpm");
        var error = Value("actualFlowLpm") - setpoint;
        if (_setpoint is not null && Math.Abs(setpoint - _setpoint.Value) >= .5) Reset("SETPOINT_CHANGE", at);
        _setpoint ??= setpoint;
        while (_history.TryPeek(out var oldest) && (at - oldest.At).TotalSeconds > 1) _history.Dequeue();
        var span = _history.Count > 1 ? (_history.Last().At - _history.Peek().At).TotalMilliseconds : 0;
        var ready = _history.Count >= 5 && span >= 200 - 1e-6;
        var center = _history.Count > 0 ? Median(_history.Select(p => p.Error)) : error;
        var mad = _history.Count > 0 ? Median(_history.Select(p => Math.Abs(p.Error - center))) : 0;
        var sensors = Related.ToDictionary(r => r.Key, r => Value(r.Key));
        var observationId = $"{frame.Run:D}:{frame.Sequence}:actualFlowLpm";
        if (!_active && ready && Math.Abs(error - center) > Math.Max(.5, 6 * mad))
        {
            _events.Add(new() { EventId = ++_count, StartObservationId = observationId, LastObservationId = observationId,
                StartAtUtc = at, LastAtUtc = at, SampleCount = 1, RawErrorMinLpm = error, RawErrorMaxLpm = error,
                PeakDeviationLpm = Math.Abs(error - center), BaselineErrorLpm = center, ThresholdLpm = Math.Max(.5, 6 * mad) });
            if (_events.Count > 32) _events.RemoveAt(0);
            _active = true;
            _reference = Related.ToDictionary(r => r.Key, r =>
            {
                var baseline = Median(_history.Select(p => p.Sensors[r.Key]));
                return (baseline, Math.Max(r.Floor, 6 * Median(_history.Select(p => Math.Abs(p.Sensors[r.Key] - baseline)))));
            });
        }
        if (_active)
        {
            var e = _events[^1];
            if (Math.Abs(error - e.BaselineErrorLpm) <= e.ThresholdLpm) Close(at, "RETURNED_TO_CONTEXT");
            else
            {
                var related = e.RelatedSignalKeys.ToList();
                foreach (var (key, reference) in _reference!)
                    if (Math.Abs(sensors[key] - reference.Center) > reference.Threshold && !related.Contains(key)) related.Add(key);
                _events[^1] = e with { SampleCount = e.SampleCount + (at > e.LastAtUtc ? 1 : 0),
                    MaxSampleIntervalMs = Math.Max(e.MaxSampleIntervalMs, (at - e.LastAtUtc).TotalMilliseconds),
                    LastAtUtc = at, LastObservationId = observationId, RawErrorMinLpm = Math.Min(e.RawErrorMinLpm, error),
                    RawErrorMaxLpm = Math.Max(e.RawErrorMaxLpm, error), PeakDeviationLpm = Math.Max(e.PeakDeviationLpm, Math.Abs(error - e.BaselineErrorLpm)),
                    RelatedSignalKeys = related.ToArray() };
            }
        }
        _trend.Enqueue(error);
        if (_trend.Count > 5) _trend.Dequeue();
        _result = new() { DeviceId = _deviceId, State = _active ? "REVIEW_REQUIRED" : ready ? _events.Count > 0 ? "REVIEW_REQUIRED" : "TRACKING" : "WARMING",
            ObservedAtUtc = at, RawErrorLpm = error, TrendErrorLpm = Median(_trend), ResidualMadLpm = ready ? mad : null,
            ContextSampleCount = _history.Count, ContextSpanMs = Math.Round(span, 6), ContextReason = ready ? null : _result.ContextReason };
        // Unresolved event samples must never become the next event's baseline.
        if (!_active)
        {
            _history.Enqueue(new(at, error, sensors));
            if (_history.Count > 256) _history.Dequeue();
        }
        _lastAt = at;
    }

    private static double Median(IEnumerable<double> values)
    {
        var sorted = values.Order().ToArray();
        return sorted.Length % 2 == 1 ? sorted[sorted.Length / 2] : (sorted[sorted.Length / 2 - 1] + sorted[sorted.Length / 2]) / 2;
    }

    private Frame Parse(string payload)
    {
        if (payload is null || Encoding.UTF8.GetByteCount(payload) > 1048576) throw new JsonException("Payload limit.");
        using var document = JsonDocument.Parse(payload, new() { MaxDepth = 8 });
        var root = document.RootElement;
        Fields(root, "protocolVersion", "source", "deviceId", "runId", "sequence", "observedAtUtc", "deviceStateVersion", "modeId", "signals");
        if (Text(root, "protocolVersion") != "1.1" || Text(root, "source") != "SIMULATOR" || Text(root, "deviceId") != _deviceId ||
            !Guid.TryParseExact(Text(root, "runId"), "D", out var run) || !root.GetProperty("sequence").TryGetInt64(out var sequence) || sequence < 0 ||
            !root.GetProperty("deviceStateVersion").TryGetInt32(out var state) || state < 0) throw new JsonException("Invalid identity.");
        var timestamp = Text(root, "observedAtUtc");
        if (!Regex.IsMatch(timestamp, @"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})\z") ||
            !DateTimeOffset.TryParse(timestamp, CultureInfo.InvariantCulture, DateTimeStyles.None, out var at)) throw new JsonException("Invalid timestamp.");
        var mode = Text(root, "modeId");
        if (mode is not ("PRODUCTION" or "SETUP" or "MAINTENANCE")) throw new JsonException("Invalid mode.");
        var rows = root.GetProperty("signals");
        if (rows.ValueKind != JsonValueKind.Array || rows.GetArrayLength() is < 1 or > 512) throw new JsonException("Signal limit.");
        var values = new Dictionary<string, double?>();
        var good = new HashSet<string>();
        foreach (var row in rows.EnumerateArray())
        {
            Fields(row, "signalKey", "value", "unit", "quality");
            var key = Text(row, "signalKey");
            var unit = Text(row, "unit");
            var quality = Text(row, "quality");
            if (!Regex.IsMatch(key, @"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z") || unit.Length is < 1 or > 32 || unit.Any(c => c < 32 || c == 127) ||
                quality is not ("GOOD" or "BAD" or "STALE" or "MISSING")) throw new JsonException("Invalid signal metadata.");
            var raw = row.GetProperty("value");
            double? value = raw.ValueKind == JsonValueKind.Null ? null : raw.GetDouble();
            if (value is not null && !double.IsFinite(value.Value) || quality == "GOOD" && value is null || !values.TryAdd(key, value)) throw new JsonException("Invalid signal value.");
            var known = Signals.FirstOrDefault(s => s.Key == key);
            if (known.Key is not null && known.Unit != unit) throw new JsonException("Invalid signal unit.");
            if (quality == "GOOD") good.Add(key);
        }
        return new(run, sequence, at.ToUniversalTime(), state, mode, values, good);
    }

    private static string Text(JsonElement value, string name) => value.GetProperty(name).GetString() ?? throw new JsonException("Expected string.");
    private static void Fields(JsonElement value, params string[] fields)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new JsonException("Expected object.");
        var names = value.EnumerateObject().Select(p => p.Name).ToArray();
        if (names.Length != fields.Length || names.Distinct().Count() != names.Length || fields.Except(names).Any()) throw new JsonException("Unexpected fields.");
    }
    private sealed record Point(DateTimeOffset At, double Error, Dictionary<string, double> Sensors);
    private sealed record Frame(Guid Run, long Sequence, DateTimeOffset At, int State, string Mode, Dictionary<string, double?> Values, HashSet<string> Good);
}
