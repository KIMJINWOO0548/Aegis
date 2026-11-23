using System.Windows;

namespace Aegis.Desktop;

public partial class App : Application
{
    internal CompanionHost? Companion { get; private set; }
    private DashboardWindow? _workbench;
    private bool _exiting;

    private void ApplicationStartup(object sender, StartupEventArgs e)
    {
        var address = Environment.GetEnvironmentVariable("AEGIS_GATEWAY_URL");
        if (string.IsNullOrWhiteSpace(address))
        {
            MainWindow = new MainWindow();
            MainWindow.Show();
            return;
        }

        DashboardEndpoint? endpoint = null;
        try { endpoint = new DashboardEndpoint(address, Environment.GetEnvironmentVariable("AEGIS_DASHBOARD_TOKEN") ?? ""); }
        catch (ArgumentException) { /* The existing connection screen explains invalid configuration. */ }
        if (endpoint is null)
        {
            OpenWorkbench();
            return;
        }

        ShutdownMode = ShutdownMode.OnExplicitShutdown;
        Companion = new CompanionHost(endpoint, OpenWorkbench, HideWorkbench, Quit);
        if (!e.Args.Contains("--tray", StringComparer.Ordinal)) OpenWorkbench();
        if (e.Args.Contains("--overlay", StringComparer.Ordinal)) Companion.ShowOverlay();
    }

    private void OpenWorkbench()
    {
        if (_workbench is null)
        {
            _workbench = new DashboardWindow();
            _workbench.Closing += (_, e) =>
            {
                if (Companion is null || _exiting) return;
                e.Cancel = true;
                HideWorkbench();
            };
            MainWindow = _workbench;
        }
        _workbench.Show();
        if (_workbench.WindowState == WindowState.Minimized) _workbench.WindowState = WindowState.Normal;
        _workbench.Activate();
    }

    private void HideWorkbench() => _workbench?.Hide();

    internal void OpenLegacyMonitor(DashboardWindow current)
    {
        var legacy = new MainWindow();
        legacy.Show();
        if (Companion is not null)
        {
            current.Hide();
            return;
        }
        MainWindow = legacy;
        current.Close();
    }

    private void Quit()
    {
        _exiting = true;
        Shutdown();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        Companion?.Dispose();
        base.OnExit(e);
    }
}
