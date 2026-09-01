using System.ComponentModel;

namespace Herdi.ViewModels;

/// <summary>
/// One row of the settings dialog's relay editor: an address and the token that gets in.
///
/// A view model rather than a bare string — which is what the list held while every relay
/// shared one token — because the two values only mean anything together, and because the URL
/// box is written back to from code when a pasted `?token=` is split out of it. A plain CLR
/// property could carry the value but could not push that edit to the screen.
/// </summary>
public sealed class RelayRow : INotifyPropertyChanged
{
    private string _url = string.Empty;
    public string Url
    {
        get => _url;
        set
        {
            if (_url == value) return;
            _url = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Url)));
        }
    }

    /// <summary>
    /// In the clear, and only for as long as the dialog is open — DPAPI happens in
    /// SettingsStore, on save.
    ///
    /// Deliberately not an observable property: PasswordBox.Password is not a dependency
    /// property and cannot be bound (WPF's own decision, so a password is not left sitting in
    /// the binding engine), so the box and this field are kept in step by the two handlers in
    /// SettingsWindow rather than by the binding that would normally do it.
    /// </summary>
    public string Token { get; set; } = string.Empty;

    public event PropertyChangedEventHandler? PropertyChanged;
}
