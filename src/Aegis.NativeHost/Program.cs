using System.Globalization;
using System.Runtime.InteropServices;
using Aegis.SignalProcessing;
using Microsoft.Win32.SafeHandles;

namespace Aegis.NativeHost;

internal static partial class Program
{
    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetNamedPipeClientProcessId(SafePipeHandle pipe, out uint clientProcessId);

    private static async Task<int> Main(string[] args)
    {
        try
        {
            if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
            if (args.Length != 6 || args[0] != "--pipe" || args[2] != "--generation" || args[4] != "--parent"
                || !args[1].StartsWith("aegis-native-", StringComparison.Ordinal)
                || !Guid.TryParseExact(args[1][13..], "N", out _)
                || !long.TryParse(args[3], NumberStyles.None, CultureInfo.InvariantCulture, out long generation) || generation <= 0
                || !uint.TryParse(args[5], NumberStyles.None, CultureInfo.InvariantCulture, out uint parent) || parent == 0)
                throw new ArgumentException("Invalid native host arguments.");
            NativeApi.Initialize();
            using var pipe = LocalPipe.Open(args[1]);
            using (var connectDeadline = new CancellationTokenSource(TimeSpan.FromSeconds(10)))
                await pipe.WaitForConnectionAsync(connectDeadline.Token);
            if (!GetNamedPipeClientProcessId(pipe.SafePipeHandle, out uint actualParent) || actualParent != parent)
                throw new UnauthorizedAccessException("Unexpected pipe client process.");
            using var engine = new NativeEngine();
            long lastRequestId = 0;
            while (true)
            {
                NativeRequest request = await NativeWire.ReadAsync<NativeRequest>(pipe, CancellationToken.None);
                if (request.Version != NativeWire.Version || request.Generation != generation
                    || request.RequestId <= lastRequestId)
                    throw new InvalidDataException("Invalid request identity.");
                lastRequestId = request.RequestId;
                NativeSnapshot? snapshot;
                switch (request.Operation)
                {
                    case "submit" when request.Frames is { Length: > 0 and <= NativeWire.MaxFrames }:
                        snapshot = engine.Submit(request.Frames);
                        break;
                    case "stop" when request.Frames is null:
                        engine.Drain();
                        snapshot = null;
                        break;
                    default:
                        throw new InvalidDataException("Invalid native operation or frame count.");
                }
                using var replyDeadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await NativeWire.WriteAsync(pipe, new NativeResponse(NativeWire.Version, generation, request.RequestId, snapshot), replyDeadline.Token);
                if (request.Operation == "stop") return 0;
            }
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"Native host failed: {exception.GetType().Name}: {exception.Message}");
            return 1;
        }
    }
}
