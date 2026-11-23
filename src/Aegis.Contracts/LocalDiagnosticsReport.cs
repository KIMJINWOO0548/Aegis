using System.Text.Json;
using System.Text.Json.Serialization;

namespace Aegis.Contracts;

/// <summary>Local support summary. Contains status codes and counts, never raw samples or source identities.</summary>
public sealed record LocalDiagnosticsReport(
    string SchemaVersion, DateTimeOffset GeneratedAtUtc, string SourceMode, string UpstreamState,
    string WorkspaceState, string ModelState, LocalDiagnosticModule[] Modules)
{
    public const string Version = "diagnostics-report-1";
    public const long MaxSentCount = 9_007_199_254_740_991;
    public const int MaxJsonLength = 48 * 1024;
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web)
    {
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        PropertyNameCaseInsensitive = false,
        RespectRequiredConstructorParameters = true,
        AllowDuplicateProperties = false,
        MaxDepth = 8,
    };

    public static LocalDiagnosticsReport? Parse(JsonElement payload)
    {
        try
        {
            var report = payload.Deserialize<LocalDiagnosticsReport>(Options);
            return report is not null && report.Valid() ? report : null;
        }
        catch (JsonException) { return null; }
    }

    public byte[] ToUtf8() => JsonSerializer.SerializeToUtf8Bytes(this, new JsonSerializerOptions(Options)
    { WriteIndented = true });

    public bool Valid() =>
        SchemaVersion == Version && GeneratedAtUtc != default &&
        SourceMode is "EMULATORS" or "FIXTURES" or "UNKNOWN" &&
        UpstreamState is "CONNECTED" or "UNAVAILABLE" or "CONFIGURATION_REQUIRED" or "UNKNOWN" &&
        WorkspaceState is "READY" or "NOT_CONFIGURED" or "UNAVAILABLE" &&
        ModelState == "NOT_REGISTERED" && Modules is { Length: <= 32 } &&
        Modules.Select((module, index) => module?.Slot == index + 1 && module.Valid()).All(valid => valid);
}

public sealed record LocalDiagnosticModule(
    int Slot, string Connection, string Engine, DateTimeOffset? LastReceivedAtUtc,
    DateTimeOffset? LastObservedAtUtc, bool? ArchiveEnabled, long? LastCommittedSequence,
    long? PendingBatches, string Freshness, string WorkerState, LocalDiagnosticQuality Quality)
{
    internal bool Valid() =>
        Connection is "CONNECTING" or "CONNECTED" or "DISCONNECTED" or "REJECTED" or "STALE" or "UNKNOWN" &&
        Engine is "RUNNING" or "UNAVAILABLE" or "ARCHIVE_NOT_CONFIGURED" or "UNKNOWN" &&
        Freshness is "CURRENT" or "STALE" or "RESTORED" or "NO_RECEIPT" or "UNAVAILABLE" or "UNKNOWN" &&
        WorkerState is "RUNNING" or "RESTARTING" or "FAILED" or "UNAVAILABLE" or "NOT_APPLICABLE" or "UNKNOWN" &&
        LastCommittedSequence is not (< 0 or > LocalDiagnosticsReport.MaxSentCount) &&
        PendingBatches is not (< 0 or > LocalDiagnosticsReport.MaxSentCount) && Quality is not null && Quality.Valid();
}

public sealed record LocalDiagnosticQuality(int Good, int Missing, int Stale, int Other)
{
    internal bool Valid() => Good is >= 0 and <= 8 && Missing is >= 0 and <= 8 &&
        Stale is >= 0 and <= 8 && Other is >= 0 and <= 8 && Good + Missing + Stale + Other <= 8;
}
