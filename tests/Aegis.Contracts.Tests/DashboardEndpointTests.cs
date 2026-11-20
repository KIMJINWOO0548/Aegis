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

    [Theory]
    [InlineData("Aegis · 로컬 대시보드 접속")]
    [InlineData("Aegis · 산업 신호 모니터링")]
    [InlineData("Aegis · 장비 · 모듈")]
    [InlineData("Aegis · 실시간 신호")]
    [InlineData("Aegis · 근거 검토")]
    [InlineData("Aegis · 태그 보관 이력")]
    [InlineData("Aegis · 데이터 흐름")]
    public void RootDocumentMaySelectOnlyFixedViewNames(string title)
    {
        var endpoint = new DashboardEndpoint("http://127.0.0.1:8780", new string('a', 32));
        Assert.Equal(title, endpoint.ResolveCaption("http://127.0.0.1:8780/", title));
    }

    [Theory]
    [InlineData("http://127.0.0.1:8780/", "Aegis · 태그 보관 이력 QA-DEVICE-02")]
    [InlineData("http://127.0.0.1:8780/", "Aegis · 실시간 신호\r\nsecret")]
    [InlineData("http://127.0.0.1:8780/", "Aegis · 실시간 신호 ")]
    [InlineData("http://127.0.0.1:8780/?tag=data", "Aegis · 태그 보관 이력")]
    [InlineData("http://127.0.0.1:8780/#review", "Aegis · 태그 보관 이력")]
    [InlineData("http://127.0.0.1:8780/details", "Aegis · 태그 보관 이력")]
    [InlineData("http://127.0.0.1:8781/", "Aegis · 태그 보관 이력")]
    [InlineData("http://user@127.0.0.1:8780/", "Aegis · 태그 보관 이력")]
    [InlineData("about:blank", "Aegis · 태그 보관 이력")]
    public void OtherTitlesOrDocumentsKeepTheStableProductCaption(string source, string title)
    {
        var endpoint = new DashboardEndpoint("http://127.0.0.1:8780", new string('a', 32));
        Assert.Equal("Aegis · 설비 신호 관측", endpoint.ResolveCaption(source, title));
    }
}
