namespace Aegis.Desktop;

public sealed class DashboardEndpoint
{
    public Uri Origin { get; }
    public string BootstrapToken { get; }

    public DashboardEndpoint(string address, string token)
    {
        if (!Uri.TryCreate(address, UriKind.Absolute, out var origin) ||
            origin.Scheme != Uri.UriSchemeHttp || origin.Host != "127.0.0.1" ||
            origin.UserInfo.Length != 0 || origin.AbsolutePath != "/" ||
            origin.Query.Length != 0 || origin.Fragment.Length != 0)
            throw new ArgumentException("A literal loopback HTTP origin is required.", nameof(address));
        if (token.Length is < 32 or > 256 || token.Any(character => character is < '!' or > '~'))
            throw new ArgumentException("A printable ASCII token of 32..256 characters is required.", nameof(token));
        Origin = origin;
        BootstrapToken = token;
    }

    public bool AllowsNavigation(string address) =>
        Uri.TryCreate(address, UriKind.Absolute, out var candidate) &&
        candidate.Scheme == Origin.Scheme && candidate.Host == Origin.Host &&
        candidate.Port == Origin.Port && candidate.UserInfo.Length == 0;
}
