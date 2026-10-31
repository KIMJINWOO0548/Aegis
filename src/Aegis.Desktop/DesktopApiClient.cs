using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using Aegis.Contracts;

namespace Aegis.Desktop;

public sealed record SignalReading(SignalKey SignalKey, double? Value, string Unit, ObservationQuality Quality, string? Issue = null);
public sealed record DesktopSnapshot(string Mode, IReadOnlyList<DeviceDescriptor> Devices, string? DeviceId,
    ConnectionState? State, ConnectionStatus? Connection, DateTimeOffset? ObservedAtUtc,
    IReadOnlyList<SignalReading> Signals, DeviceDiagnostics? Diagnostics = null, IReadOnlyList<Warning>? CurrentWarnings = null)
{
    public ObservationEvaluation AnalysisEvaluation =>
        State != ConnectionState.CONNECTED || Signals.Any(signal => signal.Quality != ObservationQuality.GOOD) ||
        Diagnostics?.Issues.Count > 0 ? ObservationEvaluation.UNAVAILABLE :
        Diagnostics?.Evaluation ?? ObservationEvaluation.INSUFFICIENT_DATA;
    public IReadOnlyList<Warning> Warnings => CurrentWarnings ?? [];
}

public sealed class DesktopApiClient : IDisposable
{
    private static readonly string[] Units = ["L/min", "L/min", "kPa", "%", "%", "A", "degC", "count"];
    private readonly HttpClient _http;

    public DesktopApiClient(string origin, string token, HttpMessageHandler? handler = null)
    {
        if (!Regex.IsMatch(origin, @"\Ahttp://(?:127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?/?\z") ||
            !Uri.TryCreate(origin, UriKind.Absolute, out var address) || address.Port is < 1 or > 65535)
            throw new ArgumentException("AEGIS_API_URL must be a literal loopback HTTP origin, such as http://127.0.0.1:8765.");
        if (token.Trim().Length < 32 || token.Any(character => character < 0x20 || character > 0x7e))
            throw new ArgumentException("Set AEGIS_API_TOKEN to the local API's token (at least 32 ASCII characters), then restart the app.");

        _http = new HttpClient(handler ?? new SocketsHttpHandler { AllowAutoRedirect = false, UseProxy = false, UseCookies = false })
        {
            BaseAddress = address,
            Timeout = Timeout.InfiniteTimeSpan,
            MaxResponseContentBufferSize = 1024 * 1024
        };
        _http.DefaultRequestHeaders.Add("X-Aegis-Token", token);
    }

    public async Task<DesktopSnapshot> ReadAsync(string? selectedDeviceId, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(2));
        var status = await GetAsync<ApiStatus>("/v1/health", timeout.Token).ConfigureAwait(false);
        if (status.Mode != "EMULATORS")
            return new DesktopSnapshot(status.Mode ?? "FIXTURES", [], null, null, null, null, []);

        var devices = await GetAsync<List<DeviceDescriptor>>("/v1/devices", timeout.Token).ConfigureAwait(false);
        if (devices.Count > 32 || devices.Select(row => row.DeviceId).Distinct(StringComparer.Ordinal).Count() != devices.Count ||
            devices.Any(row => row.Source != Source.SIMULATOR))
            throw new JsonException("Invalid emulator device list.");
        var connections = await GetAsync<List<ConnectionStatus>>("/v1/connections", timeout.Token).ConfigureAwait(false);
        if (connections.Count != devices.Count || connections.Select(row => row.DeviceId).Distinct(StringComparer.Ordinal).Count() != connections.Count ||
            connections.Any(row => !devices.Any(device => device.DeviceId == row.DeviceId)))
            throw new JsonException("Connection identities do not match configured devices.");

        var deviceId = devices.FirstOrDefault(row => row.DeviceId == selectedDeviceId)?.DeviceId ?? devices.FirstOrDefault()?.DeviceId;
        if (deviceId is null) return new DesktopSnapshot("EMULATORS", devices, null, null, null, null, []);
        var connection = connections.Single(row => row.DeviceId == deviceId);
        var observations = await GetAsync<List<Observation>>($"/v1/devices/{Uri.EscapeDataString(deviceId)}/observations", timeout.Token).ConfigureAwait(false);
        if ((observations.Count != 0 && observations.Count != 8) ||
            observations.Select(row => row.SignalKey).Distinct().Count() != observations.Count ||
            observations.Any(row => row.DeviceId != deviceId || row.Unit != Units[(int)row.SignalKey]))
            throw new JsonException("Invalid latest observation batch.");

        var diagnostics = await GetAsync<DeviceDiagnostics>($"/v1/devices/{Uri.EscapeDataString(deviceId)}/diagnostics", timeout.Token).ConfigureAwait(false);
        if (diagnostics.DeviceId != deviceId) throw new JsonException("Diagnostics identity does not match selected device.");
        var warnings = diagnostics.Enabled
            ? await GetAsync<List<Warning>>("/v1/warnings", timeout.Token).ConfigureAwait(false) : [];
        if (warnings.Count > 1024 || warnings.Any(warning => !devices.Any(device => device.DeviceId == warning.DeviceId) ||
            !Regex.IsMatch(warning.Id, @"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z")))
            throw new JsonException("Invalid emulator warning list.");

        var state = connection.State;
        var now = DateTimeOffset.UtcNow;
        if (state == ConnectionState.CONNECTED &&
            (connection.LastReceivedAtUtc is null || now - connection.LastReceivedAtUtc > TimeSpan.FromSeconds(3) ||
             connection.LastReceivedAtUtc > now.AddSeconds(1) || observations.Count == 0 ||
             observations.Any(row => now - row.ReceivedAtUtc > TimeSpan.FromSeconds(3) || row.ReceivedAtUtc > now.AddSeconds(1))))
            state = ConnectionState.STALE;
        var signals = Enum.GetValues<SignalKey>().Select(key =>
        {
            var observation = observations.SingleOrDefault(row => row.SignalKey == key);
            var issue = diagnostics.Issues.SingleOrDefault(row => row.SignalKey == key);
            var quality = observation?.Quality ?? ObservationQuality.MISSING;
            if (issue?.ReasonCode == "OUT_OF_RANGE") quality = ObservationQuality.BAD;
            if (quality == ObservationQuality.GOOD && state != ConnectionState.CONNECTED) quality = ObservationQuality.STALE;
            return new SignalReading(key, observation?.Value, Units[(int)key], quality,
                issue is null ? null : $"{issue.ReasonCode} · {issue.Message}");
        }).ToArray();
        return new DesktopSnapshot("EMULATORS", devices, deviceId, state, connection,
            observations.Count == 0 ? null : observations.Max(row => row.ObservedAtUtc), signals,
            diagnostics, warnings.Where(warning => warning.DeviceId == deviceId).ToArray());
    }

    private async Task<T> GetAsync<T>(string path, CancellationToken cancellationToken)
    {
        using var response = await _http.GetAsync(path, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return ContractJson.Deserialize<T>(await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false));
    }

    public void Dispose() => _http.Dispose();
}
