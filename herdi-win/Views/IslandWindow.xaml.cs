using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using Herdi.Models;
using Herdi.ViewModels;

namespace Herdi.Views;

/// <summary>
/// The island itself: a borderless always-on-top capsule pinned to the top edge of the
/// primary display. Port of herdi-mac's PanelWindowController + NotchPanel
/// (Sources/NotchPanel.swift).
///
/// Windows has no notch, so the shape is self-drawn rather than derived from
/// screen.auxiliaryTopLeftArea, and the window is sized to its content so there is
/// almost no invisible area to intercept clicks.
/// </summary>
public partial class IslandWindow : Window
{
    // Collapsed geometry. CapsuleWidth stands in for the physical notch width macOS
    // reports; the wings hold the status indicators either side of it.
    private const double CapsuleWidth = 120;
    private const double Wing = 50;
    private const double BlockedExtra = 20;
    private const double PrehoverExtra = 6;
    private const double ExpandedWidth = 580;

    // Hover timings, verbatim from HoverTiming (NotchContentView.swift:10).
    private static readonly TimeSpan ExpandDelay = TimeSpan.FromMilliseconds(450);
    private static readonly TimeSpan CollapseDelay = TimeSpan.FromMilliseconds(500);

    private readonly IslandViewModel _vm;
    private readonly DispatcherTimer _hoverTimer = new();
    private readonly DispatcherTimer _presenceTimer = new();
    private bool _isHovered;
    private bool _prehover;
    private bool _hiddenForFullscreen;

    public IslandWindow(IslandViewModel vm)
    {
        _vm = vm;
        InitializeComponent();
        DataContext = vm;

        // Seed the collapsed width so the first frame is already island-shaped rather than
        // shrink-wrapped around the content, and so Width never starts out Auto.
        Root.Width = TargetWidth();

        _vm.SurfaceChanged += ApplySurface;
        _hoverTimer.Tick += OnHoverTimerTick;

        // Matches the 1s cadence of observeBlockedAgents on macOS.
        _presenceTimer.Interval = TimeSpan.FromSeconds(1);
        _presenceTimer.Tick += (_, _) => UpdateFullscreenVisibility();

        SizeChanged += (_, _) => UpdatePosition();
        Deactivated += OnDeactivated;
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        ApplySurface();
        UpdatePosition();
        _presenceTimer.Start();
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        var handle = new WindowInteropHelper(this).Handle;
        // WS_EX_TOOLWINDOW keeps the island out of Alt+Tab, the same intent as
        // NSWindow's .ignoresCycle collection behaviour.
        var exStyle = GetWindowLong(handle, GwlExStyle);
        SetWindowLong(handle, GwlExStyle, exStyle | WsExToolWindow);
    }

    // --- Positioning

    /// <summary>Centre horizontally on the primary display, flush with its top edge.</summary>
    private void UpdatePosition()
    {
        var screen = System.Windows.Forms.Screen.PrimaryScreen;
        if (screen is null) return;

        var source = PresentationSource.FromVisual(this);
        var transform = source?.CompositionTarget?.TransformToDevice;
        var scaleX = transform?.M11 is > 0 ? transform.Value.M11 : 1.0;
        var scaleY = transform?.M22 is > 0 ? transform.Value.M22 : 1.0;

        // Screen.Bounds is in physical pixels; Left/Top are DIPs.
        var bounds = screen.Bounds;
        Left = (bounds.Left + bounds.Width / 2.0) / scaleX - ActualWidth / 2.0;
        Top = bounds.Top / scaleY;
    }

    // --- Hover state machine (handleHover, NotchContentView.swift:140)

    private void OnRootMouseEnter(object sender, MouseEventArgs e)
    {
        // Never collapse or re-trigger while an approval is being answered.
        if (_vm.Surface == IslandSurface.Approval) return;

        _isHovered = true;
        SetPrehover(true);
        RestartHoverTimer(ExpandDelay);
    }

    private void OnRootMouseLeave(object sender, MouseEventArgs e)
    {
        if (_vm.Surface == IslandSurface.Approval) return;

        _isHovered = false;
        SetPrehover(false);
        RestartHoverTimer(CollapseDelay);
    }

    private void RestartHoverTimer(TimeSpan delay)
    {
        _hoverTimer.Stop();
        _hoverTimer.Interval = delay;
        _hoverTimer.Start();
    }

    private void OnHoverTimerTick(object? sender, EventArgs e)
    {
        _hoverTimer.Stop();
        if (_vm.Surface == IslandSurface.Approval) return;

        if (_isHovered) _vm.ShowSessionList();
        else _vm.Collapse();
    }

    /// <summary>Immediate acknowledgement of the pointer before the full expansion.</summary>
    private void SetPrehover(bool on)
    {
        if (_prehover == on) return;
        _prehover = on;
        if (!_vm.IsExpanded) AnimateWidth(TargetWidth(), MicroEase());
    }

    private void OnDeactivated(object? sender, EventArgs e)
    {
        // Clicking outside collapses the island — the role of the global click monitor
        // installed in showPanel() on macOS. An in-flight approval survives, matching
        // the `if case .approval` guard there.
        if (_vm.Surface == IslandSurface.Approval) return;
        _isHovered = false;
        _hoverTimer.Stop();
        _vm.Collapse();
    }

    // --- Surface application + animation

    private void ApplySurface()
    {
        var expanded = _vm.IsExpanded;

        Divider.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        ExpandedHost.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        Wordmark.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        RightWing.Visibility = expanded ? Visibility.Collapsed : Visibility.Visible;
        WorkingCount.Visibility = !expanded && _vm.Working.Count > 0
            ? Visibility.Visible
            : Visibility.Collapsed;

        var easing = _vm.Surface == IslandSurface.Approval ? PopEase() : (expanded ? OpenEase() : CloseEase());
        AnimateWidth(TargetWidth(), easing);
        AnimateShape(expanded ? 14 : 3, expanded ? 24 : 12, easing);

        if (_vm.Surface == IslandSurface.Approval)
        {
            // Let the card take keyboard focus so a reply can be typed immediately.
            Activate();
            Dispatcher.BeginInvoke(new Action(() => ApprovalCard.FocusReply()),
                DispatcherPriority.Input);
        }
    }

    private double TargetWidth()
    {
        if (!_vm.IsActive) return CapsuleWidth + 60;
        if (_vm.IsExpanded) return ExpandedWidth;
        var width = CapsuleWidth + Wing * 2;
        if (_vm.Blocked.Count > 0) width += BlockedExtra;
        if (_prehover) width += PrehoverExtra;
        return width;
    }

    private void AnimateWidth(double target, IEasingFunction easing)
    {
        // From is always explicit: a To-only DoubleAnimation reads the base value, and WPF
        // rejects NaN there ("'NaN' is not a valid 'Double' value for class ..."), which is
        // what Width reads as whenever it is Auto. Root.Width sees the in-flight animated
        // value, so retargeting mid-animation stays continuous.
        var from = Root.Width;
        if (double.IsNaN(from) || double.IsInfinity(from))
            from = Root.ActualWidth > 0 ? Root.ActualWidth : target;

        var animation = new DoubleAnimation(from, target, Duration(easing))
        {
            EasingFunction = easing,
            FillBehavior = FillBehavior.HoldEnd,
        };
        Root.BeginAnimation(WidthProperty, animation);
    }

    private void AnimateShape(double extension, double radius, IEasingFunction easing)
    {
        var duration = Duration(easing);
        Silhouette.BeginAnimation(Controls.IslandShape.TopExtensionProperty,
            new DoubleAnimation(extension, duration) { EasingFunction = easing });
        Silhouette.BeginAnimation(Controls.IslandShape.BottomRadiusProperty,
            new DoubleAnimation(radius, duration) { EasingFunction = easing });
    }

    // WPF has no spring primitive, so each macOS spring maps to the easing curve with
    // the closest feel: overshoot for open/pop, none for close.
    private static IEasingFunction OpenEase() => new BackEase { Amplitude = 0.18, EasingMode = EasingMode.EaseOut };
    private static IEasingFunction CloseEase() => new CubicEase { EasingMode = EasingMode.EaseOut };
    private static IEasingFunction PopEase() => new BackEase { Amplitude = 0.35, EasingMode = EasingMode.EaseOut };
    private static IEasingFunction MicroEase() => new QuadraticEase { EasingMode = EasingMode.EaseOut };

    private static System.Windows.Duration Duration(IEasingFunction easing) => easing switch
    {
        BackEase { Amplitude: > 0.3 } => new System.Windows.Duration(TimeSpan.FromMilliseconds(300)),
        BackEase => new System.Windows.Duration(TimeSpan.FromMilliseconds(420)),
        QuadraticEase => new System.Windows.Duration(TimeSpan.FromMilliseconds(120)),
        _ => new System.Windows.Duration(TimeSpan.FromMilliseconds(380)),
    };

    /// <summary>
    /// The update badge expands to the session list, where the banner lives — the
    /// onShowUpdate closure CompactBar is handed on macOS.
    /// </summary>
    private void OnShowUpdate(object sender, RoutedEventArgs e) => _vm.ShowSessionList();

    // --- Public entry points

    /// <summary>Open the session list, e.g. from the tray menu.</summary>
    public void ShowIsland()
    {
        Show();
        _vm.ShowSessionList();
        Activate();
    }

    /// <summary>
    /// Auto-open the approval card for a newly blocked agent, then nudge the island —
    /// the equivalent of the NotchAnimation.pop bounce on macOS.
    /// </summary>
    public void PopForBlocked(Agent agent)
    {
        if (_hiddenForFullscreen) return;
        Show();
        _vm.PopApproval(agent);
    }

    // --- Fullscreen / do-not-disturb

    /// <summary>
    /// Hide while another app is fullscreen or presenting, as isActiveSpaceFullscreen
    /// does on macOS. SHQueryUserNotificationState is the idiomatic Windows probe and
    /// also respects presentation mode.
    /// </summary>
    private void UpdateFullscreenVisibility()
    {
        var quiet = SHQueryUserNotificationState(out var state) == 0 && state is
            QunsBusy or QunsRunningD3DFullScreen or QunsPresentationMode;

        if (quiet && !_hiddenForFullscreen)
        {
            _hiddenForFullscreen = true;
            Hide();
        }
        else if (!quiet && _hiddenForFullscreen)
        {
            _hiddenForFullscreen = false;
            Show();
            UpdatePosition();
        }
    }

    // --- Keyboard shortcuts

    protected override void OnPreviewKeyDown(KeyEventArgs e)
    {
        base.OnPreviewKeyDown(e);
        if (_vm.Surface != IslandSurface.Approval) return;

        if (e.Key == Key.Escape)
        {
            _vm.ShowSessionList();
            e.Handled = true;
            return;
        }

        if ((Keyboard.Modifiers & ModifierKeys.Control) == 0) return;

        // Ctrl-based equivalents of the ⌘ shortcuts ResponseAction advertises.
        var label = e.Key switch
        {
            Key.Y => "Allow",
            Key.T => "Trust",
            Key.N => "Deny",
            Key.A => "Approve All",
            Key.E => "Edit",
            Key.R => "Retry",
            _ => null,
        };
        if (label is null) return;

        var action = _vm.ResponseButtons.FirstOrDefault(b => b.Label == label);
        if (action is null) return;
        _vm.RespondCommand.Execute(action.RawValue);
        e.Handled = true;
    }

    // --- Win32

    private const int GwlExStyle = -20;
    private const int WsExToolWindow = 0x00000080;

    private const int QunsBusy = 2;
    private const int QunsRunningD3DFullScreen = 3;
    private const int QunsPresentationMode = 4;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern int GetWindowLong(IntPtr hWnd, int index);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern int SetWindowLong(IntPtr hWnd, int index, int newLong);

    [DllImport("shell32.dll")]
    private static extern int SHQueryUserNotificationState(out int state);
}
