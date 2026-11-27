using System.ComponentModel;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

namespace Aegis.NativeHost;

[SupportedOSPlatform("windows")]
internal static partial class LocalPipe
{
    private const uint PipeAccessDuplex = 0x3;
    private const uint FirstPipeInstance = 0x00080000;
    private const uint Overlapped = 0x40000000;
    private const uint RejectRemoteClients = 0x8;

    internal static NamedPipeServerStream Open(string pipeName)
    {
        using var identity = WindowsIdentity.GetCurrent();
        var sid = identity.User?.Value ?? throw new UnauthorizedAccessException("Missing current Windows user.");
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW($"O:{sid}D:P(A;;GA;;;{sid})", 1, out var descriptor, 0))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            var attributes = new SecurityAttributes { Length = (uint)Marshal.SizeOf<SecurityAttributes>(), Descriptor = descriptor };
            var handle = CreateNamedPipeW(@"\\.\pipe\" + pipeName,
                PipeAccessDuplex | Overlapped | FirstPipeInstance, RejectRemoteClients, 1, 4096, 4096, 0, ref attributes);
            if (handle.IsInvalid)
            {
                var error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error);
            }
            try { return new NamedPipeServerStream(PipeDirection.InOut, isAsync: true, isConnected: false, handle); }
            catch { handle.Dispose(); throw; }
        }
        finally { _ = LocalFree(descriptor); }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes
    {
        public uint Length;
        public nint Descriptor;
        public int InheritHandle;
    }

    [LibraryImport("advapi32.dll", StringMarshalling = StringMarshalling.Utf16, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool ConvertStringSecurityDescriptorToSecurityDescriptorW(
        string descriptor, uint revision, out nint securityDescriptor, nint size);

    [LibraryImport("kernel32.dll", StringMarshalling = StringMarshalling.Utf16, SetLastError = true)]
    private static partial SafePipeHandle CreateNamedPipeW(string name, uint openMode, uint pipeMode,
        uint instances, uint outBufferSize, uint inBufferSize, uint timeout, ref SecurityAttributes attributes);

    [LibraryImport("kernel32.dll")]
    private static partial nint LocalFree(nint handle);
}
