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
    await app.WaitForShutdownAsync();
    break;
}

public partial class Program;
