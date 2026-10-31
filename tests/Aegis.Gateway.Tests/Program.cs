using System.Net;
using System.Text;
using System.Text.Json;
using Aegis.Gateway;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

const string token = "gateway-test-credential-with-more-than-32-chars";
await using var app = GatewayApplication.Build([], builder =>
{
    builder.WebHost.UseTestServer();
    builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?>
    { ["AEGIS_DASHBOARD_TOKEN"] = token, ["AEGIS_API_TOKEN"] = token, ["AEGIS_API_URL"] = "http://127.0.0.1:8765", ["AEGIS_GATEWAY_URL"] = "http://127.0.0.1:8780" });
    builder.Services.AddHttpClient("upstream").ConfigurePrimaryHttpMessageHandler(() => new UnavailableHandler());
});
await app.StartAsync();
var server = app.GetTestServer();
int checks = 0;
void Check(bool condition, string name) { if (!condition) throw new InvalidOperationException(name); checks++; }
async Task<HttpContext> Request(string path, string method = "GET", string? origin = null, string? cookie = null, string? body = null, string? host = null, bool websocket = false, string? bootstrap = null, bool remote = false)
{
    return await server.SendAsync(context =>
    {
        context.Connection.RemoteIpAddress = remote ? IPAddress.Parse("192.0.2.1") : IPAddress.Loopback;
        context.Request.Method = method; context.Request.Scheme = "http"; context.Request.Host = new HostString(host ?? "127.0.0.1:8780"); context.Request.Path = path;
        if (origin is not null) context.Request.Headers.Origin = origin;
        if (cookie is not null) context.Request.Headers.Cookie = cookie;
        if (bootstrap is not null) context.Request.Headers["X-Aegis-Dashboard-Token"] = bootstrap;
        if (websocket) context.Request.Headers.Upgrade = "websocket";
        if (body is not null) { var bytes = Encoding.UTF8.GetBytes(body); context.Request.ContentType = "application/json"; context.Request.ContentLength = bytes.Length; context.Request.Body = new MemoryStream(bytes); }
    });
}
Check((await Request("/health")).Response.StatusCode == 200, "health");
Check((await Request("/api/dashboard")).Response.StatusCode == 401, "anonymous rejected");
Check((await Request("/health", host: "evil.example:8780")).Response.StatusCode == 400, "host rejected");
Check((await Request("/health", host: "localhost:8780")).Response.StatusCode == 400, "literal host enforced");
Check((await Request("/health", remote: true)).Response.StatusCode == 400, "remote rejected");
var duplicateHost = await server.SendAsync(context =>
{
    context.Connection.RemoteIpAddress = IPAddress.Loopback;
    context.Request.Method = "GET"; context.Request.Path = "/health";
    context.Request.Headers.Host = new Microsoft.Extensions.Primitives.StringValues(["127.0.0.1:8780", "evil.example"]);
});
Check(duplicateHost.Response.StatusCode == 400, "duplicate host rejected");
Check((await Request("/api/session", "POST", "http://evil.example", body: JsonSerializer.Serialize(new { token }))).Response.StatusCode == 403, "foreign origin rejected");
Check((await Request("/api/session", "POST", body: JsonSerializer.Serialize(new { token }))).Response.StatusCode == 403, "missing origin rejected");
Check((await Request("/api/session", "POST", "http://127.0.0.1:8780", body: "{\"token\":\"wrong\"}")).Response.StatusCode == 401, "wrong token rejected");
var login = await Request("/api/session", "POST", "http://127.0.0.1:8780", body: JsonSerializer.Serialize(new { token }));
Check(login.Response.StatusCode == 200, "session login");
var setCookie = login.Response.Headers.SetCookie.ToString();
Check(setCookie.Contains("httponly", StringComparison.OrdinalIgnoreCase) && setCookie.Contains("samesite=strict", StringComparison.OrdinalIgnoreCase) && !setCookie.Contains(token), "cookie flags no secret");
var cookie = setCookie.Split(';')[0];
Check((await Request("/api/session", cookie: cookie)).Response.StatusCode == 200, "session authenticated");
Check((await Request("/hubs/dashboard", origin: "http://evil.example", cookie: cookie, websocket: true)).Response.StatusCode == 403, "foreign websocket origin rejected");
Check((await Request("/hubs/dashboard", cookie: cookie, websocket: true)).Response.StatusCode == 403, "missing websocket origin rejected");
Check((await Request("/api/dashboard", cookie: cookie)).Response.StatusCode == 200, "dashboard authenticated");
var bootstrap = await Request("/", bootstrap: token);
Check(bootstrap.Response.Headers.SetCookie.Count > 0, "native header bootstrap cookie");
await app.Services.GetRequiredService<DashboardCache>().RefreshAsync(CancellationToken.None);
Check(app.Services.GetRequiredService<DashboardCache>().Latest.UpstreamState == "UNAVAILABLE", "upstream failure safe");
Check((await Request("/api/session", "DELETE", "http://127.0.0.1:8780", cookie)).Response.StatusCode == 204, "logout");
Check((await Request("/api/session", cookie: cookie)).Response.StatusCode == 401, "logout revokes session");
Check(!new DashboardSessions(app.Services.GetRequiredService<GatewaySettings>()).Authenticated(new DefaultHttpContext
{ Request = { Headers = { Cookie = cookie } } }), "sessions do not survive process restart");
Check(GatewaySettings.LocalUri("http://localhost:8765") is null && GatewaySettings.LocalUri("http://127.0.0.1:8765/path") is null, "upstream loopback settings");
var temporary = Path.Combine(Path.GetTempPath(), "aegis-gateway-check-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(temporary);
try
{
    var settings = new GatewaySettings(new("http://127.0.0.1:8780"), null, null, token, temporary);
    var review = new ArchiveReview(settings);
    Check(review.Read("device-1", null, null).EngineState == "UNAVAILABLE" && !Directory.EnumerateFiles(temporary).Any(), "missing archive never created");
    var path = Path.Combine(temporary, ArchiveReview.FileName("device-1"));
    using (var database = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Pooling = false }.ToString()))
    {
        database.Open(); using var command = database.CreateCommand();
        command.CommandText = "PRAGMA user_version=1; CREATE TABLE raw_batches(device_id TEXT,run_id TEXT,sequence INTEGER,observed_at_utc TEXT,received_at_utc TEXT,equipment_id TEXT,module_id TEXT,payload_json TEXT);";
        command.ExecuteNonQuery();
    }
    var original = File.ReadAllBytes(path);
    Check(review.Read("device-1", null, null).EngineState == "UNAVAILABLE", "empty archive unavailable");
    Check(original.SequenceEqual(File.ReadAllBytes(path)) && Directory.EnumerateFiles(temporary).Count() == 1, "archive read does not mutate database");
    Check(new ArchiveReview(settings with { ArchiveDirectory = null }).Read("device-1", null, null).EngineState == "ARCHIVE_NOT_CONFIGURED", "archive not configured explicit");
    CheckThrows(() => WireValidation.Identity("../escape"), "archive identity traversal blocked");
}
finally { Directory.Delete(temporary, true); }
void CheckThrows(Action action, string name) { try { action(); } catch (InvalidDataException) { Check(true, name); return; } throw new InvalidOperationException(name); }
await RawAndCacheChecks.Run(Check, app.Services.GetRequiredService<Microsoft.AspNetCore.SignalR.IHubContext<DashboardHub>>());
Console.WriteLine($"PASS {checks} in-process gateway checks (no network listeners).");
await app.StopAsync();

sealed class UnavailableHandler : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => throw new HttpRequestException("Simulated upstream failure.");
}
