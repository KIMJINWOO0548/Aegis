using System.Net.Http.Headers;
using System.Text.Json;
using System.Text.Json.Nodes;
using Aegis.Contracts;
using Microsoft.AspNetCore.SignalR;

namespace Aegis.Gateway;

public sealed record DashboardSnapshot(string SchemaVersion, DateTimeOffset GeneratedAtUtc, string UpstreamState,
    string SourceMode, string? Message, IReadOnlyList<DashboardDevice> Devices, object Inference);
public sealed record DashboardDevice(string DeviceId, string? EquipmentId, string? ModuleId, JsonNode? Connection,
    JsonNode? Ingestion, JsonArray Observations, JsonNode? Diagnostics, JsonArray Warnings,
    Aegis.SignalProcessing.FlowReviewSnapshot? Review, string EngineState, long? NativeSequence);

public sealed class DashboardCache(GatewaySettings settings, IHttpClientFactory clients, ArchiveReview archive,
    IHubContext<DashboardHub> hub) : BackgroundService
{
    private const int MaxWarningsPerDevice = 32;
    private static readonly object Inference = new { state = "NOT_REGISTERED", message = "No validated ONNX model is registered." };
    private DashboardSnapshot _latest = Empty("CONFIGURATION_REQUIRED");
    private readonly SemaphoreSlim _refresh = new(1, 1);
    public DashboardSnapshot Latest
    {
        get
        {
            var snapshot = Volatile.Read(ref _latest);
            return snapshot with { Devices = snapshot.Devices.Select(device => device with
            {
                Connection = device.Connection?.DeepClone(), Ingestion = device.Ingestion?.DeepClone(),
                Observations = device.Observations.DeepClone().AsArray(), Diagnostics = device.Diagnostics?.DeepClone(),
                Warnings = device.Warnings.DeepClone().AsArray(), Review = device.Review is { } review ? review with
                { Events = review.Events.Select(e => e with { RelatedSignalKeys = e.RelatedSignalKeys.ToArray() }).ToArray() } : null
            }).ToArray() };
        }
    }
    private static DashboardSnapshot Empty(string state) => new("dashboard-1", DateTimeOffset.UtcNow, state, "UNKNOWN",
        state == "CONFIGURATION_REQUIRED" ? "Configure the local API and dashboard credentials." : "Local upstream unavailable.", [], Inference);
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        do
        {
            await RefreshAsync(stoppingToken);
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
            deadline.CancelAfter(TimeSpan.FromMilliseconds(500));
            try { await hub.Clients.All.SendAsync("snapshot", Latest, deadline.Token); }
            catch (Exception error) when (error is OperationCanceledException or IOException or InvalidOperationException) { }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }
    public async Task RefreshAsync(CancellationToken cancellationToken)
    {
        await _refresh.WaitAsync(cancellationToken);
        try { await RefreshCoreAsync(cancellationToken); }
        finally { _refresh.Release(); }
    }
    private async Task RefreshCoreAsync(CancellationToken cancellationToken)
    {
        if (settings.Upstream is null || settings.UpstreamToken is null || settings.DashboardToken is null)
        { Volatile.Write(ref _latest, Empty("CONFIGURATION_REQUIRED")); return; }
        try
        {
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            deadline.CancelAfter(TimeSpan.FromSeconds(5));
            var health = await GetAsync("v1/health", deadline.Token);
            var mode = WireValidation.String(health, "mode") switch { "EMULATORS" => "EMULATORS", "FIXTURE" => "FIXTURES", _ => throw new InvalidDataException() };
            var devices = WireValidation.Array(await GetAsync("v1/devices", deadline.Token), 32);
            var ids = devices.Select(node => WireValidation.Identity(WireValidation.String(node!, "deviceId"))).ToHashSet(StringComparer.Ordinal);
            if (ids.Count != devices.Count) throw new InvalidDataException();
            var connections = Index(await GetAsync("v1/connections", deadline.Token), ids);
            var ingestions = Index(await GetAsync("v1/ingestion", deadline.Token), ids);
            var warnings = WireValidation.Array(await GetAsync("v1/warnings", deadline.Token), MaxWarningsPerDevice * ids.Count);
            ContractJson.Deserialize<Warning[]>(warnings.ToJsonString());
            var warningCounts = ids.ToDictionary(id => id, _ => 0, StringComparer.Ordinal);
            foreach (var warning in warnings)
            {
                var deviceId = WireValidation.String(warning!, "deviceId");
                if (!warningCounts.TryGetValue(deviceId, out var count) || count >= MaxWarningsPerDevice) throw new InvalidDataException();
                warningCounts[deviceId] = count + 1;
            }
            var result = new List<DashboardDevice>(32);
            archive.Retain(ids);
            foreach (var id in ids)
            {
                var observations = WireValidation.Observations(await GetAsync($"v1/devices/{Uri.EscapeDataString(id)}/observations", deadline.Token), id);
                var diagnostics = await GetAsync($"v1/devices/{Uri.EscapeDataString(id)}/diagnostics", deadline.Token);
                if (WireValidation.String(diagnostics, "deviceId") != id) throw new InvalidDataException();
                diagnostics = WireValidation.Project(diagnostics, ["deviceId", "enabled", "modelVersion", "evaluation", "sampleCount", "flowErrorEwmaLpm", "issues"]);
                if (!Enum.GetNames<ObservationEvaluation>().Contains(WireValidation.String(diagnostics, "evaluation")) || diagnostics["sampleCount"]!.GetValue<long>() < 0) throw new InvalidDataException();
                _ = diagnostics["enabled"]!.GetValue<bool>();
                foreach (var issue in WireValidation.Array(diagnostics["issues"]!, 8))
                    if (!WireValidation.Units.ContainsKey(WireValidation.String(issue!, "signalKey"))) throw new InvalidDataException();
                connections.TryGetValue(id, out var connection);
                if (connection is not null)
                {
                    connection = WireValidation.Project(connection, ["deviceId", "state", "lastReceivedAtUtc", "lastSequence", "acceptedBatches", "rejectedBatches", "droppedBatches", "pollLatencyMs", "reasonCode"]);
                    if (!new[] { "CONNECTING", "CONNECTED", "DISCONNECTED", "REJECTED", "STALE" }.Contains(WireValidation.String(connection, "state"))) throw new InvalidDataException();
                    foreach (var key in new[] { "lastSequence", "acceptedBatches", "rejectedBatches", "droppedBatches" }) if (connection[key] is { } number && number.GetValue<long>() < 0) throw new InvalidDataException();
                    if (connection["pollLatencyMs"] is { } latency && latency.GetValue<double>() < 0) throw new InvalidDataException();
                }
                ingestions.TryGetValue(id, out var ingestion);
                string? equipment = null, module = null;
                if (ingestion is not null)
                {
                    ingestion = WireValidation.Project(ingestion, ["deviceId", "equipmentId", "moduleId", "transport", "archiveEnabled", "signalCount", "acceptedSignalValues", "archiveCommitMs", "latestAvailableSequence", "pendingBatches", "observedLagMs", "lastObservedAtUtc", "lastCommittedRunId", "lastCommittedSequence"]);
                    equipment = WireValidation.NullableIdentity(ingestion, "equipmentId"); module = WireValidation.NullableIdentity(ingestion, "moduleId");
                    if ((equipment is null) != (module is null) || !new[] { "snapshot", "replay" }.Contains(WireValidation.String(ingestion, "transport"))) throw new InvalidDataException();
                    _ = ingestion["archiveEnabled"]!.GetValue<bool>();
                    foreach (var key in new[] { "signalCount", "acceptedSignalValues", "latestAvailableSequence", "pendingBatches", "lastCommittedSequence" })
                        if (ingestion[key] is { } number && number.GetValue<long>() < 0) throw new InvalidDataException();
                    if (ingestion["signalCount"]!.GetValue<int>() > 512) throw new InvalidDataException();
                    foreach (var key in new[] { "archiveCommitMs", "observedLagMs" }) if (ingestion[key] is { } number && number.GetValue<double>() < 0) throw new InvalidDataException();
                    if (ingestion["lastCommittedRunId"] is { } run && !Guid.TryParse(run.GetValue<string>(), out _)) throw new InvalidDataException();
                }
                var now = DateTimeOffset.UtcNow;
                foreach (var observation in observations)
                {
                    var observed = WireValidation.Timestamp(WireValidation.String(observation!, "observedAtUtc"));
                    var received = WireValidation.Timestamp(WireValidation.String(observation!, "receivedAtUtc"));
                    if (now - observed > TimeSpan.FromSeconds(3) || now - received > TimeSpan.FromSeconds(3) || observed > now.AddSeconds(1) || received > now.AddSeconds(1))
                    { if (observation!["quality"]!.GetValue<string>() == "GOOD") observation["quality"] = "STALE"; if (connection?["state"]?.GetValue<string>() == "CONNECTED") connection["state"] = "STALE"; }
                }
                var native = archive.Read(id, equipment, module);
                result.Add(new(id, equipment, module, connection, ingestion, observations, diagnostics,
                    new JsonArray(warnings.Where(warning => WireValidation.String(warning!, "deviceId") == id).Select(warning => warning!.DeepClone()).ToArray()), native.Review, native.EngineState, native.NativeSequence));
            }
            Volatile.Write(ref _latest, new("dashboard-1", DateTimeOffset.UtcNow, "CONNECTED", mode, null, result, Inference));
        }
        catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException or InvalidDataException or InvalidOperationException or FormatException or KeyNotFoundException or ArgumentException or OverflowException or NullReferenceException)
        {
            archive.Unavailable();
            var previous = Latest;
            var history = previous.Devices.Select(device =>
            {
                if (device.Connection is { } connection) connection["state"] = "DISCONNECTED";
                foreach (var observation in device.Observations)
                    if (observation?["quality"]?.GetValue<string>() == "GOOD") observation["quality"] = "STALE";
                var unavailable = archive.Snapshot(device.DeviceId);
                return device with { Review = unavailable ?? device.Review, EngineState = "UNAVAILABLE" };
            }).ToArray();
            Volatile.Write(ref _latest, previous with { GeneratedAtUtc = DateTimeOffset.UtcNow,
                UpstreamState = "UNAVAILABLE", Message = "Local upstream unavailable; displaying received history.", Devices = history });
        }
    }
    private static Dictionary<string, JsonNode> Index(JsonNode node, ISet<string> ids)
    {
        var result = new Dictionary<string, JsonNode>(StringComparer.Ordinal);
        foreach (var row in WireValidation.Array(node, 32))
        { var id = WireValidation.Identity(WireValidation.String(row!, "deviceId")); if (!ids.Contains(id) || !result.TryAdd(id, row!)) throw new InvalidDataException(); }
        return result;
    }
    private async Task<JsonNode> GetAsync(string path, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(settings.Upstream!, path));
        request.Headers.Add("X-Aegis-Token", settings.UpstreamToken);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        using var response = await clients.CreateClient("upstream").SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        response.EnsureSuccessStatusCode();
        if (response.Content.Headers.ContentLength is > 262144) throw new InvalidDataException();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var buffer = new MemoryStream();
        var bytes = new byte[8192];
        int count;
        while ((count = await stream.ReadAsync(bytes, cancellationToken)) > 0)
        { if (buffer.Length + count > 262144) throw new InvalidDataException(); buffer.Write(bytes, 0, count); }
        return JsonNode.Parse(buffer.ToArray(), documentOptions: new() { MaxDepth = 16 }) ?? throw new InvalidDataException();
    }
}
