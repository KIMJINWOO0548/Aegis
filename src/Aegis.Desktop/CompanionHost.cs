using System.Drawing;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

namespace Aegis.Desktop;

internal sealed class CompanionHost : IDisposable
{
    private readonly CompanionTelemetryClient _telemetry;
    private readonly CompanionPreferencesStore _preferencesStore;
    private readonly CancellationTokenSource _shutdown = new();
    private readonly DispatcherTimer _timer;
    private readonly Forms.NotifyIcon _tray;
    private readonly Forms.ContextMenuStrip _menu = new();
    private readonly Forms.ToolStripMenuItem _overlayMenu = new("오버레이 켜기");
    private readonly Dictionary<CompanionCorner, Forms.ToolStripMenuItem> _corners = [];
    private readonly Icon _icon;
    private CompanionPreferences _preferences = CompanionPreferences.Default;
    private CompanionSnapshot _snapshot = CompanionSnapshot.Empty;
    private CompanionOverlay? _overlay;
    private CompanionSettingsWindow? _settings;
    private bool _reading;
    private bool _reachable;
    private bool _disposed;
    private string _feedback = "";

    public CompanionHost(DashboardEndpoint endpoint, Action openWorkbench, Action hideWorkbench, Action exit)
    {
        _telemetry = new(endpoint);
        var settingsPath = Environment.GetEnvironmentVariable("AEGIS_COMPANION_SETTINGS_PATH");
        if (string.IsNullOrWhiteSpace(settingsPath) || !Path.IsPathFullyQualified(settingsPath))
            settingsPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Aegis", "companion-settings.json");
        _preferencesStore = new(settingsPath);
        try { _preferences = _preferencesStore.Load(); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        { _feedback = "고정 신호 설정을 읽지 못했습니다. 기본 위치로 시작했습니다."; }

        using var iconStream = Application.GetResourceStream(new Uri("pack://application:,,,/Aegis.Desktop;component/Assets/Aegis.ico")).Stream;
        using var resourceIcon = new Icon(iconStream);
        _icon = (Icon)resourceIcon.Clone();
        _menu.Items.Add("작업대 열기", null, (_, _) => openWorkbench());
        _menu.Items.Add("작업대 숨기기", null, (_, _) => hideWorkbench());
        _overlayMenu.Click += (_, _) => ToggleOverlay();
        _menu.Items.Add(_overlayMenu);
        _menu.Items.Add("고정 신호 설정", null, (_, _) => OpenSettings());
        var placement = new Forms.ToolStripMenuItem("오버레이 위치 (주 화면)");
        AddCornerOption(placement, CompanionCorner.TopRight, "오른쪽 위");
        AddCornerOption(placement, CompanionCorner.BottomRight, "오른쪽 아래");
        AddCornerOption(placement, CompanionCorner.TopLeft, "왼쪽 위");
        AddCornerOption(placement, CompanionCorner.BottomLeft, "왼쪽 아래");
        _menu.Items.Add(placement);
        _menu.Items.Add(new Forms.ToolStripSeparator());
        _menu.Items.Add("Aegis 종료", null, (_, _) => exit());
        _tray = new Forms.NotifyIcon { Text = "Aegis · 설비 계측", Icon = _icon, ContextMenuStrip = _menu, Visible = true };
        _tray.DoubleClick += (_, _) => openWorkbench();
        _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _timer.Tick += Poll;
        _timer.Start();
    }

    public bool IsOverlayVisible => _overlay?.IsVisible == true;
    public event Action? OverlayVisibilityChanged;

    public void ShowOverlay()
    {
        if (IsOverlayVisible) return;
        _overlay ??= new CompanionOverlay();
        _overlay.Corner = _preferences.Corner;
        Paint();
        _overlay.ShowPassive();
        UpdateVisibilityLabel();
        Poll(this, EventArgs.Empty);
    }

    public void ToggleOverlay()
    {
        if (!IsOverlayVisible) ShowOverlay();
        else
        {
            _overlay!.Hide();
            UpdateVisibilityLabel();
        }
    }

    public void OpenSettings(Window? owner = null)
    {
        if (_settings is not null)
        {
            if (_settings.WindowState == WindowState.Minimized) _settings.WindowState = WindowState.Normal;
            _settings.Activate();
            return;
        }
        var editor = new CompanionSettingsWindow();
        _settings = editor;
        if (owner?.IsVisible == true) editor.Owner = owner;
        editor.PinsChanged += pins => UpdatePreferences(_preferences with { Pins = pins });
        editor.Closed += (_, _) => _settings = null;
        editor.Report(_feedback);
        Paint();
        editor.Show();
        Poll(this, EventArgs.Empty);
    }

    private void AddCornerOption(Forms.ToolStripMenuItem placement, CompanionCorner corner, string caption)
    {
        var item = new Forms.ToolStripMenuItem(caption) { Checked = _preferences.Corner == corner };
        item.Click += (_, _) => UpdatePreferences(_preferences with { Corner = corner });
        _corners.Add(corner, item);
        placement.DropDownItems.Add(item);
    }

    private void UpdateVisibilityLabel()
    {
        _overlayMenu.Text = IsOverlayVisible ? "오버레이 끄기" : "오버레이 켜기";
        OverlayVisibilityChanged?.Invoke();
    }

    private async void Poll(object? sender, EventArgs e)
    {
        if (_disposed || (!IsOverlayVisible && _settings is null)) return;
        if (_reading)
        {
            Paint();
            return;
        }
        _reading = true;
        try
        {
            _snapshot = await _telemetry.ReadAsync(_shutdown.Token);
            _reachable = true;
        }
        catch (OperationCanceledException) when (_disposed) { return; }
        catch (Exception error) when (error is System.Net.Http.HttpRequestException or IOException or JsonException or OperationCanceledException)
        {
            _reachable = false;
        }
        finally { _reading = false; }
        if (!_disposed) Paint();
    }

    private void Paint()
    {
        var frame = _snapshot.BuildFrame(_preferences.Pins, DateTimeOffset.UtcNow, _reachable);
        _overlay?.UpdateFrame(frame);
        _settings?.UpdateSource(_reachable ? _snapshot : CompanionSnapshot.Empty, _preferences.Pins, frame.Status);
    }

    private void UpdatePreferences(CompanionPreferences updated)
    {
        try
        {
            _preferencesStore.Save(updated);
            _preferences = updated;
            if (_overlay is not null) _overlay.Corner = updated.Corner;
            foreach (var (corner, item) in _corners) item.Checked = corner == updated.Corner;
            _feedback = "설정을 저장했습니다.";
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            _feedback = "설정을 저장할 수 없습니다. 이전 설정을 유지합니다.";
            if (_settings is null) MessageBox.Show(_feedback, "Aegis", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        _settings?.Report(_feedback);
        Paint();
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _timer.Stop();
        _shutdown.Cancel();
        _telemetry.Dispose();
        _shutdown.Dispose();
        _tray.Visible = false;
        _tray.Dispose();
        _menu.Dispose();
        _icon.Dispose();
    }
}
