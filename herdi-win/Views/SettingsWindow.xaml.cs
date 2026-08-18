using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Herdi.Services;
using Drawing = System.Drawing;
using Forms = System.Windows.Forms;

namespace Herdi.Views;

/// <summary>
/// Source selection plus the fields each mode needs, and how the island looks. herdi-mac
/// spreads the same choices across its status menu (a Direct/Relay toggle, an add-remote
/// sheet) and UserDefaults it never surfaces; one dialog is easier to reason about and
/// gives the relay URL somewhere to live, which the mac app never provided.
/// </summary>
public partial class SettingsWindow : Window
{
    private readonly SettingsStore _settings;

    /// <summary>
    /// Hands the island each appearance edit as it happens. Transparency cannot be judged
    /// from a swatch in a dialog — it depends entirely on what is behind the island — so
    /// the sliders drive the real thing and <see cref="OnClosed"/> undoes it if the dialog
    /// is cancelled. Null when nobody wired a preview up.
    /// </summary>
    private readonly Action<IslandAppearance>? _preview;

    /// <summary>Colour being edited. The text box holds the same value as hex.</summary>
    private Color _fill;

    /// <summary>Set once the constructor's field seeding is done, so it previews nothing.</summary>
    private bool _ready;

    public SettingsWindow(SettingsStore settings, Action<IslandAppearance>? preview = null)
    {
        _settings = settings;
        _preview = preview;
        InitializeComponent();

        // SizeToContent would happily grow past the bottom of the screen, and this window
        // cannot be resized or dragged back into view. Capped here, the body scrolls
        // instead and Save stays reachable.
        MaxHeight = Math.Max(360, SystemParameters.WorkArea.Height - 40);

        UrlBox.Text = settings.RelayUrl;
        TokenBox.Password = settings.RelayToken;
        RemotesBox.Text = string.Join(Environment.NewLine, settings.Remotes);
        HerdrPathBox.Text = settings.HerdrPath;
        ShowAppearance(settings.Appearance);

        // Assigning IsChecked shows the matching field group through OnModeChanged.
        if (settings.Mode == ConnectionMode.Direct) DirectModeChoice.IsChecked = true;
        else RelayModeChoice.IsChecked = true;

        _ready = true;
    }

    /// <summary>Set when the user saved, so the caller knows to reconnect.</summary>
    public bool Saved { get; private set; }

    private ConnectionMode SelectedMode =>
        DirectModeChoice.IsChecked == true ? ConnectionMode.Direct : ConnectionMode.Relay;

    private void OnModeChanged(object sender, RoutedEventArgs e)
    {
        // Fires during InitializeComponent, before the field groups exist.
        if (RelayFields is null || DirectFields is null) return;
        var direct = SelectedMode == ConnectionMode.Direct;
        RelayFields.Visibility = direct ? Visibility.Collapsed : Visibility.Visible;
        DirectFields.Visibility = direct ? Visibility.Visible : Visibility.Collapsed;
    }

    private IslandAppearance CurrentAppearance =>
        new(_fill, CollapsedOpacitySlider.Value, ExpandedOpacitySlider.Value);

    /// <summary>Load an appearance into the three controls without previewing it back.</summary>
    private void ShowAppearance(IslandAppearance appearance)
    {
        var was = _ready;
        _ready = false;
        _fill = appearance.Fill;
        ColorBox.Text = IslandAppearance.ToHex(_fill);
        ColorPreview.Background = new SolidColorBrush(_fill);
        CollapsedOpacitySlider.Value = appearance.CollapsedOpacity;
        ExpandedOpacitySlider.Value = appearance.ExpandedOpacity;
        _ready = was;
    }

    private void Preview()
    {
        if (_ready) _preview?.Invoke(CurrentAppearance);
    }

    private void OnOpacityChanged(object sender, RoutedPropertyChangedEventArgs<double> e) => Preview();

    private void OnColorTextChanged(object sender, TextChangedEventArgs e)
    {
        // Half-typed hex is the normal state of this box, so it only takes effect once it
        // parses — no warning, no reverting the caret out from under the typist.
        if (IslandAppearance.ParseHex(ColorBox.Text) is { } color) SetFill(color, echoToBox: false);
    }

    private void OnSwatchPicked(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { Tag: string hex } && IslandAppearance.ParseHex(hex) is { } color)
            SetFill(color, echoToBox: true);
    }

    private void OnPickColor(object sender, RoutedEventArgs e)
    {
        // The WinForms picker, for the same reason NotifyIcon is used for the tray: WPF
        // ships no colour dialog, and this one costs nothing — WinForms is already
        // referenced. FullOpen puts it straight on the custom-colour panel.
        using var dialog = new Forms.ColorDialog
        {
            Color = Drawing.Color.FromArgb(_fill.R, _fill.G, _fill.B),
            FullOpen = true,
            AnyColor = true,
        };
        if (dialog.ShowDialog() != Forms.DialogResult.OK) return;
        SetFill(Color.FromRgb(dialog.Color.R, dialog.Color.G, dialog.Color.B), echoToBox: true);
    }

    private void OnResetAppearance(object sender, RoutedEventArgs e)
    {
        ShowAppearance(IslandAppearance.Default);
        Preview();
    }

    /// <param name="echoToBox">
    /// False when the colour came from the text box itself: rewriting the text mid-edit
    /// would move the caret to the end after every keystroke.
    /// </param>
    private void SetFill(Color color, bool echoToBox)
    {
        _fill = color;
        ColorPreview.Background = new SolidColorBrush(color);
        // Re-enters OnColorTextChanged, which lands on this same colour with echo off.
        if (echoToBox) ColorBox.Text = IslandAppearance.ToHex(color);
        Preview();
    }

    private void OnSave(object sender, RoutedEventArgs e)
    {
        var mode = SelectedMode;
        var url = UrlBox.Text.Trim();
        var remotes = RemotesBox.Text
            .Split(new[] { '\r', '\n', ',' }, StringSplitOptions.RemoveEmptyEntries)
            .Select(host => host.Trim())
            .Where(host => host.Length > 0)
            .ToList();
        var herdrPath = HerdrPathBox.Text.Trim();

        if (mode == ConnectionMode.Relay &&
            (!Uri.TryCreate(url, UriKind.Absolute, out var parsed) || parsed.Scheme is not ("ws" or "wss")))
        {
            Warn("Enter a WebSocket URL starting with ws:// or wss://.");
            return;
        }

        if (mode == ConnectionMode.Direct && remotes.Count == 0 && herdrPath.Length == 0)
        {
            // Both empty means the poller has nowhere to look. A herdr on PATH would do,
            // but this client normally runs on a machine that has none.
            Warn("Direct mode needs at least one SSH host, or a path to a local herdr binary.");
            return;
        }

        _settings.Mode = mode;
        _settings.RelayUrl = string.IsNullOrEmpty(url) ? _settings.RelayUrl : url;
        _settings.RelayToken = TokenBox.Password;
        _settings.Remotes = remotes;
        _settings.HerdrPath = herdrPath;
        _settings.Appearance = CurrentAppearance;
        Saved = true;
        DialogResult = true;
        Close();
    }

    private void Warn(string message) =>
        MessageBox.Show(this, message, "Herdi", MessageBoxButton.OK, MessageBoxImage.Warning);

    private void OnCancel(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }

    protected override void OnClosed(EventArgs e)
    {
        base.OnClosed(e);
        // Cancelled, or closed from the title bar: put the island back to what is stored,
        // undoing whatever the preview left on screen.
        if (!Saved) _preview?.Invoke(_settings.Appearance);
    }
}
