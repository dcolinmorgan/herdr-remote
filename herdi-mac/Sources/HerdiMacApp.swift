import SwiftUI
import ServiceManagement

@main
struct HerdiApp: App {
    @State private var relay = RelayConnection()
    @State private var panelController = NotchPanelController()
    @AppStorage("launchAtLogin") private var launchAtLogin = false

    var body: some Scene {
        // Minimal menu bar icon as a fallback / quit target
        MenuBarExtra {
            VStack(spacing: 8) {
                Button("Show Notch Panel") { panelController.expand() }
                Divider()
                Button("Quit Herdi") { NSApplication.shared.terminate(nil) }
            }
            .padding(8)
        } label: {
            let blocked = relay.agents.filter { $0.status == .blocked }.count
            if blocked > 0 {
                Label("\(blocked)", systemImage: "exclamationmark.circle.fill")
            } else {
                Image(systemName: relay.isConnected ? "circle.fill" : "circle")
            }
        }
        .menuBarExtraStyle(.menu)
        .onChange(of: launchAtLogin) { _, newValue in
            do {
                if newValue {
                    try SMAppService.mainApp.register()
                } else {
                    try SMAppService.mainApp.unregister()
                }
            } catch {
                launchAtLogin = !newValue
            }
        }
    }

    init() {
        // Launch the notch panel on startup
        DispatchQueue.main.async {
            panelController.setup(with: relay)
        }
    }
}
