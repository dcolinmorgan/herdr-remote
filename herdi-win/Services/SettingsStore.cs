using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

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

    /// <summary>
    /// Where agent state comes from. Stands in for the Direct/Relay toggle in herdi-mac's
    /// status menu; unlike that one it is remembered across launches.
    /// </summary>
    public ConnectionMode Mode
    {
        get => string.Equals(_data.Mode, "direct", StringComparison.OrdinalIgnoreCase)
            ? ConnectionMode.Direct
            : ConnectionMode.Relay;
        set
        {
            _data.Mode = value == ConnectionMode.Direct ? "direct" : "relay";
            Save();
        }
    }

    /// <summary>
    /// Whether an agent finishing raises a notification. On by default, since it is what the
    /// feature was asked for, and toggleable from the tray because "every agent that finishes"
    /// is exactly the kind of notification that goes from useful to unbearable with volume.
    ///
    /// Nullable in the file on purpose: a plain bool deserialises a *missing* key to false, so
    /// shipping it that way would leave the feature silently off for everyone who already has
    /// a settings.json.
    /// </summary>
    public bool NotifyOnFinish
    {
        get => _data.NotifyOnFinish ?? true;
        set { _data.NotifyOnFinish = value; Save(); }
    }

    public string RelayUrl
    {
        get => string.IsNullOrWhiteSpace(_data.RelayUrl) ? DefaultRelayUrl : _data.RelayUrl!;
        set { _data.RelayUrl = value; Save(); }
    }

    /// <summary>
    /// SSH targets polled in direct mode, e.g. "user@host" — the client-side counterpart
    /// of the relay's HERDR_REMOTES, and of herdi-mac's "herdi_remotes" UserDefaults key.
    /// Plaintext, like both of those: a hostname is not a secret, and DPAPI is reserved
    /// for the relay token.
    /// </summary>
    public IReadOnlyList<string> Remotes
    {
        get => _data.Remotes ?? (IReadOnlyList<string>)Array.Empty<string>();
        set
        {
            _data.Remotes = Normalize(value);
            Save();
        }
    }

    /// <summary>
    /// Override for the local herdr binary. Empty falls back to HERDR_BIN and then PATH,
    /// the same chain herdi-mac's "herdi_herdr_path" sits at the head of.
    /// </summary>
    public string HerdrPath
    {
        get => _data.HerdrPath ?? string.Empty;
        set { _data.HerdrPath = value.Trim(); Save(); }
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

    /// <summary>
    /// Flyout colour and opacity. Anything missing or out of range falls back to
    /// <see cref="IslandAppearance.Default"/>, so an older settings.json — or a hand-edited
    /// one — cannot leave the flyout invisible.
    ///
    /// IslandExpandedOpacity is read for the sake of settings written while the island was
    /// a top-edge capsule with two opacities. Its resting one described a capsule that no
    /// longer exists and is dropped; the expanded one described exactly this card, so it
    /// carries over. Both keys stop being written on the next save.
    /// </summary>
    public IslandAppearance Appearance
    {
        get => new IslandAppearance(
            IslandAppearance.ParseHex(_data.IslandColor) ?? IslandAppearance.DefaultFill,
            _data.IslandOpacity ?? _data.IslandExpandedOpacity ?? IslandAppearance.DefaultOpacity)
            .Normalized();
        set
        {
            var normalized = value.Normalized();
            _data.IslandColor = IslandAppearance.ToHex(normalized.Fill);
            _data.IslandOpacity = normalized.Opacity;
            _data.IslandCollapsedOpacity = null;
            _data.IslandExpandedOpacity = null;
            Save();
        }
    }

    /// <summary>Set once the Start Menu shortcut carrying our AUMID has been created.</summary>
    public bool ShortcutInstalled
    {
        get => _data.ShortcutInstalled;
        set { _data.ShortcutInstalled = value; Save(); }
    }

    /// <summary>Trim, drop blanks, and keep the first of any duplicate host.</summary>
    private static List<string> Normalize(IEnumerable<string> hosts)
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var kept = new List<string>();
        foreach (var host in hosts)
        {
            var trimmed = host.Trim();
            if (trimmed.Length > 0 && seen.Add(trimmed)) kept.Add(trimmed);
        }
        return kept;
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
            var json = JsonSerializer.Serialize(_data, SerializerOptions);
            File.WriteAllText(_path, json);
        }
        catch (Exception)
        {
            // Losing a preference write is preferable to crashing the tray app.
        }
    }

    /// <summary>
    /// Nulls are left out rather than written as JSON null, so a setting that has been
    /// retired — or never set — leaves no trace in the file.
    /// </summary>
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

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
        public string? Mode { get; set; }
        public string? RelayUrl { get; set; }
        public string? RelayTokenProtected { get; set; }
        public List<string>? Remotes { get; set; }
        public string? HerdrPath { get; set; }
        public bool LaunchAtLogin { get; set; }
        public bool? NotifyOnFinish { get; set; }
        public bool ShortcutInstalled { get; set; }
        public string? IslandColor { get; set; }
        public double? IslandOpacity { get; set; }

        // Written by the top-edge-capsule builds. Read once for migration, then nulled —
        // Save omits nulls, so the next write drops them from the file for good.
        public double? IslandCollapsedOpacity { get; set; }
        public double? IslandExpandedOpacity { get; set; }
    }
}
