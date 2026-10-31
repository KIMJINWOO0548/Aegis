using System.Globalization;
using System.Text.Json;
using Aegis.Worker;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Xunit;

namespace Aegis.Contracts.Tests;

public sealed class WorkerTests
{
    private const string TraceId = "7be7cae3-053a-4ca2-bc2d-601a832ebaf1";

    [Fact]
    public void Worker_accepts_a_bounded_run_and_preserves_trace()
    {
        var options = WorkerOptions.Parse(["--count", "3", "--interval-ms", "20", "--trace-id", TraceId]);
        Assert.Equal(3, options.Count);
        Assert.Equal(20, options.IntervalMs);
        Assert.Equal(TraceId, options.TraceId);
    }

    [Theory]
    [InlineData("--count", "0")]
    [InlineData("--count", "-1")]
    [InlineData("--count", "101")]
    [InlineData("--count", "invalid")]
    [InlineData("--interval-ms", "0")]
    [InlineData("--interval-ms", "10001")]
    [InlineData("--trace-id", "not-a-uuid")]
    [InlineData("--unknown", "3")]
    public void Worker_rejects_invalid_input_without_echoing_it(string key, string value)
    {
        var exception = Assert.Throws<ArgumentException>(() => WorkerOptions.Parse([key, value]));
        Assert.Equal("Invalid bounded heartbeat arguments.", exception.Message);
    }

    [Fact]
    public void Worker_rejects_duplicate_or_missing_options()
    {
        Assert.Throws<ArgumentException>(() => WorkerOptions.Parse(["--count", "3", "--count", "4"]));
        Assert.Throws<ArgumentException>(() => WorkerOptions.Parse(["--trace-id"]));
    }

    [Fact]
    public async Task Worker_emits_correlated_start_and_stop_events_then_exits()
    {
        using var output = new StringWriter(CultureInfo.InvariantCulture);
        var builder = Host.CreateApplicationBuilder();
        builder.Logging.ClearProviders();
        builder.Services.AddSingleton(new WorkerOptions(3, 1, TraceId));
        builder.Services.AddSingleton<TextWriter>(output);
        builder.Services.AddHostedService<HeartbeatWorker>();
        using var host = builder.Build();
        await host.RunAsync().WaitAsync(TimeSpan.FromSeconds(10));

        var lines = output.ToString().Split(Environment.NewLine, StringSplitOptions.RemoveEmptyEntries);
        Assert.Equal(2, lines.Length);
        using var started = JsonDocument.Parse(lines[0]);
        using var stopped = JsonDocument.Parse(lines[1]);
        Assert.Equal("SERVICE_STARTED", started.RootElement.GetProperty("eventName").GetString());
        Assert.Equal("SERVICE_STOPPED", stopped.RootElement.GetProperty("eventName").GetString());
        Assert.Equal(1, started.RootElement.GetProperty("sourceSequence").GetInt64());
        Assert.Equal(2, stopped.RootElement.GetProperty("sourceSequence").GetInt64());
        Assert.Equal(3, stopped.RootElement.GetProperty("safeContext").GetProperty("heartbeatCount").GetInt32());
        foreach (var line in lines)
        {
            var value = ContractJson.Deserialize<StructuredEvent>(line);
            Assert.Equal(TraceId, value.TraceId);
            Assert.Equal(Outcome.SUCCESS, value.Outcome);
        }
    }
}
