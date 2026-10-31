using System.Windows;

namespace Aegis.Desktop;

public partial class App : Application
{
    private void ApplicationStartup(object sender, StartupEventArgs e)
    {
        MainWindow = string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("AEGIS_GATEWAY_URL"))
            ? new MainWindow() : new DashboardWindow();
        MainWindow.Show();
    }
}
