using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using Aegis.Gateway;
using Microsoft.Data.Sqlite;

internal static class NativeHostRecoveryChecks
{
    public static async Task RunAsync(string hostPath)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("Native recovery checks require Windows.");
        Require(File.Exists(hostPath), "native host executable exists");
        string directory = Path.Combine(Path.GetTempPath(), "aegis-native-recovery-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var settings = new GatewaySettings(new("http://127.0.0.1:8780"), null, null, null, directory) { NativeHostPath = hostPath };
        var archive = new ArchiveReview(settings);
        var owned = new List<Process>();
        var first = new Fixture(directory, "recovery-device-a", "module-a", 2);
        var second = new Fixture(directory, "recovery-device-b", "module-b", 4);
        try
        {
            first.Seed(); second.Seed();
            var a = await ReadyAsync(first);
            var b = await ReadyAsync(second);
            Require(a.NativeSequence >= 2047 && b.NativeSequence >= 2047, "both independent production archives consume 2,048+ rows");
            Process original = Own(first), peer = Own(second);
            Require(original.Id != peer.Id, "each module owns a separate native host process");
            AssertUnchanged(first, second);

            // Kill only the PID furnished by this ArchiveReview, retaining the process handle
            // so PID reuse cannot make the shutdown assertion inspect an unrelated process.
            original.Kill();
            await original.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
            first.Tail(6); second.Tail(4);
            // Recovery may finish between polls; its exact transient duration is not asserted.
            // ReadyAsync checks numeric masking for every non-ready result it does observe.
            a = await ReadyAsync(first, () =>
            {
                var other = Read(second);
                Require(archive.NativeHostProcessId(second.Device) == peer.Id && !peer.HasExited,
                    "unaffected module retains its process during peer recovery");
                // Refreshing fixture timestamps below keeps this check about host isolation,
                // independently of the production three-second freshness boundary.
                if (other.NativeFlow?.State == "READY") AssertMean(other, second);
                second.RefreshIfNeeded();
            });
            Process replacement = Own(first);
            Require(replacement.Id != original.Id, "failed host is replaced with a new owned process");
            AssertMean(a, first);
            b = await ReadyAsync(second);
            Require(archive.NativeHostProcessId(second.Device) == peer.Id, "peer continues without a restart");
            AssertMean(b, second);
            AssertUnchanged(first, second);

            first.Tail(0, "MISSING");
            await UntilAsync(() =>
            {
                var review = Read(first);
                return review.NativeSequence == first.LastSequence && review.NativeFlow is { MeanAbsoluteDeviationLpm: null } flow
                    && flow.State != "READY";
            }, "missing flow quality clears numeric evidence");
            first.Tail(0, "BAD");
            await UntilAsync(() =>
            {
                var review = Read(first);
                return review.NativeSequence == first.LastSequence && review.NativeFlow is { MeanAbsoluteDeviationLpm: null } flow
                    && flow.State != "READY";
            }, "bad flow quality clears numeric evidence");
            first.NewRun();
            first.Tail(8);
            AssertMean(await ReadyAsync(first), first);
            Require(archive.NativeHostProcessId(first.Device) == replacement.Id, "new source run resets context within the same host");
            AssertUnchanged(first, second);

            await archive.StopAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(10));
            foreach (var process in owned)
            {
                await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
                Require(process.HasExited, "all owned native host processes actually exit after StopAsync");
            }
            Console.WriteLine("PASS native host recovery: two archives, isolated restart, deterministic 32-frame means, quality/run reset, archive integrity and process shutdown");
            Console.WriteLine("Native timing checks use 20s eventual deadlines and a loose 1s Read ceiling; they do not guarantee a recovery SLA or exact transient-state durations.");
        }
        finally
        {
            try { await archive.StopAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(10)); }
            finally
            {
                foreach (var process in owned)
                {
                    if (!process.HasExited) { process.Kill(); await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)); }
                    process.Dispose();
                }
                Directory.Delete(directory, true);
            }
        }

        NativeReview Read(Fixture fixture)
        {
            var timer = Stopwatch.StartNew();
            var value = archive.Read(fixture.Device, "equipment-test", fixture.Module);
            Require(timer.Elapsed < TimeSpan.FromSeconds(1), "Read returns within the loose one-second nonblocking ceiling");
            return value;
        }

        Process Own(Fixture fixture)
        {
            int? pid = archive.NativeHostProcessId(fixture.Device);
            Require(pid is > 0, "ready module exposes its owned host PID");
            var process = Process.GetProcessById(pid!.Value);
            _ = process.Handle;
            owned.Add(process);
            return process;
        }

        async Task<NativeReview> ReadyAsync(Fixture fixture, Action? whileWaiting = null)
        {
            NativeReview? latest = null;
            await UntilAsync(() =>
            {
                whileWaiting?.Invoke();
                fixture.RefreshIfNeeded();
                latest = Read(fixture);
                if (latest.NativeFlow is { State: not "READY" } pending)
                {
                    Require(pending.State is "WARMING" or "RECOVERING" or "UNAVAILABLE", "known native evidence state");
                    Require(pending.MeanAbsoluteDeviationLpm is null, "non-ready native evidence masks the numeric result");
                }
                return latest.NativeSequence == fixture.LastSequence && latest.NativeFlow?.State == "READY";
            }, "current native result catches up to committed tail for " + fixture.Device);
            AssertMean(latest!, fixture);
            return latest!;
        }
    }

    private static void AssertMean(NativeReview review, Fixture fixture)
    {
        var evidence = review.NativeFlow;
        Require(evidence is { State: "READY", SampleCount: 32, ObservedAtUtc: not null }
            && evidence.MeanAbsoluteDeviationLpm is double mean && Math.Abs(mean - fixture.ExpectedMean) < 1e-9,
            "native mean includes all 32 frames, including a genuine actual-flow zero, for " + fixture.Device);
    }

    private static void AssertUnchanged(params Fixture[] fixtures)
    {
        foreach (var fixture in fixtures)
            Require(fixture.CommittedHash.SequenceEqual(HashArchive(fixture.Path)),
                "Gateway leaves committed raw archive bytes unchanged for " + fixture.Device);
    }

    private static byte[] HashArchive(string path)
    {
        using var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        return SHA256.HashData(stream);
    }

    private static async Task UntilAsync(Func<bool> condition, string description)
    {
        var deadline = Stopwatch.StartNew();
        do
        {
            if (condition()) return;
            await Task.Delay(25);
        } while (deadline.Elapsed < TimeSpan.FromSeconds(20));
        throw new InvalidOperationException("Native recovery deadline: " + description);
    }

    private static void Require(bool condition, string description)
    {
        if (!condition) throw new InvalidOperationException("Native recovery check failed: " + description);
    }

    internal sealed class Fixture(string directory, string device, string module, double initialDeviation)
    {
        public string Device { get; } = device;
        public string Module { get; } = module;
        public string Path { get; } = System.IO.Path.Combine(directory, ArchiveReview.FileName(device));
        public long LastSequence { get; private set; } = -1;
        public double ExpectedMean { get; private set; }
        public byte[] CommittedHash { get; private set; } = [];
        private string _run = Guid.NewGuid().ToString();
        private DateTimeOffset _lastObserved;
        private DateTimeOffset _lastCommit;
        private double _deviation = initialDeviation;

        public void Seed()
        {
            using var database = Open();
            using (var schema = database.CreateCommand())
            {
                schema.CommandText = """
                    PRAGMA user_version=1;
                    CREATE TABLE raw_batches(device_id TEXT NOT NULL,run_id TEXT NOT NULL,sequence INTEGER NOT NULL CHECK(sequence>=0),observed_at_utc TEXT NOT NULL,received_at_utc TEXT NOT NULL,equipment_id TEXT,module_id TEXT,payload_json TEXT NOT NULL,PRIMARY KEY(device_id,run_id,sequence));
                    CREATE TABLE ingest_cursors(device_id TEXT PRIMARY KEY NOT NULL,run_id TEXT NOT NULL,sequence INTEGER NOT NULL CHECK(sequence>=0));
                    CREATE TABLE ingest_gaps(device_id TEXT NOT NULL,run_id TEXT NOT NULL,first_sequence INTEGER NOT NULL CHECK(first_sequence>=0),last_sequence INTEGER NOT NULL CHECK(last_sequence>=first_sequence),reason TEXT NOT NULL DEFAULT 'REPLAY_OVERFLOW' CHECK(reason='REPLAY_OVERFLOW'),UNIQUE(device_id,run_id,first_sequence,last_sequence));
                    """;
                schema.ExecuteNonQuery();
            }
            Write(database, 2048, _deviation, "GOOD");
        }

        public void NewRun() { _run = Guid.NewGuid().ToString(); LastSequence = -1; }
        public void RefreshIfNeeded() { if (DateTimeOffset.UtcNow - _lastCommit > TimeSpan.FromMilliseconds(750)) Tail(_deviation); }
        public void Tail(double deviation, string quality = "GOOD")
        {
            _deviation = deviation;
            using var database = Open();
            Write(database, 32, deviation, quality);
        }

        private SqliteConnection Open()
        {
            var database = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = Path, Pooling = false }.ToString());
            database.Open();
            return database;
        }

        private void Write(SqliteConnection database, int count, double deviation, string quality)
        {
            var start = DateTimeOffset.UtcNow.AddMilliseconds(-count);
            if (start <= _lastObserved) start = _lastObserved.AddMilliseconds(1);
            using (var transaction = database.BeginTransaction())
            {
                for (int i = 0; i < count; i++)
                {
                    var at = start.AddMilliseconds(i);
                    double actual = i % 32 == 0 ? 0 : 10 + deviation;
                    var signals = WireValidation.Units.Select(pair => new
                    {
                        signalKey = pair.Key,
                        value = pair.Key switch { "setpointFlowLpm" => 10d, "actualFlowLpm" => actual, "supplyPressureKpa" => 500d, "temperatureC" => 25d, _ => 1d },
                        unit = pair.Value,
                        quality = pair.Key == "actualFlowLpm" ? quality : "GOOD"
                    });
                    var payload = JsonSerializer.Serialize(new { protocolVersion = "1.1", source = "SIMULATOR", deviceId = Device,
                        runId = _run, sequence = ++LastSequence, observedAtUtc = at, deviceStateVersion = 1, modeId = "PRODUCTION", signals });
                    using var insert = database.CreateCommand(); insert.Transaction = transaction;
                    insert.CommandText = "INSERT INTO raw_batches VALUES($device,$run,$sequence,$at,$at,'equipment-test',$module,$payload)";
                    insert.Parameters.AddWithValue("$device", Device); insert.Parameters.AddWithValue("$run", _run);
                    insert.Parameters.AddWithValue("$sequence", LastSequence); insert.Parameters.AddWithValue("$at", at.ToString("O"));
                    insert.Parameters.AddWithValue("$module", Module); insert.Parameters.AddWithValue("$payload", payload);
                    insert.ExecuteNonQuery(); _lastObserved = at;
                }
                using var cursor = database.CreateCommand(); cursor.Transaction = transaction;
                cursor.CommandText = "INSERT OR REPLACE INTO ingest_cursors VALUES($device,$run,$sequence)";
                cursor.Parameters.AddWithValue("$device", Device); cursor.Parameters.AddWithValue("$run", _run);
                cursor.Parameters.AddWithValue("$sequence", LastSequence); cursor.ExecuteNonQuery();
                transaction.Commit();
            }
            ExpectedMean = (10 + 31 * deviation) / 32;
            _lastCommit = DateTimeOffset.UtcNow;
            CommittedHash = HashArchive(Path);
        }
    }
}
