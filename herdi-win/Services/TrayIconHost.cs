using System.ComponentModel;
using System.IO;
using System.Windows.Threading;
using Herdi.ViewModels;
using Drawing = System.Drawing;
using Forms = System.Windows.Forms;

namespace Herdi.Services;

/// <summary>
/// Tray icon and menu. Stands in for herdi-mac's NSStatusItem plus its rebuildMenu /
/// observeBlockedAgents pair (Sources/HerdiMacApp.swift:36).
/// </summary>
public sealed class TrayIconHost : IDisposable
{
    private readonly IslandViewModel _vm;
    private readonly SettingsStore _settings;
    private readonly Updater _updater;
    private readonly DispatcherTimer _refreshTimer = new();

    private readonly Forms.NotifyIcon _icon;
    private readonly Drawing.Icon? _normalIcon;
    private readonly Drawing.Icon? _blockedIcon;
    private bool _showingBlocked;

    private readonly Forms.ToolStripMenuItem _statusItem = new() { Enabled = false };
    private readonly Forms.ToolStripMenuItem _relayItem = new() { Enabled = false };
    private readonly Forms.ToolStripMenuItem _errorItem = new() { Enabled = false, Available = false };
    private readonly Forms.ToolStripMenuItem _remotesItem = new("Remote Hosts") { Available = false };
    private readonly Forms.ToolStripMenuItem _launchItem = new("Launch at Login");
    private readonly Forms.ToolStripMenuItem _versionItem = new();

    /// <summary>Host list the submenu was last built from, so it is only rebuilt on change.</summary>
    private string _remotesSignature = string.Empty;

    public TrayIconHost(IslandViewModel vm, SettingsStore settings, Updater updater)
    {
        _vm = vm;
        _settings = settings;
        _updater = updater;

        _normalIcon = LoadIcon("herdi.ico");
        _blockedIcon = LoadIcon("herdi-blocked.ico");

        _icon = new Forms.NotifyIcon
        {
            Icon = _normalIcon,
            Visible = true,
            Text = "Herdi",
            ContextMenuStrip = BuildMenu(),
        };
        _icon.MouseClick += OnIconClicked;

        // Same 1s beat the macOS app uses to refresh its status item and menu.
        _refreshTimer.Interval = TimeSpan.FromSeconds(1);
        _refreshTimer.Tick += (_, _) => Refresh();
        _refreshTimer.Start();
        Refresh();
    }

    /// <summary>Raised when the user asks to see the island.</summary>
    public event Action? ShowIslandRequested;

    /// <summary>Raised when the user quits from the menu.</summary>
    public event Action? QuitRequested;

    /// <summary>Raised after the relay URL or token changed, so the caller can reconnect.</summary>
    public event Action? SettingsSaved;

    private static Drawing.Icon? LoadIcon(string fileName)
    {
        try
        {
            // Resource names follow RootNamespace.Folder.File, e.g. Herdi.Assets.herdi.ico.
            var resource = $"Herdi.Assets.{fileName}";
            using var stream = typeof(TrayIconHost).Assembly.GetManifestResourceStream(resource);
            if (stream is not null)
            {
                // Pick the frame matching the current DPI's tray size rather than the
                // .ico's default 256px frame.
                var size = Forms.SystemInformation.SmallIconSize;
                return new Drawing.Icon(stream, size.Width, size.Height);
            }

            // Development fallback: a loose file next to the binary.
            var path = Path.Combine(AppContext.BaseDirectory, "Assets", fileName);
            return File.Exists(path) ? new Drawing.Icon(path) : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private Forms.ContextMenuStrip BuildMenu()
    {
        var menu = new Forms.ContextMenuStrip();

        menu.Items.Add(_statusItem);
        menu.Items.Add(_relayItem);
        menu.Items.Add(_errorItem);
        // Direct mode only: in relay mode the SSH targets are the relay's HERDR_REMOTES and
        // nothing on the wire tells us what they are.
        menu.Items.Add(_remotesItem);
        menu.Items.Add(new Forms.ToolStripSeparator());

        var show = new Forms.ToolStripMenuItem("Show Island");
        show.Click += (_, _) => ShowIslandRequested?.Invoke();
        menu.Items.Add(show);

        var configure = new Forms.ToolStripMenuItem("Settings…");
        configure.Click += (_, _) => OpenSettings();
        menu.Items.Add(configure);

        menu.Items.Add(new Forms.ToolStripSeparator());

        _launchItem.CheckOnClick = false;
        _launchItem.Click += (_, _) => ToggleLaunchAtLogin();
        menu.Items.Add(_launchItem);

        menu.Items.Add(new Forms.ToolStripSeparator());

        _versionItem.Click += async (_, _) =>
        {
            if (_updater.UpdateAvailable) await _updater.PerformUpdateAsync();
            else await _updater.CheckForUpdatesAsync(force: true);
        };
        menu.Items.Add(_versionItem);

        menu.Items.Add(new Forms.ToolStripSeparator());

        var quit = new Forms.ToolStripMenuItem("Quit Herdi");
        quit.Click += (_, _) => QuitRequested?.Invoke();
        menu.Items.Add(quit);

        return menu;
    }

    private void OnIconClicked(object? sender, Forms.MouseEventArgs e)
    {
        if (e.Button == Forms.MouseButtons.Left) ShowIslandRequested?.Invoke();
    }

    private void ToggleLaunchAtLogin()
    {
        var target = !StartupManager.IsEnabled;
        if (StartupManager.SetEnabled(target)) _settings.LaunchAtLogin = target;
        Refresh();
    }

    private void OpenSettings()
    {
        var window = new Views.SettingsWindow(_settings);
        window.ShowDialog();
        if (window.Saved) SettingsSaved?.Invoke();
    }

    private void Refresh()
    {
        _statusItem.Text = _vm.StatusSummary;
        _relayItem.Text = "  " + _vm.SourceSummary;
        _launchItem.Checked = StartupManager.IsEnabled;
        RefreshRemotes();

        // Reconnects back off to 30s, so a silent "Disconnected" can sit there for a long
        // time with the reason known but unsaid. Show it under the relay URL.
        var error = _vm.ConnectionError;
        _errorItem.Text = error is null ? string.Empty : "  ⚠ " + error;
        _errorItem.Available = error is not null;

        _versionItem.Text = _updater.UpdateAvailable
            ? $"Update to v{_updater.LatestVersion}"
            : $"v{_updater.CurrentVersion} ✓";

        // Red badge while anything is blocked, mirroring the macOS status item swap
        // to exclamationmark.circle.fill.
        var blocked = _vm.Blocked.Count > 0;
        if (blocked != _showingBlocked)
        {
            _showingBlocked = blocked;
            var next = blocked ? _blockedIcon : _normalIcon;
            if (next is not null) _icon.Icon = next;
        }

        // NotifyIcon.Text throws above 63 characters.
        var tooltip = blocked
            ? $"Herdi — {_vm.Blocked.Count} waiting on you"
            : $"Herdi — {_vm.StatusSummary}";
        _icon.Text = tooltip.Length > 63 ? tooltip[..63] : tooltip;
    }

    /// <summary>
    /// Keep the Remote Hosts submenu in step with the settings — the counterpart of the
    /// inline host list herdi-mac rebuilds into its menu (HerdiMacApp.swift:77). Rebuilt
    /// only when the list actually changed, since Refresh runs every second.
    /// </summary>
    private void RefreshRemotes()
    {
        var direct = _settings.Mode == ConnectionMode.Direct;
        _remotesItem.Available = direct;
        if (!direct) return;

        var remotes = _settings.Remotes;
        var signature = string.Join("\n", remotes);
        if (signature == _remotesSignature && _remotesItem.DropDownItems.Count > 0) return;
        _remotesSignature = signature;

        _remotesItem.DropDownItems.Clear();
        if (remotes.Count == 0)
        {
            _remotesItem.DropDownItems.Add(new Forms.ToolStripMenuItem("None configured") { Enabled = false });
            return;
        }
        foreach (var remote in remotes)
        {
            _remotesItem.DropDownItems.Add(new Forms.ToolStripMenuItem(remote) { Enabled = false });
        }
    }

    public void Dispose()
    {
        _refreshTimer.Stop();
        _icon.Visible = false;
        _icon.Dispose();
        _normalIcon?.Dispose();
        _blockedIcon?.Dispose();
    }
}
