import Foundation
import AppKit
import Observation

@Observable
final class Updater {
    static let shared = Updater()

    let currentVersion = Bundle.main.object(
        forInfoDictionaryKey: "CFBundleShortVersionString"
    ) as? String ?? "0.0.0"
    let repo = "dcolinmorgan/herdr-remote"

    var latestVersion: String?
    var updateAvailable = false
    var isChecking = false
    var isUpdating = false
    var status: String?

    private var downloadURL: URL?
    var lastCheck: Date?

    func checkForUpdates() {
        if let last = lastCheck, Date().timeIntervalSince(last) < 600 { return }
        guard !isChecking else { return }
        isChecking = true
        status = "Checking…"
        lastCheck = Date()

        Task {
            defer { DispatchQueue.main.async { self.isChecking = false } }

            // Public API (works now that repo is public)
            guard let url = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else { return }
            var request = URLRequest(url: url)
            request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")

            if let (data, response) = try? await URLSession.shared.data(for: request),
               let http = response as? HTTPURLResponse, http.statusCode == 200,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                DispatchQueue.main.async { self.handleRelease(json) }
                return
            }

            // Fallback: gh CLI for private repos
            if let result = try? await ghRelease() {
                DispatchQueue.main.async { self.handleRelease(result) }
                return
            }

            DispatchQueue.main.async { self.status = "v\(self.currentVersion) (check failed)" }
        }
    }

    private func ghRelease() async throws -> [String: Any]? {
        // Find gh binary - try common paths since .app doesn't have shell PATH
        let ghPaths = ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"]
        guard let ghPath = ghPaths.first(where: { FileManager.default.fileExists(atPath: $0) }) else { return nil }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: ghPath)
        process.arguments = ["api", "repos/\(repo)/releases/latest"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private func handleRelease(_ json: [String: Any]) {
        guard let tag = json["tag_name"] as? String else {
            status = "v\(currentVersion)"
            return
        }
        let version = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
        let assets = json["assets"] as? [[String: Any]] ?? []
        let dmgAsset = assets.first { ($0["name"] as? String)?.hasSuffix(".dmg") == true }
        let dmgURL = dmgAsset?["browser_download_url"] as? String

        latestVersion = version
        downloadURL = dmgURL.flatMap { URL(string: $0) }
        updateAvailable = version != currentVersion && downloadURL != nil
        status = updateAvailable ? "v\(version) available" : "v\(currentVersion) ✓"
    }

    func performUpdate() {
        guard let url = downloadURL, !isUpdating else { return }
        isUpdating = true
        status = "Downloading…"

        Task {
            do {
                // Download DMG via public URL
                let dmgPath = FileManager.default.temporaryDirectory.appendingPathComponent("Herdi-update.dmg")
                try? FileManager.default.removeItem(at: dmgPath)
                let (fileURL, _) = try await URLSession.shared.download(from: url)
                try FileManager.default.moveItem(at: fileURL, to: dmgPath)

                DispatchQueue.main.async { self.status = "Installing…" }

                let appDest = Bundle.main.bundlePath

                // Write a script that runs AFTER this app quits
                let script = """
                #!/bin/bash
                sleep 1
                hdiutil attach "\(dmgPath.path)" -nobrowse -quiet
                if [ -d "/Volumes/Herdi/Herdi.app" ]; then
                    rm -rf "\(appDest)"
                    cp -R "/Volumes/Herdi/Herdi.app" "\(appDest)"
                    hdiutil detach "/Volumes/Herdi" -quiet
                    rm -f "\(dmgPath.path)"
                    open "\(appDest)"
                else
                    hdiutil detach "/Volumes/Herdi" -quiet 2>/dev/null
                fi
                rm -f /tmp/herdi-update.sh
                """

                let scriptPath = "/tmp/herdi-update.sh"
                try script.write(toFile: scriptPath, atomically: true, encoding: .utf8)
                chmod(scriptPath, 0o755)

                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/bin/bash")
                process.arguments = [scriptPath]
                try process.run()

                // Quit so the script can replace us
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    NSApplication.shared.terminate(nil)
                }
            } catch {
                DispatchQueue.main.async {
                    self.status = "Update failed: \(error.localizedDescription)"
                    self.isUpdating = false
                }
            }
        }
    }
}

private func chmod(_ path: String, _ mode: mode_t) {
    Darwin.chmod(path, mode)
}
