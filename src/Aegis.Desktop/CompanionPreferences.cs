using System.IO;
using System.Text.Json;

namespace Aegis.Desktop;

internal sealed record CompanionPreferences(CompanionPin[] Pins, CompanionCorner Corner)
{
    public const int PinLimit = 32;
    public static CompanionPreferences Default { get; } = new([], CompanionCorner.TopRight);
}

internal sealed class CompanionPreferencesStore(string path)
{
    private const int FileLimit = 64 * 1024;

    public CompanionPreferences Load()
    {
        try
        {
            using var input = File.OpenRead(path);
            if (input.Length > FileLimit) throw new JsonException("Companion settings exceed the supported size.");
            var settings = JsonSerializer.Deserialize<CompanionPreferences>(input);
            if (settings is null || settings.Pins is null || settings.Pins.Length > CompanionPreferences.PinLimit ||
                settings.Pins.Any(pin => pin is null || !ValidKey(pin.DeviceId) || !ValidKey(pin.SignalKey)) ||
                settings.Pins.Distinct().Count() != settings.Pins.Length || !Enum.IsDefined(settings.Corner))
                throw new JsonException("Invalid companion settings.");
            return settings;
        }
        catch (FileNotFoundException) { return CompanionPreferences.Default; }
        catch (DirectoryNotFoundException) { return CompanionPreferences.Default; }
    }

    public void Save(CompanionPreferences value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var pending = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var output = new FileStream(pending, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(output, value);
                output.Flush(flushToDisk: true);
            }
            File.Move(pending, path, overwrite: true);
        }
        finally
        {
            if (File.Exists(pending)) File.Delete(pending);
        }
    }

    private static bool ValidKey(string? value) =>
        value is { Length: > 0 and <= 128 } && value.All(c => char.IsAsciiLetterOrDigit(c) || c is '.' or '_' or ':' or '-');
}
