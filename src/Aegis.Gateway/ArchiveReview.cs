using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Aegis.SignalProcessing;
using Microsoft.Data.Sqlite;

namespace Aegis.Gateway;

public sealed record NativeReview(FlowReviewSnapshot? Review, string EngineState, long? NativeSequence);

public sealed class ArchiveReview(GatewaySettings settings)
{
    private sealed class Cursor(string device)
    {
        public SignalReviewEngine Engine { get; } = new(device);
        public long RowId;
        public long? Sequence;
        public DateTimeOffset Observed;
        public DateTimeOffset Received;
    }
    private readonly Dictionary<string, Cursor> _cursors = new(StringComparer.Ordinal);
    public FlowReviewSnapshot? Snapshot(string deviceId) => _cursors.TryGetValue(deviceId, out var cursor) ? cursor.Engine.Snapshot() : null;
    public static string FileName(string deviceId) => $"module-{deviceId}-{Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(deviceId))).ToLowerInvariant()[..12]}.sqlite3";
    public NativeReview Read(string deviceId, string? equipmentId, string? moduleId)
    {
        if (string.IsNullOrWhiteSpace(settings.ArchiveDirectory)) return new(null, "ARCHIVE_NOT_CONFIGURED", null);
        WireValidation.Identity(deviceId);
        if (!_cursors.TryGetValue(deviceId, out var cursor))
        {
            if (_cursors.Count >= 32) return new(null, "UNAVAILABLE", null);
            _cursors[deviceId] = cursor = new(deviceId);
        }
        try
        {
            var path = Path.Combine(Path.GetFullPath(settings.ArchiveDirectory), FileName(deviceId));
            using var database = new SqliteConnection(new SqliteConnectionStringBuilder
            { DataSource = path, Mode = SqliteOpenMode.ReadOnly, Cache = SqliteCacheMode.Private, Pooling = false, DefaultTimeout = 1 }.ToString());
            database.Open();
            using (var version = database.CreateCommand())
            { version.CommandText = "PRAGMA user_version"; if (Convert.ToInt64(version.ExecuteScalar()) != 1) throw new InvalidDataException(); }
            var caughtUp = false;
            for (var page = 0; page < 4; page++)
            {
                using var command = database.CreateCommand();
                command.CommandText = "SELECT rowid,device_id,run_id,sequence,observed_at_utc,received_at_utc,equipment_id,module_id,payload_json FROM raw_batches WHERE rowid > $cursor ORDER BY rowid LIMIT 64";
                command.Parameters.AddWithValue("$cursor", cursor.RowId);
                using var rows = command.ExecuteReader();
                var count = 0;
                while (rows.Read())
                {
                    if (rows.GetString(1) != deviceId || rows.GetInt64(3) < 0 || Encoding.UTF8.GetByteCount(rows.GetString(8)) > 1048576
                        || (rows.IsDBNull(6) ? null : rows.GetString(6)) != equipmentId || (rows.IsDBNull(7) ? null : rows.GetString(7)) != moduleId) throw new InvalidDataException();
                    var observed = WireValidation.Timestamp(rows.GetString(4));
                    var received = WireValidation.Timestamp(rows.GetString(5));
                    var payload = rows.GetString(8);
                    using var document = JsonDocument.Parse(payload, new() { MaxDepth = 16 });
                    var root = document.RootElement;
                    if (root.GetProperty("deviceId").GetString() != deviceId || root.GetProperty("runId").GetString() != rows.GetString(2)
                        || root.GetProperty("sequence").GetInt64() != rows.GetInt64(3)
                        || WireValidation.Timestamp(root.GetProperty("observedAtUtc").GetString()!) != observed) throw new InvalidDataException();
                    cursor.Engine.AcceptRawJson(payload);
                    cursor.RowId = rows.GetInt64(0);
                    cursor.Sequence = rows.GetInt64(3);
                    cursor.Observed = observed;
                    cursor.Received = received;
                    count++;
                }
                if (count < 64) { caughtUp = true; break; }
            }
            // A full final page may have reached the end; inspect one scalar rather than skip raw evidence.
            if (!caughtUp)
            {
                using var pending = database.CreateCommand();
                pending.CommandText = "SELECT EXISTS(SELECT 1 FROM raw_batches WHERE rowid > $cursor)";
                pending.Parameters.AddWithValue("$cursor", cursor.RowId);
                caughtUp = Convert.ToInt64(pending.ExecuteScalar()) == 0;
            }
            var now = DateTimeOffset.UtcNow;
            var current = caughtUp && cursor.Sequence is not null && now - cursor.Observed <= TimeSpan.FromSeconds(3)
                && now - cursor.Received <= TimeSpan.FromSeconds(3) && cursor.Observed <= now.AddSeconds(1) && cursor.Received <= now.AddSeconds(1);
            // Catch-up is historical replay, so keep its analysis context across bounded pages.
            if (!current && caughtUp) cursor.Engine.MarkUnavailable();
            var snapshot = cursor.Engine.Snapshot();
            return new(current ? snapshot : snapshot with { State = "UNAVAILABLE" }, current ? "RUNNING" : "UNAVAILABLE", cursor.Sequence);
        }
        catch (Exception error) when (error is SqliteException or IOException or UnauthorizedAccessException or JsonException or InvalidDataException or ArgumentException or InvalidOperationException or KeyNotFoundException or FormatException)
        { cursor.Engine.MarkUnavailable(); return new(cursor.Engine.Snapshot(), "UNAVAILABLE", cursor.Sequence); }
    }
    public void Retain(ISet<string> devices)
    { foreach (var key in _cursors.Keys.Where(key => !devices.Contains(key)).ToArray()) _cursors.Remove(key); }
    public void Unavailable() { foreach (var cursor in _cursors.Values) cursor.Engine.MarkUnavailable(); }
}
