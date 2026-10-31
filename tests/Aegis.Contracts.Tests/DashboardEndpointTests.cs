using Aegis.Desktop;
using Xunit;

namespace Aegis.Contracts.Tests;

public sealed class DashboardEndpointTests
{
    [Theory]
    [InlineData("https://127.0.0.1:8780")]
    [InlineData("http://localhost:8780")]
    [InlineData("http://127.0.0.1.example:8780")]
    [InlineData("http://user@127.0.0.1:8780")]
    [InlineData("http://127.0.0.1:8780/?token=secret")]
    [InlineData("http://127.0.0.1:8780/#fragment")]
    [InlineData("http://127.0.0.1:8780/nested")]
    public void RejectsUnsafeOrigins(string value) =>
        Assert.Throws<ArgumentException>(() => new DashboardEndpoint(value, new string('a', 32)));

    [Theory]
    [InlineData("")]
    [InlineData("short")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\r\nX-Other: value")]
    public void RejectsInvalidHeaderTokens(string value) =>
        Assert.Throws<ArgumentException>(() => new DashboardEndpoint("http://127.0.0.1:8780", value));

    [Fact]
    public void NavigationAndCredentialsStayInsideConfiguredOrigin()
    {
        var endpoint = new DashboardEndpoint("http://127.0.0.1:8780/", new string('a', 32));
        Assert.Equal("http://127.0.0.1:8780/", endpoint.Origin.AbsoluteUri);
        Assert.True(endpoint.AllowsNavigation("http://127.0.0.1:8780/?view=signals"));
        Assert.False(endpoint.AllowsNavigation("http://127.0.0.1:8781/"));
        Assert.False(endpoint.AllowsNavigation("http://user@127.0.0.1:8780/"));
        Assert.False(endpoint.AllowsNavigation("file:///C:/Windows/win.ini"));
        Assert.False(endpoint.AllowsNavigation("javascript:alert(1)"));
        Assert.False(endpoint.AllowsNavigation("http://localhost:8780/"));
    }
}
