using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using Xunit;

namespace Aegis.Contracts.Tests;

public sealed class ContractSerializationTests
{
    private const string ValidObservation = """
        {"schemaVersion":"3.4.0","observationId":"obs-test-1","deviceId":"EQ-SUPPLY-01",
         "signalKey":"actualFlowLpm","value":9.7,"unit":"L/min",
         "observedAtUtc":"2026-10-16T00:00:00Z","receivedAtUtc":"2026-10-16T00:00:00.087Z",
         "quality":"GOOD","deviceStateVersion":41,"modeId":"PRODUCTION"}
        """;

    [Fact]
    public void Observation_preserves_contract_signal_and_enum_names()
    {
        var assembly = Assembly.Load("Aegis.Contracts");
        var observationType = assembly.GetType("Aegis.Contracts.Observation");
        Assert.NotNull(observationType);
        var jsonType = assembly.GetType("Aegis.Contracts.ContractJson");
        Assert.NotNull(jsonType);
        var options = Assert.IsType<JsonSerializerOptions>(jsonType.GetProperty("Options")?.GetValue(null));
        var value = JsonSerializer.Deserialize(ValidObservation, observationType, options);
        Assert.NotNull(value);
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(value, observationType, options));
        Assert.Equal("actualFlowLpm", document.RootElement.GetProperty("signalKey").GetString());
        Assert.Equal("GOOD", document.RootElement.GetProperty("quality").GetString());
        Assert.Equal(9.7, document.RootElement.GetProperty("value").GetDouble());
    }

    [Theory]
    [InlineData("quality", "\"UNKNOWN\"")]
    [InlineData("quality", "\"good\"")]
    [InlineData("quality", "0")]
    [InlineData("value", "null")]
    [InlineData("unit", "null")]
    [InlineData("signalKey", "\"unknownSignal\"")]
    [InlineData("observedAtUtc", "\"2026-10-16T00:00:00\"")]
    [InlineData("deviceStateVersion", "-1")]
    [InlineData("schemaVersion", "\"3.5.0\"")]
    [InlineData("extraField", "\"untrusted\"")]
    public void Observation_rejects_invalid_contract_data(string field, string jsonValue)
    {
        var observation = JsonNode.Parse(ValidObservation)!.AsObject();
        observation[field] = JsonNode.Parse(jsonValue);
        Assert.Throws<JsonException>(() => ContractJson.Deserialize<Observation>(observation.ToJsonString()));
    }

    [Theory]
    [InlineData("schemaVersion")]
    [InlineData("value")]
    [InlineData("quality")]
    [InlineData("observedAtUtc")]
    public void Observation_rejects_missing_required_fields(string field)
    {
        var observation = JsonNode.Parse(ValidObservation)!.AsObject();
        observation.Remove(field);
        Assert.Throws<JsonException>(() => ContractJson.Deserialize<Observation>(observation.ToJsonString()));
    }

    [Fact]
    public void Missing_observation_preserves_null_without_substituting_zero()
    {
        var observation = JsonNode.Parse(ValidObservation)!.AsObject();
        observation["quality"] = "MISSING";
        observation["value"] = null;
        var parsed = ContractJson.Deserialize<Observation>(observation.ToJsonString());
        using var saved = JsonDocument.Parse(ContractJson.Serialize(parsed));
        Assert.Equal(JsonValueKind.Null, saved.RootElement.GetProperty("value").ValueKind);
    }

    [Fact]
    public void Event_rejects_negative_source_sequence()
    {
        const string json = """
            {"schemaVersion":"3.4.0","eventId":"evt-test-1","occurredAtUtc":"2026-10-16T00:00:00Z",
             "component":"collector","eventName":"SERVICE_STARTED","severity":"INFO",
             "traceId":"trace-test-1","outcome":"SUCCESS","sourceSequence":-1}
            """;
        Assert.Throws<JsonException>(() => ContractJson.Deserialize<StructuredEvent>(json));
    }

    [Theory]
    [InlineData("3.4.0", null)]
    [InlineData("3.3.0", 9.7)]
    public void Root_observation_array_validates_each_item(string version, double? value)
    {
        var observation = new Observation
        {
            SchemaVersion = version, ObservationId = "obs-test-1", DeviceId = "EQ-SUPPLY-01",
            SignalKey = SignalKey.actualFlowLpm, Value = value, Unit = "L/min",
            ObservedAtUtc = DateTimeOffset.Parse("2026-10-16T00:00:00Z"),
            ReceivedAtUtc = DateTimeOffset.Parse("2026-10-16T00:00:00Z"),
            Quality = ObservationQuality.GOOD, DeviceStateVersion = 41, ModeId = ModeId.PRODUCTION
        };
        Assert.Throws<JsonException>(() => ContractJson.Serialize(new[] { observation }));
    }

    [Fact]
    public void Root_observation_array_rejects_null_items_on_read_and_write()
    {
        Assert.Throws<JsonException>(() => ContractJson.Serialize(new Observation?[] { null }));
        Assert.Throws<JsonException>(() => ContractJson.Deserialize<List<Observation>>("[null]"));
    }

    [Fact]
    public void Root_observation_array_round_trips_valid_items()
    {
        var observations = new[] { ContractJson.Deserialize<Observation>(ValidObservation) };
        var json = ContractJson.Serialize(observations);
        var saved = ContractJson.Deserialize<List<Observation>>(json);
        Assert.Single(saved);
        Assert.Equal(9.7, saved[0].Value);
        Assert.Equal(SignalKey.actualFlowLpm, saved[0].SignalKey);
    }
}
