using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Herdi.Services;

/// <summary>
/// One configured relay: where it is, and the secret it needs to get in. <see cref="Token"/>
/// is empty for a relay that wants none — a relay started without HERDR_RELAY_TOKEN skips the
/// check entirely (herdr_relay.py:1926), which is the usual case for a loopback one and must
/// stay a blank field rather than a required one.
/// </summary>
public sealed record RelayEndpoint(string Url, string Token);

/// <summary>
/// Persisted client settings. Replaces herdi-mac's UserDefaults, and each relay's token
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

    /// <summary>
    /// Every relay to watch, in the order they were entered, each with its own token.
    ///
    /// All of them are connected at once and their panes merged into one list. This was a
    /// single URL first — a second relay meant editing the first one out, which does not show
    /// you two herds, it shows you one and forgets the other — and then a list of URLs behind
    /// one shared token, which covered the common pair (a tokenless loopback relay beside a
    /// token-guarded tunnel) and nothing past it: two relays each wanting a <em>different</em>
    /// token could not both be reached.
    ///
    /// <b>The token travels beside its relay, never inside the URL.</b> `?token=` on the query
    /// string does authenticate — the relay reads it (herdr_relay.py:1931) — but it is the
    /// wrong channel from this client, because the URL is not just an address here: it is the
    /// source key stamped on every agent (Agent.SourceId), so a token written into it would be
    /// copied into every toast's launch argument, printed in the tray, and stored in this file
    /// in the clear beside the DPAPI blob that exists to prevent exactly that. It is accepted
    /// as <em>input</em> — see <see cref="SplitToken"/>, so a share link can be pasted whole —
    /// and taken straight back out.
    ///
    /// Two older shapes are read once and migrated: RelayUrls behind one RelayTokenProtected,
    /// and before that a single RelayUrl. Both are nulled on the next save, the same way
    /// IslandExpandedOpacity is — Save omits nulls, so the file drops them for good.
    /// </summary>
    public IReadOnlyList<RelayEndpoint> Relays
    {
        get
        {
            if (_data.Relays is { Count: > 0 })
            {
                return Normalize(_data.Relays.Select(r =>
                    new RelayEndpoint(r.Url ?? string.Empty, Unprotect(r.TokenProtected))));
            }

            var shared = Unprotect(_data.RelayTokenProtected);
            if (_data.RelayUrls is { Count: > 0 })
                return Normalize(_data.RelayUrls.Select(url => new RelayEndpoint(url, shared)));
            if (!string.IsNullOrWhiteSpace(_data.RelayUrl))
                return Normalize(new[] { new RelayEndpoint(_data.RelayUrl!, shared) });
            return new[] { new RelayEndpoint(DefaultRelayUrl, shared) };
        }
        set
        {
            _data.Relays = Normalize(value)
                .Select(r => new RelayData { Url = r.Url, TokenProtected = Protect(r.Token) })
                .ToList();
            _data.RelayUrls = null;
            _data.RelayUrl = null;
            _data.RelayTokenProtected = null;
            Save();
        }
    }

    /// <summary>
    /// Pull a `?token=` off a relay URL: returns the URL without it, and the value it carried
    /// (empty when there was none). Other query parameters are put back.
    ///
    /// Public because the settings dialog does this the moment the URL box loses focus, so a
    /// pasted share link visibly splits into the two fields rather than being rewritten behind
    /// the operator's back at save time.
    ///
    /// A string that is not a URL at all is handed back untouched. Validating one is the
    /// dialog's job; silently rewriting a typo here would only make it harder to see.
    /// </summary>
    public static (string Url, string Token) SplitToken(string url)
    {
        var trimmed = url.Trim();
        var mark = trimmed.IndexOf('?');
        if (mark < 0) return (trimmed, string.Empty);

        var token = string.Empty;
        var others = new List<string>();
        foreach (var pair in trimmed[(mark + 1)..].Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var eq = pair.IndexOf('=');
            var key = eq < 0 ? pair : pair[..eq];
            if (string.Equals(key, "token", StringComparison.OrdinalIgnoreCase))
            {
                token = eq < 0 ? string.Empty : Uri.UnescapeDataString(pair[(eq + 1)..]);
                continue;
            }
            others.Add(pair);
        }

        var rest = others.Count == 0 ? string.Empty : "?" + string.Join("&", others);
        return (trimmed[..mark] + rest, token);
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

    /// <summary>
    /// Trim, split any `?token=` out of the URL, drop blanks, and keep the first of any
    /// duplicate URL. Run on the way in <em>and</em> on the way out, so a settings.json edited
    /// by hand cannot leave a token sitting in a URL that is about to become a source key.
    ///
    /// A row's own token wins over one found in its URL: by the time the dialog saves, the
    /// inline one has already been moved into that field, so a value still in the URL here is
    /// the older of the two.
    /// </summary>
    private static List<RelayEndpoint> Normalize(IEnumerable<RelayEndpoint> relays)
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var kept = new List<RelayEndpoint>();
        foreach (var relay in relays)
        {
            var (url, inline) = SplitToken(relay.Url);
            if (url.Length == 0 || !seen.Add(url)) continue;
            kept.Add(new RelayEndpoint(url, relay.Token.Length > 0 ? relay.Token : inline));
        }
        return kept;
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
        public List<RelayData>? Relays { get; set; }
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

        // Written by the builds that kept relays as a list of URLs behind one shared token,
        // and by the single-URL builds before them. Read once into Relays and nulled on the
        // next save, same treatment as the two above.
        public List<string>? RelayUrls { get; set; }
        public string? RelayTokenProtected { get; set; }
        public string? RelayUrl { get; set; }
    }

    /// <summary>One relay as it sits in the file: the URL in the clear, the token under DPAPI.</summary>
    private sealed class RelayData
    {
        public string? Url { get; set; }
        public string? TokenProtected { get; set; }
    }
}
