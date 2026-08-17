using System.Windows;
using Herdi.Services;

namespace Herdi.Views;

/// <summary>
/// Source selection plus the fields each mode needs. herdi-mac spreads the same choices
/// across its status menu (a Direct/Relay toggle, an add-remote sheet) and UserDefaults it
/// never surfaces; one dialog is easier to reason about and gives the relay URL somewhere
/// to live, which the mac app never provided.
/// </summary>
public partial class SettingsWindow : Window
{
    private readonly SettingsStore _settings;

    public SettingsWindow(SettingsStore settings)
    {
        _settings = settings;
        InitializeComponent();

        UrlBox.Text = settings.RelayUrl;
        TokenBox.Password = settings.RelayToken;
        RemotesBox.Text = string.Join(Environment.NewLine, settings.Remotes);
        HerdrPathBox.Text = settings.HerdrPath;

        // Assigning IsChecked shows the matching field group through OnModeChanged.
        if (settings.Mode == ConnectionMode.Direct) DirectModeChoice.IsChecked = true;
        else RelayModeChoice.IsChecked = true;
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
}
