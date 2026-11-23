using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using Microsoft.Web.WebView2.Core;

namespace Aegis.Desktop;

public partial class DashboardWindow : Window
{
    private const uint CaptionColor = 35;
    private const uint TextColor = 36;
    private const uint SystemColor = uint.MaxValue;
    private const string ConnectingCaption = "Aegis · 로컬 연결 중";
    private const string UnavailableCaption = "Aegis · 로컬 연결 확인";
    private const string ConnectingMessage = "로컬 대시보드에 연결하고 있습니다.";
    private DashboardEndpoint? _endpoint;
    private ulong? _navigationId;
    private bool _initializing;
    private bool _closed;
    private readonly CompanionHost? _companion;

    public DashboardWindow()
    {
        InitializeComponent();
        _companion = (Application.Current as App)?.Companion;
        if (_companion is not null)
        {
            CompanionToolbar.Visibility = Visibility.Visible;
            _companion.OverlayVisibilityChanged += UpdateCompanionButton;
            UpdateCompanionButton();
        }
        var bounds = WindowBounds.Fit(Width, Height, SystemParameters.WorkArea.Width, SystemParameters.WorkArea.Height);
        MinWidth = Math.Min(MinWidth, bounds.Width);
        MinHeight = Math.Min(MinHeight, bounds.Height);
        Width = bounds.Width;
        Height = bounds.Height;
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        SystemParameters.StaticPropertyChanged += SystemParametersChanged;
        UpdateCaptionPalette();
    }

    protected override void OnActivated(EventArgs e)
    {
        base.OnActivated(e);
        UpdateCaptionPalette();
    }

    protected override void OnDeactivated(EventArgs e)
    {
        base.OnDeactivated(e);
        UpdateCaptionPalette();
    }

    private void SystemParametersChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(SystemParameters.HighContrast)) UpdateCaptionPalette();
    }

    private void UpdateCaptionPalette()
    {
        if (_closed || !OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22000)) return;
        var handle = new WindowInteropHelper(this).Handle;
        if (handle == IntPtr.Zero) return;
        // COLORREF uses BGR. DWM retains caption buttons, hit-testing and contrast colors.
        var caption = 0x00FBF2ECu; // #ECF2FB
        var text = 0x003B2317u; // #17233B
        if (IsActive && !SystemParameters.HighContrast &&
            DwmSetWindowAttribute(handle, CaptionColor, in caption, sizeof(uint)) >= 0 &&
            DwmSetWindowAttribute(handle, TextColor, in text, sizeof(uint)) >= 0)
            return;
        DwmSetWindowAttribute(handle, CaptionColor, SystemColor, sizeof(uint));
        DwmSetWindowAttribute(handle, TextColor, SystemColor, sizeof(uint));
    }

    [DllImport("dwmapi.dll", ExactSpelling = true)]
    private static extern int DwmSetWindowAttribute(IntPtr handle, uint attribute, in uint value, uint size);

    private void ShowConnection(string caption, string message)
    {
        Title = caption;
        Dashboard.Visibility = Visibility.Hidden;
        ConnectionMessage.Text = message;
        ConnectionPanel.Visibility = Visibility.Visible;
    }

    private void UpdateDocumentCaption(CoreWebView2 core)
    {
        if (!_closed && ConnectionPanel.Visibility == Visibility.Collapsed)
            Title = _endpoint?.ResolveCaption(core.Source, core.DocumentTitle) ?? DashboardEndpoint.DefaultCaption;
    }

    private async void WindowLoaded(object sender, RoutedEventArgs e) => await ConnectAsync();
    private async void RetryClicked(object sender, RoutedEventArgs e) => await ConnectAsync();
    private void ToggleOverlayClicked(object sender, RoutedEventArgs e) => _companion?.ToggleOverlay();
    private void CompanionSettingsClicked(object sender, RoutedEventArgs e) => _companion?.OpenSettings(this);
    private void HideWorkbenchClicked(object sender, RoutedEventArgs e) => Hide();
    private void UpdateCompanionButton() => ToggleOverlayButton.Content =
        _companion?.IsOverlayVisible == true ? "오버레이 끄기" : "오버레이 켜기";

    private async Task ConnectAsync()
    {
        if (_initializing || _closed) return;
        _initializing = true;
        ShowConnection(ConnectingCaption, ConnectingMessage);
        try
        {
            var endpoint = new DashboardEndpoint(Environment.GetEnvironmentVariable("AEGIS_GATEWAY_URL") ?? "http://127.0.0.1:8780",
                Environment.GetEnvironmentVariable("AEGIS_DASHBOARD_TOKEN") ?? "");
            _endpoint = endpoint;
            if (Dashboard.CoreWebView2 is null)
            {
                var profile = Environment.GetEnvironmentVariable("AEGIS_DESKTOP_PROFILE_DIR") ??
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Aegis", "WebView2Profile");
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
                core.NavigationStarting += (_, args) =>
                {
                    if (_endpoint?.AllowsNavigation(args.Uri) != true)
                    {
                        args.Cancel = true;
                        return;
                    }
                    _navigationId = args.NavigationId;
                    ShowConnection(ConnectingCaption, ConnectingMessage);
                };
                core.DocumentTitleChanged += (_, _) => UpdateDocumentCaption(core);
                core.SourceChanged += (_, _) => UpdateDocumentCaption(core);
                core.FrameNavigationStarting += (_, args) => args.Cancel = true;
                core.NewWindowRequested += (_, args) => args.Handled = true;
                core.DownloadStarting += (_, args) => args.Cancel = true;
                core.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
                core.NavigationCompleted += (_, args) =>
                {
                    if (_closed || args.NavigationId != _navigationId) return;
                    _navigationId = null;
                    if (!args.IsSuccess)
                    {
                        ShowConnection(UnavailableCaption, "로컬 서버에 연결할 수 없습니다. 하이브리드 데모 실행 상태를 확인하세요.");
                        return;
                    }
                    ConnectionPanel.Visibility = Visibility.Collapsed;
                    Dashboard.Visibility = Visibility.Visible;
                    UpdateDocumentCaption(core);
                };
                core.ProcessFailed += (_, _) =>
                {
                    if (_closed) return;
                    _navigationId = null;
                    ShowConnection(UnavailableCaption, "웹 화면 프로세스가 종료되었습니다. 앱을 다시 실행하거나 기존 모니터를 여세요.");
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
            ShowConnection(UnavailableCaption, "AEGIS_GATEWAY_URL과 AEGIS_DASHBOARD_TOKEN(32자 이상)을 확인하세요. URL에는 인증값을 넣지 마세요.");
        }
        catch (Exception error) when (error is WebView2RuntimeNotFoundException or COMException or InvalidOperationException or IOException or UnauthorizedAccessException)
        {
            ShowConnection(UnavailableCaption, "WebView2 화면을 시작할 수 없습니다. WebView2 Runtime과 로컬 앱 폴더 쓰기 권한을 확인하거나 기존 모니터를 여세요.");
        }
        finally { _initializing = false; }
    }

    private void LegacyClicked(object sender, RoutedEventArgs e)
    {
        ((App)Application.Current).OpenLegacyMonitor(this);
    }

    private void WindowClosed(object? sender, EventArgs e)
    {
        _closed = true;
        if (_companion is not null) _companion.OverlayVisibilityChanged -= UpdateCompanionButton;
        SystemParameters.StaticPropertyChanged -= SystemParametersChanged;
        Dashboard.Dispose();
    }
}
