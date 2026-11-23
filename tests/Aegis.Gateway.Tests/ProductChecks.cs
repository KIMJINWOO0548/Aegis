using System.Net;
using System.Text;
using System.Text.Json;
using Aegis.Contracts;
using Aegis.Gateway;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Data.Sqlite;

internal static class ProductChecks
{
    public static async Task Run(Action<bool, string> check, string token)
    {
        var root = Directory.CreateTempSubdirectory("aegis-product-test-");
        try
        {
            var active = Path.Combine(root.FullName, "runs", "run-abcdef12");
            Directory.CreateDirectory(active);
            var config = new Dictionary<string, string?>
            {
                ["AEGIS_PRODUCT_STATE_DIRECTORY"] = root.FullName, ["AEGIS_ARCHIVE_DIRECTORY"] = active,
                ["AEGIS_DASHBOARD_TOKEN"] = token, ["AEGIS_API_TOKEN"] = token, ["AEGIS_API_URL"] = "http://127.0.0.1:8765", ["AEGIS_GATEWAY_URL"] = "http://127.0.0.1:8780",
            };
            var id = Guid.NewGuid().ToString();
            await using var app = GatewayApplication.Build([], builder =>
            {
                builder.WebHost.UseTestServer();
                builder.Configuration.AddInMemoryCollection(config);
                builder.Services.AddHttpClient("upstream").AddHttpMessageHandler(() => new ArchiveHandler());
            });
            await app.StartAsync();
            var workspace = app.Services.GetRequiredService<ProductWorkspace>();
            var when = DateTimeOffset.UtcNow;
            Warning WarningAt(WarningLifecycle state) => new()
            {
                Id = id, DeviceId = "device-1", State = state, CreatedAtUtc = when,
                RecommendationId = "inspect-flow-tracking",
            };
            workspace.Capture([WarningAt(WarningLifecycle.OPEN)]);
            workspace.Capture([WarningAt(WarningLifecycle.OPEN)]);
            var server = app.GetTestServer();
            async Task<(int Status, string Body)> Request(string path, string method = "GET", string? body = null,
                bool authorize = true, bool sameOrigin = true, string? query = null)
            {
                var response = await server.SendAsync(context =>
                {
                    context.Connection.RemoteIpAddress = IPAddress.Loopback;
                    context.Request.Path = path;
                    context.Request.Host = new HostString("127.0.0.1:8780");
                    context.Request.Method = method;
                    if (query is not null) context.Request.QueryString = new QueryString(query);
                    if (sameOrigin) context.Request.Headers.Origin = "http://127.0.0.1:8780";
                    if (authorize) context.Request.Headers["X-Aegis-Dashboard-Token"] = token;
                    if (body is not null)
                    {
                        var bytes = Encoding.UTF8.GetBytes(body);
                        context.Request.ContentType = "application/json";
                        context.Request.ContentLength = bytes.Length;
                        context.Request.Body = new MemoryStream(bytes);
                    }
                });
                using var reader = new StreamReader(response.Response.Body);
                return (response.Response.StatusCode, await reader.ReadToEndAsync());
            }

            check((await Request("/api/local-diagnostics", authorize: false)).Status == 401,
                "local support report requires an authenticated dashboard");
            check((await Request("/api/local-diagnostics", query: "?includeRaw=1")).Status == 400,
                "diagnostics cannot opt into extra fields");
            var diagnostic = await Request("/api/local-diagnostics");
            using var diagnosticJson = JsonDocument.Parse(diagnostic.Body);
            check(diagnostic.Status == 200 && LocalDiagnosticsReport.Parse(diagnosticJson.RootElement) is not null
                && !diagnostic.Body.Contains(token) && !diagnostic.Body.Contains("device-1"),
                "local support report is allowlisted and has no token or source identity");
            using var unsupported = JsonDocument.Parse(diagnostic.Body.Insert(1, "\"rawPayload\":\"proprietary\","));
            check(LocalDiagnosticsReport.Parse(unsupported.RootElement) is null, "native export rejects unreviewed fields");
            using var duplicated = JsonDocument.Parse(diagnostic.Body.Insert(1, "\"schemaVersion\":\"internal\","));
            check(LocalDiagnosticsReport.Parse(duplicated.RootElement) is null, "native export rejects duplicate fields");

            check((await Request("/api/incidents", authorize: false)).Status == 401, "incident list requires authenticated local session");
            var incidentList = await Request("/api/incidents", query: "?deviceId=device-1");
            using var incidents = JsonDocument.Parse(incidentList.Body);
            check(incidents.RootElement.GetProperty("incidents").GetArrayLength() == 1 &&
                incidents.RootElement.GetProperty("currentSessionId").ValueEquals("run-abcdef12"), "incident projection deduplicated, session bound");
            var reviewPath = $"/api/incidents/{id}/review";
            check((await Request(reviewPath, "POST", sameOrigin: false)).Status == 403, "incident review rejects absent origin");
            var review = await Request(reviewPath, "POST");
            var repeat = await Request(reviewPath, "POST");
            check(review.Status == 200 && repeat == review, "incident review idempotent");
            workspace.Capture([WarningAt(WarningLifecycle.RESOLVED)]);
            var restartSettings = app.Services.GetRequiredService<GatewaySettings>() with
                { ArchiveDirectory = Path.Combine(root.FullName, "runs", "run-abcdef13") };
            var reopened = new ProductWorkspace(app.Configuration, restartSettings);
            check(JsonSerializer.Serialize(reopened.Incidents(null)) is { } result && result.Contains("run-abcdef12") &&
                  result.Contains("RESOLVED") && reopened.Review(id) is not null, "past incident and review recover after new session");

            const string profilePath = "/api/analysis-profile/device-1";
            const string manual = """{"mode":"MANUAL","modelId":"ewma-flow-dev-1","description":"밸브 유량","historySessionId":"run-abcdef12","version":0}""";
            check((await Request(profilePath)).Status == 200, "analysis profile initial read");
            check((await Request(profilePath, "PUT", manual, sameOrigin: false)).Status == 403, "profile mutation requires exact origin");
            check((await Request(profilePath, "PUT", manual.Replace("ewma-flow-dev-1", "unregistered"))).Status == 400,
                "profile refuses unregistered model");
            check((await Request(profilePath, "PUT", manual)).Status == 200 && reopened.Profile("device-1").Mode == "MANUAL",
                "manual profile writes and persists across new session");
            check((await Request(profilePath, "PUT", manual)).Status == 409, "stale profile cannot overwrite a newer revision");
            var catalogResponse = await Request("/api/archive-sessions");
            check(catalogResponse.Status == 200, $"history session catalog secured proxy ({catalogResponse.Status} {catalogResponse.Body})");
            check((await Request("/api/archive-sessions/run-abcdef12/records")).Status == 200, "historical query allows default receiving window");
            check((await Request("/api/stored-records")).Status == 400, "existing live history still requires explicit window");
            check((await Request("/api/archive-sessions/other/records")).Status == 400, "only known shape session keys can be proxied");
            var foreignRoot = Path.Combine(root.FullName, "foreign");
            var foreignArchive = Path.Combine(foreignRoot, "runs", "run-foreign0");
            Directory.CreateDirectory(foreignArchive);
            var foreignPath = Path.Combine(foreignRoot, "workspace.sqlite3");
            using (var database = new SqliteConnection(new SqliteConnectionStringBuilder
                { DataSource = foreignPath, Pooling = false }.ToString()))
            {
                database.Open();
                using var command = database.CreateCommand();
                command.CommandText = "CREATE TABLE user_documents (title TEXT); INSERT INTO user_documents VALUES ('keep');";
                command.ExecuteNonQuery();
            }
            var original = File.ReadAllBytes(foreignPath);
            var foreign = new ProductWorkspace(new ConfigurationBuilder().AddInMemoryCollection(
                new Dictionary<string, string?> { ["AEGIS_PRODUCT_STATE_DIRECTORY"] = foreignRoot }).Build(),
                restartSettings with { ArchiveDirectory = foreignArchive });
            foreign.CaptureSafely([WarningAt(WarningLifecycle.OPEN)]);
            check(original.SequenceEqual(File.ReadAllBytes(foreignPath)), "workspace creation does not adopt or modify foreign database");
            await app.StopAsync();
        }
        finally { root.Delete(true); }
    }

    private sealed class ArchiveHandler : DelegatingHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var response = request.RequestUri?.AbsolutePath switch
            {
                "/v1/archive-sessions" => """{"schemaVersion":"archive-sessions-1","sessions":[]}""",
                "/v1/archive-sessions/run-abcdef12/records" => """{"schemaVersion":"stored-record-query-1","records":[]}""",
                _ => null,
            };
            return Task.FromResult(response is null ? new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
                : new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(response, Encoding.UTF8, "application/json") });
        }
    }
}
