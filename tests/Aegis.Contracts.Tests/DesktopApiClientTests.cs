using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using Aegis.Desktop;
using Xunit;

namespace Aegis.Contracts.Tests;

public sealed class DesktopApiClientTests
{
    private const string Token = "desktop-test-token-01234567890123456789";

    [Theory]
    [InlineData("http://example.com:8765")]
    [InlineData("http://localhost:8765")]
    [InlineData("http://127.1:8765")]
    [InlineData("http://2130706433:8765")]
    [InlineData("http://127.0.0.1.evil.test:8765")]
    [InlineData("http://user@127.0.0.1:8765")]
    [InlineData("http://127.0.0.1:8765/path")]
    [InlineData("http://127.0.0.1:8765/?token=private")]
    [InlineData("http://127.0.0.1:8765/#fragment")]
    [InlineData("http://127.0.0.1:0")]
    [InlineData("http://127.0.0.1:65536")]
    public void Rejects_untrusted_origins_before_credentials_can_be_sent(string origin)
    {
        var error = Assert.Throws<ArgumentException>(() => new DesktopApiClient(origin, Token));
        Assert.DoesNotContain(origin, error.Message);
        Assert.DoesNotContain(Token, error.Message);
    }

    [Theory]
    [InlineData("")]
    [InlineData("short")]
    [InlineData("                                ")]
    [InlineData("private\r\nheader-injection-01234567890")]
    public void Rejects_missing_or_unsafe_tokens_without_echoing_them(string token)
    {
        var error = Assert.Throws<ArgumentException>(() => new DesktopApiClient("http://127.0.0.1:8765", token));
        Assert.Contains("AEGIS_API_TOKEN", error.Message);
        if (!string.IsNullOrWhiteSpace(token)) Assert.DoesNotContain(token, error.Message);
    }

    [Fact]
    public async Task Reads_selected_device_and_eight_latest_signals_with_authentication()
    {
        var handler = new ApiHandler();
        using var client = new DesktopApiClient("http://127.0.0.1:8765/", Token, handler);
        var snapshot = await client.ReadAsync("EMU-02", CancellationToken.None);

        Assert.Equal("EMU-02", snapshot.DeviceId);
        Assert.Equal(2, snapshot.Devices.Count);
        Assert.Equal(ConnectionState.CONNECTED, snapshot.State);
        Assert.Equal(8, snapshot.Signals.Count);
        Assert.Equal("actualFlowLpm", snapshot.Signals[1].SignalKey.ToString());
        Assert.Equal(9.8, snapshot.Signals[1].Value);
        Assert.All(snapshot.Signals, row => Assert.Equal(ObservationQuality.GOOD, row.Quality));
        Assert.Contains("/v1/devices/EMU-02/observations", handler.Paths);
        Assert.All(handler.Tokens, value => Assert.Equal(Token, value));
    }

    [Fact]
    public async Task Fixture_mode_never_becomes_a_live_connection_or_returns_fixture_signals()
    {
        var handler = new ApiHandler { Mode = "FIXTURES" };
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token, handler);
        var snapshot = await client.ReadAsync(null, CancellationToken.None);

        Assert.Equal("FIXTURES", snapshot.Mode);
        Assert.Null(snapshot.State);
        Assert.Empty(snapshot.Devices);
        Assert.Empty(snapshot.Signals);
        Assert.Equal(new[] { "/v1/health" }, handler.Paths);
    }

    [Theory]
    [InlineData(ConnectionState.STALE)]
    [InlineData(ConnectionState.DISCONNECTED)]
    [InlineData(ConnectionState.REJECTED)]
    public async Task Noncurrent_connection_never_displays_good_quality(ConnectionState state)
    {
        var handler = new ApiHandler { State = state };
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token, handler);
        var snapshot = await client.ReadAsync(null, CancellationToken.None);

        Assert.Equal(state, snapshot.State);
        Assert.Equal(8, snapshot.Signals.Count);
        Assert.All(snapshot.Signals, row => Assert.Equal(ObservationQuality.STALE, row.Quality));
    }

    [Fact]
    public async Task Locally_expired_connected_snapshot_becomes_stale()
    {
        var handler = new ApiHandler { Timestamp = DateTimeOffset.UtcNow.AddMinutes(-1) };
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token, handler);
        var snapshot = await client.ReadAsync(null, CancellationToken.None);

        Assert.Equal(ConnectionState.STALE, snapshot.State);
        Assert.All(snapshot.Signals, row => Assert.Equal(ObservationQuality.STALE, row.Quality));
    }

    [Fact]
    public async Task New_device_without_samples_shows_missing_values()
    {
        var handler = new ApiHandler { EmptyObservations = true, State = ConnectionState.CONNECTING };
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token, handler);
        var snapshot = await client.ReadAsync("removed-device", CancellationToken.None);

        Assert.Equal("EMU-01", snapshot.DeviceId);
        Assert.Equal(8, snapshot.Signals.Count);
        Assert.All(snapshot.Signals, row => { Assert.Null(row.Value); Assert.Equal(ObservationQuality.MISSING, row.Quality); });
    }

    [Theory]
    [InlineData("identity")]
    [InlineData("duplicate")]
    [InlineData("unit")]
    [InlineData("unknown")]
    public async Task Invalid_observation_batches_are_rejected_instead_of_displayed(string corruption)
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token, new ApiHandler { Corruption = corruption });
        await Assert.ThrowsAsync<JsonException>(() => client.ReadAsync(null, CancellationToken.None));
    }

    [Fact]
    public async Task Redirect_is_rejected_without_a_second_request()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        var server = Task.Run(async () =>
        {
            using var socket = await listener.AcceptTcpClientAsync(cancellation.Token);
            await using var stream = socket.GetStream();
            using var reader = new StreamReader(stream, Encoding.ASCII, leaveOpen: true);
            while (!string.IsNullOrEmpty(await reader.ReadLineAsync(cancellation.Token))) { }
            await stream.WriteAsync(Encoding.ASCII.GetBytes($"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{port}/redirect-target\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"), cancellation.Token);
        }, cancellation.Token);
        using var client = new DesktopApiClient($"http://127.0.0.1:{port}", Token);
        var error = await Assert.ThrowsAsync<HttpRequestException>(() => client.ReadAsync(null, cancellation.Token));
        Assert.Equal(HttpStatusCode.Found, error.StatusCode);
        await server;
        Assert.False(listener.Pending());
    }

    [Fact]
    public async Task Cancellation_stops_a_pending_request_without_retry()
    {
        var handler = new ApiHandler { Slow = true };
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token, handler);
        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(40));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => client.ReadAsync(null, cancellation.Token));
        Assert.Single(handler.Paths);
    }

    [Fact]
    public async Task Oversized_response_is_bounded()
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token, new ApiHandler { Oversized = true });
        await Assert.ThrowsAsync<HttpRequestException>(() => client.ReadAsync(null, CancellationToken.None));
    }

    [Fact]
    public async Task Enabled_diagnostics_preserve_warmup_and_filter_selected_device_warnings()
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token,
            new ApiHandler { EvaluationEnabled = true });
        var snapshot = await client.ReadAsync("EMU-02", CancellationToken.None);

        Assert.True(snapshot.Diagnostics!.Enabled);
        Assert.Equal("ewma-flow-dev-1", snapshot.Diagnostics.ModelVersion);
        Assert.Equal(30, snapshot.Diagnostics.SampleCount);
        Assert.Equal(0.25, snapshot.Diagnostics.FlowErrorEwmaLpm);
        Assert.Equal(ObservationEvaluation.INSUFFICIENT_DATA, snapshot.AnalysisEvaluation);
        Assert.Equal("warning-EMU-02", Assert.Single(snapshot.Warnings).Id);
    }

    [Theory]
    [InlineData("normal", ObservationEvaluation.NORMAL)]
    [InlineData("anomalous", ObservationEvaluation.ANOMALOUS)]
    public async Task Ready_valid_diagnostics_display_the_received_evaluation(string evaluation, ObservationEvaluation expected)
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token,
            new ApiHandler { EvaluationEnabled = true, DiagnosticCorruption = evaluation });
        var snapshot = await client.ReadAsync(null, CancellationToken.None);

        Assert.Equal(60, snapshot.Diagnostics!.SampleCount);
        Assert.Equal(expected, snapshot.AnalysisEvaluation);
    }

    [Fact]
    public async Task Bounded_multi_source_warning_history_does_not_disconnect_live_signals()
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token,
            new ApiHandler { EvaluationEnabled = true, LargeWarningHistory = true });
        var snapshot = await client.ReadAsync("EMU-02", CancellationToken.None);

        Assert.Equal(512, snapshot.Warnings.Count);
        Assert.Equal(ConnectionState.CONNECTED, snapshot.State);
        Assert.Equal(8, snapshot.Signals.Count);
    }

    [Fact]
    public async Task Out_of_range_issue_preserves_raw_value_but_marks_it_bad_and_blocks_analysis()
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token,
            new ApiHandler { EvaluationEnabled = true, DiagnosticCorruption = "out-of-range" });
        var snapshot = await client.ReadAsync(null, CancellationToken.None);

        var flow = snapshot.Signals.Single(row => row.SignalKey == SignalKey.actualFlowLpm);
        Assert.Equal(-5, flow.Value);
        Assert.Equal(ObservationQuality.BAD, flow.Quality);
        Assert.Contains("범위", flow.Issue);
        Assert.Equal(ObservationEvaluation.UNAVAILABLE, snapshot.AnalysisEvaluation);
    }

    [Theory]
    [InlineData(ConnectionState.DISCONNECTED)]
    [InlineData(ConnectionState.STALE)]
    public async Task Offline_transport_cannot_claim_normal_analysis(ConnectionState state)
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token,
            new ApiHandler { State = state, EvaluationEnabled = true, DiagnosticCorruption = "normal" });
        var snapshot = await client.ReadAsync(null, CancellationToken.None);
        Assert.Equal(ObservationEvaluation.UNAVAILABLE, snapshot.AnalysisEvaluation);
    }

    [Theory]
    [InlineData("identity")]
    [InlineData("sample-count")]
    [InlineData("model-version")]
    [InlineData("too-many-issues")]
    [InlineData("unknown-reason")]
    [InlineData("disabled-score")]
    public async Task Invalid_diagnostics_cannot_be_displayed(string corruption)
    {
        using var client = new DesktopApiClient("http://127.0.0.1:8765", Token,
            new ApiHandler { EvaluationEnabled = corruption != "disabled-score", DiagnosticCorruption = corruption });
        await Assert.ThrowsAsync<JsonException>(() => client.ReadAsync(null, CancellationToken.None));
    }

    private sealed class ApiHandler : HttpMessageHandler
    {
        public string Mode { get; init; } = "EMULATORS";
        public ConnectionState State { get; init; } = ConnectionState.CONNECTED;
        public DateTimeOffset Timestamp { get; init; } = DateTimeOffset.UtcNow;
        public bool EmptyObservations { get; init; }
        public string? Corruption { get; init; }
        public bool Slow { get; init; }
        public bool Oversized { get; init; }
        public bool EvaluationEnabled { get; init; }
        public bool LargeWarningHistory { get; init; }
        public string? DiagnosticCorruption { get; init; }
        public List<string> Paths { get; } = [];
        public List<string> Tokens { get; } = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri!.AbsolutePath;
            Paths.Add(path);
            Tokens.Add(request.Headers.GetValues("X-Aegis-Token").Single());
            if (Slow) await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            if (Oversized) return Json(new string(' ', 1_500_000));
            if (path == "/v1/health") return Json(JsonSerializer.Serialize(new { status = "OK", schemaVersion = "3.4.0", stage = "D0a", mock = true, mode = Mode }));
            if (path == "/v1/devices") return Json("""[{"deviceId":"EMU-01","readOnly":true,"source":"SIMULATOR"},{"deviceId":"EMU-02","readOnly":true,"source":"SIMULATOR"}]""");
            if (path == "/v1/connections") return Json(JsonSerializer.Serialize(new[] { "EMU-01", "EMU-02" }.Select(id => new
            {
                deviceId = id, state = State.ToString(), lastReceivedAtUtc = Timestamp, lastSequence = 12,
                acceptedBatches = 13, rejectedBatches = 0, droppedBatches = 0, pollLatencyMs = 2.5, reasonCode = (string?)null
            })));
            if (path == "/v1/warnings" && LargeWarningHistory) return Json(JsonSerializer.Serialize(Enumerable.Range(0, 1024).Select(index => new
            {
                id = $"warning-{index}", deviceId = index % 2 == 0 ? "EMU-01" : "EMU-02", state = "RESOLVED", createdAtUtc = Timestamp,
                recommendationId = new string('r', 100)
            })));
            if (path == "/v1/warnings") return Json(JsonSerializer.Serialize(new[] { "EMU-01", "EMU-02" }.Select(id => new
            {
                id = $"warning-{id}", deviceId = id, state = "OPEN", createdAtUtc = Timestamp,
                recommendationId = "inspect-flow"
            })));
            if (path.EndsWith("/diagnostics", StringComparison.Ordinal))
            {
                var issues = DiagnosticCorruption is "out-of-range" or "too-many-issues" or "unknown-reason"
                    ? Enumerable.Range(0, DiagnosticCorruption == "too-many-issues" ? 9 : 1).Select(_ => new
                    {
                        signalKey = "actualFlowLpm", reasonCode = DiagnosticCorruption == "unknown-reason" ? "bad reason" : "OUT_OF_RANGE",
                        message = "유량 값이 허용 범위를 벗어났습니다. 신호 설정을 확인하세요."
                    }).ToArray() : [];
                return Json(JsonSerializer.Serialize(new
                {
                    deviceId = DiagnosticCorruption == "identity" ? "OTHER" : path.Split('/')[3], enabled = EvaluationEnabled,
                    modelVersion = EvaluationEnabled ? DiagnosticCorruption == "model-version" ? "pretend-model" : "ewma-flow-dev-1" : null,
                    evaluation = DiagnosticCorruption == "normal" ? "NORMAL" : DiagnosticCorruption == "anomalous" ? "ANOMALOUS" : "INSUFFICIENT_DATA",
                    sampleCount = DiagnosticCorruption == "sample-count" ? 61 : DiagnosticCorruption is "normal" or "anomalous" ? 60 : EvaluationEnabled ? 30 : 0,
                    flowErrorEwmaLpm = EvaluationEnabled || DiagnosticCorruption == "disabled-score" ? (double?)0.25 : null, issues
                }));
            }
            if (!path.EndsWith("/observations", StringComparison.Ordinal)) return new HttpResponseMessage(HttpStatusCode.NotFound);
            if (EmptyObservations) return Json("[]");
            var deviceId = path.Split('/')[3];
            string[] keys = ["setpointFlowLpm", "actualFlowLpm", "supplyPressureKpa", "valveCommandPct", "valveFeedbackPct", "motorCurrentAmp", "temperatureC", "cycleCount"];
            string[] units = ["L/min", "L/min", "kPa", "%", "%", "A", "degC", "count"];
            return Json(JsonSerializer.Serialize(keys.Select((key, index) => new
            {
                schemaVersion = "3.4.0", observationId = $"obs-{index}", deviceId = Corruption == "identity" ? "OTHER" : deviceId,
                signalKey = Corruption == "duplicate" ? keys[0] : Corruption == "unknown" ? "unknown" : key,
                value = DiagnosticCorruption == "out-of-range" && index == 1 ? -5 : 9.8, unit = Corruption == "unit" ? "wrong" : units[index], observedAtUtc = Timestamp,
                receivedAtUtc = Timestamp, quality = "GOOD", deviceStateVersion = 1, modeId = "PRODUCTION"
            })));
        }

        private static HttpResponseMessage Json(string json) => new(HttpStatusCode.OK) { Content = new StringContent(json) };
    }
}
