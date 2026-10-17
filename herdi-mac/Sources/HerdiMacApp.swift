import SwiftUI
import ServiceManagement
import UserNotifications
import Carbon.HIToolbox

@main
struct HerdiApp: App {
    @NSApplicationDelegateAdaptor(HerdiAppDelegate.self) var appDelegate

    var body: some Scene {
        // No visible window — the panel IS the UI. macOS 26 opens a lone Settings scene at
        // launch, which showed an empty "Herdi Settings" window, so it is suppressed. macOS 14
        // cannot suppress it, so it gets no scene. The panel and the status item are AppKit.
        if #available(macOS 15.0, *) {
            Settings { EmptyView() }.defaultLaunchBehavior(.suppressed)
        }
    }
}

@MainActor
class HerdiAppDelegate: NSObject, NSApplicationDelegate {
    var panelController: PanelWindowController?
    private var cardShortcut: GlobalShortcut?
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

        // ⌃⌘H gives an open card the keyboard, for a card that opened while you typed elsewhere.
        // Not ⌃⌥: that is the VoiceOver modifier, and VO-H opens the VoiceOver Help menu.
        cardShortcut = GlobalShortcut(keyCode: kVK_ANSI_H, modifiers: controlKey | cmdKey) { [weak self] in
            MainActor.assumeIsolated { self?.panelController?.takeKeyboard(askedByShortcut: true) }
        }
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        applyStatusIcon(blocked: false)
        rebuildMenu()
    }

    /// The status glyph stays a circle in every state, because that is how you find Herdi in a
    /// row of menu bar icons. Muted and presenting vary the circle -- slashed, dashed -- rather
    /// than swapping in a speaker or an eye, which read as some other app entirely.
    private func applyStatusIcon(blocked: Bool) {
        guard let button = statusItem?.button else { return }

        let symbol: String
        let label: String
        let tint: NSColor?
        let size: CGFloat

        if blocked {
            symbol = "exclamationmark.circle.fill"
            label = "Herdi: agent blocked"
            tint = .systemRed
            size = 16
        } else if Quiet.presentationMode {
            symbol = "circle.dashed"
            label = "Herdi: presentation mode"
            // nil, not a grey: a fixed colour overrides the menu bar's template rendering and
            // paints dark-on-dark. The dashed ring is the signal; the colour stays the default.
            tint = nil
            size = 14
        } else if !Quiet.soundEnabled {
            symbol = relay.isConnected ? "circle.slash.fill" : "circle.slash"
            label = "Herdi: sound off"
            tint = nil
            size = 14
        } else {
            symbol = relay.isConnected ? "circle.fill" : "circle"
            label = relay.isConnected ? "Herdi: connected" : "Herdi: disconnected"
            tint = nil
            size = 14
        }

        // A missing symbol would leave the menu bar item invisible, so never ship without one.
        button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: label)
            ?? NSImage(systemSymbolName: "circle.fill", accessibilityDescription: label)
        button.image?.size = NSSize(width: size, height: size)
        button.contentTintColor = tint
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

        // Interruptions
        let soundItem = NSMenuItem(title: "Sound", action: #selector(toggleSound), keyEquivalent: "")
        soundItem.target = self
        soundItem.state = Quiet.soundEnabled ? .on : .off
        soundItem.image = NSImage(
            systemSymbolName: Quiet.soundEnabled ? "speaker.wave.2.fill" : "speaker.slash.fill",
            accessibilityDescription: Quiet.soundEnabled ? "Sound on" : "Sound off"
        )
        menu.addItem(soundItem)

        let testItem = NSMenuItem(title: "Test Notification", action: #selector(sendTestNotification), keyEquivalent: "")
        testItem.target = self
        testItem.image = NSImage(systemSymbolName: "bell.badge", accessibilityDescription: nil)
        menu.addItem(testItem)

        let presentItem = NSMenuItem(title: "Presentation Mode", action: #selector(togglePresentationMode), keyEquivalent: "p")
        presentItem.target = self
        presentItem.state = Quiet.presentationMode ? .on : .off
        presentItem.image = NSImage(
            systemSymbolName: Quiet.presentationMode ? "eye.slash.fill" : "eye",
            accessibilityDescription: nil
        )
        menu.addItem(presentItem)

        if Quiet.presentationMode {
            let paused = NSMenuItem(title: "Panel stays closed until you open it", action: nil, keyEquivalent: "")
            paused.isEnabled = false
            menu.addItem(paused)
        }
        menu.addItem(textSizeItem())

        menu.addItem(.separator())

        // Launch at login
        let launchItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
        launchItem.target = self
        launchItem.state = UserDefaults.standard.bool(forKey: "launchAtLogin") ? .on : .off
        menu.addItem(launchItem)

        let focusOn = UserDefaults.standard.bool(forKey: approvalFocusKey)
        let focusItem = NSMenuItem(title: "Give Approvals Keyboard Focus", action: #selector(toggleApprovalFocus), keyEquivalent: "")
        focusItem.target = self
        focusItem.state = focusOn ? .on : .off
        menu.addItem(focusItem)

        if focusOn {
            let typing = NSMenuItem(title: "Not while you are typing elsewhere", action: nil, keyEquivalent: "")
            typing.isEnabled = false
            menu.addItem(typing)
        }
        let shortcut = NSMenuItem(title: "⌃⌘H gives an open approval focus", action: nil, keyEquivalent: "")
        shortcut.isEnabled = false
        menu.addItem(shortcut)

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

    @objc private func sendTestNotification() {
        relay.sendTestNotification()
    }

    @objc private func toggleSound() {
        Quiet.soundEnabled.toggle()
        refreshChrome()
    }

    @objc private func togglePresentationMode() {
        Quiet.presentationMode.toggle()
        refreshChrome()
    }

    /// Repaint immediately rather than waiting up to a second for the next poll.
    private func refreshChrome() {
        applyStatusIcon(blocked: relay.agents.contains { $0.status == .blocked })
        rebuildMenu()
    }

    // MARK: - Text size

    private func textSizeItem() -> NSMenuItem {
        let scale = TextSize.current
        let item = NSMenuItem(title: "Text Size: \(TextSize.percent(scale))", action: nil, keyEquivalent: "")
        item.image = NSImage(systemSymbolName: "textformat.size", accessibilityDescription: nil)

        let submenu = NSMenu()
        let larger = NSMenuItem(title: "Larger", action: #selector(increaseTextSize), keyEquivalent: "=")
        larger.target = self
        submenu.addItem(larger)

        let smaller = NSMenuItem(title: "Smaller", action: #selector(decreaseTextSize), keyEquivalent: "-")
        smaller.target = self
        submenu.addItem(smaller)

        submenu.addItem(.separator())

        for step in TextSize.steps {
            let preset = NSMenuItem(title: TextSize.percent(step), action: #selector(selectTextSize(_:)), keyEquivalent: "")
            preset.target = self
            preset.representedObject = step
            preset.state = step == scale ? .on : .off
            submenu.addItem(preset)
        }

        item.submenu = submenu
        return item
    }

    @objc private func increaseTextSize() {
        guard let next = TextSize.larger else { return }
        applyTextSize(next)
    }

    @objc private func decreaseTextSize() {
        guard let previous = TextSize.smaller else { return }
        applyTextSize(previous)
    }

    @objc private func selectTextSize(_ sender: NSMenuItem) {
        guard let step = sender.representedObject as? Double else { return }
        applyTextSize(step)
    }

    private func applyTextSize(_ scale: Double) {
        TextSize.current = scale
        panelController?.reloadLayout()
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

    @objc private func toggleApprovalFocus() {
        let current = UserDefaults.standard.bool(forKey: approvalFocusKey)
        UserDefaults.standard.set(!current, forKey: approvalFocusKey)
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
            Task { @MainActor [weak self] in
                guard let self else { return }
                let blocked = self.relay.agents.filter { $0.status == .blocked }

                self.applyStatusIcon(blocked: !blocked.isEmpty)

                if let controller = self.panelController {
                    let blockedIds = Set(blocked.map(\.id))

                    // An agent that has stopped asking should not leave its card on screen. This
                    // is what strands people: the prompt gets answered in the terminal, the card
                    // stays up showing output that has since moved on, and because approval cards
                    // ignored both dismissal paths there was no way out of it.
                    if case .approval(let shownId) = controller.surface, !blockedIds.contains(shownId) {
                        controller.collapse(dismissing: nil, prompt: nil)
                        // The card leaves on its own, so say why: a VoiceOver user would otherwise
                        // find it gone with no reason given.
                        NSAccessibility.post(
                            element: NSApp as Any,
                            notification: .announcementRequested,
                            userInfo: [
                                .announcement: "Approval card closed. The agent is no longer waiting.",
                                .priority: NSAccessibilityPriorityLevel.high.rawValue,
                            ]
                        )
                    }

                    controller.dismissedBlocked = controller.dismissedBlocked.filter { blockedIds.contains($0.key) }

                    // Auto-pop for a blocked agent you have not already waved away. With
                    // the setting on, the card also takes the keyboard, so the answer is one
                    // shortcut away rather than a click first.
                    if controller.surface == .collapsed, Quiet.shouldAutoExpand,
                       let agent = blocked.first(where: { controller.dismissedBlocked[$0.id] != ($0.prompt ?? "") }) {
                        withAnimation(NotchAnimation.pop) {
                            controller.surface = .approval(agentId: agent.id)
                        }
                        controller.takeKeyboard()
                    }
                }

                // Rebuild menu every 5s for fresh status
                self.rebuildMenu()
            }
        }
    }
}
