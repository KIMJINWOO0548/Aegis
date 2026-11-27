using System.Text.Json;
using Aegis.Gateway;
using Microsoft.Data.Sqlite;

internal static class NativeArchiveReaderTests
{
    private const string RunA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    private const string RunB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    private const string RunC = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    private static readonly DateTimeOffset Start = new(2026, 11, 27, 0, 0, 0, TimeSpan.Zero);

    public static void Run(Action<bool, string> check)
    {
        var directory = Path.Combine(Path.GetTempPath(), "aegis-native-archive-reader-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            CheckLargeRestartAndForwardHandoff(directory, check);
            CheckRunAndQualityBoundaries(directory, check);
            CheckTimestampBoundaries(directory, check);
            CheckPayloadIdentityValidation(directory, check);
        }
        finally { Directory.Delete(directory, true); }
    }

    private static void CheckLargeRestartAndForwardHandoff(string directory, Action<bool, string> check)
    {
        const string device = "device-1";
        var path = CreateArchive(directory, device);
        using (var database = OpenWriter(path))
        using (var transaction = database.BeginTransaction())
        {
            for (var sequence = 0; sequence < 2200; sequence++)
                Insert(database, transaction, device, RunA, sequence, Start.AddMilliseconds(sequence * 100), 10 + sequence % 5);
            SetCursor(database, transaction, device, RunA, 2199);
            transaction.Commit();
        }

        var beforeSeed = File.ReadAllBytes(path);
        var reader = new NativeArchiveReader(directory, device, "equipment-1", "module-1");
        var seed = reader.ReadSeed();
        check(seed.RunId == RunA && seed.CaughtUp && seed.Frames.Count == 32
            && seed.Frames[0].Sequence == 2168 && seed.Frames[^1].Sequence == 2199,
            "restart seed selects newest contiguous 32-frame suffix after 2,000 rows");
        check(seed.Frames[0].ObservedAtUtc == Start.AddMilliseconds(2168 * 100)
            && seed.Frames[0].ReceivedAtUtc == Start.AddMilliseconds(2168 * 100 + 25),
            "restart seed preserves observed and received timestamps");
        check(seed.Watermark == 2200 && beforeSeed.SequenceEqual(File.ReadAllBytes(path)),
            "restart seed watermark is the read-only snapshot high-water rowid");

        using (var database = OpenWriter(path))
        using (var transaction = database.BeginTransaction())
        {
            for (var sequence = 2200; sequence < 2330; sequence++)
                Insert(database, transaction, device, RunA, sequence, Start.AddMilliseconds(sequence * 100), 12);
            SetCursor(database, transaction, device, RunA, 2329);
            transaction.Commit();
        }

        var first = reader.ReadForward(seed.Watermark);
        check(first.Frames.Count == 64 && !first.CaughtUp && first.Watermark == first.Frames[^1].RowId,
            "forward tail returns at most 64 rows and holds watermark before pending rows");
        using (var database = OpenWriter(path))
        using (var transaction = database.BeginTransaction())
        {
            Insert(database, transaction, device, RunA, 2330, Start.AddMilliseconds(233000), 12);
            SetCursor(database, transaction, device, RunA, 2330);
            transaction.Commit();
        }
        var second = reader.ReadForward(first.Watermark);
        var third = reader.ReadForward(second.Watermark);
        var delivered = first.Frames.Concat(second.Frames).Concat(third.Frames).ToArray();
        check(second.Frames.Count == 64 && !second.CaughtUp && third.Frames.Count == 3 && third.CaughtUp,
            "new inserts after an acknowledged page remain available across fixed tail pages");
        check(delivered.Select(frame => frame.Sequence).SequenceEqual(Enumerable.Range(2200, 131).Select(value => (long)value)),
            "forward watermark handoff neither skips nor repeats concurrently appended rows");
    }

    private static void CheckRunAndQualityBoundaries(string directory, Action<bool, string> check)
    {
        const string device = "device-2";
        var path = CreateArchive(directory, device);
        long rowThree;
        using (var database = OpenWriter(path))
        using (var transaction = database.BeginTransaction())
        {
            for (var sequence = 0; sequence < 8; sequence++)
                Insert(database, transaction, device, RunA, sequence, Start.AddMilliseconds(sequence * 100), 10);
            Insert(database, transaction, device, RunB, 0, Start.AddSeconds(10), 20);
            Insert(database, transaction, device, RunB, 1, Start.AddSeconds(10).AddMilliseconds(100), 21);
            Insert(database, transaction, device, RunB, 2, Start.AddSeconds(10).AddMilliseconds(200), 22);
            rowThree = Insert(database, transaction, device, RunB, 3, Start.AddSeconds(10).AddMilliseconds(300), 23);
            Insert(database, transaction, device, RunB, 4, Start.AddSeconds(10).AddMilliseconds(400), 0,
                actualQuality: "MISSING", actualValue: null);
            Insert(database, transaction, device, RunB, 5, Start.AddSeconds(10).AddMilliseconds(500), 25);
            Insert(database, transaction, device, RunB, 6, Start.AddSeconds(10).AddMilliseconds(600), 26);
            Insert(database, transaction, device, RunB, 7, Start.AddSeconds(10).AddMilliseconds(700), 27);
            SetCursor(database, transaction, device, RunB, 7);
            transaction.Commit();
        }

        var reader = new NativeArchiveReader(directory, device, "equipment-1", "module-1");
        var seed = reader.ReadSeed();
        check(seed.RunId == RunB && seed.Frames.Select(frame => frame.Sequence).SequenceEqual([5L, 6L, 7L]),
            "restart seed excludes prior runs and stops at missing quality while retaining short suffix");
        var fromMissing = reader.ReadForward(rowThree);
        check(fromMissing.Frames.Select(frame => frame.Quality).SequenceEqual([1u, 0u, 0u, 0u]),
            "forward reader reports explicit missing quality without classifying freshness");

        long nextWatermark;
        using (var database = OpenWriter(path))
        using (var transaction = database.BeginTransaction())
        {
            Insert(database, transaction, device, RunA, 8, Start.AddSeconds(11), 30);
            Insert(database, transaction, device, RunB, 8, Start.AddSeconds(11).AddMilliseconds(100), 101);
            SetCursor(database, transaction, device, RunB, 8);
            transaction.Commit();
        }
        var bad = reader.ReadForward(seed.Watermark);
        check(bad.RunId == RunB && bad.Frames.Count == 1 && bad.Frames[0].Sequence == 8 && bad.Frames[0].Quality == 2,
            "forward reader ignores newly inserted stale-run rows and marks out-of-range flow bad");
        nextWatermark = bad.Watermark;

        using (var database = OpenWriter(path))
        using (var transaction = database.BeginTransaction())
        {
            Insert(database, transaction, device, RunC, 0, Start.AddSeconds(12), 20);
            SetCursor(database, transaction, device, RunC, 0);
            transaction.Commit();
        }
        var changedRun = reader.ReadForward(nextWatermark);
        check(changedRun.RunId == RunC && changedRun.Frames.Count == 1 && changedRun.Frames[0].Sequence == 0,
            "forward page exposes the active run change and starts at its newer rowid");
    }

    private static void CheckTimestampBoundaries(string directory, Action<bool, string> check)
    {
        const string gapDevice = "device-3";
        var gapPath = CreateArchive(directory, gapDevice);
        using (var database = OpenWriter(gapPath))
        using (var transaction = database.BeginTransaction())
        {
            Insert(database, transaction, gapDevice, RunA, 0, Start, 10);
            Insert(database, transaction, gapDevice, RunA, 1, Start.AddMilliseconds(10), 10);
            Insert(database, transaction, gapDevice, RunA, 2, Start.AddMilliseconds(1520), 10);
            SetCursor(database, transaction, gapDevice, RunA, 2);
            transaction.Commit();
        }
        var gap = new NativeArchiveReader(directory, gapDevice, "equipment-1", "module-1").ReadSeed();
        check(gap.Frames.Count == 1 && gap.Frames[0].Sequence == 2,
            "restart seed breaks on an observed-time gap greater than 1,500 ms");

        const string equalDevice = "device-4";
        var equalPath = CreateArchive(directory, equalDevice);
        using (var database = OpenWriter(equalPath))
        using (var transaction = database.BeginTransaction())
        {
            Insert(database, transaction, equalDevice, RunA, 0, Start, 10);
            Insert(database, transaction, equalDevice, RunA, 1, Start.AddMilliseconds(10), 10);
            Insert(database, transaction, equalDevice, RunA, 2, Start.AddMilliseconds(10), 10);
            SetCursor(database, transaction, equalDevice, RunA, 2);
            transaction.Commit();
        }
        var equal = new NativeArchiveReader(directory, equalDevice, "equipment-1", "module-1").ReadSeed();
        check(equal.Frames.Count == 1 && equal.Frames[0].Sequence == 2,
            "restart seed breaks on equal observed timestamps");

        const string reverseDevice = "device-5";
        var reversePath = CreateArchive(directory, reverseDevice);
        using (var database = OpenWriter(reversePath))
        using (var transaction = database.BeginTransaction())
        {
            Insert(database, transaction, reverseDevice, RunA, 0, Start.AddMilliseconds(100), 10);
            Insert(database, transaction, reverseDevice, RunA, 1, Start.AddMilliseconds(50), 10);
            Insert(database, transaction, reverseDevice, RunA, 2, Start.AddMilliseconds(60), 10);
            SetCursor(database, transaction, reverseDevice, RunA, 2);
            transaction.Commit();
        }
        var reversed = new NativeArchiveReader(directory, reverseDevice, "equipment-1", "module-1").ReadSeed();
        check(reversed.Frames.Select(frame => frame.Sequence).SequenceEqual([1L, 2L]),
            "restart seed breaks before reversed observed timestamps and preserves newer order");
    }

    private static void CheckPayloadIdentityValidation(string directory, Action<bool, string> check)
    {
        const string device = "device-6";
        var path = CreateArchive(directory, device);
        using (var database = OpenWriter(path))
        using (var transaction = database.BeginTransaction())
        {
            Insert(database, transaction, device, RunA, 0, Start, 10, payloadDevice: "device-other");
            SetCursor(database, transaction, device, RunA, 0);
            transaction.Commit();
        }
        var reader = new NativeArchiveReader(directory, device, "equipment-1", "module-1");
        check(ThrowsInvalidData(() => reader.ReadSeed()), "reader rejects raw payloads whose device identity differs from the row");
    }

    private static string CreateArchive(string directory, string device)
    {
        var path = Path.Combine(directory, ArchiveReview.FileName(device));
        using var database = OpenWriter(path);
        using var command = database.CreateCommand();
        command.CommandText = """
            PRAGMA user_version=1;
            CREATE TABLE raw_batches (
                device_id TEXT NOT NULL, run_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=0),
                observed_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, equipment_id TEXT, module_id TEXT,
                payload_json TEXT NOT NULL, PRIMARY KEY(device_id,run_id,sequence));
            CREATE TABLE ingest_cursors (
                device_id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=0));
            CREATE TABLE ingest_gaps (
                device_id TEXT NOT NULL, run_id TEXT NOT NULL,
                first_sequence INTEGER NOT NULL CHECK(first_sequence>=0),
                last_sequence INTEGER NOT NULL CHECK(last_sequence>=first_sequence),
                reason TEXT NOT NULL DEFAULT 'REPLAY_OVERFLOW' CHECK(reason='REPLAY_OVERFLOW'),
                UNIQUE(device_id,run_id,first_sequence,last_sequence));
            """;
        command.ExecuteNonQuery();
        return path;
    }

    private static SqliteConnection OpenWriter(string path)
    {
        var database = new SqliteConnection(new SqliteConnectionStringBuilder
        { DataSource = path, Pooling = false, DefaultTimeout = 1 }.ToString());
        database.Open();
        return database;
    }

    private static long Insert(SqliteConnection database, SqliteTransaction transaction, string device,
        string run, long sequence, DateTimeOffset observed, double actual,
        string actualQuality = "GOOD", double? actualValue = null, string actualUnit = "L/min", string? payloadDevice = null)
    {
        var payload = Payload(payloadDevice ?? device, run, sequence, observed, actualValue ?? actual, actualQuality, actualUnit);
        using var command = database.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            INSERT INTO raw_batches
            (device_id,run_id,sequence,observed_at_utc,received_at_utc,equipment_id,module_id,payload_json)
            VALUES ($device,$run,$sequence,$observed,$received,'equipment-1','module-1',$payload)
            """;
        command.Parameters.AddWithValue("$device", device);
        command.Parameters.AddWithValue("$run", run);
        command.Parameters.AddWithValue("$sequence", sequence);
        command.Parameters.AddWithValue("$observed", observed.ToUniversalTime().ToString("O"));
        command.Parameters.AddWithValue("$received", observed.AddMilliseconds(25).ToUniversalTime().ToString("O"));
        command.Parameters.AddWithValue("$payload", payload);
        command.ExecuteNonQuery();
        using var rowId = database.CreateCommand();
        rowId.Transaction = transaction;
        rowId.CommandText = "SELECT last_insert_rowid()";
        return Convert.ToInt64(rowId.ExecuteScalar());
    }

    private static string Payload(string device, string run, long sequence, DateTimeOffset observed,
        double actual, string actualQuality, string actualUnit)
    {
        object[] signals =
        [
            new { signalKey = "actualFlowLpm", value = actualQuality == "MISSING" && actual == 0 ? (double?)null : actual, unit = actualUnit, quality = actualQuality },
            new { signalKey = "setpointFlowLpm", value = (double?)10, unit = "L/min", quality = "GOOD" },
            new { signalKey = "supplyPressureKpa", value = (double?)500, unit = "kPa", quality = "GOOD" },
            new { signalKey = "valveCommandPct", value = (double?)50, unit = "%", quality = "GOOD" },
            new { signalKey = "valveFeedbackPct", value = (double?)50, unit = "%", quality = "GOOD" },
            new { signalKey = "motorCurrentAmp", value = (double?)1, unit = "A", quality = "GOOD" },
            new { signalKey = "temperatureC", value = (double?)25, unit = "degC", quality = "GOOD" },
            new { signalKey = "cycleCount", value = (double?)100, unit = "count", quality = "GOOD" }
        ];
        return JsonSerializer.Serialize(new
        {
            protocolVersion = "1.1", source = "SIMULATOR", deviceId = device, runId = run, sequence,
            observedAtUtc = observed.ToUniversalTime().ToString("O"), deviceStateVersion = 1, modeId = "PRODUCTION", signals
        });
    }

    private static void SetCursor(SqliteConnection database, SqliteTransaction transaction,
        string device, string run, long sequence)
    {
        using var command = database.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            INSERT INTO ingest_cursors VALUES ($device,$run,$sequence)
            ON CONFLICT(device_id) DO UPDATE SET run_id=excluded.run_id, sequence=excluded.sequence
            """;
        command.Parameters.AddWithValue("$device", device);
        command.Parameters.AddWithValue("$run", run);
        command.Parameters.AddWithValue("$sequence", sequence);
        command.ExecuteNonQuery();
    }

    private static bool ThrowsInvalidData(Action action)
    {
        try { action(); }
        catch (InvalidDataException) { return true; }
        return false;
    }

}
