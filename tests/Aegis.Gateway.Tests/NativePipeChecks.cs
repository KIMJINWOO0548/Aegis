using System.Buffers.Binary;
using System.Diagnostics;
using System.IO.Pipes;
using Aegis.SignalProcessing;

internal static class NativePipeChecks
{
    public static async Task RunAsync(string executable)
    {
        await RejectAsync(executable, "oversized header", async (pipe, token) =>
        {
            var header = new byte[4];
            BinaryPrimitives.WriteUInt32LittleEndian(header, 65537);
            await pipe.WriteAsync(header, token);
        }, "InvalidDataException");
        await RejectAsync(executable, "partial frame and disconnect", async (pipe, token) =>
        {
            var fragment = new byte[5];
            BinaryPrimitives.WriteUInt32LittleEndian(fragment, 128);
            fragment[4] = (byte)'{';
            await pipe.WriteAsync(fragment, token);
        }, "EndOfStreamException");
        await RejectAsync(executable, "old generation",
            (pipe, token) => NativeWire.WriteAsync(pipe, Submit(2, 1), token), "InvalidDataException");
        await RejectAsync(executable, "duplicate request id", async (pipe, token) =>
        {
            await NativeWire.WriteAsync(pipe, Submit(3, 1), token);
            var reply = await NativeWire.ReadAsync<NativeResponse>(pipe, token);
            if (reply.Version != NativeWire.Version || reply.Generation != 3 || reply.RequestId != 1
                || reply.Snapshot?.ProcessedFrameCount != 1)
                throw new InvalidOperationException("Initial native pipe request failed.");
            await NativeWire.WriteAsync(pipe, Submit(3, 1), token);
        }, "InvalidDataException");
        await RejectAsync(executable, "unexpected client PID", (_, _) => Task.CompletedTask,
            "UnauthorizedAccessException", clientPid: uint.MaxValue);
        Console.WriteLine("PASS native pipe rejects oversized, partial, old-generation, duplicate and wrong-client requests.");
    }

    private static NativeRequest Submit(long generation, long id) =>
        new(NativeWire.Version, generation, id, "submit", [new(1, 0, 1000, 1, 4, 0)]);

    private static async Task RejectAsync(string executable, string check,
        Func<NamedPipeClientStream, CancellationToken, Task> send, string expectedError, uint? clientPid = null)
    {
        var pipeName = $"aegis-native-{Guid.NewGuid():N}";
        var start = new ProcessStartInfo(executable)
        {
            CreateNoWindow = true,
            UseShellExecute = false,
            RedirectStandardError = true
        };
        foreach (var item in new[] { "--pipe", pipeName, "--generation", "3", "--parent",
                     (clientPid ?? (uint)Environment.ProcessId).ToString(System.Globalization.CultureInfo.InvariantCulture) })
            start.ArgumentList.Add(item);
        using var host = Process.Start(start) ?? throw new InvalidOperationException("Unable to start the pipe test host.");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        var stderr = host.StandardError.ReadToEndAsync(timeout.Token);
        try
        {
            using (var pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut,
                       PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly))
            {
                await pipe.ConnectAsync(timeout.Token);
                await send(pipe, timeout.Token);
                if (clientPid is not null) await host.WaitForExitAsync(timeout.Token);
            }
            await host.WaitForExitAsync(timeout.Token);
            var diagnostic = await stderr;
            if (host.ExitCode != 1 || !diagnostic.Contains(expectedError, StringComparison.Ordinal))
                throw new InvalidOperationException($"{check} was not rejected as expected. {diagnostic}");
        }
        finally
        {
            if (!host.HasExited)
            {
                host.Kill();
                using var cleanup = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                await host.WaitForExitAsync(cleanup.Token);
            }
        }
    }
}
