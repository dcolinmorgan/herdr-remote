import Foundation
import AppKit
import Observation

@Observable
final class Updater {
    static let shared = Updater()

    let currentVersion = "0.3.2"
    let repo = "dcolinmorgan/herdi"

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

            // Try gh CLI for auth (works for private repos)
            if let result = try? await ghRelease() {
                DispatchQueue.main.async { self.handleRelease(result) }
                return
            }

            // Fallback: unauthenticated API (public repos only)
            guard let url = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else { return }
            var request = URLRequest(url: url)
            request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")

            guard let (data, response) = try? await URLSession.shared.data(for: request),
                  let http = response as? HTTPURLResponse, http.statusCode == 200,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                DispatchQueue.main.async { self.status = "v\(self.currentVersion) (can't check updates)" }
                return
            }
            DispatchQueue.main.async { self.handleRelease(json) }
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
                // For private repos, download via gh CLI
                let tmpDMG = FileManager.default.temporaryDirectory.appendingPathComponent("Herdi-update.dmg")
                try? FileManager.default.removeItem(at: tmpDMG)

                // Try gh release download first
                let ghPaths = ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"]
                let ghPath = ghPaths.first(where: { FileManager.default.fileExists(atPath: $0) })

                let ghDl = Process()
                ghDl.executableURL = URL(fileURLWithPath: ghPath ?? "/usr/bin/false")
                ghDl.arguments = ["release", "download", "v\(latestVersion ?? "")", "--repo", repo, "--pattern", "*.dmg", "--dir", tmpDMG.deletingLastPathComponent().path, "--clobber"]
                ghDl.standardError = FileHandle.nullDevice
                try? ghDl.run()
                ghDl.waitUntilExit()

                // Find downloaded DMG
                let dmgPath: URL
                if ghDl.terminationStatus == 0,
                   let found = try? FileManager.default.contentsOfDirectory(at: tmpDMG.deletingLastPathComponent(), includingPropertiesForKeys: nil)
                    .first(where: { $0.pathExtension == "dmg" && $0.lastPathComponent.contains("Herdi") }) {
                    dmgPath = found
                } else {
                    // Fallback: direct URL download
                    let (fileURL, _) = try await URLSession.shared.download(from: url)
                    try? FileManager.default.removeItem(at: tmpDMG)
                    try FileManager.default.moveItem(at: fileURL, to: tmpDMG)
                    dmgPath = tmpDMG
                }

                DispatchQueue.main.async { self.status = "Installing…" }

                // Mount
                let mount = Process()
                mount.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
                mount.arguments = ["attach", dmgPath.path, "-nobrowse", "-quiet"]
                try mount.run()
                mount.waitUntilExit()

                let mountPoint = "/Volumes/Herdi"
                let appSource = "\(mountPoint)/Herdi.app"
                let appDest = Bundle.main.bundlePath

                guard FileManager.default.fileExists(atPath: appSource) else {
                    DispatchQueue.main.async { self.status = "Install failed"; self.isUpdating = false }
                    return
                }

                // Replace
                let backup = appDest + ".bak"
                try? FileManager.default.removeItem(atPath: backup)
                try FileManager.default.moveItem(atPath: appDest, toPath: backup)
                try FileManager.default.copyItem(atPath: appSource, toPath: appDest)

                // Unmount + cleanup
                let unmount = Process()
                unmount.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
                unmount.arguments = ["detach", mountPoint, "-quiet"]
                try? unmount.run()
                try? FileManager.default.removeItem(atPath: backup)

                DispatchQueue.main.async { self.status = "Relaunching…" }

                // Relaunch
                let relaunch = Process()
                relaunch.executableURL = URL(fileURLWithPath: "/usr/bin/open")
                relaunch.arguments = ["-n", appDest]
                try relaunch.run()

                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
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
