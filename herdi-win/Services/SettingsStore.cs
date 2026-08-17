using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Herdi.Services;

/// <summary>
/// Persisted client settings. Replaces herdi-mac's UserDefaults, and the relay token
/// takes the place of its Keychain entry — encrypted with DPAPI (CurrentUser scope)
/// so it is not sitting in plaintext next to the config.
/// </summary>
public sealed class SettingsStore
{
    private const string DefaultRelayUrl = "ws://127.0.0.1:8375";

    private readonly string _path;
    private Data _data = new();

    public SettingsStore(string? directory = null)
    {
        var dir = directory ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "herdr-remote");
        Directory.CreateDirectory(dir);
        _path = Path.Combine(dir, "settings.json");
        Load();
    }

    public string RelayUrl
    {
        get => string.IsNullOrWhiteSpace(_data.RelayUrl) ? DefaultRelayUrl : _data.RelayUrl!;
        set { _data.RelayUrl = value; Save(); }
    }

    /// <summary>Shared secret for relay auth (HERDR_RELAY_TOKEN). Empty when unset.</summary>
    public string RelayToken
    {
        get => Unprotect(_data.RelayTokenProtected);
        set { _data.RelayTokenProtected = Protect(value); Save(); }
    }

    public bool LaunchAtLogin
    {
        get => _data.LaunchAtLogin;
        set { _data.LaunchAtLogin = value; Save(); }
    }

    /// <summary>Set once the Start Menu shortcut carrying our AUMID has been created.</summary>
    public bool ShortcutInstalled
    {
        get => _data.ShortcutInstalled;
        set { _data.ShortcutInstalled = value; Save(); }
    }

    private void Load()
    {
        try
        {
            if (!File.Exists(_path)) return;
            var json = File.ReadAllText(_path);
            var parsed = JsonSerializer.Deserialize<Data>(json);
            if (parsed is not null) _data = parsed;
        }
        catch (Exception)
        {
            // A corrupt or unreadable settings file falls back to defaults rather
            // than blocking startup.
            _data = new Data();
        }
    }

    private void Save()
    {
        try
        {
            var json = JsonSerializer.Serialize(_data, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(_path, json);
        }
        catch (Exception)
        {
            // Losing a preference write is preferable to crashing the tray app.
        }
    }

    private static string? Protect(string plain)
    {
        if (string.IsNullOrEmpty(plain)) return null;
        try
        {
            var bytes = ProtectedData.Protect(
                Encoding.UTF8.GetBytes(plain), null, DataProtectionScope.CurrentUser);
            return Convert.ToBase64String(bytes);
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static string Unprotect(string? cipher)
    {
        if (string.IsNullOrEmpty(cipher)) return string.Empty;
        try
        {
            var bytes = ProtectedData.Unprotect(
                Convert.FromBase64String(cipher), null, DataProtectionScope.CurrentUser);
            return Encoding.UTF8.GetString(bytes);
        }
        catch (Exception)
        {
            return string.Empty;
        }
    }

    private sealed class Data
    {
        public string? RelayUrl { get; set; }
        public string? RelayTokenProtected { get; set; }
        public bool LaunchAtLogin { get; set; }
        public bool ShortcutInstalled { get; set; }
    }
}
