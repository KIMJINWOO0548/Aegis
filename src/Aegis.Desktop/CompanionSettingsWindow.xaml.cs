using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace Aegis.Desktop;

internal partial class CompanionSettingsWindow : Window
{
    private sealed record Item(CompanionPin Pin, string Caption)
    {
        public override string ToString() => Caption;
    }
    private Item[] _catalog = [];
    private Item[] _pinned = [];
    private string _feedback = "";
    private string _connectionStatus = "";

    public CompanionSettingsWindow()
    {
        InitializeComponent();
        Loaded += (_, _) => Search.Focus();
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key != Key.Escape) return;
            e.Handled = true;
            Close();
        };
        UpdateButtons();
    }

    public event Action<CompanionPin[]>? PinsChanged;

    public void UpdateSource(CompanionSnapshot snapshot, CompanionPin[] pins, string connectionStatus)
    {
        Item Describe(CompanionPin pin) => new(pin, snapshot.LabelFor(pin));
        var catalog = snapshot.Catalog.Except(pins).Select(Describe).ToArray();
        var pinned = pins.Select(Describe).ToArray();
        if (!_catalog.SequenceEqual(catalog))
        {
            _catalog = catalog;
            FilterAvailable();
        }
        if (!_pinned.SequenceEqual(pinned))
        {
            var selected = (Pinned.SelectedItem as Item)?.Pin;
            _pinned = pinned;
            Pinned.ItemsSource = _pinned;
            Pinned.SelectedItem = _pinned.FirstOrDefault(item => item.Pin == selected);
        }
        _connectionStatus = connectionStatus;
        UpdateStatus();
        UpdateButtons();
    }

    public void Report(string message)
    {
        _feedback = message;
        UpdateStatus();
    }

    private void UpdateStatus()
    {
        var message = $"{_pinned.Length}개 고정 · {_connectionStatus}";
        if (_feedback.Length > 0) message += $"  · {_feedback}";
        if (Status.Text != message) Status.Text = message;
    }

    private void FilterAvailable()
    {
        var previous = (Available.SelectedItem as Item)?.Pin;
        var query = Search.Text.Trim();
        var matches = _catalog.Where(item => item.Caption.Contains(query, StringComparison.OrdinalIgnoreCase) ||
            item.Pin.SignalKey.Contains(query, StringComparison.OrdinalIgnoreCase)).ToArray();
        Available.ItemsSource = matches;
        Available.SelectedItem = matches.FirstOrDefault(item => item.Pin == previous);
    }

    private void SearchChanged(object sender, TextChangedEventArgs e)
    {
        if (Available is not null) FilterAvailable();
    }

    private void SelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateButtons();

    private void UpdateButtons()
    {
        if (Pinned is null || AddPin is null || RemovePin is null || MoveUp is null || MoveDown is null) return;
        AddPin.IsEnabled = Available.SelectedItem is Item && _pinned.Length < CompanionPreferences.PinLimit;
        RemovePin.IsEnabled = Pinned.SelectedIndex >= 0;
        MoveUp.IsEnabled = Pinned.SelectedIndex > 0;
        MoveDown.IsEnabled = Pinned.SelectedIndex >= 0 && Pinned.SelectedIndex + 1 < _pinned.Length;
    }

    private void AddClicked(object sender, RoutedEventArgs e)
    {
        if (Available.SelectedItem is not Item item || _pinned.Length >= CompanionPreferences.PinLimit) return;
        PinsChanged?.Invoke([.. _pinned.Select(existing => existing.Pin), item.Pin]);
        Pinned.SelectedItem = _pinned.FirstOrDefault(row => row.Pin == item.Pin);
    }

    private void RemoveClicked(object sender, RoutedEventArgs e)
    {
        if (Pinned.SelectedItem is Item item) PinsChanged?.Invoke(_pinned.Select(row => row.Pin).Where(pin => pin != item.Pin).ToArray());
    }

    private void UpClicked(object sender, RoutedEventArgs e) => MovePinned(-1);
    private void DownClicked(object sender, RoutedEventArgs e) => MovePinned(1);

    private void MovePinned(int offset)
    {
        var originalIndex = Pinned.SelectedIndex;
        var targetIndex = originalIndex + offset;
        if (originalIndex < 0 || targetIndex < 0 || targetIndex >= _pinned.Length) return;
        var updated = _pinned.Select(item => item.Pin).ToArray();
        var chosen = updated[originalIndex];
        (updated[originalIndex], updated[targetIndex]) = (updated[targetIndex], updated[originalIndex]);
        PinsChanged?.Invoke(updated);
        Pinned.SelectedItem = _pinned.FirstOrDefault(row => row.Pin == chosen);
    }
}
