import SwiftUI
import ServiceManagement
import UserNotifications

@main
struct HerdiApp: App {
    @NSApplicationDelegateAdaptor(HerdiAppDelegate.self) var appDelegate

    var body: some Scene {
        // No visible window — the panel IS the UI
        Settings { EmptyView() }
    }
}

@MainActor
class HerdiAppDelegate: NSObject, NSApplicationDelegate {
    var panelController: PanelWindowController?
    let relay = RelayConnection()
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Request notification permissions
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }

        // Minimal status bar item (quit + show panel)
        setupStatusItem()

        // Launch the notch panel
        panelController = PanelWindowController(relay: relay)
        panelController?.showPanel()

        // Auto-expand when an agent gets blocked
        observeBlockedAgents()
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "circle.fill", accessibilityDescription: "Herdi")
            button.image?.size = NSSize(width: 14, height: 14)
        }
        rebuildMenu()
    }

    private func rebuildMenu() {
        let menu = NSMenu()
        let updater = Updater.shared

        // Status
        let statusItem = NSMenuItem(title: relay.isConnected ? "● Connected" : "○ Disconnected", action: nil, keyEquivalent: "")
        statusItem.isEnabled = false
        menu.addItem(statusItem)

        let agentCount = NSMenuItem(title: "\(relay.agents.count) agents", action: nil, keyEquivalent: "")
        agentCount.isEnabled = false
        menu.addItem(agentCount)

        menu.addItem(.separator())

        // Connection mode
        let modeItem = NSMenuItem(title: "Mode: \(relay.mode.rawValue)", action: nil, keyEquivalent: "")
        modeItem.isEnabled = false
        menu.addItem(modeItem)

        let directItem = NSMenuItem(title: "  Direct (herdr CLI)", action: #selector(switchToDirect), keyEquivalent: "")
        directItem.target = self
        directItem.state = relay.mode == .direct ? .on : .off
        menu.addItem(directItem)

        let relayItem = NSMenuItem(title: "  Relay (WebSocket)", action: #selector(switchToRelay), keyEquivalent: "")
        relayItem.target = self
        relayItem.state = relay.mode == .relay ? .on : .off
        menu.addItem(relayItem)

        menu.addItem(.separator())

        // Remotes
        let remotesHeader = NSMenuItem(title: "Remote Hosts", action: nil, keyEquivalent: "")
        remotesHeader.isEnabled = false
        menu.addItem(remotesHeader)

        if relay.remotes.isEmpty {
            let noRemotes = NSMenuItem(title: "  None configured", action: nil, keyEquivalent: "")
            noRemotes.isEnabled = false
            menu.addItem(noRemotes)
        } else {
            for remote in relay.remotes {
                let item = NSMenuItem(title: "  \(remote)", action: nil, keyEquivalent: "")
                item.isEnabled = false
                menu.addItem(item)
            }
        }

        menu.addItem(.separator())

        // Launch at login
        let launchItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
        launchItem.target = self
        launchItem.state = UserDefaults.standard.bool(forKey: "launchAtLogin") ? .on : .off
        menu.addItem(launchItem)

        menu.addItem(.separator())

        // Update
        if updater.updateAvailable {
            let updateItem = NSMenuItem(title: "Update to v\(updater.latestVersion ?? "?")", action: #selector(performUpdate), keyEquivalent: "u")
            updateItem.target = self
            menu.addItem(updateItem)
        } else {
            let versionItem = NSMenuItem(title: "v\(updater.currentVersion) ✓", action: #selector(checkForUpdates), keyEquivalent: "")
            versionItem.target = self
            menu.addItem(versionItem)
        }

        menu.addItem(.separator())

        // Quit
        menu.addItem(NSMenuItem(title: "Quit Herdi", action: #selector(quitApp), keyEquivalent: "q"))

        self.statusItem?.menu = menu
    }

    @objc private func switchToDirect() {
        relay.startDirect()
        rebuildMenu()
    }

    @objc private func switchToRelay() {
        relay.connectRelay(to: relay.hostAddress)
        rebuildMenu()
    }

    @objc private func toggleLaunchAtLogin() {
        let current = UserDefaults.standard.bool(forKey: "launchAtLogin")
        let newValue = !current
        do {
            if newValue {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            UserDefaults.standard.set(newValue, forKey: "launchAtLogin")
        } catch {}
        rebuildMenu()
    }

    @objc private func checkForUpdates() {
        Updater.shared.lastCheck = nil
        Updater.shared.checkForUpdates()
    }

    @objc private func performUpdate() {
        Updater.shared.performUpdate()
    }

    @objc private func quitApp() {
        NSApplication.shared.terminate(nil)
    }

    /// Watch for agents transitioning to blocked state and auto-expand the panel
    private func observeBlockedAgents() {
        Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                let blocked = self.relay.agents.filter { $0.status == .blocked }

                // Update status item icon
                if let button = self.statusItem?.button {
                    if !blocked.isEmpty {
                        button.image = NSImage(systemSymbolName: "exclamationmark.circle.fill", accessibilityDescription: "Blocked")
                        button.image?.size = NSSize(width: 16, height: 16)
                        button.contentTintColor = .systemRed
                    } else {
                        button.image = NSImage(systemSymbolName: self.relay.isConnected ? "circle.fill" : "circle", accessibilityDescription: "Herdi")
                        button.image?.size = NSSize(width: 14, height: 14)
                        button.contentTintColor = nil
                    }
                }

                // Auto-pop the approval card if panel is collapsed and there's a blocked agent
                if let agent = blocked.first, self.panelController?.surface == .collapsed {
                    withAnimation(NotchAnimation.pop) {
                        self.panelController?.surface = .approval(agentId: agent.id)
                    }
                }

                // Rebuild menu every 5s for fresh status
                self.rebuildMenu()
            }
        }
    }
}
