using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using Herdi.Models;
using Herdi.Services;
using Herdi.ViewModels;

namespace Herdi.Views;

/// <summary>
/// The island: a tray flyout. Hidden until the tray icon is clicked, then anchored to the
/// notification-area corner of the taskbar, dismissed by clicking away — the shape Windows
/// itself uses for the network, volume and battery panels, and that Teams, Slack and
/// OneDrive use for theirs.
///
/// Port of herdi-mac's PanelWindowController + NotchPanel (Sources/NotchPanel.swift), with
/// its one structural divergence: macOS anchors that panel to the notch, which is hardware
/// with nothing underneath it, so the panel can live at the top edge permanently and open
/// on hover. Windows has no notch. A capsule pinned to the top edge sits over whatever
/// window owns that strip — tab strips, title bars, the ribbon — and hover is the wrong
/// trigger for something the pointer crosses on its way elsewhere. The tray icon is the
/// platform's own always-visible status surface and it is already carrying the state
/// (red while blocked, counts in the tooltip), so it takes the collapsed island's job and
/// the panel becomes a flyout that hangs off it.
/// </summary>
public partial class IslandWindow : Window
{
    /// <summary>
    /// Transparent ring around the card for the drop shadow to fall into. Applied to Card's
    /// margin from here rather than in XAML so the positioning maths cannot drift from it.
    /// </summary>
    private const double ShadowMargin = 14;

    /// <summary>Clearance between the card and both the taskbar and the screen edges.</summary>
    private const double Gap = 8;

    /// <summary>How far the card travels on its way in, along the taskbar's axis.</summary>
    private const double SlideDistance = 24;

    /// <summary>
    /// How long a flyout that appeared on its own stays up. Only the automatic path arms
    /// this: a flyout the user opened is dismissed by clicking away, and one they are
    /// reading or typing into has focus, which disarms it.
    /// </summary>
    private static readonly TimeSpan AutoHideDelay = TimeSpan.FromSeconds(12);

    /// <summary>
    /// How long after a dismissal a tray click is still treated as part of that dismissal.
    /// Clicking the icon while the flyout is up deactivates it first — the flyout hides
    /// itself — and the click then arrives at the tray, where reopening would make the icon
    /// look dead. Anything inside this window is swallowed instead.
    /// </summary>
    private static readonly TimeSpan ReopenGuard = TimeSpan.FromMilliseconds(400);

    private readonly IslandViewModel _vm;
    private readonly DispatcherTimer _autoHide = new();

    /// <summary>Set while the row context menu is up, which deactivates this window.</summary>
    private bool _menuOpen;

    /// <summary>Set while the fade-out is running, and cleared by anything that re-shows.</summary>
    private bool _closing;

    /// <summary>
    /// Whether this flyout may take the keyboard. False for the automatic pop: an agent
    /// going blocked must not pull focus out of whatever the user is typing in — that is
    /// what the toast is for, and it carries the same buttons and reply box.
    /// </summary>
    private bool _allowFocus;

    /// <summary>
    /// Set while the flyout is up on its own initiative rather than the user's, which is the
    /// only state <see cref="_autoHide"/> runs in. Tracked separately from
    /// <see cref="_allowFocus"/> rather than inferred from it, because whether WPF honours
    /// ShowActivated on a re-show is not something to hang a timeout on: an announcement
    /// that ends up with focus anyway must still take itself away, and only a click, a
    /// keystroke or the pointer settling on it counts as somebody arriving.
    /// </summary>
    private bool _announcing;

    /// <summary>Set while the flyout is only up so the settings dialog can preview it.</summary>
    private bool _previewing;

    /// <summary>When the flyout last finished hiding, for <see cref="ReopenGuard"/>.</summary>
    private DateTime _hiddenAt = DateTime.MinValue;

    /// <summary>Edge the last position was computed against, which sets the slide axis.</summary>
    private TaskbarEdge _edge = TaskbarEdge.Bottom;

    private IslandAppearance _appearance;

    public IslandWindow(IslandViewModel vm, SettingsStore settings)
    {
        _vm = vm;
        _appearance = settings.Appearance;
        InitializeComponent();
        DataContext = vm;

        Card.Margin = new Thickness(ShadowMargin);
        // Transparent until the first AnimateIn fades it up. Every later show inherits zero
        // from the hide fade, which holds its end value; this is only the seed for the first
        // one, and without it the card's first frame would flash at full opacity before it
        // has been positioned.
        Root.Opacity = 0;
        ApplyAppearance(_appearance);

        _vm.SurfaceChanged += ApplySurface;

        _autoHide.Interval = AutoHideDelay;
        _autoHide.Tick += OnAutoHideTick;

        // Switching surfaces changes the card's height, which moves where its anchored
        // corner has to be.
        SizeChanged += (_, _) => UpdatePosition();
        Card.MouseEnter += (_, _) => _autoHide.Stop();
        Card.MouseLeave += (_, _) => RestartAutoHide();
        Card.PreviewMouseDown += (_, _) => NoteInteraction();
        Activated += OnActivated;
        Deactivated += OnDeactivated;
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        var handle = new WindowInteropHelper(this).Handle;
        // WS_EX_TOOLWINDOW keeps the flyout out of Alt+Tab, the same intent as NSWindow's
        // .ignoresCycle collection behaviour.
        var exStyle = GetWindowLong(handle, GwlExStyle);
        SetWindowLong(handle, GwlExStyle, exStyle | WsExToolWindow);
    }

    // --- Public entry points

    /// <summary>
    /// Left-clicking the tray icon. Toggles, which is what every tray flyout does — and
    /// since the click that opened it also deactivated the flyout, a click arriving right
    /// after a dismissal is that dismissal and is dropped rather than reopening.
    /// </summary>
    public void ToggleIsland()
    {
        // Two orderings to swallow, depending on whether the fade-out has finished by the
        // time the tray click lands: one is still running, or it just ended.
        if (_closing) return;
        if (IsVisible)
        {
            Dismiss();
            return;
        }
        if (DateTime.UtcNow - _hiddenAt < ReopenGuard) return;
        ShowIsland();
    }

    /// <summary>Open the session list, e.g. from the tray menu or a toast.</summary>
    public void ShowIsland()
    {
        _vm.ShowSessionList();
        ShowFlyout(takeFocus: true);
    }

    /// <summary>
    /// Open one agent's approval card, from the toast's "Open" action. User-initiated, so
    /// unlike <see cref="PopForBlocked"/> it may take the keyboard.
    /// </summary>
    public void ShowApproval(Agent agent)
    {
        _vm.ShowApproval(agent);
        ShowFlyout(takeFocus: true);
    }

    /// <summary>
    /// A newly blocked agent, announcing itself. Shows without taking focus and takes
    /// itself away again if nobody comes — the counterpart of herdi-mac's
    /// observeBlockedAgents auto-pop (Sources/HerdiMacApp.swift:180), which can be
    /// unconditional there because a notch panel covers nothing when it opens.
    ///
    /// Suppressed while a game or a presentation is on, matching where Windows itself
    /// holds toasts back.
    /// </summary>
    public void PopForBlocked(Agent agent)
    {
        if (IsQuietHours()) return;
        // Something already open is being worked in; do not yank it out from under anyone.
        // A card mid-dismissal does not count as open — its surface has not been reset yet,
        // but nobody is working in something they just closed.
        if (IsVisible && !_closing && _vm.IsSticky) return;

        _announcing = true;
        _vm.ShowApproval(agent);
        ShowFlyout(takeFocus: false);
        _autoHide.Start();
    }

    /// <summary>Dismiss, as clicking away would.</summary>
    public void Dismiss() => HideFlyout();

    /// <summary>
    /// Repaint from the settings dialog while it is open. Opacity is applied through the
    /// animation path rather than the property because the show/hide fade owns Root.Opacity
    /// with FillBehavior.HoldEnd, and a plain assignment underneath it would never be seen.
    /// </summary>
    public void ApplyAppearance(IslandAppearance appearance)
    {
        _appearance = appearance.Normalized();
        var brush = new SolidColorBrush(_appearance.Fill);
        brush.Freeze();
        Card.Background = brush;
        if (IsVisible) AnimateOpacity(Root.Opacity, _appearance.Opacity, MicroEase(), MicroDuration);
    }

    /// <summary>
    /// The same, but showing the flyout first if it is hidden: transparency cannot be judged
    /// from a swatch in a dialog, it depends entirely on what is behind the card, so the
    /// sliders drive the real thing.
    /// </summary>
    public void PreviewAppearance(IslandAppearance appearance)
    {
        // A card fading out counts as hidden: showing it again is what cancels that fade,
        // whereas repainting one on its way out just leaves it to finish leaving.
        if (!IsVisible || _closing)
        {
            _previewing = true;
            _vm.ShowSessionList();
            ShowFlyout(takeFocus: false);
        }
        ApplyAppearance(appearance);
    }

    /// <summary>Put the flyout away again if it was only up for the preview.</summary>
    public void EndAppearancePreview()
    {
        if (!_previewing) return;
        _previewing = false;
        if (!IsActive) HideFlyout();
    }

    // --- Show / hide

    private void ShowFlyout(bool takeFocus)
    {
        // A fade-out already running has to be animated back out of, not just cancelled: its
        // Completed handler sees _closing cleared and bows out, leaving Root.Opacity held
        // wherever the fade had got to — a card that is visible and yet cannot be seen.
        var interrupting = _closing;
        _closing = false;
        _autoHide.Stop();
        if (takeFocus)
        {
            _allowFocus = true;
            _announcing = false;
        }

        var appearing = !IsVisible;
        if (appearing)
        {
            Show();
            // Where the card goes depends on how tall it ended up, and Show() does not
            // promise a finished layout pass — so force one rather than position against a
            // stale size and correct it a frame later.
            UpdateLayout();
        }
        UpdatePosition();

        if (takeFocus) Activate();
        if (appearing || interrupting) AnimateIn(appearing);
        FocusActiveSurface();
    }

    private void HideFlyout()
    {
        if (!IsVisible)
        {
            _vm.Collapse();
            return;
        }
        if (_closing) return;

        _closing = true;
        _autoHide.Stop();
        _previewing = false;
        _allowFocus = false;
        _announcing = false;

        var fade = new DoubleAnimation(Root.Opacity, 0, new Duration(CloseDuration))
        {
            EasingFunction = MicroEase(),
            FillBehavior = FillBehavior.HoldEnd,
        };
        fade.Completed += (_, _) =>
        {
            // A show that landed while the fade was running already cleared _closing and
            // owns Root.Opacity; this completion is stale.
            if (!_closing) return;
            _closing = false;
            Hide();
            _hiddenAt = DateTime.UtcNow;
            _vm.Collapse();
        };
        Root.BeginAnimation(OpacityProperty, fade);
        AnimateSlide(new Vector(Slide.X, Slide.Y), SlideOffset(), MicroEase(), CloseDuration);
    }

    /// <summary>
    /// Slide in from the taskbar's side while fading up to the chosen opacity. A card that
    /// was already on screen — one whose fade-out was interrupted — comes back from wherever
    /// that fade had reached rather than snapping out to the edge first.
    /// </summary>
    private void AnimateIn(bool appearing)
    {
        var from = appearing ? SlideOffset() : new Vector(Slide.X, Slide.Y);
        var fromOpacity = appearing ? 0 : Root.Opacity;
        AnimateSlide(from, new Vector(0, 0), OpenEase(), OpenDuration);
        AnimateOpacity(fromOpacity, _appearance.Opacity, OpenEase(), OpenDuration);
    }

    // Both animations carry an explicit From. The previous one holds its end value with
    // FillBehavior.HoldEnd, so assigning Slide.X or Root.Opacity first would set a base
    // value nothing ever reads — the From has to be stated, not staged.
    private void AnimateSlide(Vector from, Vector to, IEasingFunction easing, TimeSpan duration)
    {
        var span = new Duration(duration);
        Slide.BeginAnimation(TranslateTransform.XProperty,
            new DoubleAnimation(from.X, to.X, span) { EasingFunction = easing, FillBehavior = FillBehavior.HoldEnd });
        Slide.BeginAnimation(TranslateTransform.YProperty,
            new DoubleAnimation(from.Y, to.Y, span) { EasingFunction = easing, FillBehavior = FillBehavior.HoldEnd });
    }

    private void AnimateOpacity(double from, double to, IEasingFunction easing, TimeSpan duration)
    {
        Root.BeginAnimation(OpacityProperty,
            new DoubleAnimation(from, to, new Duration(duration))
            {
                EasingFunction = easing,
                FillBehavior = FillBehavior.HoldEnd,
            });
    }

    /// <summary>Where the card starts from and returns to: outward, along the bar's normal.</summary>
    private Vector SlideOffset() => _edge switch
    {
        TaskbarEdge.Bottom => new Vector(0, SlideDistance),
        TaskbarEdge.Top => new Vector(0, -SlideDistance),
        TaskbarEdge.Left => new Vector(-SlideDistance, 0),
        _ => new Vector(SlideDistance, 0),
    };

    // --- Positioning

    /// <summary>
    /// Anchor the card to the taskbar corner the notification area lives in, then clamp it
    /// into the work area so a tall card cannot run off the screen. Everything the shell
    /// reports is in physical pixels; Left and Top are DIPs.
    ///
    /// The conversion uses this window's own scale factor, which is exact as long as the
    /// taskbar carrying the tray is on a display at the same DPI as the one the window is
    /// currently on — the same assumption the top-edge version made. Windows only shows the
    /// notification area on one taskbar, so the two agree except in the moment after the
    /// flyout has been dragged across a DPI boundary by a display change, where the next
    /// reposition settles it.
    /// </summary>
    private void UpdatePosition()
    {
        var bar = TaskbarInfo.Current();
        if (bar.WorkArea.Width <= 0 || bar.WorkArea.Height <= 0) return;
        _edge = bar.Edge;

        var (scaleX, scaleY) = DeviceScale();

        // The visible card, not the window: the window is inflated by ShadowMargin all
        // round, and it is the card that should sit Gap away from the bar.
        var cardWidth = Math.Max(0, ActualWidth - ShadowMargin * 2);
        var cardHeight = Math.Max(0, ActualHeight - ShadowMargin * 2);

        var corner = bar.TrayCorner;
        var cornerX = corner.X / scaleX;
        var cornerY = corner.Y / scaleY;

        double cardLeft, cardTop;
        switch (bar.Edge)
        {
            case TaskbarEdge.Top:
                // Trailing end of the bar, hanging below it.
                cardLeft = cornerX - Gap - cardWidth;
                cardTop = cornerY + Gap;
                break;
            case TaskbarEdge.Left:
                cardLeft = cornerX + Gap;
                cardTop = cornerY - Gap - cardHeight;
                break;
            default: // Bottom, the usual case, and Right — both hang up and left of the corner.
                cardLeft = cornerX - Gap - cardWidth;
                cardTop = cornerY - Gap - cardHeight;
                break;
        }

        var work = bar.WorkArea;
        cardLeft = ClampInto(cardLeft, work.Left / scaleX + Gap, work.Right / scaleX - Gap - cardWidth);
        cardTop = ClampInto(cardTop, work.Top / scaleY + Gap, work.Bottom / scaleY - Gap - cardHeight);

        Left = cardLeft - ShadowMargin;
        Top = cardTop - ShadowMargin;
    }

    /// <summary>
    /// Clamp that survives an upper bound below the lower one, which is what a card taller
    /// than the work area produces. The lower bound wins there, so the card's top-left stays
    /// on screen and it overflows off the far side rather than the near one.
    /// </summary>
    private static double ClampInto(double value, double min, double max) =>
        max < min ? min : Math.Clamp(value, min, max);

    /// <summary>Physical pixels per DIP, for converting screen coordinates.</summary>
    private (double X, double Y) DeviceScale()
    {
        var transform = PresentationSource.FromVisual(this)?.CompositionTarget?.TransformToDevice;
        return (transform?.M11 is > 0 ? transform.Value.M11 : 1.0,
                transform?.M22 is > 0 ? transform.Value.M22 : 1.0);
    }

    // --- Dismissal

    /// <summary>
    /// Focus arriving. Deliberately does not clear <see cref="_announcing"/>: a flyout that
    /// only announced itself is not being used just because it ended up with the keyboard.
    /// </summary>
    private void OnActivated(object? sender, EventArgs e) => _allowFocus = true;

    /// <summary>Somebody is actually using it, so the timeout has no business firing.</summary>
    private void NoteInteraction()
    {
        _allowFocus = true;
        _announcing = false;
        _autoHide.Stop();
    }

    private void OnDeactivated(object? sender, EventArgs e)
    {
        // Clicking away dismisses — the role of the global click monitor installed in
        // showPanel() on macOS. Unlike that one this does not exempt an open approval card:
        // a topmost, borderless, taskbar-button-less window that refuses to go away when it
        // loses focus is a window with no way out, and the toast carries the same approval.
        if (_menuOpen || _previewing) return;
        HideFlyout();
    }

    /// <summary>The flyout announced itself and nobody came.</summary>
    private void OnAutoHideTick(object? sender, EventArgs e)
    {
        _autoHide.Stop();
        if (!_announcing || Card.IsMouseOver) return;
        HideFlyout();
    }

    /// <summary>Re-arm after the pointer leaves, for an announcement nobody acted on.</summary>
    private void RestartAutoHide()
    {
        _autoHide.Stop();
        if (_announcing && IsVisible) _autoHide.Start();
    }

    // A row's context menu opens in its own window, which deactivates this one.
    private void OnContextMenuOpening(object sender, ContextMenuEventArgs e)
    {
        _menuOpen = true;
        _autoHide.Stop();
    }

    /// <summary>
    /// The menu is going away. It had already cost this window its activation, and no second
    /// Deactivated is coming — so without taking focus back the flyout would sit there
    /// unfocused, deaf to the click-away that is supposed to dismiss it. Posted rather than
    /// called inline because the menu's own window is still up at this point.
    /// </summary>
    private void OnContextMenuClosing(object sender, ContextMenuEventArgs e)
    {
        _menuOpen = false;
        Dispatcher.BeginInvoke(new Action(() =>
        {
            if (!_menuOpen && IsVisible && !_closing && !IsActive) Activate();
        }), DispatcherPriority.Input);
    }

    private void OnCloseClicked(object sender, RoutedEventArgs e) => HideFlyout();

    /// <summary>
    /// The update badge opens the session list, where the banner lives — the onShowUpdate
    /// closure CompactBar is handed on macOS.
    /// </summary>
    private void OnShowUpdate(object sender, RoutedEventArgs e) => _vm.ShowSessionList();

    // --- Surface application

    private void ApplySurface()
    {
        // Every surface's own visibility is bound to the view model, so there is nothing to
        // lay out here, and the height change that follows re-anchors the card through
        // SizeChanged. Only focus is left to place.
        if (!_vm.IsExpanded) return;
        FocusActiveSurface();
    }

    /// <summary>
    /// Put the caret in whatever the current surface is for typing into, so an approval can
    /// be answered or an agent messaged without reaching for the mouse. Skipped entirely
    /// while <see cref="_allowFocus"/> is false — a flyout that appeared on its own has not
    /// been given the keyboard and must not grab it.
    /// </summary>
    private void FocusActiveSurface()
    {
        if (!_allowFocus) return;
        var surface = _vm.Surface;
        if (surface is not (IslandSurface.Approval or IslandSurface.Pane)) return;

        Dispatcher.BeginInvoke(new Action(() =>
        {
            if (!_allowFocus || _vm.Surface != surface) return;
            if (surface == IslandSurface.Approval) ApprovalCard.FocusReply();
            else Pane.FocusInput();
        }), DispatcherPriority.Input);
    }

    // --- Keyboard

    protected override void OnPreviewKeyDown(KeyEventArgs e)
    {
        base.OnPreviewKeyDown(e);
        NoteInteraction();

        if (e.Key == Key.Escape)
        {
            // Esc backs out of a surface being worked in, and closes the flyout from the
            // session list — the way out every Windows flyout has.
            if (_vm.IsSticky) _vm.ShowSessionList();
            else HideFlyout();
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

    // --- Animation presets

    // WPF has no spring primitive, so each macOS spring maps to the easing curve with the
    // closest feel. A flyout's entrance is quicker than the notch panel's expansion was —
    // it is replacing a window that appears, not a shape that grows.
    private static readonly TimeSpan OpenDuration = TimeSpan.FromMilliseconds(180);
    private static readonly TimeSpan CloseDuration = TimeSpan.FromMilliseconds(120);
    private static readonly TimeSpan MicroDuration = TimeSpan.FromMilliseconds(120);

    private static IEasingFunction OpenEase() => new CubicEase { EasingMode = EasingMode.EaseOut };
    private static IEasingFunction MicroEase() => new QuadraticEase { EasingMode = EasingMode.EaseOut };

    // --- Do not disturb

    /// <summary>
    /// Whether Windows is holding notifications back — a fullscreen game, a presentation, or
    /// Focus Assist. The equivalent of isActiveSpaceFullscreen on macOS, and asked at the
    /// moment it matters rather than polled: the flyout is hidden the rest of the time, so
    /// there is nothing to keep out of the way.
    /// </summary>
    private static bool IsQuietHours() =>
        SHQueryUserNotificationState(out var state) == 0 && state is
            QunsBusy or QunsRunningD3DFullScreen or QunsPresentationMode;

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
