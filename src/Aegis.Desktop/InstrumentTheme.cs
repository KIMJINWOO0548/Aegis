using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;

namespace Aegis.Desktop;

internal enum InstrumentTheme { Dark = 0, Light = 1, Warm = 2 }

internal sealed record InstrumentPalette(string Key, uint Surface, uint Bar, uint Raised, uint Rule,
    uint Ink, uint Muted, uint Accent, uint Selection, uint Caption, uint InactiveCaption)
{
    private static readonly InstrumentPalette Graphite = new("dark", 0x101B27, 0x14212E, 0x1D3440, 0x3B5465,
        0xE6EFF5, 0xB9CCD9, 0x71E8D4, 0x18343C, 0x0D151F, 0x101824);
    private static readonly InstrumentPalette Porcelain = new("light", 0xF4F7FA, 0xEAF0F6, 0xDCE7F1, 0x879CAE,
        0x182A3B, 0x425A70, 0x145F70, 0xD7EBEF, 0xE8F0F8, 0xF1F5F9);
    private static readonly InstrumentPalette Sand = new("warm", 0xF5EEE1, 0xEDE1CE, 0xE5D3B9, 0xA28C71,
        0x32261D, 0x68503C, 0x7D481F, 0xEDD5B1, 0xE9DEC9, 0xF1E7D5);

    public static InstrumentPalette For(InstrumentTheme theme) => theme switch
    {
        InstrumentTheme.Light => Porcelain,
        InstrumentTheme.Warm => Sand,
        _ => Graphite
    };

    public static Color ColorOf(uint rgb) => Color.FromRgb((byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb);

    public static SolidColorBrush BrushOf(uint rgb)
    {
        var brush = new SolidColorBrush(ColorOf(rgb));
        brush.Freeze();
        return brush;
    }

    public void ApplyCaption(Window window)
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22000)) return;
        var handle = new WindowInteropHelper(window).Handle;
        if (handle == IntPtr.Zero) return;

        const uint systemDefault = uint.MaxValue;
        var highContrast = SystemParameters.HighContrast;
        DwmSetWindowAttribute(handle, 20, !highContrast && Key == "dark" ? 1u : 0u, sizeof(uint));
        DwmSetWindowAttribute(handle, 35, highContrast ? systemDefault : ColorRef(window.IsActive ? Caption : InactiveCaption), sizeof(uint));
        DwmSetWindowAttribute(handle, 36, highContrast ? systemDefault : ColorRef(window.IsActive ? Ink : Muted), sizeof(uint));
        DwmSetWindowAttribute(handle, 34, highContrast ? systemDefault : ColorRef(Rule), sizeof(uint));
    }

    // DWM uses COLORREF (BGR) for the native frame; Windows still supplies the caption controls.
    private static uint ColorRef(uint rgb) => (rgb >> 16) | (rgb & 0xff00) | ((rgb & 0xff) << 16);

    [DllImport("dwmapi.dll", ExactSpelling = true)]
    private static extern int DwmSetWindowAttribute(IntPtr handle, uint attribute, in uint value, uint size);
}
