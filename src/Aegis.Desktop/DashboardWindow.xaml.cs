using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using Microsoft.Web.WebView2.Core;

namespace Aegis.Desktop;

public partial class DashboardWindow : Window
{
    private DashboardEndpoint? _endpoint;
    private bool _initializing;
    private bool _closed;

    public DashboardWindow()
    {
        InitializeComponent();
        var bounds = WindowBounds.Fit(Width, Height, SystemParameters.WorkArea.Width, SystemParameters.WorkArea.Height);
        MinWidth = Math.Min(MinWidth, bounds.Width);
        MinHeight = Math.Min(MinHeight, bounds.Height);
        Width = bounds.Width;
        Height = bounds.Height;
    }

    private async void WindowLoaded(object sender, RoutedEventArgs e) => await ConnectAsync();
    private async void RetryClicked(object sender, RoutedEventArgs e) => await ConnectAsync();

    private async Task ConnectAsync()
    {
        if (_initializing || _closed) return;
        _initializing = true;
        ConnectionPanel.Visibility = Visibility.Visible;
        ConnectionMessage.Text = "로컬 대시보드에 연결하고 있습니다.";
        try
        {
            var endpoint = new DashboardEndpoint(Environment.GetEnvironmentVariable("AEGIS_GATEWAY_URL") ?? "http://127.0.0.1:8780",
                Environment.GetEnvironmentVariable("AEGIS_DASHBOARD_TOKEN") ?? "");
            _endpoint = endpoint;
            if (Dashboard.CoreWebView2 is null)
            {
                var profile = Path.Combine(AppContext.BaseDirectory, "WebView2Profile");
                var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: profile);
                if (_closed) return;
                await Dashboard.EnsureCoreWebView2Async(environment);
                if (_closed) return;
                var core = Dashboard.CoreWebView2 ?? throw new InvalidOperationException("WebView2 initialization did not complete.");
                core.Settings.AreHostObjectsAllowed = false;
                core.Settings.IsWebMessageEnabled = false;
                core.Settings.AreDefaultScriptDialogsEnabled = false;
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.AreDevToolsEnabled = false;
                core.Settings.IsPasswordAutosaveEnabled = false;
                core.Settings.IsGeneralAutofillEnabled = false;
                core.NavigationStarting += (_, args) => args.Cancel = _endpoint?.AllowsNavigation(args.Uri) != true;
                core.FrameNavigationStarting += (_, args) => args.Cancel = true;
                core.NewWindowRequested += (_, args) => args.Handled = true;
                core.DownloadStarting += (_, args) => args.Cancel = true;
                core.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
                core.NavigationCompleted += (_, args) =>
                {
                    if (_closed) return;
                    ConnectionPanel.Visibility = args.IsSuccess ? Visibility.Collapsed : Visibility.Visible;
                    if (!args.IsSuccess) ConnectionMessage.Text = "로컬 서버에 연결할 수 없습니다. 하이브리드 데모 실행 상태를 확인하세요.";
                };
                core.ProcessFailed += (_, _) =>
                {
                    if (_closed) return;
                    ConnectionPanel.Visibility = Visibility.Visible;
                    ConnectionMessage.Text = "웹 화면 프로세스가 종료되었습니다. 앱을 다시 실행하거나 기존 모니터를 여세요.";
                };
            }
            // Only this first same-origin document carries the bootstrap secret; fetch and SignalR use an HttpOnly cookie.
            var initialized = Dashboard.CoreWebView2 ?? throw new InvalidOperationException("WebView2 initialization did not complete.");
            var request = initialized.Environment.CreateWebResourceRequest(
                endpoint.Origin.AbsoluteUri, "GET", null,
                "X-Aegis-Dashboard-Token: " + endpoint.BootstrapToken + "\r\n");
            initialized.NavigateWithWebResourceRequest(request);
        }
        catch (ArgumentException)
        {
            ConnectionMessage.Text = "AEGIS_GATEWAY_URL과 AEGIS_DASHBOARD_TOKEN(32자 이상)을 확인하세요. URL에는 인증값을 넣지 마세요.";
        }
        catch (Exception error) when (error is WebView2RuntimeNotFoundException or COMException or InvalidOperationException or IOException or UnauthorizedAccessException)
        {
            ConnectionMessage.Text = "WebView2 화면을 시작할 수 없습니다. WebView2 Runtime과 로컬 앱 폴더 쓰기 권한을 확인하거나 기존 모니터를 여세요.";
        }
        finally { _initializing = false; }
    }

    private void LegacyClicked(object sender, RoutedEventArgs e)
    {
        var legacy = new MainWindow();
        Application.Current.MainWindow = legacy;
        legacy.Show();
        Close();
    }

    private void WindowClosed(object? sender, EventArgs e)
    {
        _closed = true;
        Dashboard.Dispose();
    }
}
