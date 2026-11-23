using System.Buffers.Binary;
using System.Text.RegularExpressions;
using Aegis.Contracts;
using Microsoft.Data.Sqlite;

namespace Aegis.Gateway;

public sealed partial class ProductWorkspace
{
    private readonly string? _stateDirectory;
    private readonly string? _connectionString;
    private readonly Dictionary<string, WarningLifecycle> _captured = new(StringComparer.Ordinal);
    private readonly object _captureGate = new();
    private readonly object _initializationGate = new();
    private const long ApplicationId = 0x41454750; // AEGP
    private readonly HashSet<string> _evidenceSaved = new(StringComparer.Ordinal);
    private string _captureStatus = "READY";
    private const string ExistingRule = "ewma-flow-dev-1";

    public ProductWorkspace(IConfiguration config, GatewaySettings settings)
    {
        var state = config["AEGIS_PRODUCT_STATE_DIRECTORY"];
        if (string.IsNullOrEmpty(state) || !Path.IsPathFullyQualified(state) ||
            string.IsNullOrEmpty(settings.ArchiveDirectory)) return;
        var directory = Path.GetFullPath(state);
        var archive = Path.TrimEndingDirectorySeparator(Path.GetFullPath(settings.ArchiveDirectory));
        var name = Path.GetFileName(archive);
        if (!SessionId(name) || !string.Equals(Path.GetDirectoryName(archive),
                Path.Combine(directory, "runs"), OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
            return;

        CurrentSessionId = name;
        _stateDirectory = directory;
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = Path.Combine(directory, "workspace.sqlite3"),
            Mode = SqliteOpenMode.ReadWrite,
            DefaultTimeout = 1,
            Pooling = false,
        }.ToString();
    }

    public string? CurrentSessionId { get; }
    public bool Enabled => _connectionString is not null;
    internal string StatusForDiagnostics => !Enabled ? "NOT_CONFIGURED" :
        Volatile.Read(ref _captureStatus) == "READY" ? "READY" : "UNAVAILABLE";

    [GeneratedRegex(@"\Arun-[a-z0-9_]{8}\z", RegexOptions.CultureInvariant)]
    private static partial Regex SessionPattern();
    public static bool SessionId(string value) => SessionPattern().IsMatch(value);

    private SqliteConnection Open()
    {
        if (_connectionString is null || _stateDirectory is null)
            throw new InvalidOperationException("Product workspace is not configured.");
        var database = Path.Combine(_stateDirectory, "workspace.sqlite3");
        if ((File.GetAttributes(_stateDirectory) & FileAttributes.ReparsePoint) != 0 ||
            File.Exists(database) && (File.GetAttributes(database) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("Product workspace requires local files.");
        lock (_initializationGate)
        {
            if (!File.Exists(database)) Initialize(database);
        }
        using (var header = File.Open(database, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        {
            Span<byte> bytes = stackalloc byte[72];
            header.ReadExactly(bytes);
            if (!bytes[..16].SequenceEqual("SQLite format 3\0"u8) ||
                BinaryPrimitives.ReadUInt32BigEndian(bytes[68..]) != ApplicationId)
                throw new IOException("The existing database is not an Aegis product workspace.");
        }
        var connection = new SqliteConnection(_connectionString);
        try
        {
            connection.Open();
            using var command = connection.CreateCommand();
            command.CommandText = "PRAGMA application_id";
            if (Convert.ToInt64(command.ExecuteScalar(), System.Globalization.CultureInfo.InvariantCulture) != ApplicationId)
                throw new IOException("The existing database is not an Aegis product workspace.");
            command.CommandText = "PRAGMA user_version";
            if (Convert.ToInt64(command.ExecuteScalar(), System.Globalization.CultureInfo.InvariantCulture) != 1)
                throw new IOException("The product workspace version is unsupported.");
            return connection;
        }
        catch
        {
            connection.Dispose();
            throw;
        }
    }

    private static void Initialize(string database)
    {
        var stage = Path.Combine(Path.GetDirectoryName(database)!, $".aegis-workspace-{Guid.NewGuid():N}.tmp");
        var ownsStage = false;
        try
        {
            using (var stream = File.Open(stage, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                ownsStage = true;
            var settings = new SqliteConnectionStringBuilder
                { DataSource = stage, Pooling = false, Mode = SqliteOpenMode.ReadWrite };
            using (var connection = new SqliteConnection(settings.ToString()))
            {
                connection.Open();
                using var transaction = connection.BeginTransaction();
                using var command = connection.CreateCommand();
                command.Transaction = transaction;
                command.CommandText = $"""
                    PRAGMA application_id={ApplicationId};
                    PRAGMA user_version=1;
                    CREATE TABLE incidents (
                        id TEXT PRIMARY KEY, device TEXT NOT NULL, state TEXT NOT NULL,
                        created TEXT NOT NULL, updated TEXT NOT NULL, recommendation TEXT NOT NULL,
                        session TEXT NOT NULL, reviewed TEXT
                    );
                    CREATE TABLE incident_evidence (
                        incident TEXT PRIMARY KEY, reason TEXT, model TEXT, window_start TEXT, window_end TEXT
                    );
                    CREATE TABLE analysis_profiles (
                        device TEXT PRIMARY KEY, mode TEXT NOT NULL, model TEXT,
                        description TEXT NOT NULL, historical_session TEXT, version INTEGER NOT NULL
                    );
                    """;
                command.ExecuteNonQuery();
                transaction.Commit();
            }
            try { File.Move(stage, database); }
            catch (IOException) when (File.Exists(database)) { } // Verify the winning file on open.
        }
        finally { if (ownsStage) File.Delete(stage); }
    }

    public void CaptureSafely(IReadOnlyList<Warning> warnings)
    {
        try
        {
            Capture(warnings);
            if (warnings.Count > 0) Volatile.Write(ref _captureStatus, "READY");
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or SqliteException)
        {
            // Live samples and the original archive remain independent of the incident journal.
            Volatile.Write(ref _captureStatus, "INCIDENT_STORAGE_UNAVAILABLE");
        }
    }

    public void Capture(IReadOnlyList<Warning> warnings)
    {
        if (!Enabled || warnings.Count == 0) return;
        lock (_captureGate)
        {
            var changed = warnings.Where(warning => !_captured.TryGetValue(warning.Id, out var previous)
                || warning.State != previous).ToArray();
            if (changed.Length == 0) return;
            using var connection = Open();
            using var transaction = connection.BeginTransaction();
            foreach (var warning in changed)
            {
                using var command = connection.CreateCommand();
                command.Transaction = transaction;
                command.CommandText = """
                    INSERT INTO incidents(id,device,state,created,updated,recommendation,session)
                    VALUES($id,$device,$state,$created,$updated,$recommendation,$session)
                    ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated=excluded.updated
                    WHERE incidents.device=excluded.device AND incidents.session=excluded.session
                      AND incidents.state!=excluded.state AND incidents.state!='RESOLVED'
                    """;
                command.Parameters.AddWithValue("$id", warning.Id);
                command.Parameters.AddWithValue("$device", warning.DeviceId);
                command.Parameters.AddWithValue("$state", warning.State.ToString());
                command.Parameters.AddWithValue("$created", warning.CreatedAtUtc.UtcDateTime.ToString("O"));
                command.Parameters.AddWithValue("$updated", DateTime.UtcNow.ToString("O"));
                command.Parameters.AddWithValue("$recommendation", warning.RecommendationId);
                command.Parameters.AddWithValue("$session", CurrentSessionId!);
                command.ExecuteNonQuery();
            }
            transaction.Commit();
            _captured.Clear();
            foreach (var warning in warnings) _captured[warning.Id] = warning.State;
        }
    }

    public Warning? PendingEvidence(IReadOnlyList<Warning> warnings)
    {
        if (!Enabled) return null;
        if (_evidenceSaved.Count >= 2048)
            _evidenceSaved.IntersectWith(warnings.Select(warning => warning.Id));
        return warnings.FirstOrDefault(warning => warning.RecommendationId == "inspect-flow-tracking"
            && !_evidenceSaved.Contains(warning.Id));
    }

    public void RecordEvidence(Warning warning, WarningTimeline timeline)
    {
        if (!Enabled || timeline.WarningId != warning.Id) return;
        var first = timeline.Events.FirstOrDefault(evt => evt.WarningId == warning.Id && evt.DeviceId == warning.DeviceId
            && evt.EventName == EventName.EWMA_WARNING_RAISED);
        if (first is null || first.DataWindowStartUtc is not { } from || first.DataWindowEndUtc is not { } to ||
            from >= to || to - from > TimeSpan.FromHours(1)) return;
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO incident_evidence(incident,reason,model,window_start,window_end)
            VALUES($id,$reason,$model,$from,$to) ON CONFLICT(incident) DO NOTHING
            """;
        command.Parameters.AddWithValue("$id", warning.Id);
        command.Parameters.AddWithValue("$reason", (object?)first.ReasonCode ?? DBNull.Value);
        command.Parameters.AddWithValue("$model", (object?)first.ModelVersion ?? DBNull.Value);
        command.Parameters.AddWithValue("$from", from.UtcDateTime.ToString("O"));
        command.Parameters.AddWithValue("$to", to.UtcDateTime.ToString("O"));
        command.ExecuteNonQuery();
        _evidenceSaved.Add(warning.Id);
    }

    public object Incidents(string? deviceId)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id,device,state,created,updated,recommendation,session,reviewed,
                   evidence.reason,evidence.model,evidence.window_start,evidence.window_end
            FROM incidents LEFT JOIN incident_evidence evidence ON evidence.incident=incidents.id
            WHERE $device IS NULL OR device=$device
            ORDER BY created DESC, id LIMIT 101
            """;
        command.Parameters.AddWithValue("$device", (object?)deviceId ?? DBNull.Value);
        using var reader = command.ExecuteReader();
        var items = new List<object>(100);
        var hasMore = false;
        while (reader.Read())
        {
            if (items.Count == 100) { hasMore = true; break; }
            var recommendation = reader.GetString(5);
            var isFlowRule = recommendation == "inspect-flow-tracking";
            items.Add(new
            {
                id = reader.GetString(0), deviceId = reader.GetString(1), state = reader.GetString(2),
                createdAtUtc = reader.GetString(3), updatedAtUtc = reader.GetString(4), sessionId = reader.GetString(6),
                reviewedAtUtc = reader.IsDBNull(7) ? null : reader.GetString(7),
                modelVersion = reader.IsDBNull(9) ? (isFlowRule ? ExistingRule : null) : reader.GetString(9),
                details = new
                {
                    recommendationId = recommendation,
                    reasonCode = reader.IsDBNull(8) ? null : reader.GetString(8),
                    windowStartUtc = reader.IsDBNull(10) ? null : reader.GetString(10),
                    windowEndUtc = reader.IsDBNull(11) ? null : reader.GetString(11),
                    evidenceSignalKeys = isFlowRule ? new[] { "actualFlowLpm", "setpointFlowLpm" } : Array.Empty<string>(),
                },
            });
        }
        return new { schemaVersion = "incidents-1", currentSessionId = CurrentSessionId, incidents = items, hasMore, captureState = Volatile.Read(ref _captureStatus) };
    }

    public string? Review(string incidentId)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE incidents SET reviewed=COALESCE(reviewed,$at) WHERE id=$id RETURNING reviewed
            """;
        command.Parameters.AddWithValue("$id", incidentId);
        command.Parameters.AddWithValue("$at", DateTime.UtcNow.ToString("O"));
        return command.ExecuteScalar() as string;
    }

    public AnalysisProfile Profile(string deviceId)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT mode,model,description,historical_session,version FROM analysis_profiles WHERE device=$device";
        command.Parameters.AddWithValue("$device", deviceId);
        using var reader = command.ExecuteReader();
        return reader.Read() ? new(deviceId, reader.GetString(0), reader.IsDBNull(1) ? null : NormalizeModelId(reader.GetString(1)),
            reader.GetString(2), reader.IsDBNull(3) ? null : reader.GetString(3), reader.GetInt32(4))
            : new(deviceId, "AUTO", null, "", null, 0);
    }

    private static string NormalizeModelId(string modelId) => modelId == "ewma-demo-1" ? ExistingRule : modelId;

    public bool Save(AnalysisProfile profile)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = profile.Version == 0
            ? """
                INSERT INTO analysis_profiles(device,mode,model,description,historical_session,version)
                VALUES($device,$mode,$model,$description,$history,1) ON CONFLICT(device) DO NOTHING
                """
            : """
                UPDATE analysis_profiles SET mode=$mode, model=$model, description=$description,
                historical_session=$history, version=version+1 WHERE device=$device AND version=$version
                """;
        command.Parameters.AddWithValue("$device", profile.DeviceId);
        command.Parameters.AddWithValue("$mode", profile.Mode);
        command.Parameters.AddWithValue("$model", (object?)profile.ModelId ?? DBNull.Value);
        command.Parameters.AddWithValue("$description", profile.Description);
        command.Parameters.AddWithValue("$history", (object?)profile.HistorySessionId ?? DBNull.Value);
        if (profile.Version != 0) command.Parameters.AddWithValue("$version", profile.Version);
        return command.ExecuteNonQuery() == 1;
    }

    public static object Describe(AnalysisProfile profile, bool ruleRunning) => new
    {
        schemaVersion = "analysis-profile-1",
        profile,
        active = new { modelId = ruleRunning ? ExistingRule : null, state = ruleRunning ? "DEVELOPMENT_RULE" : "UNAVAILABLE" },
        recommendation = new
        {
            modelId = ruleRunning ? ExistingRule : null,
            state = ruleRunning ? "RULE_AVAILABLE" : "NO_QUALIFIED_MODEL",
            reasonCode = ruleRunning ? "REGISTERED_DEVELOPMENT_RULE_ONLY" : "NO_COMPATIBLE_RULE_AVAILABLE",
        },
    };
}

public sealed record AnalysisProfile(string DeviceId, string Mode, string? ModelId, string Description,
    string? HistorySessionId, int Version);
