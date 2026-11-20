using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http.Connections;
using Microsoft.AspNetCore.SignalR;
using Microsoft.Extensions.FileProviders;

namespace Aegis.Gateway;

public sealed record GatewaySettings(Uri Origin, Uri? Upstream, string? UpstreamToken, string? DashboardToken, string? ArchiveDirectory)
{
    public static GatewaySettings Read(IConfiguration config)
    {
        var origin = LocalUri(config["AEGIS_GATEWAY_URL"] ?? "http://127.0.0.1:8780")
            ?? throw new InvalidOperationException("Gateway requires a literal loopback HTTP origin.");
        return new(origin, LocalUri(config["AEGIS_API_URL"] ?? "http://127.0.0.1:8765"),
            Token(config["AEGIS_API_TOKEN"]), Token(config["AEGIS_DASHBOARD_TOKEN"]), config["AEGIS_ARCHIVE_DIRECTORY"]);
    }
    public static Uri? LocalUri(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.UserInfo == "" && uri.AbsolutePath == "/"
        && uri.Query == "" && uri.Fragment == "" && value.TrimEnd('/') == uri.GetLeftPart(UriPartial.Authority) ? uri : null;
    private static string? Token(string? value) => value is { Length: >= 32 and <= 512 } && value.Trim().Length >= 32
        && !value.Any(char.IsControl) ? value : null;
}

public static class GatewayApplication
{
    public static WebApplication Build(string[] args, Action<WebApplicationBuilder>? configure = null)
    {
        var builder = WebApplication.CreateBuilder(args);
        configure?.Invoke(builder);
        var settings = GatewaySettings.Read(builder.Configuration);
        builder.WebHost.UseUrls(settings.Origin.GetLeftPart(UriPartial.Authority));
        builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 4096);
        builder.Logging.ClearProviders(); // Never log request headers, tokens or raw upstream exceptions.
        builder.Services.AddSingleton(settings);
        builder.Services.AddSingleton<DashboardSessions>();
        builder.Services.AddSingleton<ArchiveReview>();
        builder.Services.AddSingleton<DashboardCache>();
        builder.Services.AddHostedService(provider => provider.GetRequiredService<DashboardCache>());
        builder.Services.AddSignalR(options => { options.EnableDetailedErrors = false; options.MaximumReceiveMessageSize = 4096; options.MaximumParallelInvocationsPerClient = 1; });
        builder.Services.ConfigureHttpJsonOptions(options => options.SerializerOptions.Converters.Add(new System.Text.Json.Serialization.JsonStringEnumConverter()));
        builder.Services.AddHttpClient("upstream", client => { client.Timeout = TimeSpan.FromSeconds(2); })
            .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler { UseProxy = false, AllowAutoRedirect = false, MaxResponseHeadersLength = 16, ConnectTimeout = TimeSpan.FromSeconds(1) });
        var app = builder.Build();
        app.Use(async (context, next) =>
        {
            context.Response.Headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws://127.0.0.1:" + settings.Origin.Port + "; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";
            context.Response.Headers["X-Content-Type-Options"] = "nosniff";
            context.Response.Headers["Referrer-Policy"] = "no-referrer";
            context.Response.Headers.CacheControl = "no-store";
            var origins = context.Request.Headers.Origin;
            var expected = settings.Origin.GetLeftPart(UriPartial.Authority);
            if (!IPAddress.Loopback.Equals(context.Connection.RemoteIpAddress)
                || context.Request.Headers.Host.Count != 1 || context.Request.Host.Value != settings.Origin.Authority)
            { context.Response.StatusCode = 400; return; }
            var hub = context.Request.Path.StartsWithSegments("/hubs");
            if ((origins.Count > 0 && (origins.Count != 1 || origins[0] != expected))
                || (hub && (context.WebSockets.IsWebSocketRequest || context.Request.Headers.Upgrade.Any(value => string.Equals(value, "websocket", StringComparison.OrdinalIgnoreCase))) && origins.Count != 1)
                || context.Request.Query.Keys.Any(key => key.Contains("token", StringComparison.OrdinalIgnoreCase)))
            { context.Response.StatusCode = 403; return; }
            var sessions = context.RequestServices.GetRequiredService<DashboardSessions>();
            var header = context.Request.Headers["X-Aegis-Dashboard-Token"];
            if (header.Count > 0)
            {
                if (header.Count != 1 || !sessions.ValidToken(header[0])) { context.Response.StatusCode = 401; return; }
                sessions.Create(context);
            }
            if (context.Request.Path.StartsWithSegments("/api/session"))
            {
                if (context.Request.Method is "POST" or "DELETE" && (origins.Count != 1 || origins[0] != expected))
                { context.Response.StatusCode = 403; return; }
            }
            else if ((context.Request.Path.StartsWithSegments("/api") || hub) && !sessions.Authenticated(context))
            { context.Response.StatusCode = 401; return; }
            try { await next(context); }
            catch (Exception) when (!context.Response.HasStarted)
            { context.Response.StatusCode = 500; await context.Response.WriteAsJsonAsync(new { message = "Request unavailable." }); }
        });
        app.MapGet("/health", () => Results.Json(new { status = "OK", schemaVersion = "dashboard-1" }));
        app.MapGet("/api/session", (HttpContext context, DashboardSessions sessions) => sessions.Authenticated(context) ? Results.Ok(new { authenticated = true }) : Results.Unauthorized());
        app.MapPost("/api/session", async (HttpContext context, DashboardSessions sessions) =>
        {
            if (context.Request.ContentLength is > 4096) return Results.StatusCode(413);
            try
            {
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted);
                deadline.CancelAfter(TimeSpan.FromSeconds(2));
                using var memory = new MemoryStream();
                var buffer = new byte[4097];
                var read = 0;
                while (read < buffer.Length)
                { var count = await context.Request.Body.ReadAsync(buffer.AsMemory(read), deadline.Token); if (count == 0) break; read += count; }
                if (read > 4096) return Results.StatusCode(413);
                using var body = JsonDocument.Parse(buffer.AsMemory(0, read), new() { MaxDepth = 4 });
                if (body.RootElement.ValueKind != JsonValueKind.Object || body.RootElement.EnumerateObject().Count() != 1
                    || !body.RootElement.TryGetProperty("token", out var token) || token.ValueKind != JsonValueKind.String || !sessions.ValidToken(token.GetString())) return Results.Unauthorized();
                sessions.Create(context);
                return Results.Ok(new { authenticated = true });
            }
            catch (Exception error) when (error is JsonException or OperationCanceledException) { return Results.BadRequest(); }
        });
        app.MapDelete("/api/session", (HttpContext context, DashboardSessions sessions) => { sessions.Delete(context); return Results.NoContent(); });
        app.MapGet("/api/dashboard", (DashboardCache cache) => Results.Json(cache.Latest));
        app.MapGet("/api/stored-records", async (HttpContext context, IHttpClientFactory clients) =>
        {
            if (settings.Upstream is null || settings.UpstreamToken is null) return Results.StatusCode(503);
            var query = context.Request.Query;
            bool selectedRun=query.ContainsKey("runId");
            if (context.Request.QueryString.Value?.Length > 2600 || query.Keys.Any(key =>
                key is not ("receivedFromUtc" or "receivedToUtc" or "signals" or "deviceId" or "storeId" or "runId") || query[key].Count != 1)
                || !query.ContainsKey("receivedFromUtc") || !query.ContainsKey("receivedToUtc")
                || selectedRun!=query.ContainsKey("storeId") || selectedRun&&!query.ContainsKey("deviceId")) return Results.BadRequest();
            try
            {
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted);
                deadline.CancelAfter(TimeSpan.FromSeconds(7));
                using var client = clients.CreateClient("upstream");
                client.Timeout = TimeSpan.FromSeconds(7);
                using var request = new HttpRequestMessage(HttpMethod.Get,
                    new Uri(settings.Upstream, "v1/stored-records" + context.Request.QueryString));
                request.Headers.Add("X-Aegis-Token", settings.UpstreamToken);
                using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
                await using var stream = await response.Content.ReadAsStreamAsync(deadline.Token);
                var bytes = new byte[response.IsSuccessStatusCode ? 4 * 1024 * 1024 + 1 : 4097];
                var length = 0;
                while (length < bytes.Length)
                { var count = await stream.ReadAsync(bytes.AsMemory(length), deadline.Token); if (count == 0) break; length += count; }
                if (!response.IsSuccessStatusCode)
                {
                    var status=response.StatusCode switch
                    { HttpStatusCode.NotFound => 404, HttpStatusCode.UnprocessableEntity => 422,
                      HttpStatusCode.RequestEntityTooLarge => 413, _ => 503 };
                    string? code=null;
                    if(length>0&&length<bytes.Length)
                    {
                        try
                        {
                            using var error=JsonDocument.Parse(bytes.AsMemory(0,length),new(){MaxDepth=8});
                            if(error.RootElement.ValueKind==JsonValueKind.Object&&error.RootElement.TryGetProperty("code",out var value)
                                &&value.ValueKind==JsonValueKind.String)
                                code=value.GetString();
                        }
                        catch(JsonException){}
                    }
                    return code is "INVALID_QUERY" or "QUERY_LIMIT" or "SOURCE_UNAVAILABLE" or "SOURCE_INVALID"
                        or "SOURCE_BINDING_MISMATCH" or "DUPLICATE_SOURCE"
                        ? Results.Json(new {code},statusCode:status) : Results.StatusCode(status);
                }
                if (length == bytes.Length) return Results.StatusCode(413);
                using var body = JsonDocument.Parse(bytes.AsMemory(0, length), new() { MaxDepth = 32 });
                if (!body.RootElement.TryGetProperty("schemaVersion", out var schema)
                    || schema.GetString() != "stored-record-query-1") return Results.StatusCode(503);
                if(selectedRun&&!WireValidation.ValidRunEvidence(body.RootElement,query["storeId"].ToString(),
                    query["deviceId"].ToString(),query["runId"].ToString()))return Results.StatusCode(503);
                return Results.Json(body.RootElement.Clone());
            }
            catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException or InvalidOperationException)
            { return Results.StatusCode(503); }
        });
        app.MapHub<DashboardHub>("/hubs/dashboard", options =>
        { options.Transports = HttpTransportType.WebSockets | HttpTransportType.LongPolling; options.ApplicationMaxBufferSize = 4096; options.TransportMaxBufferSize = 65536; options.TransportSendTimeout = TimeSpan.FromSeconds(2); options.LongPolling.PollTimeout = TimeSpan.FromSeconds(20); });
        var staticDirectory = Path.Combine(app.Environment.ContentRootPath, "..", "..", "ui", "dist");
        var published = Path.Combine(AppContext.BaseDirectory, "wwwroot");
        if (Directory.Exists(published)) staticDirectory = published;
        if (Directory.Exists(staticDirectory))
        {
            var files = new PhysicalFileProvider(Path.GetFullPath(staticDirectory));
            app.UseDefaultFiles(new DefaultFilesOptions { FileProvider = files });
            app.UseStaticFiles(new StaticFileOptions { FileProvider = files });
        }
        return app;
    }
}

public sealed class DashboardHub : Hub;

public sealed class DashboardSessions(GatewaySettings settings)
{
    private const string Cookie = "aegis-dashboard";
    private readonly Dictionary<string, DateTimeOffset> _sessions = new(StringComparer.Ordinal);
    private readonly object _gate = new();
    public bool ValidToken(string? supplied) => settings.DashboardToken is not null && supplied is { Length: <= 512 }
        && CryptographicOperations.FixedTimeEquals(SHA256.HashData(Encoding.UTF8.GetBytes(supplied)), SHA256.HashData(Encoding.UTF8.GetBytes(settings.DashboardToken)));
    public bool Authenticated(HttpContext context)
    {
        if (context.Items.ContainsKey(Cookie)) return true;
        var supplied = context.Request.Cookies[Cookie];
        if (supplied is not { Length: 64 }) return false;
        lock (_gate) return _sessions.Any(entry => entry.Value > DateTimeOffset.UtcNow
            && CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(entry.Key), Encoding.UTF8.GetBytes(supplied)));
    }
    public void Create(HttpContext context)
    {
        var value = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        lock (_gate)
        {
            foreach (var key in _sessions.Where(entry => entry.Value <= DateTimeOffset.UtcNow).Select(entry => entry.Key).ToArray()) _sessions.Remove(key);
            if (_sessions.Count >= 32) _sessions.Remove(_sessions.MinBy(entry => entry.Value).Key);
            _sessions[value] = DateTimeOffset.UtcNow.AddHours(8);
        }
        context.Items[Cookie] = true;
        context.Response.Cookies.Append(Cookie, value, new CookieOptions { HttpOnly = true, SameSite = SameSiteMode.Strict, IsEssential = true, Path = "/", MaxAge = TimeSpan.FromHours(8) });
    }
    public void Delete(HttpContext context)
    { lock (_gate) { if (context.Request.Cookies[Cookie] is { } value) _sessions.Remove(value); } context.Response.Cookies.Delete(Cookie, new CookieOptions { Path = "/", SameSite = SameSiteMode.Strict, HttpOnly = true }); }
}
