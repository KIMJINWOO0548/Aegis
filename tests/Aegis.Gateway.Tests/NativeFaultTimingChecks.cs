using System.Buffers.Binary;
using System.Diagnostics;
using System.IO.Pipes;
using Aegis.Gateway;
using Aegis.SignalProcessing;

// Controlled protocol peer: these tests exercise real Gateway workers, Windows processes,
// named pipes, identity checks and job cleanup, but do not claim C++ fault injection.
internal static class NativeFaultTimingChecks
{
    private static readonly TimeSpan Deadline = TimeSpan.FromSeconds(20);

    public static async Task RunAsync()
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
        foreach (string mode in new[] { "accepted", "processed", "torn", "older", "delayed" })
            await ScenarioAsync(mode);
    }

    public static async Task<int> ChildAsync(string[] args)
    {
        string root = AppContext.BaseDirectory;
        string mode = File.ReadAllText(Path.Combine(root, "fault-mode"));
        long generation = long.Parse(args[3], System.Globalization.CultureInfo.InvariantCulture);
        using var pipe = new NamedPipeServerStream(args[1], PipeDirection.InOut, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
        await pipe.WaitForConnectionAsync();
        var window = new Queue<double>();
        ulong processed = 0;
        bool? target = null;
        while (true)
        {
            var request = await NativeWire.ReadAsync<NativeRequest>(pipe, CancellationToken.None);
            if (request.Operation == "stop")
            {
                await NativeWire.WriteAsync(pipe, new NativeResponse(NativeWire.Version, generation, request.RequestId, null), CancellationToken.None);
                return 0;
            }
            var frames = request.Frames ?? throw new InvalidDataException();
            target ??= frames.Any(f => f.ActualFlowLpm == 12);
            bool fault = target.Value && generation == 1 && File.Exists(Path.Combine(root, "arm"));
            if (fault && mode == "accepted")
            {
                // ReadAsync completed: OS-verified connection and complete request accepted;
                // no frame has yet entered the deterministic processing loop below.
                Mark("accepted");
                await Task.Delay(Timeout.InfiniteTimeSpan);
            }
            foreach (var frame in frames)
            {
                window.Enqueue(Math.Abs(frame.ActualFlowLpm - frame.SetpointFlowLpm));
                if (window.Count > 32) window.Dequeue();
                processed++;
            }
            var last = frames[^1];
            var snapshot = new NativeSnapshot(last.SourceRunToken, last.Sequence, last.ObservedUnixMs,
                window.Average(), processed, processed, 0, 0, (uint)window.Count,
                window.Count == 32 ? 2u : 1u, 1);
            var response = new NativeResponse(NativeWire.Version, generation, request.RequestId, snapshot);
            if (fault)
            {
                Mark("processed");
                if (mode is "processed" or "delayed")
                {
                    // Delayed intentionally remains past the 1500ms request deadline. The
                    // production job must kill this process; no timing-dependent release.
                    await Task.Delay(Timeout.InfiniteTimeSpan);
                }
                await UntilAsync(() => File.Exists(Path.Combine(root, "release")), "reply release");
                response = response with { Snapshot = snapshot with { MeanAbsoluteDeviationLpm = 99 } };
                if (mode == "older")
                    await NativeWire.WriteAsync(pipe, response with { Generation = generation - 1 }, CancellationToken.None);
                else
                {
                    using var encoded = new MemoryStream();
                    await NativeWire.WriteAsync(encoded, response, CancellationToken.None);
                    byte[] bytes = encoded.ToArray();
                    // A valid length prefix and truncated JSON guarantee the reader is in
                    // the response body when the parent kills this owned process.
                    await pipe.WriteAsync(bytes.AsMemory(0, bytes.Length / 2));
                    await pipe.FlushAsync();
                }
                Mark("reply");
                await Task.Delay(Timeout.InfiniteTimeSpan);
            }
            await NativeWire.WriteAsync(pipe, response, CancellationToken.None);
        }
        void Mark(string stage) => File.WriteAllText(Path.Combine(root, stage), Environment.ProcessId.ToString(System.Globalization.CultureInfo.InvariantCulture));
    }

    private static async Task ScenarioAsync(string mode)
    {
        string root = Path.Combine(Path.GetTempPath(), "aegis-fault-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        // Copy the existing test output, including native SQLite runtimes; no new package.
        CopyDirectory(AppContext.BaseDirectory, root);
        File.Copy(Path.Combine(root, "Aegis.Gateway.Tests.exe"), Path.Combine(root, "Aegis.NativeHost.exe"));
        File.WriteAllText(Path.Combine(root, "fault-mode"), mode);
        var a = new NativeHostRecoveryChecks.Fixture(root, "fault-a", "module-a", 2);
        var b = new NativeHostRecoveryChecks.Fixture(root, "fault-b", "module-b", 4);
        var archive = new ArchiveReview(new GatewaySettings(new("http://127.0.0.1:8780"), null, null, null, root)
            { NativeHostPath = Path.Combine(root, "Aegis.NativeHost.exe") });
        var owned = new List<Process>();
        try
        {
            a.Seed(); b.Seed();
            await Ready(a); await Ready(b);
            var original = Own(a); var peer = Own(b);
            File.WriteAllText(Path.Combine(root, "arm"), "");
            a.Tail(6);
            long rejectedSequence = a.LastSequence;
            string barrier = mode == "accepted" ? "accepted" : "processed";
            await UntilAsync(() => File.Exists(Path.Combine(root, barrier)), mode + " barrier");
            Require(File.ReadAllText(Path.Combine(root, barrier)) == original.Id.ToString(System.Globalization.CultureInfo.InvariantCulture), "barrier belongs to owned target PID");
            var pause = Stopwatch.StartNew();
            b.Tail(5);
            // Assert cached target reads while its submit is blocked and prove another
            // device actually processes a new tail before the target's deadline.
            await UntilAsync(() =>
            {
                var held = Read(a);
                Require(held.NativeSequence != rejectedSequence, "in-flight response never commits");
                Require(held.NativeFlow?.MeanAbsoluteDeviationLpm != 99, "poisoned reply never commits");
                Require(archive.NativeHostProcessId(b.Device) == peer.Id && !peer.HasExited, "peer keeps its owned process");
                var other = Read(b);
                return other.NativeSequence == b.LastSequence && other.NativeFlow?.State == "READY";
            }, "peer advances while target is held");
            Require(pause.Elapsed < TimeSpan.FromMilliseconds(1200) && !original.HasExited,
                "held checks finish before the 1500ms submit deadline");
            a.Tail(8); // Recovery seed differs from the request whose result must be rejected.
            if (mode is "torn" or "older")
            {
                File.WriteAllText(Path.Combine(root, "release"), "");
                await UntilAsync(() => File.Exists(Path.Combine(root, "reply")), "reply boundary");
            }
            if (mode is "accepted" or "processed" or "torn" && !original.HasExited) original.Kill();
            await original.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
            await Ready(a, () =>
            {
                Require(Read(a).NativeSequence != rejectedSequence, "earlier result cannot publish during restart");
                Require(archive.NativeHostProcessId(b.Device) == peer.Id && !peer.HasExited, "peer survives target restart");
                b.RefreshIfNeeded();
            });
            var replacement = Own(a);
            Require(replacement.Id != original.Id, "new process serves a fresh correct result");
            await Ready(b);
            await archive.StopAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(10));
            foreach (var process in owned)
            {
                await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
                Require(process.HasExited, "all owned processes exit");
            }
            Console.WriteLine($"PASS controlled Windows IPC {mode}: fixed-point barrier, cached Read, peer progress, rejected prior result, fresh restart and shutdown.");
        }
        finally
        {
            await archive.StopAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(10));
            foreach (var process in owned)
            {
                if (!process.HasExited) { process.Kill(); await process.WaitForExitAsync(); }
                process.Dispose();
            }
            Directory.Delete(root, true);
        }

        NativeReview Read(NativeHostRecoveryChecks.Fixture fixture)
        {
            var watch = Stopwatch.StartNew();
            var result = archive.Read(fixture.Device, "equipment-test", fixture.Module);
            Require(watch.Elapsed < TimeSpan.FromSeconds(1), "cached Read returns within loose 1s ceiling");
            Require(result.NativeFlow?.MeanAbsoluteDeviationLpm != 99, "invalid response is never visible");
            return result;
        }
        Process Own(NativeHostRecoveryChecks.Fixture fixture)
        {
            var process = Process.GetProcessById(archive.NativeHostProcessId(fixture.Device) ?? throw new InvalidOperationException("No owned PID"));
            _ = process.Handle;
            owned.Add(process);
            return process;
        }
        async Task Ready(NativeHostRecoveryChecks.Fixture fixture, Action? tick = null)
        {
            await UntilAsync(() =>
            {
                tick?.Invoke(); fixture.RefreshIfNeeded();
                var result = Read(fixture);
                if (result.NativeFlow?.State != "READY")
                    Require(result.NativeFlow?.MeanAbsoluteDeviationLpm is null, "non-ready evidence masks numeric values");
                if (result.NativeSequence != fixture.LastSequence || result.NativeFlow?.State != "READY") return false;
                Require(result.NativeFlow.SampleCount == 32 && result.NativeFlow.MeanAbsoluteDeviationLpm is double mean
                    && Math.Abs(mean - fixture.ExpectedMean) < 1e-9, "fresh deterministic 32-frame mean");
                return true;
            }, "ready " + fixture.Device);
        }
    }

    private static void CopyDirectory(string source, string target)
    {
        foreach (var file in Directory.EnumerateFiles(source)) File.Copy(file, Path.Combine(target, Path.GetFileName(file)));
        foreach (var directory in Directory.EnumerateDirectories(source))
        {
            string destination = Path.Combine(target, Path.GetFileName(directory));
            Directory.CreateDirectory(destination); CopyDirectory(directory, destination);
        }
    }
    private static async Task UntilAsync(Func<bool> condition, string description)
    {
        var watch = Stopwatch.StartNew();
        do { if (condition()) return; await Task.Delay(10); } while (watch.Elapsed < Deadline);
        throw new InvalidOperationException("Controlled IPC deadline: " + description);
    }
    private static void Require(bool condition, string description)
    {
        if (!condition) throw new InvalidOperationException("Controlled IPC check failed: " + description);
    }
}
