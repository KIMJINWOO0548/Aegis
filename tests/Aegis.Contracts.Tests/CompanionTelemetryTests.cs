using System.Net;
using System.Text;
using System.Text.Json;
using Aegis.Desktop;
using Xunit;

namespace Aegis.Contracts.Tests;

public sealed class CompanionTelemetryTests
{
    private static readonly DateTimeOffset Now = new(2026, 11, 23, 9, 30, 0, TimeSpan.Zero);
    private static readonly CompanionPin FlowA = new("DEV-A", "actualFlowLpm");
    private static readonly CompanionPin FlowB = new("DEV-B", "actualFlowLpm");
    private static readonly DashboardEndpoint Endpoint = new("http://127.0.0.1:8921/", new string('a', 32));

    [Fact]
    public void ProjectsIndependentCollectorsAndDistinguishesRestoredZeroAndMissingValue()
    {
        var snapshot = Parse(Snapshot([
            Device("DEV-A", "MODULE-A", [Observation("DEV-A", "actualFlowLpm", "L/min", 0, "STALE", -60)],
                freshness: "RESTORED", worker: "RESTARTING", provenance: "RESTORED", age: -60),
            Device("DEV-B", "MODULE-B", [
                Observation("DEV-B", "actualFlowLpm", "L/min", 0),
                Observation("DEV-B", "valveFeedbackPct", "%", null, "MISSING")
            ])
        ]));
        CompanionPin[] pins = [FlowA, FlowB, new("DEV-B", "valveFeedbackPct"), new("DEV-B", "temperatureC")];
        var frame = snapshot.BuildFrame(pins, Now, connected: true);
        Assert.Equal("합성 데이터", frame.Status);
        Assert.Collection(frame.Signals,
            row => { Assert.Equal("0", row.Value); Assert.Equal("복원 이력", row.State); Assert.NotEqual("—", row.ObservedAt); },
            row => { Assert.Equal("0", row.Value); Assert.Equal("실시간", row.State); },
            row => { Assert.Equal("—", row.Value); Assert.Equal("값 없음", row.State); Assert.NotEqual("—", row.ObservedAt); },
            row => { Assert.Equal("—", row.Value); Assert.Equal("미수신", row.State); Assert.Equal("—", row.ObservedAt); });
        Assert.Equal(["재시작 중", "수집 중"], frame.Collectors.Select(collector => collector.State));
        Assert.All(frame.Collectors, collector => Assert.NotEqual("—", collector.ReceivedAt));
        Assert.Equal(16, snapshot.Catalog.Count);
    }

    [Fact]
    public void FailedTransportRetainsSameSamplesAndNeverSubstitutesAnotherDevice()
    {
        var snapshot = Parse(Snapshot([Device("DEV-B", "MODULE-B", [Observation("DEV-B", "actualFlowLpm", "L/min", 12.5)])]));
        var frame = snapshot.BuildFrame([FlowA, FlowB], Now, connected: false);
        Assert.Equal("합성 · 연결 끊김", frame.Status);
        Assert.Equal("미수신", frame.Signals[0].State);
        Assert.Equal("—", frame.Signals[0].Value);
        Assert.Equal("연결 끊김", frame.Signals[1].State);
        Assert.Equal("12.5", frame.Signals[1].Value);
        Assert.Single(frame.Collectors);
        Assert.Equal("연결 끊김", frame.Collectors[0].State);
        Assert.Equal("연결 확인", CompanionSnapshot.Empty.BuildFrame([FlowA], Now, false).Status);
        Assert.Equal("DEV-A · 실측 유량", snapshot.LabelFor(FlowA));
    }

    [Fact]
    public void ScheduledOrDelayedDataCannotBecomeLiveAndNonzeroDoesNotRenderAsZero()
    {
        var snapshot = Parse(Snapshot([
            Device("DEV-A", "MODULE-A", [Observation("DEV-A", "actualFlowLpm", "L/min", 0.000014)], collectorMode: "FIXTURE"),
            Device("DEV-B", "MODULE-B", [Observation("DEV-B", "actualFlowLpm", "L/min", 92, tick: 2)])
        ]));
        var atReceipt = snapshot.BuildFrame([FlowA, FlowB], Now, connected: true);
        Assert.Equal("1.4E-5", atReceipt.Signals[0].Value);
        Assert.All(atReceipt.Signals, row => Assert.Equal("오래됨", row.State));
        var delayed = snapshot.BuildFrame([FlowA, FlowB], Now.AddSeconds(30), connected: true);
        Assert.Equal("합성 · 수신 지연", delayed.Status);
        Assert.All(delayed.Signals, row => Assert.NotEqual("실시간", row.State));
        Assert.Equal(["고정 자료", "확인 불가"], delayed.Collectors.Select(row => row.State));
    }

    [Fact]
    public void ExpiredReceiptDowngradesOnlyItsCollectorAndSignal()
    {
        var snapshot = Parse(Snapshot([
            Device("DEV-A", "MODULE-A", [Observation("DEV-A", "actualFlowLpm", "L/min", 0)], expiry: 1),
            Device("DEV-B", "MODULE-B", [Observation("DEV-B", "actualFlowLpm", "L/min", 21)], expiry: 3)
        ]));
        var frame = snapshot.BuildFrame([FlowA, FlowB], Now.AddSeconds(1), connected: true);
        Assert.Equal(["오래됨", "실시간"], frame.Signals.Select(row => row.State));
        Assert.Equal(["수신 지연", "수집 중"], frame.Collectors.Select(row => row.State));
    }

    [Fact]
    public void LegacyRestoreMarkerCannotBeMistakenForLiveData()
    {
        var snapshot = Parse(Snapshot([new
        {
            deviceId = "DEV-A",
            moduleId = "MODULE-A",
            connection = new { state = "DISCONNECTED", lastReceivedAtUtc = Now.AddSeconds(-1), reasonCode = "RESTORED_HISTORY" },
            ingestion = (object?)null,
            observations = new[] { Observation("DEV-A", "actualFlowLpm", "L/min", 0, "STALE") }
        }]));
        var frame = snapshot.BuildFrame([FlowA], Now, connected: true);
        Assert.Equal("복원 이력", frame.Signals.Single().State);
        Assert.Equal("복원 이력", frame.Collectors.Single().State);
    }

    [Fact]
    public void RejectsDuplicateDeviceWrongOwnershipAndUnreasonablePin()
    {
        var device = Device("DEV-A", "MODULE-A", []);
        Assert.Throws<JsonException>(() => Parse(Snapshot([device, device])));
        var wrong = Device("DEV-A", "MODULE-A", [Observation("DEV-B", "actualFlowLpm", "L/min", 0)]);
        Assert.Throws<JsonException>(() => Parse(Snapshot([wrong])));
        Assert.False(CompanionSnapshot.IsKnownPin(new("DEV-A", "unlisted")));
        Assert.False(CompanionSnapshot.IsKnownPin(new("../path", "actualFlowLpm")));
        Assert.True(CompanionSnapshot.IsKnownPin(FlowA));
    }

    [Fact]
    public async Task AuthenticatesOnlyFirstAndExpiredReadsAtExactLocalPath()
    {
        using var fake = new Handler(HttpStatusCode.OK, HttpStatusCode.OK, HttpStatusCode.Unauthorized, HttpStatusCode.OK);
        using var client = new CompanionTelemetryClient(Endpoint, fake);
        await client.ReadAsync(CancellationToken.None);
        await client.ReadAsync(CancellationToken.None);
        await client.ReadAsync(CancellationToken.None);
        Assert.Equal([true, false, false, true], fake.Requests.Select(request => request.Bootstrap));
        Assert.All(fake.Requests, request =>
        {
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("http://127.0.0.1:8921/api/dashboard", request.Uri);
        });
    }

    [Fact]
    public async Task RefusesRedirectWithoutTransmittingCredentialsToAnotherRoute()
    {
        using var fake = new Handler(HttpStatusCode.Redirect);
        using var client = new CompanionTelemetryClient(Endpoint, fake);
        var error = await Assert.ThrowsAsync<HttpRequestException>(() => client.ReadAsync(CancellationToken.None));
        Assert.Equal(HttpStatusCode.Redirect, error.StatusCode);
        Assert.Single(fake.Requests);
    }

    [Fact]
    public async Task RefusesOversizedSnapshotBeforeProjection()
    {
        using var fake = new Handler(HttpStatusCode.OK) { Body = new string('x', 4 * 1024 * 1024 + 1) };
        using var client = new CompanionTelemetryClient(Endpoint, fake);
        await Assert.ThrowsAsync<JsonException>(() => client.ReadAsync(CancellationToken.None));
    }

    private static CompanionSnapshot Parse(string json)
    {
        using var document = JsonDocument.Parse(json);
        return CompanionSnapshot.Parse(document.RootElement);
    }

    private static string Snapshot(object[] devices) => JsonSerializer.Serialize(new
    {
        schemaVersion = "dashboard-1",
        generatedAtUtc = Now,
        upstreamState = "CONNECTED",
        sourceMode = "EMULATORS",
        devices
    });

    private static object Device(string id, string module, object[] observations, string freshness = "CURRENT",
        string worker = "RUNNING", string provenance = "ACCEPTED", int age = -1, int expiry = 2,
        string collectorMode = "ISOLATED") => new
    {
        deviceId = id,
        moduleId = module,
        connection = new { state = "CONNECTED", lastReceivedAtUtc = Now.AddSeconds(age) },
        ingestion = new
        {
            collectionStatus = new
            {
                collectorMode,
                declaredOrigin = "SYNTHETIC",
                receiptProvenance = provenance,
                freshness,
                workerStatus = worker,
                lastReceivedAtUtc = Now.AddSeconds(age),
                validUntilUtc = freshness == "CURRENT" ? Now.AddSeconds(expiry) : (DateTimeOffset?)null
            }
        },
        observations
    };

    private static object Observation(string id, string signal, string unit, double? value, string quality = "GOOD", int tick = -1) => new
    {
        schemaVersion = "3.4.0",
        observationId = $"{id}-{signal}",
        deviceId = id,
        signalKey = signal,
        unit,
        value,
        observedAtUtc = Now.AddSeconds(tick),
        receivedAtUtc = Now.AddSeconds(tick),
        quality,
        deviceStateVersion = 0,
        modeId = "PRODUCTION"
    };

    private sealed class Handler(params HttpStatusCode[] statuses) : HttpMessageHandler
    {
        public List<(HttpMethod Method, string? Uri, bool Bootstrap)> Requests { get; } = [];
        public string Body { get; init; } = Snapshot([]);

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var status = statuses[Requests.Count];
            Requests.Add((request.Method, request.RequestUri?.AbsoluteUri, request.Headers.Contains("X-Aegis-Dashboard-Token")));
            var response = new HttpResponseMessage(status)
            {
                Content = new StringContent(Body, Encoding.UTF8, "application/json")
            };
            if (status == HttpStatusCode.Redirect) response.Headers.Location = new Uri("http://192.0.2.9/");
            return Task.FromResult(response);
        }
    }
}
