using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace Aegis.Gateway;

internal static class ProductEndpoints
{
    public static void Map(WebApplication app, GatewaySettings settings)
    {
        app.MapGet("/api/local-diagnostics", (HttpContext context, DashboardCache cache, ProductWorkspace workspace) =>
            context.Request.Query.Count == 0 ? Results.Json(LocalDiagnostics.Project(cache.Latest, workspace))
                : Results.BadRequest());
        app.MapGet("/api/incidents", (HttpContext context, ProductWorkspace workspace) =>
        {
            var query = context.Request.Query;
            if (query.Count == 0) return Run(workspace, () => Results.Json(workspace.Incidents(null)));
            if (query.Count != 1 || query["deviceId"].Count != 1 || !Identified(query["deviceId"].ToString()))
                return Results.BadRequest();
            return Run(workspace, () => Results.Json(workspace.Incidents(query["deviceId"].ToString())));
        });
        app.MapPost("/api/incidents/{id}/review", (string id, HttpContext context, ProductWorkspace workspace) =>
        {
            if (!SameOrigin(context, settings)) return Results.StatusCode(403);
            if (!Identified(id) || context.Request.ContentLength is > 0 || context.Request.Headers.TransferEncoding.Count != 0)
                return Results.BadRequest();
            return Run(workspace, () => workspace.Review(id) is { } at ?
                Results.Json(new { schemaVersion = "incident-review-1", reviewedAtUtc = at }) : Results.NotFound());
        });
        app.MapGet("/api/analysis-profile/{deviceId}", (string deviceId, ProductWorkspace workspace, DashboardCache cache) =>
            Identified(deviceId) ? Run(workspace, () => Results.Json(ProductWorkspace.Describe(workspace.Profile(deviceId), RuleRunning(cache, deviceId))))
                : Results.BadRequest());
        app.MapPut("/api/analysis-profile/{deviceId}", async (string deviceId, HttpContext context, ProductWorkspace workspace, DashboardCache cache) =>
        {
            if (!SameOrigin(context, settings)) return Results.StatusCode(403);
            if (!Identified(deviceId)) return Results.BadRequest();
            try
            {
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted);
                deadline.CancelAfter(TimeSpan.FromSeconds(2));
                using var document = await JsonDocument.ParseAsync(context.Request.Body, new() { MaxDepth = 3 }, deadline.Token);
                var profile = Parse(deviceId, document.RootElement);
                return profile is null ? Results.BadRequest()
                    : Run(workspace, () => workspace.Save(profile)
                        ? Results.Json(ProductWorkspace.Describe(profile with { Version = profile.Version + 1 }, RuleRunning(cache, deviceId)))
                        : Results.Conflict(new { code = "PROFILE_VERSION_CHANGED" }));
            }
            catch (JsonException) { return Results.BadRequest(); }
            catch (OperationCanceledException) { return Results.StatusCode(408); }
            catch (BadHttpRequestException error) { return Results.StatusCode(error.StatusCode); }
        });
    }

    private static bool SameOrigin(HttpContext context, GatewaySettings settings) =>
        context.Request.Headers.Origin.Count == 1 && context.Request.Headers.Origin[0] == settings.Origin.GetLeftPart(UriPartial.Authority);

    private static bool Identified(string value)
    {
        try { return !value.Any(char.IsControl) && WireValidation.Identity(value) == value; }
        catch (InvalidDataException) { return false; }
    }

    private static bool RuleRunning(DashboardCache cache, string deviceId)
    {
        var snapshot = cache.Latest;
        return snapshot.UpstreamState == "CONNECTED" && snapshot.Devices.Any(device =>
            device.DeviceId == deviceId && device.Diagnostics?["enabled"]?.GetValue<bool>() == true
            && device.Diagnostics?["evaluation"]?.GetValue<string>() != "UNAVAILABLE"
            && device.Diagnostics?["modelVersion"]?.GetValue<string>() == "ewma-flow-dev-1");
    }

    private static IResult Run(ProductWorkspace workspace, Func<IResult> action)
    {
        if (!workspace.Enabled) return Results.Json(new { code = "WORKSPACE_NOT_CONFIGURED" }, statusCode: 503);
        try { return action(); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or SqliteException)
        { return Results.Json(new { code = "WORKSPACE_UNAVAILABLE" }, statusCode: 503); }
    }

    private static AnalysisProfile? Parse(string deviceId, JsonElement root)
    {
        string[] keys = ["mode", "modelId", "description", "historySessionId", "version"];
        if (root.ValueKind != JsonValueKind.Object) return null;
        var properties = root.EnumerateObject().Select(property => property.Name).ToArray();
        if (properties.Length != keys.Length || !properties.ToHashSet(StringComparer.Ordinal).SetEquals(keys)) return null;
        var mode = root.GetProperty("mode");
        var model = root.GetProperty("modelId");
        var text = root.GetProperty("description");
        var history = root.GetProperty("historySessionId");
        var version = root.GetProperty("version");
        if (mode.ValueKind != JsonValueKind.String || !mode.ValueEquals("MANUAL") && !mode.ValueEquals("AUTO")
            || model.ValueKind != JsonValueKind.Null && (model.ValueKind != JsonValueKind.String || !model.ValueEquals("ewma-flow-dev-1"))
            || mode.ValueEquals("MANUAL") != (model.ValueKind == JsonValueKind.String)
            || text.ValueKind != JsonValueKind.String || text.GetString()!.Length > 300
            || text.GetString()!.Any(ch => char.IsControl(ch) && ch is not ('\n' or '\r' or '\t'))
            || history.ValueKind != JsonValueKind.Null && (history.ValueKind != JsonValueKind.String || !ProductWorkspace.SessionId(history.GetString()!))
            || version.ValueKind != JsonValueKind.Number || !version.TryGetInt32(out var revision) || revision is < 0 or int.MaxValue)
            return null;
        return new(deviceId, mode.GetString()!, model.GetString(), text.GetString()!, history.GetString(), revision);
    }
}
