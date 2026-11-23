using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

namespace Aegis.Desktop;

internal enum CompanionCorner { TopRight, BottomRight, TopLeft, BottomLeft }

internal partial class CompanionOverlay : Window
{
    private static readonly (string Key, string Light, string Warm)[] OverlayColors =
    [
        ("OverlayPanel", "#E8F4F7FA", "#E8F5EEE1"),
        ("OverlayBorder", "#B0637E91", "#B08B7256"),
        ("OverlayInk", "#172936", "#36291F"),
        ("OverlaySecondary", "#42576A", "#584739"),
        ("OverlayRule", "#80647E91", "#807E664B"),
        ("OverlaySubtleRule", "#50647E91", "#507E664B"),
        ("OverlayLive", "#105F54", "#275946"),
        ("OverlayArchived", "#264982", "#59446E"),
        ("OverlayWarning", "#774013", "#783D17")
    ];

    private const double StandardWidth = 366;
    private const double EdgeMargin = 14;
    private const double CaptionClearance = 56;
    private const int MaximumSignals = 5;
    private const int MaximumCollectors = 2;

    private const int GwlExStyle = -20;
    private const int WsExToolWindow = 0x00000080;
    private const int WsExAppWindow = 0x00040000;
    private const int WsExNoActivate = 0x08000000;
    private const uint SwpNoSize = 0x0001;
    private const uint SwpNoZOrder = 0x0004;
    private const uint SwpNoActivate = 0x0010;
    private const int WmMouseActivate = 0x0021;
    private const int WmDisplayChange = 0x007E;
    private const int WmDpiChanged = 0x02E0;
    private const int MaNoActivateAndEat = 4;

    private readonly Dictionary<string, Brush> _normalPalette = [];
    private InstrumentTheme _theme;
    private CompanionFrame _frame = new([], [], "수집 대기");
    private CompanionCorner _corner = CompanionCorner.TopRight;
    private HwndSource? _source;
    private bool _positionQueued;

    public CompanionOverlay()
    {
        InitializeComponent();
        foreach (var (key, _, _) in OverlayColors)
            _normalPalette.Add(key, (Brush)Resources[key]);
        ApplyPalette();
        SizeChanged += (_, _) => QueuePosition();
        RenderFrame();
    }

    internal CompanionCorner Corner
    {
        get => _corner;
        set
        {
            if (_corner == value) return;
            _corner = value;
            QueuePosition();
        }
    }

    internal void UpdateFrame(CompanionFrame frame)
    {
        _frame = frame;
        RenderFrame();
    }

    internal void ApplyTheme(InstrumentTheme theme)
    {
        if (_theme == theme) return;
        _theme = theme;
        ApplyPalette();
    }

    internal void ShowPassive()
    {
        if (IsVisible)
        {
            QueuePosition();
            return;
        }

        // Establish the native no-activate style before any pixels appear.
        new WindowInteropHelper(this).EnsureHandle();
        RenderFrame();
        Opacity = 0;
        try
        {
            Show();
            UpdateLayout();
            PositionInWorkArea();
        }
        finally { Opacity = 1; }
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        var handle = new WindowInteropHelper(this).Handle;
        var style = GetWindowLong(handle, GwlExStyle);
        SetWindowLong(handle, GwlExStyle, (style | WsExNoActivate | WsExToolWindow) & ~WsExAppWindow);
        var error = Marshal.GetLastPInvokeError();
        var expected = WsExNoActivate | WsExToolWindow;
        if ((GetWindowLong(handle, GwlExStyle) & (expected | WsExAppWindow)) != expected)
            throw new Win32Exception(error, "수동 계측 창을 비활성 창으로 설정할 수 없습니다.");

        _source = HwndSource.FromHwnd(handle) ?? throw new InvalidOperationException("계측 창을 시작할 수 없습니다.");
        _source.AddHook(WindowMessage);
        SystemParameters.StaticPropertyChanged += SystemParametersChanged;
    }

    protected override void OnClosed(EventArgs e)
    {
        SystemParameters.StaticPropertyChanged -= SystemParametersChanged;
        _source?.RemoveHook(WindowMessage);
        _source = null;
        base.OnClosed(e);
    }

    private IntPtr WindowMessage(IntPtr handle, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message == WmMouseActivate)
        {
            handled = true;
            return new IntPtr(MaNoActivateAndEat);
        }

        if (message is WmDisplayChange or WmDpiChanged)
            QueuePosition(reflow: true);
        return IntPtr.Zero;
    }

    private void SystemParametersChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(SystemParameters.HighContrast)) ApplyPalette();
        if (e.PropertyName == nameof(SystemParameters.WorkArea)) QueuePosition(reflow: true);
    }

    private void ApplyPalette()
    {
        foreach (var (key, light, warm) in OverlayColors)
        {
            if (SystemParameters.HighContrast)
            {
                Resources[key] = key == "OverlayPanel" ? SystemColors.WindowBrush : SystemColors.WindowTextBrush;
                continue;
            }
            Resources[key] = _theme switch
            {
                InstrumentTheme.Light => CreateBrush(light),
                InstrumentTheme.Warm => CreateBrush(warm),
                _ => _normalPalette[key]
            };
        }
    }

    private static Brush CreateBrush(string value)
    {
        var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(value));
        brush.Freeze();
        return brush;
    }

    private void RenderFrame()
    {
        var size = GetWorkAreaSize();
        Width = Math.Min(StandardWidth, Math.Max(1, size.Width - 2 * EdgeMargin));
        MaxHeight = Math.Max(1, size.Height - EdgeMargin - CaptionClearance);

        var count = _frame.Signals.Count;
        var collectorCount = _frame.Collectors.Count;
        var budget = Math.Min(MaxHeight, Math.Max(200, size.Height * .45));

        // Reserve the header/footer, table column names and overflow notice before adding 30 DIP rows.
        var fixedHeight = 211 + (count > MaximumSignals ? 22 : 0) + (collectorCount > MaximumCollectors ? 15 : 0);
        var additionalLimit = Math.Clamp((int)((budget - fixedHeight) / 30), 0, MaximumSignals - 1);
        if (count - 1 > additionalLimit && count <= MaximumSignals)
            additionalLimit = Math.Clamp((int)((budget - fixedHeight - 22) / 30), 0, MaximumSignals - 1);

        FrameStatus.Text = _frame.Status;
        var signal = _frame.Signals.FirstOrDefault();
        PrimaryLabel.Text = signal?.Label ?? "고정한 신호 없음";
        PrimarySource.Text = signal?.Pin.DeviceId ?? "";
        PrimaryValue.Text = signal?.Value ?? "—";
        PrimaryUnit.Text = string.IsNullOrWhiteSpace(signal?.Unit) ? "" : " " + signal.Unit;
        PrimaryState.Text = signal?.State ?? "";
        PrimaryObserved.Text = string.IsNullOrWhiteSpace(signal?.ObservedAt) ? "" : "원본 관측 " + signal.ObservedAt;
        PrimaryEvidence.Visibility = VisibleWhen(signal is not null);
        EmptyNotice.Visibility = VisibleWhen(signal is null);

        var rows = _frame.Signals.Skip(1).Take(additionalLimit).ToArray();
        SetItems(SecondarySignals, rows);
        SignalTable.Visibility = VisibleWhen(rows.Length > 0);
        var hiddenSignals = Math.Max(0, count - 1 - rows.Length);
        SignalOverflow.Visibility = VisibleWhen(hiddenSignals > 0);
        SignalOverflow.Text = $"외 {hiddenSignals}개 · 전체 신호는 작업대에서";

        SetItems(Collectors, _frame.Collectors.Take(MaximumCollectors).ToArray());
        CollectorSection.Visibility = VisibleWhen(collectorCount > 0);
        CollectorOverflow.Visibility = VisibleWhen(collectorCount > MaximumCollectors);
        CollectorOverflow.Text = $"수집기 외 {collectorCount - MaximumCollectors}개 · 작업대에서 보기";
    }

    private static void SetItems<T>(System.Windows.Controls.ItemsControl control, T[] values)
    {
        if (control.ItemsSource is not T[] old || !old.SequenceEqual(values))
            control.ItemsSource = values;
    }

    private static Visibility VisibleWhen(bool condition) => condition ? Visibility.Visible : Visibility.Collapsed;

    private void QueuePosition(bool reflow = false)
    {
        if (!IsVisible || _source is null) return;
        if (reflow) RenderFrame();
        if (_positionQueued) return;
        _positionQueued = true;
        Dispatcher.InvokeAsync(() =>
        {
            _positionQueued = false;
            if (IsVisible) PositionInWorkArea();
        }, DispatcherPriority.Loaded);
    }

    private Size GetWorkAreaSize()
    {
        if (_source?.CompositionTarget is { } target && TryGetWorkArea(out var work))
        {
            var scale = target.TransformToDevice;
            return new Size((work.Right - work.Left) / scale.M11, (work.Bottom - work.Top) / scale.M22);
        }
        return SystemParameters.WorkArea.Size;
    }

    private void PositionInWorkArea()
    {
        if (_source is not { CompositionTarget: { } target } source ||
            !TryGetWorkArea(out var work) || !GetWindowRect(source.Handle, out var bounds))
            return;

        var scale = target.TransformToDevice;
        var xMargin = (int)Math.Round(EdgeMargin * scale.M11);
        var yMargin = (int)Math.Round(EdgeMargin * scale.M22);
        var topInset = (int)Math.Round(CaptionClearance * scale.M22);
        var width = bounds.Right - bounds.Left;
        var height = bounds.Bottom - bounds.Top;
        var left = _corner is CompanionCorner.TopLeft or CompanionCorner.BottomLeft;
        var bottom = _corner is CompanionCorner.BottomLeft or CompanionCorner.BottomRight;
        var x = left ? work.Left + xMargin : work.Right - width - xMargin;
        var y = bottom ? work.Bottom - height - yMargin : work.Top + topInset;
        x = Math.Clamp(x, work.Left, Math.Max(work.Left, work.Right - width));
        y = Math.Clamp(y, work.Top, Math.Max(work.Top, work.Bottom - height));
        if (x != bounds.Left || y != bounds.Top)
            SetWindowPos(source.Handle, IntPtr.Zero, x, y, 0, 0, SwpNoSize | SwpNoZOrder | SwpNoActivate);
    }

    private static bool TryGetWorkArea(out RectPixels workArea)
    {
        // The primary display contains the virtual desktop coordinate (0, 0).
        var monitor = MonitorFromPoint(new PointPixels { X = 0, Y = 0 }, 1);
        var info = new MonitorInfo { Size = Marshal.SizeOf<MonitorInfo>() };
        var found = monitor != IntPtr.Zero && GetMonitorInfo(monitor, ref info);
        workArea = info.Work;
        return found;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PointPixels { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct RectPixels { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo { public int Size; public RectPixels Monitor, Work; public uint Flags; }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern int GetWindowLong(IntPtr handle, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong(IntPtr handle, int index, int value);

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr handle, out RectPixels rect);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr handle, IntPtr after, int x, int y, int width, int height, uint flags);

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromPoint(PointPixels point, uint flags);

    [DllImport("user32.dll", EntryPoint = "GetMonitorInfoW")]
    private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
}
