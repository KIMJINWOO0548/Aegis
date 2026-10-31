using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using Aegis.Contracts;

namespace Aegis.Desktop;

public partial class MainWindow : Window
{
    private readonly CancellationTokenSource _shutdown = new();
    private bool _pollingStarted;
    private bool _applyingSnapshot;

    public MainWindow()
    {
        InitializeComponent();
        var bounds = WindowBounds.Fit(Width, Height, SystemParameters.WorkArea.Width, SystemParameters.WorkArea.Height);
        MinWidth = Math.Min(MinWidth, bounds.Width);
        MinHeight = Math.Min(MinHeight, bounds.Height);
        Width = bounds.Width;
        Height = bounds.Height;
    }

    private async void WindowLoaded(object sender, RoutedEventArgs e)
    {
        if (_pollingStarted) return;
        _pollingStarted = true;
        var cancellationToken = _shutdown.Token;
        DesktopApiClient client;
        try
        {
            client = new DesktopApiClient(Environment.GetEnvironmentVariable("AEGIS_API_URL") ?? "http://127.0.0.1:8765",
                Environment.GetEnvironmentVariable("AEGIS_API_TOKEN") ?? "");
        }
        catch (ArgumentException)
        {
            ShowDisconnected("실행 환경에 API와 같은 AEGIS_API_TOKEN(32자 이상)을 설정한 뒤 앱을 다시 실행하세요. AEGIS_API_URL은 http://127.0.0.1:8765 같은 루프백 주소여야 합니다.");
            return;
        }

        using (client)
        {
            try
            {
                while (!cancellationToken.IsCancellationRequested)
                {
                    var selectedDeviceId = DeviceSelector.SelectedValue as string;
                    try
                    {
                        var snapshot = await client.ReadAsync(selectedDeviceId, cancellationToken);
                        cancellationToken.ThrowIfCancellationRequested();
                        if (selectedDeviceId != DeviceSelector.SelectedValue as string) continue;
                        ShowSnapshot(snapshot);
                    }
                    catch (HttpRequestException error)
                    {
                        ShowDisconnected(error.StatusCode == HttpStatusCode.Unauthorized
                            ? "로컬 API 인증을 확인하세요. API와 앱의 AEGIS_API_TOKEN을 같게 설정한 뒤 앱을 다시 실행하세요."
                            : "로컬 API에 연결할 수 없습니다. API 실행 상태와 연결 안내를 확인하세요.");
                    }
                    catch (JsonException)
                    {
                        ShowDisconnected("API 응답 형식이나 장비 정보가 일치하지 않습니다. 관측값을 표시하지 않습니다.");
                    }
                    catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
                    {
                        ShowDisconnected("로컬 API 응답 시간이 초과되었습니다. 연결 상태를 다시 확인하고 있습니다.");
                    }
                    await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken);
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
        }
    }

    private void ShowSnapshot(DesktopSnapshot snapshot)
    {
        _applyingSnapshot = true;
        try
        {
            if (DeviceSelector.ItemsSource is not IReadOnlyList<DeviceDescriptor> previous ||
                !previous.Select(device => device.DeviceId).SequenceEqual(snapshot.Devices.Select(device => device.DeviceId)))
                DeviceSelector.ItemsSource = snapshot.Devices;
            DeviceSelector.SelectedValue = snapshot.DeviceId;
        }
        finally { _applyingSnapshot = false; }
        DeviceIdText.Text = snapshot.DeviceId ?? "장비 없음";
        if (snapshot.Mode != "EMULATORS")
        {
            ShowDisconnected("API가 고정 Fixture 모드입니다. 별도 에뮬레이터와 --emulators 설정으로 API를 실행하세요.");
            ModeBadgeText.Text = "FIXTURE";
            return;
        }

        ModeBadgeText.Text = "EMULATOR";
        SourceText.Text = "시뮬레이터";
        TransportText.Text = snapshot.State switch
        {
            ConnectionState.CONNECTED => "연결됨",
            ConnectionState.CONNECTING => "연결 중",
            ConnectionState.STALE => "수신 지연",
            ConnectionState.REJECTED => "응답 거부",
            ConnectionState.DISCONNECTED => "연결 끊김",
            _ => "장비 없음"
        };
        HeadlineText.Text = snapshot.State == ConnectionState.CONNECTED ? "에뮬레이터 연결" : TransportText.Text;
        DescriptionText.Text = snapshot.State == ConnectionState.CONNECTED
            ? "최신 신호를 수신 중입니다. 관측 데이터 품질과 유량 분석 결과를 아래에서 확인하세요."
            : "현재 데이터를 확인할 수 없습니다. 마지막 값은 정상 품질로 표시하지 않습니다.";
        ObservationTimeText.Text = snapshot.ObservedAtUtc is { } observed && snapshot.Connection?.LastReceivedAtUtc is { } received
            ? $"관측 {observed.ToLocalTime():yyyy-MM-dd HH:mm:ss zzz} · 수신 {received.ToLocalTime():HH:mm:ss}"
            : "수신 전";
        var qualities = snapshot.Signals.Select(signal => signal.Quality).ToArray();
        QualityText.Text = qualities.Length == 0 ? "아직 평가하지 않음" :
            qualities.Contains(ObservationQuality.BAD) ? "BAD · 유효하지 않은 신호" :
            qualities.Contains(ObservationQuality.MISSING) ? "MISSING · 수신 전 / 누락" :
            qualities.Contains(ObservationQuality.STALE) ? "STALE · 최신 데이터 아님" : "GOOD · 관측 데이터 품질";
        if (SignalList.ItemsSource is not IReadOnlyList<SignalReading> previousSignals || !previousSignals.SequenceEqual(snapshot.Signals))
            SignalList.ItemsSource = snapshot.Signals;
        SignalsPanel.Visibility = snapshot.Signals.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
        ConnectionStatsText.Text = snapshot.Connection is { } connection
            ? $"수신 {connection.AcceptedBatches:N0} · 거부 {connection.RejectedBatches:N0} · 누락 {connection.DroppedBatches:N0} · 순번 {connection.LastSequence?.ToString() ?? "—"} · 응답 {connection.PollLatencyMs?.ToString("0.0") ?? "—"} ms" +
              (connection.ReasonCode is null ? "" : $" · {connection.ReasonCode}")
            : "";
        ShowAnalysis(snapshot);
    }

    private void ShowAnalysis(DesktopSnapshot snapshot)
    {
        var diagnostics = snapshot.Diagnostics;
        var enabled = diagnostics?.Enabled == true;
        AnalysisPanel.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
        if (!enabled)
        {
            AnalysisStateText.Text = "NO_MODEL";
            DescriptionText.Text = snapshot.State == ConnectionState.CONNECTED
                ? "최신 신호를 수신 중입니다. 분석이 꺼져 있어 장비 정상 여부는 판단하지 않습니다."
                : DescriptionText.Text;
            NoticeText.Text = "별도 에뮬레이터의 시뮬레이터 데이터입니다. 실제 설비 데이터가 아니며 분석은 꺼져 있습니다 (NO_MODEL).";
            return;
        }

        AnalysisStateText.Text = snapshot.AnalysisEvaluation switch
        {
            ObservationEvaluation.ANOMALOUS => "EWMA · 점검 권고",
            ObservationEvaluation.NORMAL => "EWMA · 기준 이내",
            ObservationEvaluation.INSUFFICIENT_DATA => "EWMA · 준비 중",
            _ => "EWMA · 평가 불가"
        };
        if (snapshot.AnalysisEvaluation == ObservationEvaluation.ANOMALOUS)
        {
            HeadlineText.Text = "유량 점검 권고";
            DescriptionText.Text = "유량 오차가 개발 기준을 지속해서 초과했습니다. 설정값과 실제 유량, 아래 경고를 확인하세요.";
        }
        AnalysisDetailsText.Text = $"{diagnostics!.ModelVersion} · 유효 샘플 {diagnostics.SampleCount}/60 · 유량 오차 EWMA {diagnostics.FlowErrorEwmaLpm?.ToString("0.###") ?? "—"} L/min" +
            (snapshot.AnalysisEvaluation == ObservationEvaluation.UNAVAILABLE ? " · 연결 또는 신호 품질을 확인하세요. 현재 정상 판정은 없습니다." : "");
        var activeWarnings = snapshot.Warnings.Where(warning => warning.State is WarningLifecycle.OPEN or WarningLifecycle.ACKNOWLEDGED).ToArray();
        WarningText.Text = activeWarnings.Length == 0 ? "활성 경고 0개" :
            $"활성 경고 {activeWarnings.Length}개 · {string.Join(", ", activeWarnings.Select(warning => warning.Id))}";
        if (snapshot.State != ConnectionState.CONNECTED) WarningText.Text += " · 마지막 평가 기록";
        NoticeText.Text = "시뮬레이터 데이터를 개발용 EWMA 규칙으로 분석합니다. 학습된 ML 모델이나 실제 설비의 정상 판정이 아닙니다.";
    }

    private void ShowDisconnected(string message)
    {
        ModeBadgeText.Text = "확인 전";
        HeadlineText.Text = "데이터 연결 전";
        DescriptionText.Text = message;
        TransportText.Text = "확인 불가";
        SourceText.Text = "확인 전";
        ObservationTimeText.Text = "최신 수신 확인 불가";
        QualityText.Text = "확인 불가 · 정상 품질 아님";
        AnalysisStateText.Text = "확인 불가";
        AnalysisPanel.Visibility = Visibility.Collapsed;
        SignalList.ItemsSource = null;
        SignalsPanel.Visibility = Visibility.Collapsed;
        NoticeText.Text = message;
    }

    private void DeviceSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_applyingSnapshot) return;
        DeviceIdText.Text = DeviceSelector.SelectedValue as string ?? "선택 전";
        ShowDisconnected("선택한 장비의 최신 신호를 조회하고 있습니다.");
    }

    protected override void OnClosed(EventArgs e)
    {
        _shutdown.Cancel();
        _shutdown.Dispose();
        base.OnClosed(e);
    }

    private void CloseWindow(object sender, RoutedEventArgs e) => Close();

    private void MinimizeWindow(object sender, RoutedEventArgs e) => SystemCommands.MinimizeWindow(this);

    private void ToggleMaximize(object sender, RoutedEventArgs e)
    {
        if (WindowState == WindowState.Maximized)
            SystemCommands.RestoreWindow(this);
        else
            SystemCommands.MaximizeWindow(this);
    }

    private void ShowConnectionGuide(object sender, RoutedEventArgs e) =>
        new ConnectionGuideWindow { Owner = this }.ShowDialog();

    private void WindowSizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (HeroArtwork is not null)
            HeroArtwork.Visibility = ActualWidth < 860 ? Visibility.Collapsed : Visibility.Visible;
    }

    private void WindowStateChanged(object sender, EventArgs e)
    {
        if (MaximizeImage is null)
            return;
        var maximized = WindowState == WindowState.Maximized;
        MaximizeImage.Source = (ImageSource)FindResource(maximized ? "RestoreIcon" : "MaximizeIcon");
        MaximizeButton.ToolTip = maximized ? "이전 크기로" : "최대화";
        AutomationProperties.SetName(MaximizeButton, maximized ? "이전 크기로" : "최대화");
    }
}
