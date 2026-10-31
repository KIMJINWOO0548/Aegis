using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Aegis.Gateway;
using Microsoft.AspNetCore.SignalR;
using Microsoft.Data.Sqlite;

static class RawAndCacheChecks
{
    public static async Task Run(Action<bool, string> check, IHubContext<DashboardHub> hub)
    {
        var settings = new GatewaySettings(new("http://127.0.0.1:8780"), new("http://127.0.0.1:8765"), new('x', 32), new('y', 32), null);
        var source = new SnapshotFactory();
        var cache = new DashboardCache(settings, source, new(settings), hub);
        await Task.WhenAll(Enumerable.Range(0, 4).Select(_ => cache.RefreshAsync(CancellationToken.None)));
        var latest = cache.Latest;
        check(latest.UpstreamState == "CONNECTED" && latest.Devices.Count == 1, "actual collector API shape accepted");
        var device = latest.Devices.Single();
        check(device.Ingestion!["signalCount"]!.GetValue<int>() == 512 && device.Observations.Count == 8, "raw512 signals retain bounded8 dashboard observations");
        check(device.EngineState == "ARCHIVE_NOT_CONFIGURED" && device.Review is null, "native archive absence honest");
        check(JsonSerializer.Serialize(latest.Inference).Contains("NOT_REGISTERED"), "inference absence honest");
        device.Observations.Clear(); device.Ingestion["signalCount"] = 0;
        check(cache.Latest.Devices[0].Observations.Count == 8 && cache.Latest.Devices[0].Ingestion!["signalCount"]!.GetValue<int>() == 512, "published snapshot mutation isolated");
        source.Stale = true;
        await cache.RefreshAsync(CancellationToken.None);
        check(cache.Latest.Devices[0].Observations.All(row => row!["quality"]!.GetValue<string>() == "STALE")
            && cache.Latest.Devices[0].Connection!["state"]!.GetValue<string>() == "STALE", "three second stale observations and connection");
        source.BadUnit = true;
        await cache.RefreshAsync(CancellationToken.None);
        check(cache.Latest.UpstreamState == "UNAVAILABLE", "wrong signal unit rejected");
        source.BadUnit = false; source.BadDevice = true;
        await cache.RefreshAsync(CancellationToken.None);
        check(cache.Latest.UpstreamState == "UNAVAILABLE", "wrong observation device rejected");
        check(cache.Latest.Devices.Count == 1 && cache.Latest.Devices[0].Observations.Count == 8
            && cache.Latest.Devices[0].Connection!["state"]!.GetValue<string>() != "CONNECTED",
            "upstream interruption retains explicitly stale history");

        var directory = Path.Combine(Path.GetTempPath(), "aegis-archive-continuity-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            settings = settings with { ArchiveDirectory = directory };
            var path = Path.Combine(directory, ArchiveReview.FileName("device-1"));
            using var database = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Pooling = false }.ToString());
            database.Open();
            using (var schema = database.CreateCommand())
            {
                schema.CommandText = "PRAGMA user_version=1; CREATE TABLE raw_batches(device_id TEXT NOT NULL,run_id TEXT NOT NULL,sequence INTEGER NOT NULL CHECK(sequence>=0),observed_at_utc TEXT NOT NULL,received_at_utc TEXT NOT NULL,equipment_id TEXT,module_id TEXT,payload_json TEXT NOT NULL,PRIMARY KEY(device_id,run_id,sequence));";
                schema.ExecuteNonQuery();
            }
            var start = DateTimeOffset.UtcNow.AddSeconds(-6);
            const string run = "b8e088cc-712a-4b43-ae5a-9fc49b98ab6b";
            using (var transaction = database.BeginTransaction())
            {
                for (var i = 0; i < 600; i++) Insert(database, transaction, run, i, start.AddMilliseconds(i * 10), i is >= 250 and <= 270 ? 14 : 10, i == 0);
                transaction.Commit();
            }
            database.Close();
            var original = File.ReadAllBytes(path);
            var archive = new ArchiveReview(settings);
            var first = archive.Read("device-1", "equipment-1", "module-1");
            check(first.NativeSequence == 255 && first.EngineState == "UNAVAILABLE", "archive read bounded four pages of64");
            var second = archive.Read("device-1", "equipment-1", "module-1");
            check(second.NativeSequence == 511 && second.Review!.Events.Single().SampleCount == 21
                && second.Review.Events[0].EndReason == "RETURNED_TO_CONTEXT", "catchup preserves pulse across page boundary");
            var third = archive.Read("device-1", "equipment-1", "module-1");
            check(third.NativeSequence == 599 && third.EngineState == "RUNNING" && third.Review!.ContextSampleCount > 5, "every committed raw frame consumed");
            var restarted = new ArchiveReview(settings);
            restarted.Read("device-1", "equipment-1", "module-1"); restarted.Read("device-1", "equipment-1", "module-1");
            var replay = restarted.Read("device-1", "equipment-1", "module-1");
            check(replay.NativeSequence == 599 && replay.Review!.EventCount == third.Review!.EventCount, "restart reconstructs committed review");
            check(original.SequenceEqual(File.ReadAllBytes(path)), "committed archive bytes unchanged after replay");
            database.Open();
            Insert(database, null, "61ec4caa-181f-4141-9472-3f9d558b026f", 0, DateTimeOffset.UtcNow, 10, false);
            var newRun = archive.Read("device-1", "equipment-1", "module-1");
            check(newRun.NativeSequence == 0 && newRun.EngineState == "RUNNING" && newRun.Review!.EventCount == 1, "new source run continues archive row cursor");
        }
        finally { Directory.Delete(directory, true); }
    }

    private static void Insert(SqliteConnection database, SqliteTransaction? transaction, string run, int sequence, DateTimeOffset at, double actual, bool expanded)
    {
        var signals = WireValidation.Units.Select(pair => new { signalKey = pair.Key, value = pair.Key switch
        { "setpointFlowLpm" => 10d, "actualFlowLpm" => actual, "supplyPressureKpa" => 500d, "temperatureC" => 25d, _ => 1d }, unit = pair.Value, quality = "GOOD" }).ToList();
        if (expanded) for (var i = 0; i < 504; i++) signals.Add(new { signalKey = "extra-" + i.ToString("D3") + new string('a', 118), value = 1d, unit = "raw-unit", quality = "GOOD" });
        var payload = JsonSerializer.Serialize(new { protocolVersion = "1.1", source = "SIMULATOR", deviceId = "device-1", runId = run, sequence,
            observedAtUtc = at, deviceStateVersion = 1, modeId = "PRODUCTION", signals });
        using var insert = database.CreateCommand(); insert.Transaction = transaction;
        insert.CommandText = "INSERT INTO raw_batches VALUES ('device-1',$run,$sequence,$at,$at,'equipment-1','module-1',$payload)";
        insert.Parameters.AddWithValue("$run", run); insert.Parameters.AddWithValue("$sequence", sequence);
        insert.Parameters.AddWithValue("$at", at.ToString("O")); insert.Parameters.AddWithValue("$payload", payload); insert.ExecuteNonQuery();
    }
}

sealed class SnapshotFactory : IHttpClientFactory
{
    public bool Stale, BadUnit, BadDevice;
    public HttpClient CreateClient(string name) => new(new Handler(this));
    private sealed class Handler(SnapshotFactory owner) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var at = DateTimeOffset.UtcNow.AddSeconds(owner.Stale ? -4 : 0).ToString("O");
            object result = request.RequestUri!.AbsolutePath switch
            {
                "/v1/health" => new { mode = "EMULATORS" },
                "/v1/devices" => new[] { new { deviceId = "device-1", readOnly = true, source = "SIMULATOR" } },
                "/v1/connections" => new[] { new { deviceId = "device-1", state = "CONNECTED", lastReceivedAtUtc = at, lastSequence = 599, acceptedBatches = 600, rejectedBatches = 0, droppedBatches = 0, pollLatencyMs = .1, reasonCode = (string?)null } },
                "/v1/ingestion" => new[] { new { deviceId = "device-1", equipmentId = "equipment-1", moduleId = "module-1", transport = "replay", archiveEnabled = true, signalCount = 512, acceptedSignalValues = 307200, archiveCommitMs = .1, latestAvailableSequence = 599, pendingBatches = 0, observedLagMs = 0, lastObservedAtUtc = at, lastCommittedRunId = "b8e088cc-712a-4b43-ae5a-9fc49b98ab6b", lastCommittedSequence = 599 } },
                "/v1/warnings" => Array.Empty<object>(),
                "/v1/devices/device-1/diagnostics" => new { deviceId = "device-1", enabled = false, modelVersion = (string?)null, evaluation = "INSUFFICIENT_DATA", sampleCount = 0, flowErrorEwmaLpm = (double?)null, issues = Array.Empty<object>() },
                "/v1/devices/device-1/observations" => WireValidation.Units.Select(pair => new { schemaVersion = Aegis.Contracts.Contract.Version,
                    observationId = new string('a', 200), deviceId = owner.BadDevice ? "device-2" : "device-1", signalKey = pair.Key, value = 1d,
                    unit = owner.BadUnit ? "wrong" : pair.Value, observedAtUtc = at, receivedAtUtc = at, quality = "GOOD", deviceStateVersion = 1, modeId = "PRODUCTION" }).ToArray(),
                _ => throw new InvalidOperationException("Unexpected upstream route.")
            };
            return Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK)
            { Content = new StringContent(JsonSerializer.Serialize(result), Encoding.UTF8, "application/json") });
        }
    }
}
