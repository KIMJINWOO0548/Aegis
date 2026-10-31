namespace Aegis.SignalProcessing;

public sealed record FlowExcursion
{
    public long EventId { get; init; }
    public string Disposition { get; init; } = "UNRESOLVED";
    public required string StartObservationId { get; init; }
    public required string LastObservationId { get; init; }
    public DateTimeOffset StartAtUtc { get; init; }
    public DateTimeOffset LastAtUtc { get; init; }
    public DateTimeOffset? ClosedAtUtc { get; init; }
    public string? EndReason { get; init; }
    public long SampleCount { get; init; }
    public double RawErrorMinLpm { get; init; }
    public double RawErrorMaxLpm { get; init; }
    public double PeakDeviationLpm { get; init; }
    public double BaselineErrorLpm { get; init; }
    public double ThresholdLpm { get; init; }
    public double MaxSampleIntervalMs { get; init; }
    public IReadOnlyList<string> RelatedSignalKeys { get; init; } = [];
}

public sealed record FlowReviewSnapshot
{
    public required string DeviceId { get; init; }
    public string AlgorithmVersion { get; init; } = "flow-review-dotnet-dev-1";
    public string State { get; init; } = "WARMING";
    public DateTimeOffset? ObservedAtUtc { get; init; }
    public double? RawErrorLpm { get; init; }
    public double? TrendErrorLpm { get; init; }
    public double? ResidualMadLpm { get; init; }
    public int ContextSampleCount { get; init; }
    public double ContextSpanMs { get; init; }
    public string? ContextReason { get; init; } = "STARTUP";
    public long EventCount { get; init; }
    public long OmittedEventCount { get; init; }
    public IReadOnlyList<FlowExcursion> Events { get; init; } = [];
}
