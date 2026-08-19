using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using Herdi.Models;
using Herdi.Services;
using Herdi.ViewModels;
using Herdi.Views;

namespace Herdi;

/// <summary>
/// Application entry point. Port of herdi-mac's HerdiApp + HerdiAppDelegate
/// (Sources/HerdiMacApp.swift): no main window, a tray icon, and the island flyout that
/// hangs off it.
/// </summary>
public partial class App : Application
{
    private const string SingleInstanceMutex = @"Local\Herdi.Win.SingleInstance";

    private Mutex? _instanceMutex;
    private SettingsStore? _settings;
    private RelayConnection? _relay;
    private ToastService? _toasts;
    private TrayIconHost? _tray;
    private IslandWindow? _island;
    private IslandViewModel? _vm;
    private Updater? _updater;
    private DispatcherTimer? _updateCheckTimer;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // Toast activation normally lands in the running process via the registered COM
        // class object. If Windows launched a fresh copy instead, hand off and exit.
        _instanceMutex = new Mutex(initiallyOwned: true, SingleInstanceMutex, out var isFirst);
        if (!isFirst)
        {
            Shutdown();
            return;
        }

        DispatcherUnhandledException += OnUnhandledException;

        ApplyRenderMode();

        _settings = new SettingsStore();
        _updater = new Updater();
        _relay = new RelayConnection(_settings, action => Dispatcher.Invoke(action));
        _toasts = new ToastService(_settings);
        _vm = new IslandViewModel(_relay, _updater);

        // The island is not built here. See Island().

        _tray = new TrayIconHost(_vm, _settings, _updater, _toasts);
        _tray.ShowIslandRequested += () => Island().ShowIsland();
        _tray.ToggleIslandRequested += () => Island().ToggleIsland();
        _tray.QuitRequested += Shutdown;
        _tray.SettingsSaved += () =>
        {
            _relay?.Connect();
            // Only if it exists: the live preview has already applied this to a flyout the
            // user actually saw, and a save made without ever opening one has nothing to
            // repaint. Building the window here would undo the whole point of Island().
            _island?.ApplyAppearance(_settings.Appearance);
        };
        // Previewing means showing the flyout, so this one does build it.
        _tray.AppearancePreviewed += appearance => Island().PreviewAppearance(appearance);
        _tray.SettingsClosed += () => _island?.EndAppearancePreview();

        // Notifications are optional: if the shortcut or COM registration fails the app
        // still works, it just cannot toast.
        _toasts.Initialize();
        _toasts.ActionInvoked += OnToastAction;

        _relay.AgentBlocked += OnAgentBlocked;
        _relay.AgentUnblocked += _ => _toasts?.ClearBlocked();
        _relay.AgentFinished += OnAgentFinished;

        _relay.Connect();

        // Hourly update check, plus one at launch (matching the macOS onAppear check).
        _updateCheckTimer = new DispatcherTimer { Interval = TimeSpan.FromHours(1) };
        _updateCheckTimer.Tick += async (_, _) => await _updater.CheckForUpdatesAsync();
        _updateCheckTimer.Start();
        _ = _updater.CheckForUpdatesAsync();
    }

    /// <summary>
    /// Keep WPF off the GPU.
    ///
    /// The flyout sets AllowsTransparency, which makes it a layered window, and WPF has no
    /// hardware path for those - it rasterises them in software and hands the result to
    /// UpdateLayeredWindow either way. What it does anyway, the first time any window is
    /// shown, is stand up its composition engine for the whole process: load the display
    /// driver's user-mode DLL and create a D3D device. Measured, that cost 170 MB - opening
    /// the flyout took the process from 20 MB to 250 MB, and to 80 MB with this switched
    /// off - for a 608 px card that never draws a single frame through it.
    ///
    /// So the accelerated path here is pure cost, and switching it off should be invisible.
    /// Set HERDI_RENDER=hardware to put it back - worth trying if the flyout ever renders
    /// wrongly rather than merely slowly, since that would mean something in the card does
    /// depend on the accelerated rasteriser after all.
    /// </summary>
    private static void ApplyRenderMode()
    {
        var requested = Environment.GetEnvironmentVariable("HERDI_RENDER");
        if (string.Equals(requested, "hardware", StringComparison.OrdinalIgnoreCase)) return;

        RenderOptions.ProcessRenderMode = RenderMode.SoftwareOnly;
    }

    /// <summary>
    /// The flyout, built the first time something actually needs to show it and kept alive
    /// from then on.
    ///
    /// This used to be constructed during startup, with its handle forced up front, so that
    /// the first tray click cost no more than the tenth. That was the wrong trade for a
    /// process that spends its life in the tray: constructing the window parses the XAML,
    /// realises the whole visual tree and spins up WPF's rendering stack, and the app then
    /// held all of it resident for a panel that is hidden by default and on many days never
    /// opened at all.
    ///
    /// What it costs is that the first open now pays for that work. Every later one does
    /// not, because the window is hidden rather than closed.
    /// </summary>
    private IslandWindow Island()
    {
        if (_island is not null) return _island;
        _island = new IslandWindow(_vm!, _settings!);
        return _island;
    }

    private void OnAgentBlocked(Agent agent)
    {
        // Toast first: it is what the user actually sees, and it must not queue behind the
        // flyout's first realisation on the one blocked agent that happens to build it.
        _toasts?.ShowBlocked(agent);
        Island().PopForBlocked(agent);
    }

    /// <summary>
    /// An agent finished. Notify, but do not pop the panel: being told is the point, and a
    /// panel that appeared every time any agent went idle would be worse than no notification
    /// at all. The toast's own click opens it for anyone who wants to look.
    /// </summary>
    private void OnAgentFinished(Agent agent)
    {
        if (_settings?.NotifyOnFinish ?? true) _toasts?.ShowFinished(agent);
    }

    /// <summary>Apply a button press or inline reply from a toast.</summary>
    private void OnToastAction(ToastAction action)
    {
        if (_relay is null || _vm is null) return;

        if (action.Kind == "open")
        {
            // Clicking the toast is the user asking for the flyout, so unlike the automatic
            // pop this one may take focus and stays until dismissed.
            var target = _relay.Find(action.PaneId);
            if (target is not null) Island().ShowApproval(target);
            else Island().ShowIsland();
            return;
        }

        var agent = _relay.Find(action.PaneId);
        if (agent is null || string.IsNullOrWhiteSpace(action.Text)) return;

        // Respond routes allowlisted values through `respond` and free text through
        // `agent_prompt`, so both toast buttons and typed replies land correctly.
        _relay.Respond(agent, action.Text);
        _toasts?.ClearBlocked();
    }

    private void OnUnhandledException(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        // A tray app with no window would otherwise die silently. Inner exceptions are
        // included because WPF's own messages (animation failures especially) push the
        // actual cause down there and say so.
        var detail = e.Exception.Message;
        for (var inner = e.Exception.InnerException; inner is not null; inner = inner.InnerException)
            detail += $"\n\n{inner.GetType().Name}: {inner.Message}";

        MessageBox.Show(
            detail,
            "Herdi — unexpected error",
            MessageBoxButton.OK,
            MessageBoxImage.Error);
        e.Handled = true;
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _updateCheckTimer?.Stop();
        _tray?.Dispose();
        _relay?.Dispose();
        _toasts?.Dispose();
        _instanceMutex?.Dispose();
        base.OnExit(e);
    }
}
