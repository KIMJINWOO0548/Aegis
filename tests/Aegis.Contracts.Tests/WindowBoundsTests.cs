using Aegis.Desktop;
using Xunit;

namespace Aegis.Contracts.Tests;

public sealed class WindowBoundsTests
{
    [Theory]
    [InlineData(1366, 728)]
    [InlineData(1093, 582)]
    [InlineData(640, 440)]
    [InlineData(2560, 1400)]
    public void FitsLogicalWorkAreaWithoutUpscalingLargeScreens(double workWidth, double workHeight)
    {
        var (width, height) = WindowBounds.Fit(1600, 1000, workWidth, workHeight);
        Assert.InRange(width, 1, Math.Min(1600, workWidth));
        Assert.InRange(height, 1, Math.Min(1000, workHeight));
    }
}
