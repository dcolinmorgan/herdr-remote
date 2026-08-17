using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;

namespace Herdi.Services;

/// <summary>
/// GitHub Releases self-update. Port of herdi-mac's Updater (Sources/Updater.swift):
/// same repo, same 10-minute check throttle, same "hand off to a script that runs after
/// we exit" trick — a .cmd here instead of a bash script, and a ZIP instead of a DMG.
/// </summary>
public sealed class Updater : INotifyPropertyChanged
{
    private const string Repo = "dcolinmorgan/herdr-remote";
    private static readonly HttpClient Http = CreateClient();

    private DateTime? _lastCheck;
    private string? _downloadUrl;

    public string CurrentVersion { get; } =
        Assembly.GetExecutingAssembly().GetName().Version is { } v
            ? $"{v.Major}.{v.Minor}.{v.Build}"
            : "0.0.0";

    private string? _latestVersion;
    public string? LatestVersion { get => _latestVersion; private set => Set(ref _latestVersion, value); }

    private bool _updateAvailable;
    public bool UpdateAvailable { get => _updateAvailable; private set => Set(ref _updateAvailable, value); }

    private bool _isChecking;
    public bool IsChecking { get => _isChecking; private set => Set(ref _isChecking, value); }

    private bool _isUpdating;
    public bool IsUpdating { get => _isUpdating; private set => Set(ref _isUpdating, value); }

    private string? _status;
    public string? Status { get => _status; private set => Set(ref _status, value); }

    private static HttpClient CreateClient()
    {
        var client = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
        client.DefaultRequestHeaders.UserAgent.ParseAdd("Herdi-Win");
        client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
        return client;
    }

    public async Task CheckForUpdatesAsync(bool force = false)
    {
        if (!force && _lastCheck is { } last && DateTime.UtcNow - last < TimeSpan.FromMinutes(10)) return;
        if (IsChecking) return;

        IsChecking = true;
        Status = "Checking…";
        _lastCheck = DateTime.UtcNow;

        try
        {
            var json = await Http.GetStringAsync($"https://api.github.com/repos/{Repo}/releases/latest");
            HandleRelease(json);
        }
        catch (Exception)
        {
            Status = $"v{CurrentVersion} (check failed)";
        }
        finally
        {
            IsChecking = false;
        }
    }

    private void HandleRelease(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            if (!root.TryGetProperty("tag_name", out var tagEl) || tagEl.GetString() is not { } tag)
            {
                Status = $"v{CurrentVersion}";
                return;
            }

            var version = tag.StartsWith('v') ? tag[1..] : tag;
            string? url = null;
            if (root.TryGetProperty("assets", out var assets) && assets.ValueKind == JsonValueKind.Array)
            {
                foreach (var asset in assets.EnumerateArray())
                {
                    var name = asset.TryGetProperty("name", out var n) ? n.GetString() : null;
                    if (name is null) continue;
                    // Match what build.ps1 publishes, e.g. Herdi-win-x64-0.7.3.zip.
                    if (name.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) &&
                        name.Contains("win", StringComparison.OrdinalIgnoreCase))
                    {
                        url = asset.TryGetProperty("browser_download_url", out var u) ? u.GetString() : null;
                        break;
                    }
                }
            }

            LatestVersion = version;
            _downloadUrl = url;
            UpdateAvailable = version != CurrentVersion && url is not null;
            Status = UpdateAvailable ? $"v{version} available" : $"v{CurrentVersion} ✓";
        }
        catch (JsonException)
        {
            Status = $"v{CurrentVersion}";
        }
    }

    public async Task PerformUpdateAsync()
    {
        if (_downloadUrl is null || IsUpdating) return;
        IsUpdating = true;
        Status = "Downloading…";

        try
        {
            var temp = Path.Combine(Path.GetTempPath(), "herdi-update");
            if (Directory.Exists(temp)) Directory.Delete(temp, recursive: true);
            Directory.CreateDirectory(temp);

            var zipPath = Path.Combine(temp, "herdi.zip");
            await using (var stream = await Http.GetStreamAsync(_downloadUrl))
            await using (var file = File.Create(zipPath))
            {
                await stream.CopyToAsync(file);
            }

            Status = "Installing…";
            var extractDir = Path.Combine(temp, "extracted");
            ZipFile.ExtractToDirectory(zipPath, extractDir, overwriteFiles: true);

            var installDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            var exePath = Environment.ProcessPath;
            if (exePath is null) throw new InvalidOperationException("Cannot resolve the running executable.");

            var scriptPath = Path.Combine(temp, "apply-update.cmd");
            // Waits for this process to exit, swaps the files in, relaunches, self-deletes.
            var script = $"""
                @echo off
                setlocal
                :wait
                tasklist /FI "PID eq {Environment.ProcessId}" 2>nul | find "{Environment.ProcessId}" >nul
                if not errorlevel 1 (
                  timeout /t 1 /nobreak >nul
                  goto wait
                )
                robocopy "{extractDir}" "{installDir}" /E /IS /IT /NFL /NDL /NJH /NJS /NP >nul
                start "" "{exePath}"
                timeout /t 1 /nobreak >nul
                del "%~f0"
                """;
            await File.WriteAllTextAsync(scriptPath, script, Encoding.ASCII);

            Process.Start(new ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = $"/c \"{scriptPath}\"",
                CreateNoWindow = true,
                UseShellExecute = false,
            });

            // Step aside so the script can replace our files.
            System.Windows.Application.Current?.Shutdown();
        }
        catch (Exception ex)
        {
            Status = "Update failed: " + ex.Message;
            IsUpdating = false;
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
