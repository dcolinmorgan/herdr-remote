import Foundation

/// What the Connect a Phone window can offer, read from the files install-service.sh writes.
///
/// A phone needs the token once. The relay then sets a cookie, so the plain address works from
/// that browser on. The link therefore carries the token. A temp tunnel gets a new address on
/// every restart, and the cookie belongs to the old one, so its code has to be scanned again.
enum PhoneLink: Equatable {
    /// `link` holds the token. `address` is safe to show as text.
    case ready(link: String, address: String)
    /// A temp tunnel's current address. It stops working when the tunnel restarts.
    case temporary(link: String, address: String)
    /// The tunnel is up, but the relay checks no token, so anyone with the address gets in.
    case noToken(address: String)
    /// A temp tunnel with no address in its log yet, so it is not running or has not connected.
    case tempTunnel
    case noTunnel
}

/// Where install-service.sh keeps `config.env` and `secrets.env`.
let herdrRemoteConfigDir = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".config/herdr-remote")

/// Reads the `KEY=value` lines the installer writes. Blank lines and `#` comments are skipped.
/// Quotes and an `export` prefix are dropped, in case someone edited the file by hand.
func parseEnvFile(_ text: String) -> [String: String] {
    var values: [String: String] = [:]
    for rawLine in text.components(separatedBy: .newlines) {
        var line = rawLine.trimmingCharacters(in: .whitespaces)
        if line.isEmpty || line.hasPrefix("#") { continue }
        if line.hasPrefix("export ") { line = String(line.dropFirst(7)) }
        guard let equals = line.firstIndex(of: "=") else { continue }
        let key = String(line[..<equals]).trimmingCharacters(in: .whitespaces)
        var value = String(line[line.index(after: equals)...]).trimmingCharacters(in: .whitespaces)
        if value.count >= 2, let first = value.first, first == value.last, first == "\"" || first == "'" {
            value = String(value.dropFirst().dropLast())
        }
        values[key] = value
    }
    return values
}

func readEnvFile(_ url: URL) -> [String: String]? {
    guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
    return parseEnvFile(text)
}

/// The hostname as the installer asked for it, `relay.example.com`. A scheme and a trailing slash
/// are forgiven. Anything else that is not a plain DNS name is refused, because it goes into a
/// link the phone opens.
func tunnelAddress(_ hostname: String) -> String? {
    var host = hostname.trimmingCharacters(in: .whitespaces)
    for scheme in ["https://", "http://"] where host.lowercased().hasPrefix(scheme) {
        host = String(host.dropFirst(scheme.count))
    }
    while host.hasSuffix("/") { host = String(host.dropLast()) }

    let labels = host.split(separator: ".", omittingEmptySubsequences: false)
    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
    let valid = labels.count >= 2 && labels.allSatisfy { label in
        !label.isEmpty && !label.hasPrefix("-") && !label.hasSuffix("-")
            && label.unicodeScalars.allSatisfy { allowed.contains($0) }
    }
    return valid ? "https://\(host)" : nil
}

/// The newest quick-tunnel address cloudflared printed. launchd appends to the log, so the last
/// one is the current one. `api.trycloudflare.com` shows up in cloudflared's own errors and is
/// never a tunnel.
func latestTempTunnelAddress(_ log: String) -> String? {
    let pattern = try! NSRegularExpression(pattern: "https://([a-z0-9-]+)\\.trycloudflare\\.com")
    let matches = pattern.matches(in: log, range: NSRange(log.startIndex..., in: log))
    return matches.reversed().lazy
        .compactMap { Range($0.range(at: 1), in: log).map { String(log[$0]) } }
        .first { $0 != "api" }
        .flatMap { tunnelAddress("\($0).trycloudflare.com") }
}

func phoneLink(config: [String: String]?, secrets: [String: String]?, tunnelLog: String? = nil) -> PhoneLink {
    guard let config else { return .noTunnel }
    let token = secrets?["HERDR_RELAY_TOKEN"] ?? ""
    switch config["HERDR_TUNNEL_MODE"] {
    case "temp":
        guard let address = tunnelLog.flatMap(latestTempTunnelAddress) else { return .tempTunnel }
        guard !token.isEmpty else { return .noToken(address: address) }
        return .temporary(link: tokenLink(address, token), address: address)
    case "named":
        guard let address = tunnelAddress(config["HERDR_TUNNEL_HOSTNAME"] ?? "") else { return .noTunnel }
        guard !token.isEmpty else { return .noToken(address: address) }
        return .ready(link: tokenLink(address, token), address: address)
    default:
        return .noTunnel
    }
}

private func tokenLink(_ address: String, _ token: String) -> String {
    var query = CharacterSet.urlQueryAllowed
    query.remove(charactersIn: "&=+?#")
    let escaped = token.addingPercentEncoding(withAllowedCharacters: query) ?? token
    return "\(address)/?token=\(escaped)"
}

/// The installer's log folder for this platform, unless config.env names another.
private func herdrLogDir(_ config: [String: String]?) -> URL {
    if let dir = config?["HERDR_LOG_DIR"], !dir.isEmpty { return URL(fileURLWithPath: dir) }
    return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/herdr-remote")
}

/// The last 256KB of the tunnel log, which holds the newest address. The log grows on every
/// reconnect and is never rotated, so it is not read whole.
private func readTunnelLogTail(_ url: URL) -> String? {
    guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
    defer { try? handle.close() }
    let size = (try? handle.seekToEnd()) ?? 0
    try? handle.seek(toOffset: size > 262_144 ? size - 262_144 : 0)
    return (try? handle.readToEnd()).map { String(decoding: $0, as: UTF8.self) }
}

/// A made-up state for each screen of the window, so every screen can be checked on one Mac:
/// `open --env HERDI_CONNECT_PHONE_PREVIEW=temp dist/Herdi.app`. It never holds a real token.
func previewPhoneLink(_ name: String) -> PhoneLink? {
    let named = "https://relay.example.com"
    let temp = "https://example-words-here.trycloudflare.com"
    switch name {
    case "named": return .ready(link: "\(named)/?token=preview", address: named)
    case "temp": return .temporary(link: "\(temp)/?token=preview", address: temp)
    case "temp-waiting": return .tempTunnel
    case "none": return .noTunnel
    case "no-token": return .noToken(address: named)
    default: return nil
    }
}

/// Reads both files fresh, so a re-run of the installer shows up the next time the window opens.
func currentPhoneLink() -> PhoneLink {
    if let name = ProcessInfo.processInfo.environment["HERDI_CONNECT_PHONE_PREVIEW"],
       let preview = previewPhoneLink(name) {
        return preview
    }
    let config = readEnvFile(herdrRemoteConfigDir.appendingPathComponent("config.env"))
    return phoneLink(
        config: config,
        secrets: readEnvFile(herdrRemoteConfigDir.appendingPathComponent("secrets.env")),
        tunnelLog: readTunnelLogTail(herdrLogDir(config).appendingPathComponent("tunnel-stderr.log"))
    )
}
