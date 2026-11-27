using System.ComponentModel;
using System.Text.Json;
using Aegis.SignalProcessing;
using Microsoft.Data.Sqlite;

namespace Aegis.Gateway;

public sealed record NativeFlowEvidence(string State, DateTimeOffset? ObservedAtUtc = null,
    int SampleCount = 0, double? MeanAbsoluteDeviationLpm = null, string? Reason = null, string? SourceRunId = null);

internal sealed class NativeDeviceReview
{
    private sealed record Published(NativeReview Value, DateTimeOffset? FreshUntil);
    private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(250);
    private static readonly TimeSpan Freshness = TimeSpan.FromSeconds(3);
    private readonly CancellationTokenSource _stop = new();
    private readonly Task _worker;
    private Published _latest = new(new(null, "UNAVAILABLE", null, new("WARMING")), null);
    private int _hostPid;
    private int _sourceAvailable = 1;

    public NativeDeviceReview(string? equipmentId, string? moduleId, string path, NativeArchiveReader reader,
        Func<NativeReview> managed)
    {
        EquipmentId = equipmentId;
        ModuleId = moduleId;
        _worker = Task.Run(() => RunAsync(path, reader, managed, _stop.Token));
    }

    public string? EquipmentId { get; }
    public string? ModuleId { get; }
    public int? ProcessId => Volatile.Read(ref _hostPid) is var pid && pid > 0 ? pid : null;

    public NativeReview Read()
    {
        var current = Volatile.Read(ref _latest);
        if (Volatile.Read(ref _sourceAvailable) == 0 || current.FreshUntil <= DateTimeOffset.UtcNow)
        {
            return current.Value with
            {
                EngineState = "UNAVAILABLE",
                Review = current.Value.Review is { } review ? review with { State = "UNAVAILABLE" } : null,
                NativeFlow = new("UNAVAILABLE", current.Value.NativeFlow?.ObservedAtUtc, Reason: "SOURCE_STALE")
            };
        }
        return current.Value;
    }

    public void SetSourceAvailable(bool available) => Volatile.Write(ref _sourceAvailable, available ? 1 : 0);

    public async Task StopAsync()
    {
        try
        {
            await _stop.CancelAsync();
            await _worker;
        }
        finally { _stop.Dispose(); }
    }

    private async Task RunAsync(string path, NativeArchiveReader reader, Func<NativeReview> managed, CancellationToken token)
    {
        var recovering = false;
        long generation = 0;
        var startupFailures = 0;
        while (!token.IsCancellationRequested)
        {
            NativeHostSession? host = null;
            var synchronized = false;
            try
            {
                var beforeConnect = Volatile.Read(ref _latest);
                Volatile.Write(ref _latest, beforeConnect with
                {
                    Value = beforeConnect.Value with { Review = managed().Review }
                });
                host = await NativeHostSession.OpenAsync(path, checked(++generation), token);
                synchronized = true;
                startupFailures = 0;
                Volatile.Write(ref _hostPid, host.ProcessId);
                long cursor = 0;
                ulong runToken = 0;
                string? run = null;
                NativeArchiveFrame? latestFrame = null;
                NativeSnapshot? latestSnapshot = null;
                var seedPending = recovering;
                while (!token.IsCancellationRequested)
                {
                    if (host.HasExited) throw new IOException("The owned native host exited.");
                    var caughtUp = false;
                    var sourceError = false;
                    for (var pageIndex = 0; pageIndex < 4; pageIndex++)
                    {
                        var seeding = seedPending;
                        NativeArchivePage page;
                        try { page = seeding ? reader.ReadSeed() : reader.ReadForward(cursor); }
                        catch (Exception error) when (IsArchiveError(error))
                        {
                            sourceError = true;
                            break;
                        }
                        if (page.RunId != run)
                        {
                            run = page.RunId;
                            runToken = checked(runToken + 1);
                            latestFrame = null;
                            latestSnapshot = null;
                        }
                        if (page.Frames.Count > 0)
                        {
                            var frames = page.Frames.Select(frame => new NativeFrame(runToken, frame.Sequence,
                                frame.ObservedAtUtc.ToUnixTimeMilliseconds(), frame.ActualFlowLpm, frame.SetpointFlowLpm,
                                frame.Quality)).ToArray();
                            synchronized = false;
                            latestSnapshot = await host.SubmitAsync(frames, token);
                            ValidateResult(latestSnapshot, frames[^1]);
                            synchronized = true;
                            latestFrame = page.Frames[^1];
                        }
                        // Advance only once this generation applied the page. A seed is
                        // followed by a forward query, including writes made during seeding.
                        cursor = page.Watermark;
                        seedPending = false;
                        if (!seeding && page.CaughtUp)
                        {
                            caughtUp = true;
                            break;
                        }
                    }
                    Publish(managed(), run, latestFrame, latestSnapshot, caughtUp, recovering, sourceError);
                    if (caughtUp && latestSnapshot?.ResultState == 2 && latestFrame is not null
                        && FreshUntil(latestFrame) > DateTimeOffset.UtcNow) recovering = false;
                    await Task.Delay(PollInterval, token);
                }
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { }
            catch (Exception error) when (IsHostError(error))
            {
                if (host is null) startupFailures = Math.Min(startupFailures + 1, 4);
                recovering |= host is not null;
                var previous = Volatile.Read(ref _latest).Value;
                Volatile.Write(ref _latest, new(previous with
                {
                    EngineState = "UNAVAILABLE",
                    NativeSequence = null,
                    NativeFlow = new(recovering ? "RECOVERING" : "UNAVAILABLE", Reason: "NATIVE_HOST_UNAVAILABLE")
                }, null));
            }
            finally
            {
                Volatile.Write(ref _hostPid, 0);
                if (host is not null)
                {
                    if (token.IsCancellationRequested && synchronized)
                    {
                        try { await host.StopAsync(); }
                        catch (Exception error) when (IsHostError(error)) { }
                    }
                    await host.DisposeAsync();
                }
            }
            if (token.IsCancellationRequested) break;
            try { await Task.Delay(PollInterval * (1 << startupFailures), token); }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { break; }
        }
    }

    private void Publish(NativeReview managed, string? run, NativeArchiveFrame? frame, NativeSnapshot? snapshot, bool caughtUp,
        bool recovering, bool sourceError)
    {
        var now = DateTimeOffset.UtcNow;
        var freshUntil = frame is null ? (DateTimeOffset?)null : FreshUntil(frame);
        var current = caughtUp && frame is not null && freshUntil > now
            && frame.ObservedAtUtc <= now.AddSeconds(1) && frame.ReceivedAtUtc <= now.AddSeconds(1);
        var ready = current && snapshot?.ResultState == 2;
        var evidence = sourceError ? new NativeFlowEvidence("UNAVAILABLE", Reason: "ARCHIVE_UNAVAILABLE")
            : ready ? new("READY", frame!.ObservedAtUtc, (int)snapshot!.SampleCount, snapshot.MeanAbsoluteDeviationLpm, SourceRunId: run)
            : recovering ? new("RECOVERING", frame?.ObservedAtUtc, Reason: "REPLAY_WINDOW_INCOMPLETE")
            : current || !caughtUp || frame is null ? new("WARMING", frame?.ObservedAtUtc,
                current ? (int)(snapshot?.SampleCount ?? 0) : 0)
            : new("UNAVAILABLE", frame.ObservedAtUtc, Reason: "SOURCE_STALE");
        var value = managed with
        {
            EngineState = ready ? "RUNNING" : "UNAVAILABLE",
            NativeSequence = frame?.Sequence,
            NativeFlow = evidence
        };
        Volatile.Write(ref _latest, new(value, ready ? freshUntil : null));
    }

    private static DateTimeOffset FreshUntil(NativeArchiveFrame frame)
    {
        var earliest = frame.ObservedAtUtc < frame.ReceivedAtUtc ? frame.ObservedAtUtc : frame.ReceivedAtUtc;
        return earliest >= DateTimeOffset.MaxValue - Freshness ? DateTimeOffset.MaxValue : earliest + Freshness;
    }

    private static void ValidateResult(NativeSnapshot snapshot, NativeFrame last)
    {
        if (snapshot.SourceRunToken != last.SourceRunToken || snapshot.LastSequence != last.Sequence
            || snapshot.ObservedUnixMs != last.ObservedUnixMs || snapshot.EngineState != 1
            || snapshot.AcceptedFrameCount != snapshot.ProcessedFrameCount || snapshot.DiscardedFrameCount != 0
            || snapshot.ResultState is < 1 or > 2 || snapshot.SampleCount > 32
            || (snapshot.ResultState == 2) != (snapshot.SampleCount == 32)
            || !double.IsFinite(snapshot.MeanAbsoluteDeviationLpm) || snapshot.MeanAbsoluteDeviationLpm is < 0 or > 100)
            throw new InvalidDataException("The native host returned an invalid archive result.");
    }

    private static bool IsArchiveError(Exception error) => error is SqliteException or IOException or InvalidDataException
        or UnauthorizedAccessException or JsonException or InvalidOperationException or FormatException or ArgumentException;

    private static bool IsHostError(Exception error) => error is IOException or InvalidDataException or UnauthorizedAccessException
        or JsonException or Win32Exception or OperationCanceledException or InvalidOperationException or PlatformNotSupportedException;
}
