using System.Globalization;

namespace Aegis.Worker;

public sealed record WorkerOptions(int Count, int IntervalMs, string TraceId)
{
    public static WorkerOptions Parse(string[] args)
    {
        const string error = "Invalid bounded heartbeat arguments.";
        if (args.Length % 2 != 0) throw new ArgumentException(error);
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 0; index < args.Length; index += 2)
            if (args[index] is not ("--count" or "--interval-ms" or "--trace-id") ||
                !values.TryAdd(args[index], args[index + 1]))
                throw new ArgumentException(error);
        if (!int.TryParse(values.GetValueOrDefault("--count", "3"), NumberStyles.None,
                CultureInfo.InvariantCulture, out var count) || count is < 1 or > 100 ||
            !int.TryParse(values.GetValueOrDefault("--interval-ms", "1000"), NumberStyles.None,
                CultureInfo.InvariantCulture, out var interval) || interval is < 1 or > 10000 ||
            !Guid.TryParseExact(values.GetValueOrDefault("--trace-id", Guid.NewGuid().ToString("D")), "D", out var trace))
            throw new ArgumentException(error);
        return new WorkerOptions(count, interval, trace.ToString("D"));
    }
}
