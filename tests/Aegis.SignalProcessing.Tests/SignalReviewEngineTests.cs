using System.Text.Json;
using System.Text.Json.Nodes;
using Aegis.SignalProcessing;
using Xunit;

namespace Aegis.SignalProcessing.Tests;

public sealed class SignalReviewEngineTests
{
    private static readonly DateTimeOffset At = DateTimeOffset.Parse("2026-10-22T00:00:00Z");
    private const string Run = "70cd6dc0-1948-4d77-9b96-50a322a26fb1";
    private static JsonObject Frame(int tick, double error = 0, double rate = 100)
    {
        var keys = new[] { "setpointFlowLpm", "actualFlowLpm", "supplyPressureKpa", "valveCommandPct", "valveFeedbackPct", "motorCurrentAmp", "temperatureC", "cycleCount" };
        var units = new[] { "L/min", "L/min", "kPa", "%", "%", "A", "degC", "count" };
        var values = new[] { 10, 10 + error, 200, 50, 50, 2, 25, 1 };
        var rows = new JsonArray();
        for (var i = 0; i < keys.Length; i++) rows.Add(new JsonObject { ["signalKey"] = keys[i], ["unit"] = units[i], ["quality"] = "GOOD", ["value"] = values[i] });
        return new() { ["protocolVersion"] = "1.1", ["source"] = "SIMULATOR", ["deviceId"] = "flow-1", ["runId"] = Run,
            ["sequence"] = tick, ["observedAtUtc"] = At.AddSeconds(tick / rate).ToString("O"),
            ["deviceStateVersion"] = 1, ["modeId"] = "PRODUCTION", ["signals"] = rows };
    }
    private static void Send(SignalReviewEngine engine, JsonObject frame) => engine.AcceptRawJson(frame.ToJsonString());
    private static SignalReviewEngine Warm()
    {
        var engine = new SignalReviewEngine("flow-1");
        for (var i = 0; i < 100; i++) Send(engine, Frame(i));
        return engine;
    }
    private static JsonObject Row(JsonObject frame, int index) => frame["signals"]![index]!.AsObject();

    [Theory]
    [InlineData(100, 123, 10)]
    [InlineData(100, 199, 10)]
    [InlineData(10, 12, 1)]
    [InlineData(100, 123, 1)]
    public void RawPulsesRetainPeakAndExactSampleEvidence(int rate, int start, int length)
    {
        var engine = new SignalReviewEngine("flow-1");
        for (var i = 0; i < 3 * rate; i++)
        {
            var payload = Frame(i, i >= start && i < start + length ? -4 : 0, rate).ToJsonString();
            var original = payload;
            engine.AcceptRawJson(payload);
            Assert.Equal(original, payload);
        }
        var review = engine.Snapshot();
        var e = Assert.Single(review.Events);
        Assert.Equal("REVIEW_REQUIRED", review.State);
        Assert.Equal("UNRESOLVED", e.Disposition);
        Assert.Equal(-4, e.RawErrorMinLpm);
        Assert.Equal(4, e.PeakDeviationLpm);
        Assert.Equal(length, e.SampleCount);
        Assert.Equal(At.AddSeconds((double)start / rate), e.StartAtUtc);
        Assert.Equal("RETURNED_TO_CONTEXT", e.EndReason);
        Assert.Empty(e.RelatedSignalKeys);
    }

    [Fact]
    public void SustainedStepUsesFrozenBaselineThenReturnAndSecondPulse()
    {
        var engine = Warm();
        for (var i = 100; i < 600; i++) Send(engine, Frame(i, -3));
        var e = Assert.Single(engine.Snapshot().Events);
        Assert.Null(e.ClosedAtUtc);
        Assert.Equal(500, e.SampleCount);
        Assert.Equal(0, e.BaselineErrorLpm);
        Assert.Equal(-3, engine.Snapshot().TrendErrorLpm);
        Assert.Equal(0, engine.Snapshot().ContextSampleCount);
        for (var i = 600; i < 900; i++) Send(engine, Frame(i));
        Assert.Equal("RETURNED_TO_CONTEXT", Assert.Single(engine.Snapshot().Events).EndReason);
        Send(engine, Frame(900, -4));
        Assert.Equal(2, engine.Snapshot().EventCount);
        Assert.Equal(0, engine.Snapshot().Events[^1].BaselineErrorLpm);
    }

    [Theory]
    [InlineData("quality", "QUALITY")]
    [InlineData("range", "QUALITY")]
    [InlineData("missing", "QUALITY")]
    [InlineData("gap", "GAP")]
    [InlineData("time", "GAP")]
    [InlineData("mode", "STATE_CHANGE")]
    [InlineData("state", "STATE_CHANGE")]
    [InlineData("setpoint", "SETPOINT_CHANGE")]
    [InlineData("run", "SOURCE_UNAVAILABLE")]
    public void InterruptionsCloseWithoutClaimingRecovery(string kind, string reason)
    {
        var engine = Warm();
        Send(engine, Frame(100, -3));
        var next = Frame(101, -3);
        switch (kind)
        {
            case "quality": Row(next, 6)["quality"] = "BAD"; break;
            case "range": Row(next, 1)["value"] = -1; break;
            case "missing": next["signals"]!.AsArray().RemoveAt(6); break;
            case "gap": next["sequence"] = 103; break;
            case "time": next["observedAtUtc"] = At.AddSeconds(3).ToString("O"); break;
            case "mode": next["modeId"] = "SETUP"; break;
            case "state": next["deviceStateVersion"] = 2; break;
            case "setpoint": Row(next, 0)["value"] = 12; break;
            case "run": next["runId"] = Guid.NewGuid().ToString(); break;
        }
        Send(engine, next);
        var result = engine.Snapshot();
        Assert.Equal(reason, Assert.Single(result.Events).EndReason);
        Assert.Contains(result.State, new[] { "WARMING", "UNAVAILABLE" });
        Assert.Equal(reason, result.ContextReason);
        Assert.Equal(0, result.ContextSampleCount);
    }

    [Fact]
    public void UnavailableClearsValuesPreservesHistoryAndRewarms()
    {
        var engine = Warm();
        Send(engine, Frame(100, -3));
        engine.MarkUnavailable();
        var result = engine.Snapshot();
        Assert.Equal("UNAVAILABLE", result.State);
        Assert.Null(result.RawErrorLpm);
        Assert.Null(result.TrendErrorLpm);
        Assert.Null(result.ResidualMadLpm);
        Assert.Equal("SOURCE_UNAVAILABLE", Assert.Single(result.Events).EndReason);
        engine.MarkUnavailable();
        Send(engine, Frame(101));
        Assert.Equal("WARMING", engine.Snapshot().State);
    }

    [Fact]
    public void RelatedSensorsUseStrictFloorsAndRemainUnresolved()
    {
        var engine = Warm();
        var onset = Frame(100, -3);
        Row(onset, 2)["value"] = 210;
        Row(onset, 4)["value"] = 53;
        Send(engine, onset);
        Assert.Empty(engine.Snapshot().Events[0].RelatedSignalKeys);
        var next = Frame(101, -3);
        Row(next, 2)["value"] = 211;
        Row(next, 4)["value"] = 54;
        Row(next, 5)["value"] = 2.3;
        Send(engine, next);
        var e = engine.Snapshot().Events[0];
        Assert.Equal(new[] { "supplyPressureKpa", "motorCurrentAmp", "valveFeedbackPct" }, e.RelatedSignalKeys);
        Assert.Equal("UNRESOLVED", e.Disposition);
        Assert.Equal(10, e.MaxSampleIntervalMs);
    }

    [Fact]
    public void HistoryIsBoundedAndSnapshotCopiesAreIndependent()
    {
        var engine = Warm();
        for (var i = 100; i < 500; i++) Send(engine, Frame(i, i % 10 == 0 ? -3 : 0));
        var result = engine.Snapshot();
        Assert.Equal(40, result.EventCount);
        Assert.Equal(8, result.OmittedEventCount);
        Assert.Equal(32, result.Events.Count);
        Assert.Equal(9, result.Events[0].EventId);
        ((FlowExcursion[])result.Events)[0] = result.Events[0] with { Disposition = "MUTATED" };
        Assert.Equal("UNRESOLVED", engine.Snapshot().Events[0].Disposition);
        var options = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
        Assert.Contains("\"algorithmVersion\":\"flow-review-dotnet-dev-1\"", JsonSerializer.Serialize(result, options));
    }

    [Fact]
    public void DuplicateAndOlderIdsCannotAlterEvidence()
    {
        var engine = Warm();
        var before = JsonSerializer.Serialize(engine.Snapshot());
        Send(engine, Frame(99, -3));
        Send(engine, Frame(50, -4));
        Assert.Equal(before, JsonSerializer.Serialize(engine.Snapshot()));
    }

    [Fact]
    public void RegressedTimeCannotLowerAcceptedTimeWatermark()
    {
        var engine = Warm();
        Send(engine, Frame(100));
        for (var tick = 101; tick < 160; tick++)
        {
            var regressed = Frame(tick, tick >= 140 ? -4 : 0);
            regressed["observedAtUtc"] = At.AddMilliseconds(tick - 101).ToString("O");
            Send(engine, regressed);
        }
        Assert.Equal(0, engine.Snapshot().EventCount);
        Assert.Equal("UNAVAILABLE", engine.Snapshot().State);
        Send(engine, Frame(160));
        Assert.Equal("WARMING", engine.Snapshot().State);
        Assert.Equal(At.AddSeconds(1.6), engine.Snapshot().ObservedAtUtc);
    }

    [Fact]
    public void SparseAndTooFastSamplesCannotInventTimeContext()
    {
        var sparse = new SignalReviewEngine("flow-1");
        for (var i = 0; i < 10; i++) Send(sparse, Frame(i, rate: 1));
        Assert.Equal("WARMING", sparse.Snapshot().State);
        var fast = new SignalReviewEngine("flow-1");
        for (var i = 0; i < 1500; i++) Send(fast, Frame(i, rate: 10000));
        Assert.Equal("WARMING", fast.Snapshot().State);
        Assert.Equal(256, fast.Snapshot().ContextSampleCount);
        Assert.True(fast.Snapshot().ContextSpanMs < 200);
    }

    [Fact]
    public void SetpointAnchorAccumulatesGradualChange()
    {
        var engine = new SignalReviewEngine("flow-1");
        for (var i = 0; i < 300; i++)
        {
            var frame = Frame(i);
            Row(frame, 0)["value"] = 10 + i * .0001;
            Send(engine, frame);
        }
        Assert.True(engine.Snapshot().ContextSampleCount > 5);
        var step = Frame(300);
        Row(step, 0)["value"] = 10.5;
        Send(engine, step);
        Assert.Equal("SETPOINT_CHANGE", engine.Snapshot().ContextReason);
        Assert.Equal("WARMING", engine.Snapshot().State);
    }

    [Theory]
    [InlineData("device")]
    [InlineData("unit")]
    [InlineData("mode")]
    [InlineData("run")]
    [InlineData("time")]
    [InlineData("duplicate")]
    [InlineData("number")]
    [InlineData("quality")]
    [InlineData("extra")]
    [InlineData("sequence")]
    public void MalformedPayloadsResetAndThrowAtTrustBoundary(string kind)
    {
        var engine = Warm();
        Send(engine, Frame(100, -3));
        var bad = Frame(101);
        switch (kind)
        {
            case "device": bad["deviceId"] = "other"; break;
            case "unit": Row(bad, 1)["unit"] = "gallon"; break;
            case "mode": bad["modeId"] = "UNKNOWN"; break;
            case "run": bad["runId"] = "no-run"; break;
            case "time": bad["observedAtUtc"] = "2026-10-22T00:00:01"; break;
            case "duplicate": bad["signals"]!.AsArray().Add(Row(bad, 0).DeepClone()); break;
            case "number": Row(bad, 0)["value"] = "NaN"; break;
            case "quality": Row(bad, 0)["quality"] = "UNKNOWN"; break;
            case "extra": bad["unexpected"] = true; break;
            case "sequence": bad["sequence"] = 1.5; break;
        }
        Assert.Throws<JsonException>(() => Send(engine, bad));
        Assert.Equal("QUALITY", engine.Snapshot().Events[0].EndReason);
        Assert.Equal("UNAVAILABLE", engine.Snapshot().State);
    }

    [Fact]
    public void UpTo512UnknownNumericSignalsAreAcceptedButEntirePayloadIsValidated()
    {
        var engine = Warm();
        var frame = Frame(100);
        var rows = frame["signals"]!.AsArray();
        for (var i = 8; i < 512; i++) rows.Add(new JsonObject { ["signalKey"] = $"raw:{i}", ["value"] = i, ["unit"] = "raw", ["quality"] = "GOOD" });
        Send(engine, frame);
        Assert.Equal("TRACKING", engine.Snapshot().State);
        frame["sequence"] = 101;
        Row(frame, 511)["value"] = "not-a-number";
        Assert.Throws<JsonException>(() => Send(engine, frame));
        Assert.Null(engine.Snapshot().RawErrorLpm);
        Row(frame, 511)["value"] = 1;
        rows.Add(new JsonObject { ["signalKey"] = "overflow", ["value"] = 1, ["unit"] = "raw", ["quality"] = "GOOD" });
        Assert.Throws<JsonException>(() => Send(engine, frame));
    }
}
