using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Aegis.Contracts;

namespace Aegis.Gateway;

public static partial class WireValidation
{
    public static readonly IReadOnlyDictionary<string, string> Units = new System.Collections.ObjectModel.ReadOnlyDictionary<string, string>(new Dictionary<string, string>(StringComparer.Ordinal)
    { ["setpointFlowLpm"] = "L/min", ["actualFlowLpm"] = "L/min", ["supplyPressureKpa"] = "kPa", ["valveCommandPct"] = "%", ["valveFeedbackPct"] = "%", ["motorCurrentAmp"] = "A", ["temperatureC"] = "degC", ["cycleCount"] = "count" });
    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
    private static partial Regex IdentityPattern();
    [GeneratedRegex(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")]
    private static partial Regex TimePattern();
    public static string Identity(string value) => IdentityPattern().IsMatch(value) ? value : throw new InvalidDataException();
    public static DateTimeOffset Timestamp(string value) => TimePattern().IsMatch(value) && DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsed) ? parsed.ToUniversalTime() : throw new InvalidDataException();
    public static JsonArray Array(JsonNode node, int maximum)
    { if (node is not JsonArray array || array.Count > maximum || array.Any(item => item is null)) throw new InvalidDataException(); return array; }
    public static string String(JsonNode node, string field, int maximum = 128)
    { var value = node[field]?.GetValue<string>() ?? throw new InvalidDataException(); if (value.Length > maximum || value.Any(char.IsControl)) throw new InvalidDataException(); return value; }
    public static string? NullableIdentity(JsonNode node, string field) => node[field] is null ? null : Identity(String(node, field));
    public static bool ValidRunEvidence(JsonElement root,string store,string device,string run)
    {
        if(!root.TryGetProperty("runEvidence",out var evidence)||evidence.ValueKind!=JsonValueKind.Object
            ||!root.TryGetProperty("records",out var records)||records.ValueKind!=JsonValueKind.Array
            ||records.GetArrayLength()>64)return false;
        string[] fields=["state","scope","storeId","deviceId","runId","storedRowsInReceivedWindow",
            "recordedGaps","ordinalGapState","ordinalGaps","resendCount"];
        var names=evidence.EnumerateObject().Select(p=>p.Name).ToArray();
        if(names.Length!=fields.Length||!names.ToHashSet(StringComparer.Ordinal).SetEquals(fields))return false;
        if(!evidence.GetProperty("scope").ValueEquals("SELECTED_RUN_SEQUENCE_ONLY")
            ||!evidence.GetProperty("storeId").ValueEquals(store)||!evidence.GetProperty("deviceId").ValueEquals(device)
            ||!evidence.GetProperty("runId").ValueEquals(run)||!evidence.GetProperty("ordinalGapState").ValueEquals("not_assessed")
            ||evidence.GetProperty("ordinalGaps").ValueKind!=JsonValueKind.Null||evidence.GetProperty("resendCount").ValueKind!=JsonValueKind.Null)
            return false;
        var state=evidence.GetProperty("state");
        var count=evidence.GetProperty("storedRowsInReceivedWindow");
        var gaps=evidence.GetProperty("recordedGaps");
        if(state.ValueEquals("not_found")||state.ValueEquals("not_applicable"))
            return count.ValueKind==JsonValueKind.Null&&gaps.ValueKind==JsonValueKind.Null&&records.GetArrayLength()==0;
        if(!state.ValueEquals("recorded")||count.ValueKind!=JsonValueKind.Number||!count.TryGetInt32(out int matched)
            ||matched!=records.GetArrayLength()||gaps.ValueKind!=JsonValueKind.Array||gaps.GetArrayLength()>1024)return false;
        foreach(var row in records.EnumerateArray())
        {
            if(row.ValueKind!=JsonValueKind.Object||!row.TryGetProperty("deviceId",out var rowDevice)
                ||!rowDevice.ValueEquals(device)||!row.TryGetProperty("identity",out var identity)||identity.ValueKind!=JsonValueKind.Object
                ||!identity.TryGetProperty("storeId",out var rowStore)||!rowStore.ValueEquals(store)
                ||!identity.TryGetProperty("storeKind",out var storeKind)||!storeKind.ValueEquals("RAW_V1")
                ||!row.TryGetProperty("sourceMetadata",out var metadata)||metadata.ValueKind!=JsonValueKind.Object
                ||!metadata.TryGetProperty("runId",out var rowRun)||!rowRun.ValueEquals(run))return false;
        }
        foreach(var gap in gaps.EnumerateArray())
        {
            if(gap.ValueKind!=JsonValueKind.Object||gap.EnumerateObject().Count()!=3
                ||!gap.TryGetProperty("firstSequence",out var first)||!gap.TryGetProperty("lastSequence",out var last)
                ||!gap.TryGetProperty("reason",out var reason)||!reason.ValueEquals("REPLAY_OVERFLOW")
                ||!SequenceNumber(first,out long start)||!SequenceNumber(last,out long end)||start>end)return false;
        }
        return true;
    }
    private static bool SequenceNumber(JsonElement value,out long number)
    {
        number=0;
        return value.ValueKind==JsonValueKind.String&&value.GetString() is { Length:>=1 and <=19 } text
            &&long.TryParse(text,NumberStyles.None,CultureInfo.InvariantCulture,out number)
            &&number>=0&&text==number.ToString(CultureInfo.InvariantCulture);
    }
    public static JsonNode Project(JsonNode node, string[] fields)
    {
        var result = new JsonObject();
        foreach (var field in fields) { if (node.AsObject().ContainsKey(field)) result[field] = node[field]?.DeepClone(); else throw new InvalidDataException(); }
        ValidateScalars(result);
        return result;
    }
    public static JsonNode? CollectionStatus(JsonNode? node)
    {
        if (node is not JsonObject obj || obj.Count != 11) return null;
        try
        {
            var row = Project(obj, ["collectorMode", "declaredOrigin", "receiptProvenance", "lastReceivedAtUtc",
                "receivedAgeMs", "freshness", "validUntilUtc", "lastCommittedAtUtc", "pendingBatches", "workerStatus", "workerReason"]);
            if (String(row, "collectorMode") is not ("SHARED" or "ISOLATED" or "FIXTURE")
                || String(row, "declaredOrigin") != "SYNTHETIC"
                || String(row, "receiptProvenance") is not ("ACCEPTED" or "RESTORED" or "NONE")
                || String(row, "workerStatus") is not ("RUNNING" or "RESTARTING" or "FAILED" or "UNAVAILABLE" or "NOT_APPLICABLE")
                || (row["workerReason"] is not null && String(row, "workerReason") is not ("ARCHIVE_IDENTITY_MISMATCH" or "WORKER_UNAVAILABLE"))
                || row["receivedAgeMs"]?.GetValue<long>() < 0
                || row["pendingBatches"]?.GetValue<long>() is < 0 or > 4095)
                return null;
            return String(row, "freshness") switch
            {
                "CURRENT" when String(row, "receiptProvenance") == "ACCEPTED" && row["receivedAgeMs"] is not null
                    && Timestamp(String(row, "validUntilUtc")) > Timestamp(String(row, "lastReceivedAtUtc")) => row,
                "STALE" or "RESTORED" or "NO_RECEIPT" or "UNAVAILABLE"
                    when row["validUntilUtc"] is null && row["pendingBatches"] is null => row,
                _ => null
            };
        }
        catch (Exception error) when (error is InvalidDataException or InvalidOperationException or FormatException or OverflowException)
        { return null; }
    }
    private static void ValidateScalars(JsonNode? node, int depth = 0)
    {
        if (depth > 8) throw new InvalidDataException();
        if (node is JsonObject obj)
        {
            if (obj.Count > 32) throw new InvalidDataException();
            foreach (var field in obj)
            {
                if (field.Key.EndsWith("AtUtc", StringComparison.Ordinal) && field.Value is not null) Timestamp(field.Value.GetValue<string>());
                ValidateScalars(field.Value, depth + 1);
            }
        }
        else if (node is JsonArray array) { if (array.Count > 32) throw new InvalidDataException(); foreach (var child in array) ValidateScalars(child, depth + 1); }
        else if (node is JsonValue value)
        {
            if (value.TryGetValue<string>(out var text) && (text.Length > 256 || text.Any(char.IsControl))) throw new InvalidDataException();
            if (value.GetValueKind() == JsonValueKind.Number && (!value.TryGetValue<double>(out var number) || !double.IsFinite(number) || Math.Abs(number) > 1e15)) throw new InvalidDataException();
        }
    }
    public static JsonArray Observations(JsonNode node, string deviceId)
    {
        var array = Array(node, 8);
        var observations = ContractJson.Deserialize<Observation[]>(array.ToJsonString());
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var observation in observations)
        {
            var json = JsonSerializer.SerializeToNode(observation, ContractJson.Options)!;
            var signal = String(json, "signalKey");
            if (observation.DeviceId != deviceId || !Units.TryGetValue(signal, out var unit) || observation.Unit != unit
                || !seen.Add(signal) || String(json, "observationId", 256).Length == 0 || observation.Value is { } number && Math.Abs(number) > 1e15) throw new InvalidDataException();
        }
        return array.DeepClone().AsArray();
    }
}
