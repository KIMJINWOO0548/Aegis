using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;

namespace Aegis.Gateway;

internal sealed record NativeArchivePage(string? RunId, long Watermark,
    IReadOnlyList<NativeArchiveFrame> Frames, bool CaughtUp);

internal sealed record NativeArchiveFrame(long RowId, long Sequence, DateTimeOffset ObservedAtUtc,
    DateTimeOffset ReceivedAtUtc, double ActualFlowLpm, double SetpointFlowLpm, uint Quality);

internal sealed class NativeArchiveReader(string archiveDirectory, string deviceId, string? equipmentId, string? moduleId)
{
    private const int MaximumPayloadBytes = 1_048_576;
    private const int PageSize = 64;
    private const int SeedSize = 32;
    private static readonly TimeSpan MaximumSeedGap = TimeSpan.FromMilliseconds(1500);
    private static readonly Regex SignalKeyPattern = new(@"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z", RegexOptions.CultureInvariant);
    private static readonly HashSet<string> Modes = ["PRODUCTION", "SETUP", "MAINTENANCE"];

    private static readonly TableSchema RawBatches = new("raw_batches",
        ["device_id", "run_id", "sequence", "observed_at_utc", "received_at_utc", "equipment_id", "module_id", "payload_json"],
        ["TEXT", "TEXT", "INTEGER", "TEXT", "TEXT", "TEXT", "TEXT", "TEXT"],
        [1, 2, 3, 0, 0, 0, 0, 0], [null, null, null, null, null, null, null, null]);
    private static readonly TableSchema IngestCursors = new("ingest_cursors",
        ["device_id", "run_id", "sequence"], ["TEXT", "TEXT", "INTEGER"], [1, 0, 0], [null, null, null]);
    private static readonly TableSchema IngestGaps = new("ingest_gaps",
        ["device_id", "run_id", "first_sequence", "last_sequence", "reason"],
        ["TEXT", "TEXT", "INTEGER", "INTEGER", "TEXT"], [0, 0, 0, 0, 0],
        [null, null, null, null, "'REPLAY_OVERFLOW'"]);

    private readonly string _path = Path.Combine(Path.GetFullPath(archiveDirectory), ArchiveReview.FileName(WireValidation.Identity(deviceId)));
    private readonly string? _equipmentId = OptionalIdentity(equipmentId);
    private readonly string? _moduleId = OptionalIdentity(moduleId);

    public NativeArchivePage ReadSeed() => ReadSnapshot(afterRowId: 0, seed: true);

    public NativeArchivePage ReadForward(long afterRowId)
    {
        if (afterRowId < 0) throw new ArgumentOutOfRangeException(nameof(afterRowId));
        return ReadSnapshot(afterRowId, seed: false);
    }

    private NativeArchivePage ReadSnapshot(long afterRowId, bool seed)
    {
        using var database = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = _path,
            Mode = SqliteOpenMode.ReadOnly,
            Cache = SqliteCacheMode.Private,
            Pooling = false,
            DefaultTimeout = 1
        }.ToString());
        database.Open();

        // The cursor and raw upper rowid must come from the same WAL snapshot. This
        // deferred transaction remains read-only because the connection is read-only.
        using var transaction = database.BeginTransaction(deferred: true);
        ValidateSchema(database, transaction);
        var cursor = ReadCursor(database, transaction);
        var upperRowId = ReadUpperRowId(database, transaction);

        NativeArchivePage page;
        if (seed)
        {
            var frames = cursor is null
                ? Array.Empty<NativeArchiveFrame>()
                : ReadSeedFrames(database, transaction, cursor, upperRowId);
            page = new(cursor?.RunId, upperRowId, frames, CaughtUp: true);
        }
        else if (cursor is null)
        {
            page = new(null, Math.Max(afterRowId, upperRowId), Array.Empty<NativeArchiveFrame>(), CaughtUp: true);
        }
        else
        {
            page = ReadForwardFrames(database, transaction, cursor, afterRowId, upperRowId);
        }

        transaction.Commit();
        return page;
    }

    private NativeArchiveFrame[] ReadSeedFrames(SqliteConnection database, SqliteTransaction transaction,
        ArchiveCursor cursor, long upperRowId)
    {
        using var command = CreateRowsCommand(database, transaction, cursor, rowId: null, upperRowId);
        command.CommandText += " ORDER BY rowid DESC LIMIT 64";
        using var reader = command.ExecuteReader();
        var suffix = new List<NativeArchiveFrame>(SeedSize);
        while (reader.Read())
        {
            var frame = ReadFrame(reader, cursor.RunId);
            // A seed is a usable suffix ending at the newest committed row. Do not
            // reach behind a missing/bad sample and present older data as current.
            if (frame.Quality != 0) break;
            if (suffix.Count > 0 && !IsContinuous(frame, suffix[^1])) break;
            suffix.Add(frame);
            if (suffix.Count == SeedSize) break;
        }
        suffix.Reverse();
        return suffix.ToArray();
    }

    private NativeArchivePage ReadForwardFrames(SqliteConnection database, SqliteTransaction transaction,
        ArchiveCursor cursor, long afterRowId, long upperRowId)
    {
        var rows = new List<NativeArchiveFrame>(PageSize);
        if (upperRowId > afterRowId)
        {
            using var command = CreateRowsCommand(database, transaction, cursor, afterRowId, upperRowId);
            command.CommandText += " ORDER BY rowid LIMIT 64";
            using var reader = command.ExecuteReader();
            while (reader.Read()) rows.Add(ReadFrame(reader, cursor.RunId));
        }

        var caughtUp = true;
        if (rows.Count == PageSize)
        {
            using var pending = database.CreateCommand();
            pending.Transaction = transaction;
            pending.CommandText = """
                SELECT EXISTS(SELECT 1 FROM raw_batches
                WHERE rowid > $after AND rowid <= $upper AND device_id = $device AND run_id = $run
                    AND sequence <= $sequence AND equipment_id IS $equipment AND module_id IS $module)
                """;
            AddCursorParameters(pending, cursor);
            pending.Parameters.AddWithValue("$after", rows[^1].RowId);
            pending.Parameters.AddWithValue("$upper", upperRowId);
            caughtUp = Convert.ToInt64(pending.ExecuteScalar()) == 0;
        }

        // When this snapshot is exhausted, it is safe to skip rowids belonging to
        // other devices or inactive runs. Later commits necessarily have larger rowids.
        var watermark = caughtUp ? Math.Max(afterRowId, upperRowId) : rows[^1].RowId;
        return new(cursor.RunId, watermark, rows.ToArray(), caughtUp);
    }

    private SqliteCommand CreateRowsCommand(SqliteConnection database, SqliteTransaction transaction,
        ArchiveCursor cursor, long? rowId, long upperRowId)
    {
        var command = database.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            SELECT rowid, device_id, run_id, sequence, observed_at_utc, received_at_utc,
            equipment_id, module_id, payload_json FROM raw_batches
            WHERE rowid <= $upper AND device_id = $device AND run_id = $run AND sequence <= $sequence
                AND equipment_id IS $equipment AND module_id IS $module
            """;
        if (rowId is not null) command.CommandText += " AND rowid > $after";
        AddCursorParameters(command, cursor);
        command.Parameters.AddWithValue("$upper", upperRowId);
        if (rowId is not null) command.Parameters.AddWithValue("$after", rowId.Value);
        return command;
    }

    private void AddCursorParameters(SqliteCommand command, ArchiveCursor cursor)
    {
        command.Parameters.AddWithValue("$device", deviceId);
        command.Parameters.AddWithValue("$run", cursor.RunId);
        command.Parameters.AddWithValue("$sequence", cursor.Sequence);
        command.Parameters.AddWithValue("$equipment", (object?)_equipmentId ?? DBNull.Value);
        command.Parameters.AddWithValue("$module", (object?)_moduleId ?? DBNull.Value);
    }

    private ArchiveCursor? ReadCursor(SqliteConnection database, SqliteTransaction transaction)
    {
        using var command = database.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = "SELECT run_id, sequence FROM ingest_cursors WHERE device_id = $device";
        command.Parameters.AddWithValue("$device", deviceId);
        using var reader = command.ExecuteReader();
        if (!reader.Read()) return null;
        var runId = reader.GetString(0);
        var sequence = reader.GetInt64(1);
        if (!Guid.TryParseExact(runId, "D", out _) || sequence < 0 || reader.Read()) throw new InvalidDataException("Invalid archive ingest cursor.");
        return new(runId, sequence);
    }

    private static long ReadUpperRowId(SqliteConnection database, SqliteTransaction transaction)
    {
        using var command = database.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = "SELECT coalesce(max(rowid), 0) FROM raw_batches";
        var value = Convert.ToInt64(command.ExecuteScalar());
        if (value < 0) throw new InvalidDataException("Invalid archive rowid high-water mark.");
        return value;
    }

    private static NativeArchiveFrame ReadFrame(SqliteDataReader reader, string expectedRunId)
    {
        var rowId = reader.GetInt64(0);
        var device = reader.GetString(1);
        var runId = reader.GetString(2);
        var sequence = reader.GetInt64(3);
        var observedText = reader.GetString(4);
        var receivedText = reader.GetString(5);
        var equipment = reader.IsDBNull(6) ? null : reader.GetString(6);
        var module = reader.IsDBNull(7) ? null : reader.GetString(7);
        var payload = reader.GetString(8);
        if (rowId <= 0 || sequence < 0 || device.Length is < 1 or > 64 || runId != expectedRunId
            || equipment is { Length: > 64 } || module is { Length: > 64 }
            || equipment is not null && !IdentityIsValid(equipment) || module is not null && !IdentityIsValid(module)
            || Encoding.UTF8.GetByteCount(payload) > MaximumPayloadBytes)
            throw new InvalidDataException("Invalid archive row identity or payload size.");
        var observed = WireValidation.Timestamp(observedText);
        var received = WireValidation.Timestamp(receivedText);
        var (payloadObserved, actual, setpoint, quality) = ReadPayload(payload, device, runId, sequence);
        if (payloadObserved != observed) throw new InvalidDataException("Archive timestamp does not match its payload.");
        return new(rowId, sequence, observed, received, actual, setpoint, quality);
    }

    private static (DateTimeOffset Observed, double Actual, double Setpoint, uint Quality) ReadPayload(
        string payload, string deviceId, string runId, long sequence)
    {
        try
        {
            using var document = JsonDocument.Parse(payload, new() { MaxDepth = 16 });
            var root = document.RootElement;
            RequireFields(root, "protocolVersion", "source", "deviceId", "runId", "sequence", "observedAtUtc",
                "deviceStateVersion", "modeId", "signals");
            if (Text(root, "protocolVersion") != "1.1" || Text(root, "source") != "SIMULATOR"
                || Text(root, "deviceId") != deviceId || Text(root, "runId") != runId
                || !root.GetProperty("sequence").TryGetInt64(out var payloadSequence) || payloadSequence != sequence
                || !root.GetProperty("deviceStateVersion").TryGetInt32(out var stateVersion) || stateVersion < 0
                || !Modes.Contains(Text(root, "modeId")) || !Guid.TryParseExact(runId, "D", out _))
                throw new InvalidDataException("Archive payload identity or schema is invalid.");

            var observed = WireValidation.Timestamp(Text(root, "observedAtUtc"));
            var signals = root.GetProperty("signals");
            if (signals.ValueKind != JsonValueKind.Array || signals.GetArrayLength() is < 1 or > 512)
                throw new InvalidDataException("Archive signal list is invalid.");
            var values = new Dictionary<string, ArchiveSignal>(StringComparer.Ordinal);
            foreach (var row in signals.EnumerateArray())
            {
                RequireFields(row, "signalKey", "value", "unit", "quality");
                var key = Text(row, "signalKey");
                var unit = Text(row, "unit");
                var signalQuality = Text(row, "quality");
                if (!SignalKeyPattern.IsMatch(key) || unit.Length is < 1 or > 32
                    || unit.Any(c => c < 32 || c == 127) || signalQuality is not ("GOOD" or "BAD" or "STALE" or "MISSING"))
                    throw new InvalidDataException("Archive signal metadata is invalid.");

                var rawValue = row.GetProperty("value");
                double? value = rawValue.ValueKind switch
                {
                    JsonValueKind.Null => null,
                    JsonValueKind.Number => rawValue.GetDouble(),
                    _ => throw new InvalidDataException("Archive signal value is invalid.")
                };
                if (WireValidation.Units.TryGetValue(key, out var expectedUnit)
                    && expectedUnit != unit && key is not ("actualFlowLpm" or "setpointFlowLpm"))
                    throw new InvalidDataException("Archive signal unit is invalid.");
                if (key is not ("actualFlowLpm" or "setpointFlowLpm") && value is { } other && !double.IsFinite(other))
                    throw new InvalidDataException("Archive signal value is non-finite.");
                if (!values.TryAdd(key, new(value, unit, signalQuality)))
                    throw new InvalidDataException("Archive signal key is duplicated.");
            }

            var hasActual = values.TryGetValue("actualFlowLpm", out var actualSignal);
            var hasSetpoint = values.TryGetValue("setpointFlowLpm", out var setpointSignal);
            var quality = FlowQuality(actualSignal, hasActual, setpointSignal, hasSetpoint);
            return (observed, FiniteValue(actualSignal), FiniteValue(setpointSignal), quality);
        }
        catch (JsonException error) { throw new InvalidDataException("Archive payload JSON is invalid.", error); }
        catch (InvalidOperationException error) { throw new InvalidDataException("Archive payload shape is invalid.", error); }
        catch (FormatException error) { throw new InvalidDataException("Archive payload value is invalid.", error); }
        catch (OverflowException error) { throw new InvalidDataException("Archive payload value is invalid.", error); }
    }

    private static uint FlowQuality(ArchiveSignal? actual, bool hasActual, ArchiveSignal? setpoint, bool hasSetpoint)
    {
        if (hasActual && actual!.Quality == "MISSING" || hasSetpoint && setpoint!.Quality == "MISSING"
            || !hasActual || !hasSetpoint) return 1;
        if (IsBad(actual!, "actualFlowLpm") || IsBad(setpoint!, "setpointFlowLpm")) return 2;
        return 0;
    }

    private static bool IsBad(ArchiveSignal signal, string key) => signal.Quality is "BAD" or "STALE"
        || signal.Unit != "L/min" || signal.Value is not { } value || !double.IsFinite(value) || value is < 0 or > 100;

    private static double FiniteValue(ArchiveSignal? signal) => signal?.Value is { } value && double.IsFinite(value) ? value : 0;

    private static bool IsContinuous(NativeArchiveFrame older, NativeArchiveFrame newer) =>
        newer.Sequence > older.Sequence && newer.Sequence - older.Sequence == 1
        && newer.ObservedAtUtc > older.ObservedAtUtc
        && newer.ObservedAtUtc - older.ObservedAtUtc <= MaximumSeedGap;

    private static string Text(JsonElement parent, string name) =>
        parent.GetProperty(name).GetString() ?? throw new InvalidDataException("Expected archive string field.");

    private static void RequireFields(JsonElement value, params string[] fields)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new InvalidDataException("Expected archive object.");
        var names = value.EnumerateObject().Select(property => property.Name).ToArray();
        if (names.Length != fields.Length || names.Distinct(StringComparer.Ordinal).Count() != names.Length
            || !names.ToHashSet(StringComparer.Ordinal).SetEquals(fields))
            throw new InvalidDataException("Unexpected archive payload fields.");
    }

    private static bool IdentityIsValid(string identity)
    {
        try { WireValidation.Identity(identity); return true; }
        catch (InvalidDataException) { return false; }
    }

    private static string? OptionalIdentity(string? identity) => identity is null ? null : WireValidation.Identity(identity);

    private static void ValidateSchema(SqliteConnection database, SqliteTransaction transaction)
    {
        using (var version = database.CreateCommand())
        {
            version.Transaction = transaction;
            version.CommandText = "PRAGMA user_version";
            if (Convert.ToInt64(version.ExecuteScalar()) != 1) throw new InvalidDataException("Unsupported archive schema version.");
        }

        using (var tables = database.CreateCommand())
        {
            tables.Transaction = transaction;
            tables.CommandText = "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'";
            using var reader = tables.ExecuteReader();
            var names = new HashSet<string>(StringComparer.Ordinal);
            while (reader.Read()) names.Add(reader.GetString(0));
            if (!names.SetEquals([RawBatches.Name, IngestCursors.Name, IngestGaps.Name]))
                throw new InvalidDataException("Unknown archive table schema.");
        }

        ValidateTable(database, transaction, RawBatches);
        ValidateTable(database, transaction, IngestCursors);
        ValidateTable(database, transaction, IngestGaps);
        ValidateGapIndex(database, transaction);

        using var rowId = database.CreateCommand();
        rowId.Transaction = transaction;
        rowId.CommandText = "SELECT rowid FROM raw_batches LIMIT 0";
        using var rowIdReader = rowId.ExecuteReader();
    }

    private static void ValidateTable(SqliteConnection database, SqliteTransaction transaction, TableSchema schema)
    {
        using var command = database.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = $"PRAGMA table_info({schema.Name})";
        using var reader = command.ExecuteReader();
        var index = 0;
        while (reader.Read())
        {
            var nullable = schema.Name == RawBatches.Name && reader.GetString(1) is "equipment_id" or "module_id";
            if (index >= schema.Columns.Length || reader.GetInt32(0) != index
                || reader.GetString(1) != schema.Columns[index] || reader.GetString(2).ToUpperInvariant() != schema.Types[index]
                || reader.GetInt32(3) != (nullable ? 0 : 1) || !DefaultMatches(reader, 4, schema.Defaults[index])
                || reader.GetInt32(5) != schema.PrimaryKeys[index])
                throw new InvalidDataException("Unknown archive table schema.");
            index++;
        }
        if (index != schema.Columns.Length) throw new InvalidDataException("Unknown archive table schema.");
    }

    private static void ValidateGapIndex(SqliteConnection database, SqliteTransaction transaction)
    {
        var uniqueIndexes = new List<string>();
        using (var indexes = database.CreateCommand())
        {
            indexes.Transaction = transaction;
            indexes.CommandText = "PRAGMA index_list(ingest_gaps)";
            using var reader = indexes.ExecuteReader();
            while (reader.Read())
                if (reader.GetInt32(2) == 1 && reader.FieldCount >= 5 && reader.GetInt32(4) == 0)
                    uniqueIndexes.Add(reader.GetString(1));
        }
        var valid = false;
        foreach (var index in uniqueIndexes)
        {
            using var fields = database.CreateCommand();
            fields.Transaction = transaction;
            fields.CommandText = "SELECT name FROM pragma_index_info($index) ORDER BY seqno";
            fields.Parameters.AddWithValue("$index", index);
            using var fieldReader = fields.ExecuteReader();
            var names = new List<string>();
            while (fieldReader.Read()) names.Add(fieldReader.GetString(0));
            if (names.SequenceEqual(["device_id", "run_id", "first_sequence", "last_sequence"], StringComparer.Ordinal))
                valid = true;
        }
        if (!valid) throw new InvalidDataException("Unknown archive gap schema.");
    }

    private sealed record ArchiveCursor(string RunId, long Sequence);
    private sealed record ArchiveSignal(double? Value, string Unit, string Quality);
    private sealed record TableSchema(string Name, string[] Columns, string[] Types, int[] PrimaryKeys, string?[] Defaults);

    private static bool DefaultMatches(SqliteDataReader reader, int ordinal, string? expected) => expected is null
        ? reader.IsDBNull(ordinal) : !reader.IsDBNull(ordinal) && reader.GetString(ordinal) == expected;
}
