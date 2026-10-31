using System.Text.Json;
using System.Text.RegularExpressions;

namespace Aegis.Contracts;

public enum ConnectionState { CONNECTING, CONNECTED, DISCONNECTED, STALE, REJECTED }

public sealed class SignalIssue : ContractModel
{
    public required SignalKey SignalKey { get; init; }
    public required string ReasonCode { get; init; }
    public required string Message { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (!Regex.IsMatch(ReasonCode, @"\A[A-Z][A-Z0-9_]{0,63}\z") ||
            string.IsNullOrWhiteSpace(Message) || Message.Length > 512 || Message.Any(char.IsControl))
            throw new JsonException("Invalid signal issue.");
    }
}

public sealed class DeviceDiagnostics : ContractModel
{
    public required string DeviceId { get; init; }
    public required bool Enabled { get; init; }
    public required string? ModelVersion { get; init; }
    public required ObservationEvaluation Evaluation { get; init; }
    public required int SampleCount { get; init; }
    public required double? FlowErrorEwmaLpm { get; init; }
    public required List<SignalIssue> Issues { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (string.IsNullOrWhiteSpace(DeviceId) || SampleCount is < 0 or > 60 || FlowErrorEwmaLpm < 0 ||
            Issues.Count > 8 || Issues.Select(issue => issue.SignalKey).Distinct().Count() != Issues.Count ||
            (Enabled && ModelVersion != "ewma-flow-dev-1") ||
            (!Enabled && (ModelVersion is not null || SampleCount != 0 || FlowErrorEwmaLpm is not null || Issues.Count != 0 ||
                          Evaluation is not (ObservationEvaluation.INSUFFICIENT_DATA or ObservationEvaluation.UNAVAILABLE))))
            throw new JsonException("Invalid development evaluation diagnostics.");
    }
}

public sealed class ApiStatus : ContractModel
{
    public required string Status { get; init; }
    public required string SchemaVersion { get; init; }
    public required string Stage { get; init; }
    public required bool Mock { get; init; }
    public string? Mode { get; init; }
}

public sealed class DeviceDescriptor : ContractModel
{
    public required string DeviceId { get; init; }
    public required bool ReadOnly { get; init; }
    public required Source Source { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (!Regex.IsMatch(DeviceId, @"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z") || !ReadOnly)
            throw new JsonException("Invalid read-only device descriptor.");
    }
}

public sealed class ConnectionStatus : ContractModel
{
    public required string DeviceId { get; init; }
    public required ConnectionState State { get; init; }
    public required DateTimeOffset? LastReceivedAtUtc { get; init; }
    public required long? LastSequence { get; init; }
    public required long AcceptedBatches { get; init; }
    public required long RejectedBatches { get; init; }
    public required long DroppedBatches { get; init; }
    public required double? PollLatencyMs { get; init; }
    public required string? ReasonCode { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (string.IsNullOrWhiteSpace(DeviceId) || LastSequence < 0 || AcceptedBatches < 0 ||
            RejectedBatches < 0 || DroppedBatches < 0 || PollLatencyMs < 0 ||
            (ReasonCode is not null && !Regex.IsMatch(ReasonCode, @"\A[A-Z][A-Z0-9_]{0,63}\z")))
            throw new JsonException("Invalid connection status.");
    }
}
