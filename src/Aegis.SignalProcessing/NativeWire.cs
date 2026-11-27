using System.Buffers.Binary;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Aegis.SignalProcessing;

public static class NativeWire
{
    public const int Version = 1, MaxFrames = 64;
    private const int MaxBytes = 64 * 1024;
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        RespectRequiredConstructorParameters = true,
        RespectNullableAnnotations = true,
        AllowDuplicateProperties = false,
        MaxDepth = 16
    };

    public static async Task WriteAsync<T>(Stream stream, T message, CancellationToken ct)
    {
        byte[] payload = JsonSerializer.SerializeToUtf8Bytes(message, Options);
        if (payload.Length is 0 or > MaxBytes) throw new InvalidDataException("Invalid wire message length.");
        byte[] prefix = new byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(prefix, (uint)payload.Length);
        await stream.WriteAsync(prefix, ct);
        await stream.WriteAsync(payload, ct);
        await stream.FlushAsync(ct);
    }

    public static async Task<T> ReadAsync<T>(Stream stream, CancellationToken ct)
    {
        byte[] prefix = new byte[4];
        await stream.ReadExactlyAsync(prefix, ct);
        uint size = BinaryPrimitives.ReadUInt32LittleEndian(prefix);
        if (size is 0 or > MaxBytes) throw new InvalidDataException("Invalid wire message length.");
        byte[] payload = new byte[(int)size];
        await stream.ReadExactlyAsync(payload, ct);
        return JsonSerializer.Deserialize<T>(payload, Options) ?? throw new InvalidDataException("Null wire message.");
    }
}

public sealed record NativeRequest(int Version, long Generation, long RequestId, string Operation, NativeFrame[]? Frames = null);
public sealed record NativeResponse(int Version, long Generation, long RequestId, NativeSnapshot? Snapshot);
public readonly record struct NativeFrame(ulong SourceRunToken, long Sequence, long ObservedUnixMs, double ActualFlowLpm, double SetpointFlowLpm, uint Quality);
public sealed record NativeSnapshot(ulong SourceRunToken, long LastSequence, long ObservedUnixMs, double MeanAbsoluteDeviationLpm, ulong AcceptedFrameCount, ulong ProcessedFrameCount, ulong DiscardedFrameCount, ulong IgnoredFrameCount, uint SampleCount, uint ResultState, uint EngineState);
