using System.Windows;
using System.Windows.Threading;
using Herdi.Models;
using Herdi.Services;
using Herdi.ViewModels;
using Herdi.Views;

namespace Herdi;

/// <summary>
/// Application entry point. Port of herdi-mac's HerdiApp + HerdiAppDelegate
/// (Sources/HerdiMacApp.swift): no main window, a tray icon, and the island panel.
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

        _settings = new SettingsStore();
        _updater = new Updater();
        _relay = new RelayConnection(_settings, action => Dispatcher.Invoke(action));
        _toasts = new ToastService(_settings);
        _vm = new IslandViewModel(_relay, _updater);

        _island = new IslandWindow(_vm);
        _island.Show();

        _tray = new TrayIconHost(_vm, _settings, _updater);
        _tray.ShowIslandRequested += () => _island?.ShowIsland();
        _tray.QuitRequested += Shutdown;
        _tray.SettingsSaved += () => _relay?.Connect();

        // Notifications are optional: if the shortcut or COM registration fails the app
        // still works, it just cannot toast.
        _toasts.Initialize();
        _toasts.ActionInvoked += OnToastAction;

        _relay.AgentBlocked += OnAgentBlocked;
        _relay.AgentUnblocked += _ => _toasts?.ClearBlocked();

        _relay.Connect();

        // Hourly update check, plus one at launch (matching the macOS onAppear check).
        _updateCheckTimer = new DispatcherTimer { Interval = TimeSpan.FromHours(1) };
        _updateCheckTimer.Tick += async (_, _) => await _updater.CheckForUpdatesAsync();
        _updateCheckTimer.Start();
        _ = _updater.CheckForUpdatesAsync();
    }

    private void OnAgentBlocked(Agent agent)
    {
        _toasts?.ShowBlocked(agent);
        _island?.PopForBlocked(agent);
    }

    /// <summary>Apply a button press or inline reply from a toast.</summary>
    private void OnToastAction(ToastAction action)
    {
        if (_relay is null || _vm is null) return;

        if (action.Kind == "open")
        {
            var target = _relay.Find(action.PaneId);
            if (target is not null) _island?.PopForBlocked(target);
            else _island?.ShowIsland();
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
