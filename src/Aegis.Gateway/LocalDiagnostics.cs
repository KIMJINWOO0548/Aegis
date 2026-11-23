using System.Globalization;
using System.Text.Json.Nodes;
using Aegis.Contracts;

namespace Aegis.Gateway;

internal static class LocalDiagnostics
{
    public static LocalDiagnosticsReport Project(DashboardSnapshot snapshot, ProductWorkspace workspace) => new(
        LocalDiagnosticsReport.Version, snapshot.GeneratedAtUtc,
        Allowed(snapshot.SourceMode, "EMULATORS", "FIXTURES"),
        Allowed(snapshot.UpstreamState, "CONNECTED", "UNAVAILABLE", "CONFIGURATION_REQUIRED"),
        workspace.StatusForDiagnostics, "NOT_REGISTERED",
        snapshot.Devices.OrderBy(device => device.DeviceId, StringComparer.Ordinal).Take(32)
            .Select((device, index) => ProjectModule(device, index + 1)).ToArray());

    private static LocalDiagnosticModule ProjectModule(DashboardDevice device, int slot)
    {
        var connection = device.Connection;
        var ingestion = device.Ingestion;
        var collection = ingestion?["collectionStatus"];
        var qualities = device.Observations.Take(8).Select(item => Text(item?["quality"])).ToArray();
        var good = qualities.Count(value => value == "GOOD");
        var missing = qualities.Count(value => value == "MISSING");
        var stale = qualities.Count(value => value == "STALE");

        return new(slot,
            Allowed(Text(connection?["state"]), "CONNECTING", "CONNECTED", "DISCONNECTED", "REJECTED", "STALE"),
            Allowed(device.EngineState, "RUNNING", "UNAVAILABLE", "ARCHIVE_NOT_CONFIGURED"),
            Timestamp(connection?["lastReceivedAtUtc"]), Timestamp(ingestion?["lastObservedAtUtc"]),
            ingestion?["archiveEnabled"] is JsonValue configured && configured.TryGetValue<bool>(out var enabled) ? enabled : null,
            Count(ingestion?["lastCommittedSequence"]), Count(ingestion?["pendingBatches"]),
            Allowed(Text(collection?["freshness"]), "CURRENT", "STALE", "RESTORED", "NO_RECEIPT", "UNAVAILABLE"),
            Allowed(Text(ingestion?["workerState"]) ?? Text(collection?["workerStatus"]),
                "RUNNING", "RESTARTING", "FAILED", "UNAVAILABLE", "NOT_APPLICABLE"),
            new(good, missing, stale, qualities.Length - good - missing - stale));
    }

    private static string Allowed(string? value, params string[] allowed) =>
        value is not null && allowed.Contains(value, StringComparer.Ordinal) ? value : "UNKNOWN";

    private static string? Text(JsonNode? node) =>
        node is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;

    private static long? Count(JsonNode? node) =>
        node is JsonValue value && value.TryGetValue<long>(out var count) && count is >= 0 and <= LocalDiagnosticsReport.MaxSentCount ? count : null;

    private static DateTimeOffset? Timestamp(JsonNode? node) =>
        DateTimeOffset.TryParse(Text(node), CultureInfo.InvariantCulture, DateTimeStyles.None, out var time)
            ? time.ToUniversalTime() : null;
}
