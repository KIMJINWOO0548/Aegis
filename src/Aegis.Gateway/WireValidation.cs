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
    public static JsonNode Project(JsonNode node, string[] fields)
    {
        var result = new JsonObject();
        foreach (var field in fields) { if (node.AsObject().ContainsKey(field)) result[field] = node[field]?.DeepClone(); else throw new InvalidDataException(); }
        ValidateScalars(result);
        return result;
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
