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
    private readonly ToastService? _toasts;
    private readonly DispatcherTimer _refreshTimer = new();

    private readonly Forms.NotifyIcon _icon;

    /// <summary>Shipped glyphs at the current tray size. Reloaded when that size changes.</summary>
    private Drawing.Icon? _glyphIcon;
    private Drawing.Icon? _blockedGlyphIcon;

    /// <summary>The badged icon currently assigned, owned here and replaced as state moves.</summary>
    private Drawing.Icon? _composedIcon;

    /// <summary>
    /// What <see cref="_icon"/> was last painted for. Refresh runs every second and
    /// reassigning NotifyIcon.Icon makes the shell repaint, so the icon is only rebuilt when
    /// one of these actually moved. The count is capped at the highest the badge can spell,
    /// so 12 → 13 agents is not a repaint.
    /// </summary>
    private (TrayBadge Badge, int Count, int Size) _iconState = (TrayBadge.None, -1, 0);

    private readonly Forms.ToolStripMenuItem _statusItem = new() { Enabled = false };
    private readonly Forms.ToolStripMenuItem _relayItem = new() { Enabled = false };
    private readonly Forms.ToolStripMenuItem _errorItem = new() { Enabled = false, Available = false };
    private readonly Forms.ToolStripMenuItem _toastErrorItem = new() { Enabled = false, Available = false };
    private readonly Forms.ToolStripMenuItem _remotesItem = new("Remote Hosts") { Available = false };
    private readonly Forms.ToolStripMenuItem _launchItem = new("Launch at Login");
    private readonly Forms.ToolStripMenuItem _notifyFinishItem = new("Notify When Finished");
    private readonly Forms.ToolStripMenuItem _versionItem = new();

    /// <summary>Host list the submenu was last built from, so it is only rebuilt on change.</summary>
    private string _remotesSignature = string.Empty;

    /// <summary>Pixel size the glyphs were loaded at, i.e. the tray size at that DPI.</summary>
    private int _glyphSize;

    public TrayIconHost(IslandViewModel vm, SettingsStore settings, Updater updater, ToastService? toasts)
    {
        _vm = vm;
        _settings = settings;
        _updater = updater;
        _toasts = toasts;

        LoadGlyphs(Forms.SystemInformation.SmallIconSize.Width);

        _icon = new Forms.NotifyIcon
        {
            Icon = _glyphIcon,
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

    /// <summary>Raised when the user asks to see the island from the menu.</summary>
    public event Action? ShowIslandRequested;

    /// <summary>
    /// Raised by a left click on the icon. Separate from <see cref="ShowIslandRequested"/>
    /// because the icon is the flyout's anchor: clicking it again is how you put the flyout
    /// away, while the menu item only ever opens it.
    /// </summary>
    public event Action? ToggleIslandRequested;

    /// <summary>Raised when the user quits from the menu.</summary>
    public event Action? QuitRequested;

    /// <summary>Raised after the relay URL or token changed, so the caller can reconnect.</summary>
    public event Action? SettingsSaved;

    /// <summary>
    /// Raised once the settings dialog has closed, saved or not, so the island can put away
    /// a preview it only came out for.
    /// </summary>
    public event Action? SettingsClosed;

    /// <summary>
    /// Raised for every appearance edit made in the settings dialog, saved or not, so the
    /// island can preview it — which means showing the flyout, since it is otherwise hidden.
    /// Cancelling the dialog raises one last time with the stored appearance, which puts it
    /// back.
    /// </summary>
    public event Action<IslandAppearance>? AppearancePreviewed;

    /// <summary>
    /// Load both shipped glyphs at the tray's current pixel size, dropping whatever was
    /// loaded for a previous one. The size follows the DPI of the display the taskbar is on,
    /// which can change under a running process — docking a laptop, or moving the taskbar —
    /// and a glyph scaled from the wrong frame is visibly soft.
    /// </summary>
    private void LoadGlyphs(int size)
    {
        if (_glyphSize == size && _glyphIcon is not null) return;
        _glyphSize = size;

        var previousGlyph = _glyphIcon;
        var previousBlocked = _blockedGlyphIcon;

        _glyphIcon = LoadIcon("herdi.ico", size);
        _blockedGlyphIcon = LoadIcon("herdi-blocked.ico", size);

        // Only after the replacements exist: the old pair may still be what the shell is
        // showing until the next ApplyIcon.
        previousGlyph?.Dispose();
        previousBlocked?.Dispose();

        // Force the next ApplyIcon to repaint against the new glyphs.
        _iconState = (TrayBadge.None, -1, 0);
    }

    private static Drawing.Icon? LoadIcon(string fileName, int size)
    {
        try
        {
            // Resource names follow RootNamespace.Folder.File, e.g. Herdi.Assets.herdi.ico.
            var resource = $"Herdi.Assets.{fileName}";
            using var stream = typeof(TrayIconHost).Assembly.GetManifestResourceStream(resource);
            // Pick the frame matching the tray's pixel size rather than the .ico's default
            // 256px frame.
            if (stream is not null) return new Drawing.Icon(stream, size, size);

            // Development fallback: a loose file next to the binary.
            var path = Path.Combine(AppContext.BaseDirectory, "Assets", fileName);
            return File.Exists(path) ? new Drawing.Icon(path, size, size) : null;
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
        menu.Items.Add(_toastErrorItem);
        // Direct mode only: in relay mode the SSH targets are the relay's HERDR_REMOTES and
        // nothing on the wire tells us what they are.
        menu.Items.Add(_remotesItem);
        menu.Items.Add(new Forms.ToolStripSeparator());

        var show = new Forms.ToolStripMenuItem("Show Panel");
        show.Click += (_, _) => ShowIslandRequested?.Invoke();
        menu.Items.Add(show);

        var configure = new Forms.ToolStripMenuItem("Settings…");
        configure.Click += (_, _) => OpenSettings();
        menu.Items.Add(configure);

        menu.Items.Add(new Forms.ToolStripSeparator());

        _notifyFinishItem.CheckOnClick = false;
        _notifyFinishItem.Click += (_, _) =>
        {
            _settings.NotifyOnFinish = !_settings.NotifyOnFinish;
            Refresh();
        };
        menu.Items.Add(_notifyFinishItem);

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
        if (e.Button == Forms.MouseButtons.Left) ToggleIslandRequested?.Invoke();
    }

    private void ToggleLaunchAtLogin()
    {
        var target = !StartupManager.IsEnabled;
        if (StartupManager.SetEnabled(target)) _settings.LaunchAtLogin = target;
        Refresh();
    }

    private void OpenSettings()
    {
        var window = new Views.SettingsWindow(_settings, a => AppearancePreviewed?.Invoke(a));
        window.ShowDialog();
        if (window.Saved) SettingsSaved?.Invoke();
        SettingsClosed?.Invoke();
    }

    private void Refresh()
    {
        _statusItem.Text = _vm.StatusSummary;
        _relayItem.Text = "  " + _vm.SourceSummary;
        _launchItem.Checked = StartupManager.IsEnabled;
        _notifyFinishItem.Checked = _settings.NotifyOnFinish;
        RefreshRemotes();

        // Reconnects back off to 30s, so a silent "Disconnected" can sit there for a long
        // time with the reason known but unsaid. Show it under the relay URL.
        var error = _vm.ConnectionError;
        _errorItem.Text = error is null ? string.Empty : "  ⚠ " + error;
        _errorItem.Available = error is not null;

        // Notification setup fails silently by design — it must not block the app — but a
        // silent failure the user can never see is just a bug with better manners. Surfacing
        // it here is the same treatment ConnectionError above already gets.
        var toastError = _toasts?.Problem;
        _toastErrorItem.Text = toastError is null ? string.Empty : "  ⚠ Notifications: " + toastError;
        _toastErrorItem.Available = toastError is not null;

        _versionItem.Text = _updater.UpdateAvailable
            ? $"Update to v{_updater.LatestVersion}"
            : $"v{_updater.CurrentVersion} ✓";

        ApplyIcon();
        ApplyTooltip();
    }

    /// <summary>
    /// Paint the icon for the current state: a red count while agents are blocked, a green
    /// count while they are working, the bare glyph when neither. This is what the tray icon
    /// is for now that the panel is hidden by default — the icon is the resting state, so it
    /// has to carry enough that a glance is worth taking, and the number is what makes it
    /// worth clicking.
    ///
    /// Supersedes the two-file swap this used to do (herdi.ico ↔ herdi-blocked.ico), which
    /// could say "somebody is waiting" but not how many, and nothing at all about work in
    /// progress. Both files are still shipped and are what a failed compose falls back to.
    /// </summary>
    private void ApplyIcon()
    {
        LoadGlyphs(Forms.SystemInformation.SmallIconSize.Width);

        var blocked = _vm.Blocked.Count;
        var working = _vm.Working.Count;

        // Blocked outranks working: one is a question addressed to the user, the other is
        // progress they can ignore.
        var (badge, count) = blocked > 0
            ? (TrayBadge.Blocked, blocked)
            : working > 0 ? (TrayBadge.Working, working) : (TrayBadge.None, 0);

        var state = (badge, Math.Min(count, TrayIconRenderer.MaxShownCount + 1), _glyphSize);
        if (state == _iconState) return;
        _iconState = state;

        var glyph = _glyphIcon;
        var composed = glyph is null ? null : TrayIconRenderer.Compose(glyph, badge, count, _glyphSize);

        // Composition failed, or there is no glyph to compose onto: fall back to the shipped
        // pair, which still says blocked-or-not even though it cannot count.
        _icon.Icon = composed
            ?? (badge == TrayBadge.Blocked ? _blockedGlyphIcon ?? glyph : glyph);

        // Only once the shell has been handed the replacement.
        _composedIcon?.Dispose();
        _composedIcon = composed;
    }

    /// <summary>
    /// The full breakdown, which is where the numbers the badge cannot fit end up — the
    /// idle count, and any count past 9. NotifyIcon.Text throws above 63 characters.
    /// </summary>
    private void ApplyTooltip()
    {
        string detail;
        if (!_vm.IsConnected)
        {
            detail = "disconnected";
        }
        else
        {
            var parts = new List<string>(3);
            if (_vm.Blocked.Count > 0) parts.Add($"{_vm.Blocked.Count} waiting on you");
            if (_vm.Working.Count > 0) parts.Add($"{_vm.Working.Count} working");
            if (_vm.Idle.Count > 0) parts.Add($"{_vm.Idle.Count} idle");
            detail = parts.Count > 0 ? string.Join(" · ", parts) : "no agents";
        }

        var tooltip = "Herdi — " + detail;
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
        _composedIcon?.Dispose();
        _glyphIcon?.Dispose();
        _blockedGlyphIcon?.Dispose();
    }
}
