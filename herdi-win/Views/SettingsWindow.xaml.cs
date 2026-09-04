using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Herdi.Services;
using Herdi.ViewModels;
using Drawing = System.Drawing;
using Forms = System.Windows.Forms;

namespace Herdi.Views;

/// <summary>
/// Source selection plus the fields each mode needs, and how the panel looks. herdi-mac
/// spreads the same choices across its status menu (a Direct/Relay toggle, an add-remote
/// sheet) and UserDefaults it never surfaces; one dialog is easier to reason about and
/// gives the relay URL somewhere to live, which the mac app never provided.
/// </summary>
public partial class SettingsWindow : Window
{
    private readonly SettingsStore _settings;

    /// <summary>
    /// The relay editor's rows. An ObservableCollection rather than a re-read of the text in
    /// a box, because a relay is now two fields that have to stay married to each other:
    /// re-parsing lines out of a TextBox could not say which token belonged to which URL.
    /// </summary>
    private readonly ObservableCollection<RelayRow> _relays = new();

    /// <summary>
    /// A row just added by the button, waiting for its URL box to exist so it can be focused.
    /// Adding a relay you then have to click into is the kind of small rudeness that makes a
    /// list feel like a form.
    /// </summary>
    private RelayRow? _focusOnLoad;

    /// <summary>
    /// Hands the island each appearance edit as it happens. Transparency cannot be judged
    /// from a swatch in a dialog — it depends entirely on what is behind the panel — so the
    /// controls drive the real thing and <see cref="OnClosed"/> undoes it if the dialog is
    /// cancelled. Null when nobody wired a preview up.
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

        foreach (var relay in settings.Relays)
            _relays.Add(new RelayRow { Url = relay.Url, Token = relay.Token });
        RelayList.ItemsSource = _relays;

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

    private IslandAppearance CurrentAppearance => new(_fill, OpacitySliderControl.Value);

    /// <summary>Load an appearance into the controls without previewing it back.</summary>
    private void ShowAppearance(IslandAppearance appearance)
    {
        var was = _ready;
        _ready = false;
        _fill = appearance.Fill;
        ColorBox.Text = IslandAppearance.ToHex(_fill);
        ColorPreview.Background = new SolidColorBrush(_fill);
        OpacitySliderControl.Value = appearance.Opacity;
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

    private void OnAddRelay(object sender, RoutedEventArgs e)
    {
        var row = new RelayRow();
        _relays.Add(row);
        _focusOnLoad = row;
    }

    private void OnRemoveRelay(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: RelayRow row }) _relays.Remove(row);
    }

    private void OnRelayUrlLoaded(object sender, RoutedEventArgs e)
    {
        if (sender is not TextBox box || !ReferenceEquals(box.DataContext, _focusOnLoad)) return;
        _focusOnLoad = null;
        box.Focus();
    }

    /// <summary>
    /// Split a `?token=` out of a URL the moment the box is left.
    ///
    /// A relay URL shared from the web client carries its token in the query string, and the
    /// relay does accept it there — but this client must not keep it there: the URL is the
    /// source key stamped on every agent, so it travels into each toast's launch argument, the
    /// tray line and settings.json, none of which are places for a secret. Done here rather
    /// than silently at save so the operator watches it land in the field it belongs in.
    /// </summary>
    private void OnRelayUrlLostFocus(object sender, RoutedEventArgs e)
    {
        if (sender is not TextBox { DataContext: RelayRow row } box) return;
        var (url, token) = SettingsStore.SplitToken(row.Url);
        row.Url = url;
        if (token.Length == 0) return;
        row.Token = token;
        // Password is not bindable, so the box beside this one has to be told directly.
        if (TokenBoxBeside(box) is { } tokenBox) tokenBox.Password = token;
    }

    /// <summary>
    /// The row's own token box. Found as a sibling rather than by name: a name inside a
    /// DataTemplate belongs to every instantiation of it, so there is no one control it
    /// identifies.
    /// </summary>
    private static PasswordBox? TokenBoxBeside(TextBox url) =>
        (url.Parent as Panel)?.Children.OfType<PasswordBox>().FirstOrDefault();

    private void OnRelayTokenLoaded(object sender, RoutedEventArgs e)
    {
        // Raises PasswordChanged below, which writes the same value back. Harmless, and
        // cheaper than a flag that has to be right on every path.
        if (sender is PasswordBox { DataContext: RelayRow row } box) box.Password = row.Token;
    }

    private void OnRelayTokenChanged(object sender, RoutedEventArgs e)
    {
        if (sender is PasswordBox { DataContext: RelayRow row } box) row.Token = box.Password;
    }

    private void OnSave(object sender, RoutedEventArgs e)
    {
        var mode = SelectedMode;
        // A blank row is one added and then thought better of, not an error worth a dialog.
        var relays = _relays
            .Select(row => new RelayEndpoint(SettingsStore.SplitToken(row.Url).Url, row.Token))
            .Where(relay => relay.Url.Length > 0)
            .ToList();
        var remotes = Lines(RemotesBox.Text);
        var herdrPath = HerdrPathBox.Text.Trim();

        if (mode == ConnectionMode.Relay)
        {
            if (relays.Count == 0)
            {
                Warn("Relay mode needs at least one relay URL.");
                return;
            }

            // Every row, not just the first: a typo on the third would otherwise be saved and
            // then spend the rest of the session as a relay that silently never connects.
            var bad = relays.FirstOrDefault(relay =>
                !Uri.TryCreate(relay.Url, UriKind.Absolute, out var parsed) ||
                parsed.Scheme is not ("ws" or "wss"));
            if (bad is not null)
            {
                Warn($"Not a WebSocket URL: {bad.Url}\n\nEach relay must start with ws:// or wss://.");
                return;
            }

            // Said out loud rather than deduped quietly: the store keeps the first of two
            // rows naming one relay, and if the second is the one carrying the token, the
            // silent version of this is a relay that answers 401 for no visible reason.
            var duplicate = relays
                .GroupBy(relay => relay.Url, StringComparer.OrdinalIgnoreCase)
                .FirstOrDefault(group => group.Count() > 1);
            if (duplicate is not null)
            {
                Warn($"{duplicate.Key} is listed more than once.\n\nOne row per relay — only the first would be used.");
                return;
            }
        }

        if (mode == ConnectionMode.Direct && remotes.Count == 0 && herdrPath.Length == 0)
        {
            // Both empty means the poller has nowhere to look. A herdr on PATH would do,
            // but this client normally runs on a machine that has none.
            Warn("Direct mode needs at least one SSH host, or a path to a local herdr binary.");
            return;
        }

        _settings.Mode = mode;
        // Only when there is something to write: switching to direct mode with the relay
        // fields never touched must not blank the relay list on the way past.
        if (relays.Count > 0) _settings.Relays = relays;
        _settings.Remotes = remotes;
        _settings.HerdrPath = herdrPath;
        _settings.Appearance = CurrentAppearance;
        Saved = true;
        DialogResult = true;
        Close();
    }

    /// <summary>
    /// One entry per line, blanks dropped. Commas split too: the SSH host box has always
    /// taken either.
    /// </summary>
    private static List<string> Lines(string text) => text
        .Split(new[] { '\r', '\n', ',' }, StringSplitOptions.RemoveEmptyEntries)
        .Select(line => line.Trim())
        .Where(line => line.Length > 0)
        .ToList();

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
