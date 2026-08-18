using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
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

    // Hover timings from HoverTiming (NotchContentView.swift:10). The collapse delay is
    // the one divergence: macOS trackpads glide, while a mouse at the very top edge of the
    // screen jitters, and every twitch that clips the island's edge would otherwise start
    // shutting it 500 ms later.
    private static readonly TimeSpan ExpandDelay = TimeSpan.FromMilliseconds(450);
    private static readonly TimeSpan CollapseDelay = TimeSpan.FromMilliseconds(800);

    /// <summary>
    /// How often the cursor is sampled. Much faster buys nothing at 60 Hz; much slower and
    /// the island is visibly late to notice the pointer.
    /// </summary>
    private static readonly TimeSpan PointerPollInterval = TimeSpan.FromMilliseconds(120);

    /// <summary>
    /// Opacity of the collapsed island. On macOS the collapsed panel hides inside the
    /// notch, where it covers nothing; here it sits on top of whichever window owns the
    /// top-centre of the screen, so it is toned down to read as an overlay rather than a
    /// hole punched in that window. Hover and expansion take it back to full.
    /// </summary>
    private const double IdleOpacity = 0.75;

    private readonly IslandViewModel _vm;
    private readonly DispatcherTimer _hoverTimer = new();
    private readonly DispatcherTimer _presenceTimer = new();
    private readonly DispatcherTimer _pointerTimer = new();
    private bool _isHovered;
    private bool _prehover;
    private bool _hiddenForFullscreen;
    private bool _menuOpen;
    /// <summary>Whether the surface applied last was one the pointer could not close.</summary>
    private bool _wasSticky;
    /// <summary>Width the last animation aimed at, so a regroup can skip a no-op animation.</summary>
    private double _widthTarget = double.NaN;

    public IslandWindow(IslandViewModel vm)
    {
        _vm = vm;
        InitializeComponent();
        DataContext = vm;

        // Seed the collapsed width so the first frame is already island-shaped rather than
        // shrink-wrapped around the content, and so Width never starts out Auto.
        Root.Width = _widthTarget = TargetWidth();
        Root.Opacity = IdleOpacity;

        _vm.SurfaceChanged += ApplySurface;
        // Agents appearing or changing group resizes the collapsed island and toggles the
        // working indicator, neither of which is tied to a surface transition.
        _vm.GroupingChanged += ApplyGrouping;
        _hoverTimer.Tick += OnHoverTimerTick;

        // Matches the 1s cadence of observeBlockedAgents on macOS.
        _presenceTimer.Interval = TimeSpan.FromSeconds(1);
        _presenceTimer.Tick += (_, _) => UpdateFullscreenVisibility();

        _pointerTimer.Interval = PointerPollInterval;
        _pointerTimer.Tick += OnPointerTick;

        SizeChanged += (_, _) => UpdatePosition();
        Deactivated += OnDeactivated;
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        ApplySurface();
        UpdatePosition();
        _presenceTimer.Start();
        _pointerTimer.Start();
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        var handle = new WindowInteropHelper(this).Handle;
        // WS_EX_TOOLWINDOW keeps the island out of Alt+Tab, the same intent as
        // NSWindow's .ignoresCycle collection behaviour.
        var exStyle = GetWindowLong(handle, GwlExStyle);
        SetWindowLong(handle, GwlExStyle, exStyle | WsExToolWindow);
        ApplyClickThrough();
    }

    // --- Positioning

    /// <summary>Centre horizontally on the primary display, flush with its top edge.</summary>
    private void UpdatePosition()
    {
        var screen = System.Windows.Forms.Screen.PrimaryScreen;
        if (screen is null) return;

        var (scaleX, scaleY) = DeviceScale();

        // Screen.Bounds is in physical pixels; Left/Top are DIPs.
        var bounds = screen.Bounds;
        Left = (bounds.Left + bounds.Width / 2.0) / scaleX - ActualWidth / 2.0;
        Top = bounds.Top / scaleY;
    }

    /// <summary>Physical pixels per DIP, for converting screen coordinates.</summary>
    private (double X, double Y) DeviceScale()
    {
        var transform = PresentationSource.FromVisual(this)?.CompositionTarget?.TransformToDevice;
        return (transform?.M11 is > 0 ? transform.Value.M11 : 1.0,
                transform?.M22 is > 0 ? transform.Value.M22 : 1.0);
    }

    /// <summary>Is the pointer geometrically over the island?</summary>
    private bool PointerOverIsland()
    {
        var (scaleX, scaleY) = DeviceScale();
        var cursor = System.Windows.Forms.Control.MousePosition;
        return new Rect(Left, Top, ActualWidth, ActualHeight)
            .Contains(new Point(cursor.X / scaleX, cursor.Y / scaleY));
    }

    /// <summary>
    /// Let clicks through while the island is collapsed. Collapsed it is signage, pinned
    /// over the top-centre of the screen where tab strips and title bars live, and a
    /// window that swallowed every click landing there would be worse than the notch it
    /// stands in for — a notch is hardware, with nothing underneath it to click. Expanded,
    /// the island is the thing being used, so it takes its input back.
    /// </summary>
    private void ApplyClickThrough()
    {
        var handle = new WindowInteropHelper(this).Handle;
        if (handle == IntPtr.Zero) return;
        var style = GetWindowLong(handle, GwlExStyle);
        var updated = _vm.IsExpanded ? style & ~WsExTransparent : style | WsExTransparent;
        if (updated != style) SetWindowLong(handle, GwlExStyle, updated);
    }

    // --- Hover state machine (handleHover, NotchContentView.swift:140)

    /// <summary>
    /// Hover is decided from where the cursor is, not from MouseEnter/MouseLeave. Two
    /// reasons: the collapsed island is click-through and so receives no mouse messages at
    /// all, and expanding animates the width and re-centres the window, sweeping its edges
    /// past a pointer that never moved — each sweep raising a leave that would start
    /// closing the thing the user is reaching for.
    /// </summary>
    private void OnPointerTick(object? sender, EventArgs e)
    {
        if (_hiddenForFullscreen) return;

        var over = PointerOverIsland();
        if (over == _isHovered) return;
        _isHovered = over;

        // A surface being worked in ignores the pointer, and while a context menu is up
        // the pointer is legitimately off the island.
        if (_vm.IsSticky || _menuOpen) return;

        SetPrehover(over);
        RestartHoverTimer(over ? ExpandDelay : CollapseDelay);
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
        if (_vm.IsSticky || _menuOpen) return;
        if (_isHovered)
        {
            _vm.ShowSessionList();
        }
        else
        {
            // Clear the prehover widening first: it is still set from the way in, and
            // Collapse() sizes the island from it on the way out.
            SetPrehover(false);
            _vm.Collapse();
        }
    }

    /// <summary>Immediate acknowledgement of the pointer before the full expansion.</summary>
    private void SetPrehover(bool on)
    {
        if (_prehover == on) return;
        _prehover = on;
        if (_vm.IsExpanded) return;
        AnimateWidth(TargetWidth(), MicroEase());
        AnimateOpacity(on ? 1.0 : IdleOpacity, MicroEase());
    }

    // A row's context menu opens in its own window, which deactivates this one and puts
    // the pointer outside the island — both of which otherwise mean "collapse".
    private void OnContextMenuOpening(object sender, ContextMenuEventArgs e)
    {
        _menuOpen = true;
        _hoverTimer.Stop();
    }

    private void OnContextMenuClosing(object sender, ContextMenuEventArgs e)
    {
        _menuOpen = false;
        // Give the usual grace period rather than deciding from wherever the pointer
        // happens to be the instant the menu goes away.
        if (!_vm.IsSticky) RestartHoverTimer(CollapseDelay);
    }

    private void OnDeactivated(object? sender, EventArgs e)
    {
        // Clicking outside collapses the island — the role of the global click monitor
        // installed in showPanel() on macOS. An in-flight approval survives, matching
        // the `if case .approval` guard there.
        if (_vm.Surface == IslandSurface.Approval || _menuOpen) return;
        // Focus can also leave while the pointer is still on the island: pressing a button
        // there activates the window, and whatever it was stolen from deactivates in turn.
        // That is not a click elsewhere, so the island stays open.
        if (PointerOverIsland()) return;
        _isHovered = false;
        _hoverTimer.Stop();
        SetPrehover(false);
        _vm.Collapse();
    }

    // --- Surface application + animation

    private void ApplySurface()
    {
        var expanded = _vm.IsExpanded;

        // Backing out of a surface that ignored the pointer, the hover state can be stale:
        // the poll acts on transitions, and one that happened while the island was sticky
        // left no timer behind. Without this the island stays open under a pointer that
        // left minutes ago. Only on the way out of sticky — an island opened from the tray
        // with the pointer nowhere near it is meant to stay up until a click elsewhere.
        if (_wasSticky && !_vm.IsSticky && expanded && !_isHovered && !_menuOpen)
        {
            RestartHoverTimer(CollapseDelay);
        }
        _wasSticky = _vm.IsSticky;

        Divider.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        ExpandedHost.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        Wordmark.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        RightWing.Visibility = expanded ? Visibility.Collapsed : Visibility.Visible;
        ApplyWorkingCount();

        var easing = _vm.Surface == IslandSurface.Approval ? PopEase() : (expanded ? OpenEase() : CloseEase());
        AnimateWidth(TargetWidth(), easing);
        AnimateShape(expanded ? 14 : 3, expanded ? 24 : 12, easing);
        AnimateOpacity(expanded || _isHovered ? 1.0 : IdleOpacity, easing);
        ApplyClickThrough();

        if (_vm.Surface == IslandSurface.Approval)
        {
            // Let the card take keyboard focus so a reply can be typed immediately.
            Activate();
            Dispatcher.BeginInvoke(new Action(() => ApprovalCard.FocusReply()),
                DispatcherPriority.Input);
        }
        else if (_vm.Surface == IslandSurface.Pane)
        {
            Activate();
            Dispatcher.BeginInvoke(new Action(() => Pane.FocusInput()), DispatcherPriority.Input);
        }
    }

    /// <summary>
    /// Re-apply the chrome that follows the agent list rather than the surface. Kept apart
    /// from <see cref="ApplySurface"/> so a poll that merely regroups the agents cannot
    /// replay the open/pop animation or steal focus back to an approval card.
    /// </summary>
    private void ApplyGrouping()
    {
        ApplyWorkingCount();
        var target = TargetWidth();
        if (Math.Abs(target - _widthTarget) < 0.5) return;
        AnimateWidth(target, MicroEase());
    }

    /// <summary>The pulsing working count belongs to the collapsed island only.</summary>
    private void ApplyWorkingCount() =>
        WorkingCount.Visibility = !_vm.IsExpanded && _vm.Working.Count > 0
            ? Visibility.Visible
            : Visibility.Collapsed;

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
        _widthTarget = target;
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

    private void AnimateOpacity(double target, IEasingFunction easing)
    {
        Root.BeginAnimation(OpacityProperty,
            new DoubleAnimation(target, Duration(easing))
            {
                EasingFunction = easing,
                FillBehavior = FillBehavior.HoldEnd,
            });
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
        // Esc backs out of either worked-in surface; the response shortcuts belong to the
        // approval card alone.
        if (!_vm.IsSticky) return;

        if (e.Key == Key.Escape)
        {
            _vm.ShowSessionList();
            e.Handled = true;
            return;
        }

        if (_vm.Surface != IslandSurface.Approval) return;
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
    private const int WsExTransparent = 0x00000020;

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
