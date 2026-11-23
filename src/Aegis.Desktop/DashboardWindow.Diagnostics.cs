using System.IO;
using System.Text.Json;
using Aegis.Contracts;
using Microsoft.Web.WebView2.Core;

namespace Aegis.Desktop;

public partial class DashboardWindow
{
    private bool _diagnosticsPickerOpen;

    private bool IsCurrentDocument(CoreWebView2 core) =>
        !_closed && _navigationId is null && core.Source == _endpoint?.Origin.AbsoluteUri;

    private void ReceiveDiagnosticsMessage(CoreWebView2 core, CoreWebView2WebMessageReceivedEventArgs args)
    {
        if (!IsCurrentDocument(core) || args.Source != _endpoint?.Origin.AbsoluteUri) return;
        var contents = args.WebMessageAsJson;
        if (contents.Length > LocalDiagnosticsReport.MaxJsonLength) return;

        try
        {
            using var document = JsonDocument.Parse(contents, new JsonDocumentOptions { MaxDepth = 8, AllowDuplicateProperties = false });
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || root.EnumerateObject().Count() != 3 ||
                !root.TryGetProperty("type", out var type) || type.ValueKind != JsonValueKind.String ||
                !type.ValueEquals("aegis-save-diagnostics") ||
                !root.TryGetProperty("requestId", out var id) || id.ValueKind != JsonValueKind.String ||
                !Guid.TryParseExact(id.GetString(), "D", out _) ||
                !root.TryGetProperty("report", out var payload) || LocalDiagnosticsReport.Parse(payload) is not { } report)
                return;

            var requestId = id.GetString()!;
            if (_diagnosticsPickerOpen) { ReportSave(core, requestId, "error"); return; }
            _diagnosticsPickerOpen = true;
            // Return from the WebView event before opening a modal Windows dialog.
            Dispatcher.BeginInvoke(() => SaveDiagnostics(core, requestId, report));
        }
        catch (JsonException) { }
    }

    private void SaveDiagnostics(CoreWebView2 core, string requestId, LocalDiagnosticsReport report)
    {
        var result = "cancelled";
        try
        {
            if (!IsCurrentDocument(core)) return;
            var picker = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Aegis 진단 자료를 새 파일로 저장",
                InitialDirectory = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                RestoreDirectory = true,
                FileName = $"Aegis-diagnostics-{DateTime.Now:yyyyMMdd-HHmmss}.json",
                Filter = "JSON (*.json)|*.json",
                DefaultExt = ".json",
                AddExtension = true,
                OverwritePrompt = false,
            };
            if (picker.ShowDialog(this) != true) return;
            var path = Path.GetFullPath(picker.FileName);
            var root = Path.GetPathRoot(path);
            if (!string.Equals(Path.GetExtension(path), ".json", StringComparison.OrdinalIgnoreCase) ||
                root is not { Length: 3 } || root[1] != ':' || path.AsSpan(3).Contains(':') ||
                new DriveInfo(root).DriveType != DriveType.Fixed)
            {
                result = "error";
                return;
            }

            WriteNewDiagnostics(path, report.ToUtf8());
            result = "saved";
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or InvalidOperationException)
        { result = "error"; }
        finally
        {
            _diagnosticsPickerOpen = false;
            ReportSave(core, requestId, result);
        }
    }

    private static void WriteNewDiagnostics(string path, byte[] contents)
    {
        var directory = Path.GetDirectoryName(path)!;
        for (DirectoryInfo? parent = new(directory); parent is not null; parent = parent.Parent)
        {
            if ((parent.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Choose a local directory without a redirect.");
        }

        var stage = Path.Combine(directory, $".aegis-diagnostics-{Guid.NewGuid():N}.tmp");
        var ownsStage = false;
        try
        {
            using (var stream = new FileStream(stage, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                ownsStage = true;
                stream.Write(contents);
                stream.Flush(flushToDisk: true);
            }
            File.Move(stage, path, overwrite: false);
            ownsStage = false;
        }
        finally { if (ownsStage) File.Delete(stage); }
    }

    private void ReportSave(CoreWebView2 core, string requestId, string status)
    {
        if (IsCurrentDocument(core))
            core.PostWebMessageAsJson(JsonSerializer.Serialize(new
            { type = "aegis-diagnostics-save-result", requestId, status }));
    }
}
