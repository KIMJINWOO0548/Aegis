using System.Diagnostics;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using Aegis.SignalProcessing;
using Microsoft.Win32.SafeHandles;

namespace Aegis.NativeHost;

internal static partial class NativeApi
{
    internal const string Library = "aegis_native";
    private static nint _module;
    internal static void Initialize()
    {
        if (!OperatingSystem.IsWindows() || RuntimeInformation.ProcessArchitecture != Architecture.X64)
            throw new PlatformNotSupportedException("Native host requires Windows x64.");
        _module = NativeLibrary.Load(Path.Combine(AppContext.BaseDirectory, "aegis_native.dll"));
        NativeLibrary.SetDllImportResolver(typeof(NativeApi).Assembly, Resolve);
        if (AbiVersion() != 1 || Marshal.SizeOf<Frame>() != 48 || Marshal.SizeOf<Snapshot>() != 80)
            throw new InvalidDataException("Native ABI mismatch.");
    }
    private static nint Resolve(string name, Assembly assembly, DllImportSearchPath? path) =>
        name == Library ? _module : 0;

    [StructLayout(LayoutKind.Sequential)]
    internal struct Frame
    {
        public ulong Run; public long Sequence; public long Observed;
        public double Actual; public double Setpoint; public uint Quality; public uint Reserved;
    }
    [StructLayout(LayoutKind.Sequential)]
    internal struct Snapshot
    {
        public ulong Run; public long Sequence; public long Observed; public double Mean;
        public ulong Accepted; public ulong Processed; public ulong Discarded; public ulong Ignored;
        public uint Samples; public uint Result; public uint Engine; public uint Reserved;
        public readonly NativeSnapshot ToWire() => new(Run, Sequence, Observed, Mean, Accepted, Processed, Discarded, Ignored, Samples, Result, Engine);
    }
    [LibraryImport(Library, EntryPoint = "aegis_abi_version")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static partial uint AbiVersion();
    [LibraryImport(Library, EntryPoint = "aegis_create")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static partial int Create(uint capacity, out nint handle);
    [LibraryImport(Library, EntryPoint = "aegis_start")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static partial int Start(EngineHandle handle);
    [LibraryImport(Library, EntryPoint = "aegis_submit_frames")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static unsafe partial int Submit(EngineHandle handle, Frame* frames, uint count, uint size, out uint accepted);
    [LibraryImport(Library, EntryPoint = "aegis_read_snapshot")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static partial int Read(EngineHandle handle, out Snapshot snapshot, uint size);
    [LibraryImport(Library, EntryPoint = "aegis_request_stop")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static partial int Stop(nint handle, uint mode);
    [LibraryImport(Library, EntryPoint = "aegis_wait_stopped")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static partial int Wait(nint handle, uint timeout);
    [LibraryImport(Library, EntryPoint = "aegis_release")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    internal static partial int Release(nint handle);
    internal static void Check(int result)
    {
        if (result != 0) throw new InvalidOperationException($"Native operation failed ({result}).");
    }
}

// A timed-out worker retains its native allocation until host process exit. Never release an unjoined worker.
internal sealed class EngineHandle : SafeHandleZeroOrMinusOneIsInvalid
{
    internal EngineHandle(nint value) : base(true) => SetHandle(value);
    internal bool Started { get; set; }
    internal bool Drain()
    {
        NativeApi.Check(NativeApi.Stop(handle, 0));
        return NativeApi.Wait(handle, 500) == 0;
    }
    protected override bool ReleaseHandle()
    {
        if (!Started) return NativeApi.Release(handle) == 0;
        int stop = NativeApi.Stop(handle, 0);
        int wait = stop == 0 ? NativeApi.Wait(handle, 500) : 4;
        if (wait != 0)
        {
            NativeApi.Stop(handle, 1);
            wait = NativeApi.Wait(handle, 500);
        }
        return wait == 0 && NativeApi.Release(handle) == 0;
    }
}

internal sealed class NativeEngine : IDisposable
{
    private readonly EngineHandle handle;
    internal NativeEngine()
    {
        NativeApi.Check(NativeApi.Create(NativeWire.MaxFrames, out nint value));
        handle = new EngineHandle(value);
        try { NativeApi.Check(NativeApi.Start(handle)); handle.Started = true; }
        catch { handle.Dispose(); throw; }
    }
    internal unsafe NativeSnapshot Submit(NativeFrame[] frames)
    {
        NativeApi.Frame[] native = frames.Select(f => new NativeApi.Frame
        { Run = f.SourceRunToken, Sequence = f.Sequence, Observed = f.ObservedUnixMs,
          Actual = f.ActualFlowLpm, Setpoint = f.SetpointFlowLpm, Quality = f.Quality }).ToArray();
        uint accepted;
        fixed (NativeApi.Frame* ptr = native)
            NativeApi.Check(NativeApi.Submit(handle, ptr, (uint)native.Length, 48, out accepted));
        if (accepted != native.Length) throw new InvalidOperationException("Incomplete native submission.");
        var timer = Stopwatch.StartNew();
        do
        {
            NativeApi.Check(NativeApi.Read(handle, out var snapshot, 80));
            if (snapshot.Processed == snapshot.Accepted) return snapshot.ToWire();
            Thread.Sleep(1);
        } while (timer.ElapsedMilliseconds < 500);
        throw new TimeoutException("Native processing deadline exceeded.");
    }
    internal void Drain()
    {
        if (!handle.Drain()) throw new TimeoutException("Native drain deadline exceeded.");
    }
    public void Dispose() => handle.Dispose();
}
