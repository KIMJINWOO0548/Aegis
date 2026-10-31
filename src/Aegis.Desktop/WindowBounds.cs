namespace Aegis.Desktop;

public static class WindowBounds
{
    public static (double Width, double Height) Fit(double preferredWidth, double preferredHeight, double workWidth, double workHeight) =>
        (Math.Min(preferredWidth, workWidth * .94), Math.Min(preferredHeight, workHeight * .94));
}
