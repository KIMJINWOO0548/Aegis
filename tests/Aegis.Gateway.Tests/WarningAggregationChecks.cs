using System.Text;
using System.Text.Json;
using Aegis.Gateway;
using Microsoft.AspNetCore.SignalR;

static class WarningAggregationChecks
{
    public static async Task Run(Action<bool, string> check, IHubContext<DashboardHub> hub)
    {
        var settings = new GatewaySettings(new("http://127.0.0.1:8780"), new("http://127.0.0.1:8765"), new('x', 32), new('y', 32), null);
        async Task<DashboardSnapshot> Refresh(WarningSource source)
        {
            var cache = new DashboardCache(settings, source, new(settings), hub);
            await cache.RefreshAsync(CancellationToken.None);
            return cache.Latest;
        }

        // Each module may retain 32 warnings; 34 across two modules is valid.
        var combined = await Refresh(new([17, 17]));
        check(combined.UpstreamState == "CONNECTED" && combined.Devices.Count == 2,
            "valid per-module warning histories above32 aggregate remain connected");
        check(combined.Devices.All(device => device.Warnings.Count == 17
            && device.Warnings.All(warning => warning!["deviceId"]!.GetValue<string>() == device.DeviceId)),
            "aggregate warnings remain partitioned by their module identity");

        var full = await Refresh(new([32, 32]));
        check(full.UpstreamState == "CONNECTED" && full.Devices.Sum(device => device.Warnings.Count) == 64,
            "two complete per-module warning histories are retained without truncation");
        var maximum = await Refresh(new(Enumerable.Repeat(32, 32).ToArray()));
        check(maximum.UpstreamState == "CONNECTED" && maximum.Devices.Count == 32
            && maximum.Devices.All(device => device.Warnings.Count == 32),
            "declared32 module boundary preserves each bounded warning history");
        check((await Refresh(new([33, 0]))).UpstreamState == "UNAVAILABLE",
            "a single module still cannot exceed32 warnings within a larger aggregate");
        check((await Refresh(new([17, 17], unknownDevice: true))).UpstreamState == "UNAVAILABLE",
            "warning identity outside the declared device set remains rejected");
        check((await Refresh(new([32, 33]))).UpstreamState == "UNAVAILABLE",
            "aggregate above the module-count bound remains rejected");
    }

    private sealed class WarningSource(int[] counts, bool unknownDevice = false) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => new(new Handler(counts, unknownDevice));

        private sealed class Handler(int[] counts, bool unknownDevice) : HttpMessageHandler
        {
            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
            {
                var at = DateTimeOffset.UtcNow.ToString("O");
                var ids = Enumerable.Range(1, counts.Length).Select(index => $"device-{index}").ToArray();
                var route = request.RequestUri!.AbsolutePath;
                object result;
                if (route == "/v1/health") result = new { mode = "EMULATORS" };
                else if (route == "/v1/devices") result = ids.Select(id => new { deviceId = id, readOnly = true, source = "SIMULATOR" }).ToArray();
                else if (route is "/v1/connections" or "/v1/ingestion") result = Array.Empty<object>();
                else if (route == "/v1/warnings")
                    result = ids.SelectMany((id, index) => Enumerable.Range(0, counts[index]).Select(number => new
                    {
                        id = $"warning-{id}-{number}", deviceId = unknownDevice && index == 0 && number == 0 ? "unknown-device" : id,
                        state = "RESOLVED", createdAtUtc = at, recommendationId = "inspect-flow-tracking"
                    })).ToArray();
                else
                {
                    var id = ids.Single(device => route.StartsWith($"/v1/devices/{device}/", StringComparison.Ordinal));
                    if (route.EndsWith("/observations", StringComparison.Ordinal)) result = Array.Empty<object>();
                    else if (route.EndsWith("/diagnostics", StringComparison.Ordinal))
                        result = new { deviceId = id, enabled = false, modelVersion = (string?)null,
                            evaluation = "INSUFFICIENT_DATA", sampleCount = 0, flowErrorEwmaLpm = (double?)null, issues = Array.Empty<object>() };
                    else throw new InvalidOperationException("Unexpected warning-probe upstream route.");
                }
                return Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK)
                { Content = new StringContent(JsonSerializer.Serialize(result), Encoding.UTF8, "application/json") });
            }
        }
    }
}
