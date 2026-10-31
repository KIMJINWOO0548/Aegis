using System.Collections;
using System.Globalization;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace Aegis.Contracts;

public static class ContractJson
{
    public static JsonSerializerOptions Options { get; } = CreateOptions();

    private static JsonSerializerOptions CreateOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
            RespectNullableAnnotations = true
        };
        options.Converters.Add(new UtcTimestampConverter());
        options.Converters.Add(new StrictEnumConverterFactory());
        return options;
    }

    public static T Deserialize<T>(string json)
    {
        var value = JsonSerializer.Deserialize<T>(json, Options) ?? throw new JsonException("A contract must not be null.");
        ValidateRoot(value);
        return value;
    }

    public static string Serialize<T>(T value)
    {
        ValidateRoot(value);
        return JsonSerializer.Serialize(value, Options);
    }

    private static void ValidateRoot(object? value)
    {
        if (value is null) throw new JsonException("A contract must not be null.");
        if (value is ContractModel model) model.Validate();
        if (value is IEnumerable<ContractModel?> models)
            foreach (var item in models)
                (item ?? throw new JsonException("Contract arrays cannot contain null items.")).Validate();
    }
}

internal sealed class UtcTimestampConverter : JsonConverter<DateTimeOffset>
{
    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.String) throw new JsonException("Timestamp must be an ISO 8601 string.");
        var value = reader.GetString()!;
        if (!Regex.IsMatch(value, @"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$") ||
            !DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out var timestamp))
            throw new JsonException("Timestamp requires an explicit UTC offset.");
        return timestamp.ToUniversalTime();
    }

    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options) =>
        writer.WriteStringValue(value.UtcDateTime);
}

internal sealed class StrictEnumConverterFactory : JsonConverterFactory
{
    public override bool CanConvert(Type type) => type.IsEnum;
    public override JsonConverter CreateConverter(Type type, JsonSerializerOptions options) =>
        (JsonConverter)Activator.CreateInstance(typeof(StrictEnumConverter<>).MakeGenericType(type), nonPublic: true)!;
}

internal sealed class StrictEnumConverter<T> : JsonConverter<T> where T : struct, Enum
{
    private static readonly Dictionary<string, T> Values = Enum.GetValues<T>().ToDictionary(
        value => typeof(T).GetField(value.ToString())!.GetCustomAttribute<JsonStringEnumMemberNameAttribute>()?.Name ?? value.ToString(),
        value => value, StringComparer.Ordinal);

    public override T Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.String && Values.TryGetValue(reader.GetString()!, out var value)) return value;
        throw new JsonException($"Invalid {typeof(T).Name}.");
    }

    public override void Write(Utf8JsonWriter writer, T value, JsonSerializerOptions options)
    {
        foreach (var pair in Values)
            if (EqualityComparer<T>.Default.Equals(pair.Value, value)) { writer.WriteStringValue(pair.Key); return; }
        throw new JsonException($"Invalid {typeof(T).Name}.");
    }
}

public abstract class ContractModel : IJsonOnDeserialized
{
    void IJsonOnDeserialized.OnDeserialized() => Validate();

    public virtual void Validate()
    {
        foreach (var property in GetType().GetProperties())
        {
            var value = property.GetValue(this);
            if (value is null)
            {
                if (new NullabilityInfoContext().Create(property).ReadState == NullabilityState.NotNull)
                    throw new JsonException($"{property.Name} must not be null.");
                continue;
            }
            if (property.Name == "SchemaVersion" && !Equals(value, Contract.Version))
                throw new JsonException("Unsupported schemaVersion.");
            if (value is double number && !double.IsFinite(number)) throw new JsonException("Numbers must be finite.");
            if (value is ContractModel child) child.Validate();
            if (value is IEnumerable items && value is not string)
                foreach (var item in items)
                {
                    if (item is null) throw new JsonException("Contract arrays cannot contain null items.");
                    if (item is ContractModel childItem) childItem.Validate();
                }
        }
    }
}

public sealed class Observation : ContractModel
{
    public required string SchemaVersion { get; init; }
    public required string ObservationId { get; init; }
    public required string DeviceId { get; init; }
    public required SignalKey SignalKey { get; init; }
    public required double? Value { get; init; }
    public required string Unit { get; init; }
    public required DateTimeOffset ObservedAtUtc { get; init; }
    public required DateTimeOffset ReceivedAtUtc { get; init; }
    public required ObservationQuality Quality { get; init; }
    public required int DeviceStateVersion { get; init; }
    public required ModeId ModeId { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (Quality == ObservationQuality.GOOD && Value is null) throw new JsonException("GOOD observations require a value.");
        if (DeviceStateVersion < 0) throw new JsonException("deviceStateVersion must be nonnegative.");
    }
}

public sealed class DeviceHealth : ContractModel
{
    public required string SchemaVersion { get; init; }
    public required string DeviceId { get; init; }
    public required ModelLifecycle ModelLifecycle { get; init; }
    public required ObservationEvaluation ObservationEvaluation { get; init; }
    public required string? ModelVersion { get; init; }
    public required DateTimeOffset WindowEndUtc { get; init; }
    public required List<SignalKey> EvidenceSignalKeys { get; init; }
    public required ObservationQuality DataQuality { get; init; }
    public double? NormalReferencePercentile { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (NormalReferencePercentile is < 0 or > 100 ||
            (ObservationEvaluation == ObservationEvaluation.INSUFFICIENT_DATA && NormalReferencePercentile is not null))
            throw new JsonException("Invalid normal-reference score.");
    }
}

public sealed class Warning : ContractModel
{
    public required string Id { get; init; }
    public required string DeviceId { get; init; }
    public required WarningLifecycle State { get; init; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
    public required string RecommendationId { get; init; }
    public DateTimeOffset? AcknowledgedAtUtc { get; init; }
}

public sealed class ApiError : ContractModel
{
    public required string Code { get; init; }
    public required string Message { get; init; }
    public required string RequestId { get; init; }
    public Dictionary<string, JsonElement>? Details { get; init; }
}

public sealed class StructuredEvent : ContractModel
{
    public required string SchemaVersion { get; init; }
    public required string EventId { get; init; }
    public required DateTimeOffset OccurredAtUtc { get; init; }
    public required Component Component { get; init; }
    public required EventName EventName { get; init; }
    public required Severity Severity { get; init; }
    public required string TraceId { get; init; }
    public required Outcome Outcome { get; init; }
    public string? DeviceId { get; init; }
    public string? WarningId { get; init; }
    public string? IncidentId { get; init; }
    public string? RequestId { get; init; }
    public string? RunId { get; init; }
    public string? ModelVersion { get; init; }
    public string? SourceVersion { get; init; }
    public double? DurationMs { get; init; }
    public string? ReasonCode { get; init; }
    public Dictionary<string, JsonElement>? SafeContext { get; init; }
    public long? SourceSequence { get; init; }
    public string? ConfigRevision { get; init; }
    public string? AggregationVersion { get; init; }
    public DateTimeOffset? DataWindowStartUtc { get; init; }
    public DateTimeOffset? DataWindowEndUtc { get; init; }
    public long? ObservationGapCount { get; init; }
    public Resolution? QueryResolution { get; init; }
    public long? DroppedCount { get; init; }
    public long? StorageBytes { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (DurationMs < 0 || SourceSequence < 0 || ObservationGapCount < 0 || DroppedCount < 0 || StorageBytes < 0)
            throw new JsonException("Event counters and durations must be nonnegative.");
    }
}

public class TimeWindow : ContractModel
{
    public required DateTimeOffset FromUtc { get; init; }
    public required DateTimeOffset ToUtc { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (FromUtc >= ToUtc) throw new JsonException("fromUtc must precede toUtc.");
    }
}

public sealed class TrendGap : TimeWindow
{
    public required string Reason { get; init; }
}

public sealed class TrendPoint : ContractModel
{
    public required DateTimeOffset T { get; init; }
    public required double? V { get; init; }
    public required ObservationQuality Quality { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (Quality == ObservationQuality.GOOD && V is null) throw new JsonException("GOOD points require a value.");
    }
}

public sealed class TrendSeries : ContractModel
{
    public required SignalKey SignalKey { get; init; }
    public required string Unit { get; init; }
    public required List<TrendPoint> Points { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (Points.Count > Contract.MaxPointsPerSeries) throw new JsonException("Too many trend points.");
    }
}

public sealed class WarningMarker : ContractModel
{
    public required string WarningId { get; init; }
    public required DateTimeOffset AtUtc { get; init; }
    public required string Type { get; init; }
}

public sealed class TrendResponse : ContractModel
{
    public required string SchemaVersion { get; init; }
    public required string DeviceId { get; init; }
    public required Source Source { get; init; }
    public required Resolution Resolution { get; init; }
    public required TimeWindow Window { get; init; }
    public required DateTimeOffset GeneratedAtUtc { get; init; }
    public required bool IsComplete { get; init; }
    public required List<TrendGap> Gaps { get; init; }
    public required List<TrendSeries> Series { get; init; }
    public required List<WarningMarker> WarningMarkers { get; init; }

    public override void Validate()
    {
        base.Validate();
        if (Resolution == Resolution.auto) throw new JsonException("Response resolution must be actual, not auto.");
    }
}

public sealed class WarningTimeline : ContractModel
{
    public required string SchemaVersion { get; init; }
    public required string WarningId { get; init; }
    public required List<StructuredEvent> Events { get; init; }
    public required DateTimeOffset GeneratedAtUtc { get; init; }
}
