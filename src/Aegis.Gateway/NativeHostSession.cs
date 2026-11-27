using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using Aegis.SignalProcessing;
using Microsoft.Win32.SafeHandles;

namespace Aegis.Gateway;

internal sealed class NativeHostSession : IAsyncDisposable
{
    private readonly Process _process;
    private readonly SafeFileHandle _job;
    private readonly NamedPipeClientStream _pipe;
    private readonly long _generation;
    private long _requestId;

    private NativeHostSession(Process process, SafeFileHandle job, NamedPipeClientStream pipe, long generation)
    {
        _process = process;
        _job = job;
        _pipe = pipe;
        _generation = generation;
    }

    public int ProcessId => _process.Id;
    public bool HasExited => _process.HasExited;

    // Keep the startup deadline separate from a request deadline. Only an OS-verified
    // pipe endpoint owned by the process we launched can receive archived frames.
    public static async Task<NativeHostSession> OpenAsync(string executable, long generation, CancellationToken token)
    {
        if (!OperatingSystem.IsWindows() || RuntimeInformation.ProcessArchitecture != Architecture.X64)
            throw new PlatformNotSupportedException("The native review host requires Windows x64.");
        if (!Path.IsPathFullyQualified(executable) || executable.StartsWith(@"\\", StringComparison.Ordinal)
            || !string.Equals(Path.GetFileName(executable), "Aegis.NativeHost.exe", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("The native host requires a local absolute Aegis.NativeHost.exe path.");
        var pipeName = $"aegis-native-{Guid.NewGuid():N}";
        var job = NativeHostWindows.CreateKillJob();
        Process? process = null;
        NamedPipeClientStream? pipe = null;
        try
        {
            var start = new ProcessStartInfo(executable)
            {
                CreateNoWindow = true,
                UseShellExecute = false,
                WorkingDirectory = Path.GetDirectoryName(executable)!
            };
            foreach (var key in start.Environment.Keys.Where(key => key.StartsWith("AEGIS_", StringComparison.OrdinalIgnoreCase)).ToArray())
                start.Environment.Remove(key);
            foreach (var argument in new[] { "--pipe", pipeName, "--generation", generation.ToString(CultureInfo.InvariantCulture),
                         "--parent", Environment.ProcessId.ToString(CultureInfo.InvariantCulture) })
                start.ArgumentList.Add(argument);
            process = Process.Start(start) ?? throw new IOException("The native host could not start.");
            if (!NativeHostWindows.AssignProcessToJobObject(job, process.SafeHandle))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
            deadline.CancelAfter(TimeSpan.FromSeconds(4));
            await pipe.ConnectAsync(deadline.Token);
            if (!NativeHostWindows.GetNamedPipeServerProcessId(pipe.SafePipeHandle, out var serverPid)
                || serverPid != (uint)process.Id || process.HasExited)
                throw new IOException("The native pipe peer is not the owned host.");
            return new NativeHostSession(process, job, pipe, generation);
        }
        catch
        {
            pipe?.Dispose();
            job.Dispose();
            if (process is not null)
            {
                try
                {
                    if (!process.HasExited) process.Kill();
                    using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(1));
                    await process.WaitForExitAsync(deadline.Token);
                }
                catch (Exception error) when (error is InvalidOperationException or Win32Exception or OperationCanceledException) { }
                finally { process.Dispose(); }
            }
            throw;
        }
    }

    public async Task<NativeSnapshot> SubmitAsync(NativeFrame[] frames, CancellationToken token)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromMilliseconds(1500));
        var result = await ExchangeAsync("submit", frames, deadline.Token);
        return result.Snapshot ?? throw new InvalidDataException("The native host omitted the result.");
    }

    private async Task<NativeResponse> ExchangeAsync(string operation, NativeFrame[]? frames, CancellationToken token)
    {
        var requestId = checked(++_requestId);
        await NativeWire.WriteAsync(_pipe, new NativeRequest(NativeWire.Version, _generation, requestId, operation, frames), token);
        var result = await NativeWire.ReadAsync<NativeResponse>(_pipe, token);
        if (result.Version != NativeWire.Version || result.Generation != _generation || result.RequestId != requestId)
            throw new InvalidDataException("The native host response does not match the current request.");
        return result;
    }

    public async Task StopAsync()
    {
        if (_process.HasExited) return;
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        if ((await ExchangeAsync("stop", null, deadline.Token)).Snapshot is not null)
            throw new InvalidDataException("The native stop response is invalid.");
        await _process.WaitForExitAsync(deadline.Token);
        if (_process.ExitCode != 0) throw new IOException("The native host failed to shut down.");
    }

    public async ValueTask DisposeAsync()
    {
        _pipe.Dispose();
        _job.Dispose(); // KILL_ON_JOB_CLOSE only targets children assigned to this job.
        try
        {
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(1));
            await _process.WaitForExitAsync(deadline.Token);
        }
        catch (OperationCanceledException) { }
        finally { _process.Dispose(); }
    }
}

internal static class NativeHostWindows
{
    public static SafeFileHandle CreateKillJob()
    {
        var job = CreateJobObjectW(IntPtr.Zero, IntPtr.Zero);
        if (job.IsInvalid)
        {
            var error = Marshal.GetLastWin32Error();
            job.Dispose();
            throw new Win32Exception(error);
        }
        var limits = new JobLimits { Basic = new BasicJobLimits { Flags = 0x2000 } }; // KILL_ON_JOB_CLOSE
        if (SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf<JobLimits>())) return job;
        var lastError = Marshal.GetLastWin32Error();
        job.Dispose();
        throw new Win32Exception(lastError);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicJobLimits
    {
        public long ProcessUserTime, JobUserTime;
        public uint Flags;
        public nuint MinMemory, MaxMemory;
        public uint ProcessLimit;
        public nuint Affinity;
        public uint Priority, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobLimits
    {
        public BasicJobLimits Basic;
        public ulong Reads, Writes, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        public nuint ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }

    [DllImport("kernel32.dll", SetLastError = true, ExactSpelling = true)]
    private static extern SafeFileHandle CreateJobObjectW(IntPtr attributes, IntPtr name);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(SafeFileHandle job, int infoClass, ref JobLimits limits, uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool AssignProcessToJobObject(SafeFileHandle job, SafeProcessHandle process);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out uint processId);
}
