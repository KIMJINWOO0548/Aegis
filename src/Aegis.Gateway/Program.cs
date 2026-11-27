using Aegis.Gateway;
using System.Net.Sockets;

const int startupAttempts = 4;
for (var attempt = 1; ; attempt++)
{
    await using var app = GatewayApplication.Build(args);
    try
    {
        await app.StartAsync();
    }
    catch (SocketException error) when (OperatingSystem.IsWindows() &&
        error.SocketErrorCode == SocketError.Fault && attempt < startupAttempts)
    {
        // Only a transient startup failure on the configured literal loopback listener is retried.
        Console.Error.WriteLine($"Aegis Gateway loopback listen returned {error.NativeErrorCode}. Retrying ({attempt}/{startupAttempts - 1}).");
        await Task.Delay(TimeSpan.FromMilliseconds(120 * attempt));
        continue;
    }
    if (OperatingSystem.IsWindows()
        && Environment.GetEnvironmentVariable("AEGIS_GATEWAY_STOP_ON_STDIN_EOF") == "1"
        && Console.IsInputRedirected)
        _ = StopWhenLauncherClosesAsync(app.Lifetime);
    await app.WaitForShutdownAsync();
    break;
}

static async Task StopWhenLauncherClosesAsync(IHostApplicationLifetime lifetime)
{
    try
    {
        using var input = Console.OpenStandardInput();
        var buffer = new byte[64];
        while (await input.ReadAsync(buffer, lifetime.ApplicationStopping) != 0) { }
        lifetime.StopApplication();
    }
    catch (OperationCanceledException) when (lifetime.ApplicationStopping.IsCancellationRequested) { }
    catch (IOException) { lifetime.StopApplication(); }
}

public partial class Program;
