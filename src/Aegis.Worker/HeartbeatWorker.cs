using System.Diagnostics;
using System.Text.Json;
using Aegis.Contracts;
using Microsoft.Extensions.Hosting;

namespace Aegis.Worker;

public sealed class HeartbeatWorker(IHostApplicationLifetime lifetime, WorkerOptions options, TextWriter output) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var elapsed = Stopwatch.StartNew();
        var completed = 0;
        var outcome = Outcome.SUCCESS;
        try
        {
            await Emit(EventName.SERVICE_STARTED, 1, "D0A_HEARTBEAT_PROBE");
            for (; completed < options.Count; completed++)
                await Task.Delay(options.IntervalMs, stoppingToken);
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            outcome = Outcome.SKIPPED;
        }
        catch
        {
            outcome = Outcome.FAILED;
            Environment.ExitCode = 1;
            throw;
        }
        finally
        {
            try
            {
                await Emit(EventName.SERVICE_STOPPED, 2,
                    outcome switch
                    {
                        Outcome.SUCCESS => "D0A_HEARTBEAT_COMPLETE",
                        Outcome.SKIPPED => "D0A_HEARTBEAT_CANCELLED",
                        _ => "D0A_HEARTBEAT_FAILED"
                    },
                    outcome, elapsed.Elapsed.TotalMilliseconds, completed);
            }
            finally { lifetime.StopApplication(); }
        }
    }

    private Task Emit(EventName name, long sequence, string reason, Outcome outcome = Outcome.SUCCESS,
        double? duration = null, int? count = null) => output.WriteLineAsync(ContractJson.Serialize(new StructuredEvent
        {
            SchemaVersion = Contract.Version, EventId = Guid.NewGuid().ToString("D"),
            OccurredAtUtc = DateTimeOffset.UtcNow, Component = Component.collector,
            EventName = name, Severity = Severity.INFO, TraceId = options.TraceId,
            Outcome = outcome, SourceSequence = sequence, ReasonCode = reason, DurationMs = duration,
            SafeContext = count is null ? null : new Dictionary<string, JsonElement>
            {
                ["heartbeatCount"] = JsonSerializer.SerializeToElement(count.Value)
            }
        }));
}
