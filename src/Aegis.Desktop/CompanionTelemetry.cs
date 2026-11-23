using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using Aegis.Contracts;

namespace Aegis.Desktop;

internal sealed record CompanionPin(string DeviceId, string SignalKey);
internal sealed record CompanionSignal(CompanionPin Pin, string Label, string Value, string Unit, string State, string ObservedAt);
internal sealed record CompanionCollector(string Label, string State, string ReceivedAt);
internal sealed record CompanionFrame(IReadOnlyList<CompanionSignal> Signals, IReadOnlyList<CompanionCollector> Collectors, string Status);

internal sealed class CompanionTelemetryClient : IDisposable
{
    private const int MaximumBytes = 4 * 1024 * 1024;
    private readonly Uri _dashboard;
    private readonly string _token;
    private readonly HttpClient _http;
    private bool _bootstrapAttempted;

    public CompanionTelemetryClient(DashboardEndpoint endpoint, HttpMessageHandler? handler = null)
    {
        _dashboard = new Uri(endpoint.Origin, "/api/dashboard");
        _token = endpoint.BootstrapToken;
        _http = new HttpClient(handler ?? new SocketsHttpHandler
        {
            UseProxy = false,
            AllowAutoRedirect = false,
            UseCookies = true,
            ConnectTimeout = TimeSpan.FromSeconds(1),
            MaxResponseHeadersLength = 16
        }) { Timeout = Timeout.InfiniteTimeSpan };
    }

    public async Task<CompanionSnapshot> ReadAsync(CancellationToken cancellationToken)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(TimeSpan.FromSeconds(3));
        if (!_bootstrapAttempted)
        {
            _bootstrapAttempted = true;
            using var bootstrap = await SendAsync(true, deadline.Token).ConfigureAwait(false);
            return await ReadSnapshotAsync(bootstrap, deadline.Token).ConfigureAwait(false);
        }
        using var response = await SendAsync(false, deadline.Token).ConfigureAwait(false);
        if (response.StatusCode != HttpStatusCode.Unauthorized)
            return await ReadSnapshotAsync(response, deadline.Token).ConfigureAwait(false);
        using var renewed = await SendAsync(true, deadline.Token).ConfigureAwait(false);
        return await ReadSnapshotAsync(renewed, deadline.Token).ConfigureAwait(false);
    }

    private async Task<HttpResponseMessage> SendAsync(bool bootstrap, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, _dashboard);
        if (bootstrap) request.Headers.Add("X-Aegis-Dashboard-Token", _token);
        request.Headers.Accept.ParseAdd("application/json");
        return await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
    }

    private static async Task<CompanionSnapshot> ReadSnapshotAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        response.EnsureSuccessStatusCode();
        if (response.Content.Headers.ContentType?.MediaType != "application/json" ||
            response.Content.Headers.ContentLength > MaximumBytes)
            throw new JsonException("Invalid dashboard snapshot content.");
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var output = new MemoryStream();
        var buffer = new byte[8192];
        int read;
        while ((read = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) != 0)
        {
            if (output.Length + read > MaximumBytes) throw new JsonException("Dashboard snapshot exceeded the response limit.");
            output.Write(buffer, 0, read);
        }
        using var document = JsonDocument.Parse(output.GetBuffer().AsMemory(0, (int)output.Length),
            new JsonDocumentOptions { MaxDepth = 24, AllowDuplicateProperties = false });
        return CompanionSnapshot.Parse(document.RootElement);
    }

    public void Dispose() => _http.Dispose();
}

internal sealed class CompanionSnapshot
{
    private sealed record Device(string Id, string? Module, string? Connection, DateTimeOffset? LastReceived, bool Restored,
        Receipt? Collection, IReadOnlyDictionary<SignalKey, Observation> Observations);
    private sealed record Receipt(string Mode, string Freshness, string Worker, string Provenance,
        DateTimeOffset? Received, DateTimeOffset? ValidUntil);
    private static readonly Dictionary<SignalKey, (string Label, string Unit)> Profile = new()
    {
        [SignalKey.actualFlowLpm] = ("실측 유량", "L/min"),
        [SignalKey.supplyPressureKpa] = ("공급 압력", "kPa"),
        [SignalKey.motorCurrentAmp] = ("모터 전류", "A"),
        [SignalKey.temperatureC] = ("온도", "degC"),
        [SignalKey.valveFeedbackPct] = ("밸브 피드백", "%"),
        [SignalKey.setpointFlowLpm] = ("설정 유량", "L/min"),
        [SignalKey.valveCommandPct] = ("밸브 명령", "%"),
        [SignalKey.cycleCount] = ("사이클 수", "count")
    };
    private static readonly Dictionary<string, SignalKey> SignalNames =
        Profile.Keys.ToDictionary(key => key.ToString(), StringComparer.Ordinal);
    private readonly string _upstream;
    private readonly string _mode;
    private readonly DateTimeOffset _generated;
    private readonly IReadOnlyDictionary<string, Device> _devices;

    public static CompanionSnapshot Empty { get; } = new("CONFIGURATION_REQUIRED", "UNKNOWN", default, []);
    public IReadOnlyList<CompanionPin> Catalog { get; }

    private CompanionSnapshot(string upstream, string mode, DateTimeOffset generated, IReadOnlyList<Device> devices)
    {
        _upstream = upstream;
        _mode = mode;
        _generated = generated;
        _devices = devices.ToDictionary(device => device.Id, StringComparer.Ordinal);
        Catalog = devices.OrderBy(device => device.Id, StringComparer.Ordinal)
            .SelectMany(device => SignalNames.Keys.Select(signal => new CompanionPin(device.Id, signal))).ToArray();
    }

    public static bool IsKnownPin(CompanionPin? pin) =>
        pin is { DeviceId: { } id, SignalKey: { } signal } && ValidIdentity(id) && SignalNames.ContainsKey(signal);

    public string LabelFor(CompanionPin pin)
    {
        var name = SignalNames.TryGetValue(pin.SignalKey, out var key) ? Profile[key].Label : "태그";
        return _devices.TryGetValue(pin.DeviceId, out var device) && device.Module is { } module && module != pin.DeviceId
            ? $"{module} · {name} ({pin.DeviceId})" : $"{pin.DeviceId} · {name}";
    }

    public CompanionFrame BuildFrame(IReadOnlyList<CompanionPin> pins, DateTimeOffset now, bool connected)
    {
        var disconnected = !connected || _upstream == "UNAVAILABLE";
        var snapshotCurrent = !disconnected && _upstream == "CONNECTED" && _mode == "EMULATORS" && Fresh(_generated, now);
        var status = _mode switch
        {
            "EMULATORS" when disconnected => "합성 · 연결 끊김",
            "EMULATORS" when snapshotCurrent => "합성 데이터",
            "EMULATORS" => "합성 · 수신 지연",
            "FIXTURES" when !disconnected => "고정 자료",
            _ when disconnected => "연결 확인",
            _ => "수집 대기"
        };
        var selectedDevices = pins.Select(pin => pin.DeviceId).Distinct(StringComparer.Ordinal);
        var orderedDevices = selectedDevices.Concat(_devices.Keys.Order(StringComparer.Ordinal)).Distinct(StringComparer.Ordinal);
        var collectors = new List<CompanionCollector>();
        foreach (var id in orderedDevices)
        {
            if (_devices.TryGetValue(id, out var device))
                collectors.Add(new(device.Module ?? id, CollectorState(device, now, snapshotCurrent, disconnected),
                    DisplayTime(device.Collection?.Received ?? device.LastReceived, now)));
        }
        return new(pins.Select(pin => ProjectSignal(pin, now, snapshotCurrent, disconnected)).ToArray(), collectors, status);
    }

    private CompanionSignal ProjectSignal(CompanionPin pin, DateTimeOffset now, bool snapshotCurrent, bool disconnected)
    {
        if (!SignalNames.TryGetValue(pin.SignalKey, out var key))
            return new(pin, "태그", "—", "", "미수신", "—");
        var (label, unit) = Profile[key];
        if (!_devices.TryGetValue(pin.DeviceId, out var device) || !device.Observations.TryGetValue(key, out var observation))
            return new(pin, label, "—", "", "미수신", "—");
        var missing = observation.Quality == ObservationQuality.MISSING || observation.Value is null;
        var state = observation.Quality switch
        {
            _ when missing => "값 없음",
            ObservationQuality.BAD => "품질 불량",
            _ when IsRestored(device) => "복원 이력",
            _ when _mode == "FIXTURES" => "고정 자료",
            ObservationQuality.GOOD when snapshotCurrent && DeviceCurrent(device, now)
                && Fresh(observation.ObservedAtUtc, now) && Fresh(observation.ReceivedAtUtc, now) => "실시간",
            _ when disconnected => "연결 끊김",
            _ => "오래됨"
        };
        return new(pin, label, missing ? "—" : DisplayValue(observation.Value!.Value),
            missing ? "" : unit == "degC" ? "°C" : unit, state, DisplayTime(observation.ObservedAtUtc, now));
    }

    private string CollectorState(Device device, DateTimeOffset now, bool snapshotCurrent, bool disconnected)
    {
        if (disconnected) return "연결 끊김";
        if (_mode == "FIXTURES" || device.Collection?.Mode == "FIXTURE") return "고정 자료";
        var receipt = device.Collection;
        if (!snapshotCurrent) return "확인 불가";
        return receipt?.Worker switch
        {
            "RESTARTING" => "재시작 중",
            "FAILED" => "수집 실패",
            "UNAVAILABLE" => "확인 불가",
            _ when IsRestored(device) => "복원 이력",
            _ when DeviceCurrent(device, now) => "수집 중",
            _ => receipt?.Freshness switch
            {
                "RESTORED" => "복원 이력",
                "NO_RECEIPT" => "수신 대기",
                "UNAVAILABLE" => "확인 불가",
                "CURRENT" or "STALE" => "수신 지연",
                _ => device.Connection switch
                {
                    "DISCONNECTED" => "연결 끊김",
                    "STALE" or "CONNECTED" => "수신 지연",
                    "CONNECTING" => "수신 대기",
                    "REJECTED" => "수집 실패",
                    _ => "확인 불가"
                }
            }
        };
    }

    private static bool IsRestored(Device device) =>
        device.Collection?.Provenance == "RESTORED" || device.Collection is null && device.Restored;

    private static bool DeviceCurrent(Device device, DateTimeOffset now) =>
        device.Connection == "CONNECTED" && !IsRestored(device) &&
        (device.Collection is { } receipt
            ? receipt.Mode != "FIXTURE" && receipt.Freshness == "CURRENT" && receipt.Provenance == "ACCEPTED" &&
              receipt.Worker is "RUNNING" or "NOT_APPLICABLE" &&
              receipt.Received is { } received && received <= now &&
              receipt.ValidUntil is { } validUntil && now < validUntil
            : device.LastReceived is { } lastReceived && Fresh(lastReceived, now));

    private static bool Fresh(DateTimeOffset value, DateTimeOffset now) =>
        now - value < TimeSpan.FromSeconds(3) && value - now <= TimeSpan.FromSeconds(1);

    private static string DisplayValue(double value) => value.ToString(
        Math.Abs(value) is (> 0 and < 0.001) or >= 1e9 ? "0.###E+0" : "#,##0.###", CultureInfo.InvariantCulture);

    private static string DisplayTime(DateTimeOffset? value, DateTimeOffset now)
    {
        if (value is null) return "—";
        var local = value.Value.ToLocalTime();
        return local.ToString(local.Date == now.ToLocalTime().Date ? "HH:mm:ss" : "MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
    }

    internal static CompanionSnapshot Parse(JsonElement root)
    {
        if (Text(root, "schemaVersion") != "dashboard-1") throw new JsonException("Unsupported dashboard snapshot.");
        var upstream = Choice(root, "upstreamState", "CONNECTED", "UNAVAILABLE", "CONFIGURATION_REQUIRED");
        var mode = Choice(root, "sourceMode", "EMULATORS", "FIXTURES", "UNKNOWN");
        var generated = Timestamp(root, "generatedAtUtc", required: true)!.Value;
        var devices = new Dictionary<string, Device>(StringComparer.Ordinal);
        foreach (var device in Array(root, "devices", 32))
        {
            var id = Text(device, "deviceId");
            var module = OptionalObject(device, "moduleId", JsonValueKind.String)?.GetString();
            if (!ValidIdentity(id) || module is not null && !ValidIdentity(module))
                throw new JsonException("Invalid dashboard device identity.");
            var connection = OptionalObject(device, "connection", JsonValueKind.Object);
            var state = connection is { } row
                ? Choice(row, "state", "CONNECTING", "CONNECTED", "DISCONNECTED", "REJECTED", "STALE") : null;
            var received = connection is { } seen ? Timestamp(seen, "lastReceivedAtUtc") : null;
            var restored = connection is { } marker &&
                OptionalObject(marker, "reasonCode", JsonValueKind.String)?.ValueEquals("RESTORED_HISTORY") == true;
            var ingestion = OptionalObject(device, "ingestion", JsonValueKind.Object);
            var collection = ingestion is { } info ? OptionalObject(info, "collectionStatus", JsonValueKind.Object) : null;
            var observations = new Dictionary<SignalKey, Observation>();
            foreach (var value in Array(device, "observations", Profile.Count))
            {
                var observation = ContractJson.Deserialize<Observation>(value.GetRawText());
                if (observation.DeviceId != id || !Profile.TryGetValue(observation.SignalKey, out var signal) ||
                    observation.Unit != signal.Unit || observation.Value is { } number && Math.Abs(number) > 1e15 ||
                    !observations.TryAdd(observation.SignalKey, observation))
                    throw new JsonException("Invalid or duplicated dashboard observation.");
            }
            var record = new Device(id, module, state, received, restored,
                collection is { } metadata ? ParseReceipt(metadata) : null, observations);
            if (!devices.TryAdd(id, record)) throw new JsonException("Duplicated dashboard device.");
        }
        return new(upstream, mode, generated, devices.Values.ToArray());
    }

    private static Receipt ParseReceipt(JsonElement item)
    {
        if (Text(item, "declaredOrigin") != "SYNTHETIC") throw new JsonException("Unsupported collector source.");
        var mode = Choice(item, "collectorMode", "SHARED", "ISOLATED", "FIXTURE");
        var freshness = Choice(item, "freshness", "CURRENT", "STALE", "RESTORED", "NO_RECEIPT", "UNAVAILABLE");
        var worker = Choice(item, "workerStatus", "RUNNING", "RESTARTING", "FAILED", "UNAVAILABLE", "NOT_APPLICABLE");
        var provenance = Choice(item, "receiptProvenance", "ACCEPTED", "RESTORED", "NONE");
        var received = Timestamp(item, "lastReceivedAtUtc");
        var expiry = Timestamp(item, "validUntilUtc");
        if (freshness == "CURRENT" && (provenance != "ACCEPTED" || received is null || expiry is null || expiry <= received))
            throw new JsonException("Invalid collector freshness interval.");
        return new(mode, freshness, worker, provenance, received, expiry);
    }

    private static bool ValidIdentity(string value) => value.Length is >= 1 and <= 64 && char.IsAsciiLetterOrDigit(value[0])
        && value.All(character => char.IsAsciiLetterOrDigit(character) || character is '_' or '-' or '.');

    private static JsonElement.ArrayEnumerator Array(JsonElement value, string name, int maximum)
    {
        var field = OptionalObject(value, name, JsonValueKind.Array);
        if (field is null || field.Value.GetArrayLength() > maximum) throw new JsonException("Invalid dashboard collection.");
        return field.Value.EnumerateArray();
    }

    private static string Choice(JsonElement value, string name, params string[] allowed)
    {
        var text = Text(value, name);
        return allowed.Contains(text, StringComparer.Ordinal) ? text : throw new JsonException("Unsupported dashboard state.");
    }

    private static string Text(JsonElement value, string name) =>
        OptionalObject(value, name, JsonValueKind.String)?.GetString() ?? throw new JsonException("Required dashboard field missing.");

    private static DateTimeOffset? Timestamp(JsonElement value, string name, bool required = false)
    {
        var field = OptionalObject(value, name, JsonValueKind.String);
        if (field is null && !required) return null;
        if (field is { } stamp && stamp.GetString() is { } text &&
            (text.EndsWith('Z') || text.Length >= 6 && text[^6] is '+' or '-' && text[^3] == ':') &&
            stamp.TryGetDateTimeOffset(out var time))
            return time.ToUniversalTime();
        throw new JsonException("Dashboard timestamp requires an explicit offset.");
    }

    private static JsonElement? OptionalObject(JsonElement value, string name, JsonValueKind kind)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new JsonException("Invalid dashboard object.");
        if (!value.TryGetProperty(name, out var field) || field.ValueKind == JsonValueKind.Null) return null;
        return field.ValueKind == kind ? field : throw new JsonException("Invalid dashboard field.");
    }
}
