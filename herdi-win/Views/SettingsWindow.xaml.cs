using System.Windows;
using Herdi.Services;

namespace Herdi.Views;

/// <summary>
/// Relay URL and token entry. herdi-mac has no equivalent — it only exposes a
/// Direct/Relay toggle in its menu and reads hostAddress from UserDefaults — but a
/// relay-only client is unusable without somewhere to paste the tunnel URL.
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
    }

    /// <summary>Set when the user saved, so the caller knows to reconnect.</summary>
    public bool Saved { get; private set; }

    private void OnSave(object sender, RoutedEventArgs e)
    {
        var url = UrlBox.Text.Trim();
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed) ||
            parsed.Scheme is not ("ws" or "wss"))
        {
            MessageBox.Show(this,
                "Enter a WebSocket URL starting with ws:// or wss://.",
                "Herdi", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        _settings.RelayUrl = url;
        _settings.RelayToken = TokenBox.Password;
        Saved = true;
        DialogResult = true;
        Close();
    }

    private void OnCancel(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }
}
