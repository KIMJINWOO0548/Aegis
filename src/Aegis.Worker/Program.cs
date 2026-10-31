using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Aegis.Worker;

internal static class Program
{
    public static async Task<int> Main(string[] args)
    {
        WorkerOptions options;
        try { options = WorkerOptions.Parse(args); }
        catch (ArgumentException)
        {
            Console.Error.WriteLine("Invalid bounded heartbeat arguments.");
            return 2;
        }
        var builder = Host.CreateApplicationBuilder();
        builder.Services.AddWindowsService(settings => settings.ServiceName = "Aegis D0a Heartbeat Probe");
        builder.Logging.ClearProviders();
        builder.Services.AddSingleton(options);
        builder.Services.AddSingleton<TextWriter>(Console.Out);
        builder.Services.AddHostedService<HeartbeatWorker>();
        using var host = builder.Build();
        await host.RunAsync();
        return Environment.ExitCode;
    }
}
