import SwiftUI

struct MenuBarPanel: View {
    let relay: RelayConnection
    @Binding var launchAtLogin: Bool
    @State private var selectedAgent: Agent?
    @State private var showSettings = false
    private let updater = Updater.shared

    private var blocked: [Agent] { relay.agents.filter { $0.status == .blocked } }
    private var working: [Agent] { relay.agents.filter { $0.status == .working } }
    private var idle: [Agent] { relay.agents.filter { $0.status == .idle || $0.status == .unknown } }

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Circle().fill(relay.isConnected ? .green : .red).frame(width: 6, height: 6)
                Text("herdi").font(.headline)
                Spacer()
                Text("\(relay.agents.count) agents").font(.caption).foregroundStyle(.secondary)
                Button { showSettings.toggle() } label: {
                    Image(systemName: "gear").font(.caption)
                }.buttonStyle(.plain)
            }
            .padding(.horizontal, 12).padding(.vertical, 8)

            Divider()

            if showSettings {
                SettingsPanel(relay: relay, launchAtLogin: $launchAtLogin, updater: updater)
            } else if let agent = selectedAgent {
                ApprovalPanel(agent: agent, relay: relay) { selectedAgent = nil }
            } else {
                // Agent list
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        if !blocked.isEmpty { section("Blocked", .red, blocked) }
                        if !working.isEmpty { section("Working", .green, working) }
                        if !idle.isEmpty { section("Idle", .gray, idle) }
                        if relay.agents.isEmpty {
                            VStack(spacing: 8) {
                                Text(relay.isConnected ? "No agents running" : "Connecting…")
                                    .foregroundStyle(.secondary)
                                Text("Mode: \(relay.mode.rawValue)")
                                    .font(.caption2).foregroundStyle(.tertiary)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                        }
                    }
                    .padding(12)
                }
            }

            Divider()

            // Footer
            HStack(spacing: 8) {
                if let status = updater.status {
                    Text(status).font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                if updater.updateAvailable {
                    Button("Update") { updater.performUpdate() }
                        .font(.caption).disabled(updater.isUpdating)
                }
                Button("Quit") { NSApplication.shared.terminate(nil) }
                    .buttonStyle(.plain).font(.caption)
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
        }
        .onAppear { updater.checkForUpdates() }
    }

    private func section(_ title: String, _ color: Color, _ agents: [Agent]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Circle().fill(color).frame(width: 6, height: 6)
                Text(title).font(.caption).foregroundStyle(.secondary)
            }
            ForEach(agents) { agent in
                AgentRow(agent: agent)
                    .onTapGesture {
                        if agent.status == .blocked { selectedAgent = agent }
                    }
            }
        }
    }
}

// MARK: - Settings

struct SettingsPanel: View {
    let relay: RelayConnection
    @Binding var launchAtLogin: Bool
    let updater: Updater
    @State private var relayURL = "ws://127.0.0.1:8375"
    @State private var newRemote = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // Connection
                GroupBox("Connection") {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("Mode").font(.caption)
                            Spacer()
                            Picker("", selection: Binding(
                                get: { relay.mode },
                                set: { newMode in
                                    if newMode == .direct { relay.startDirect() }
                                    else { relay.connectRelay(to: relayURL) }
                                }
                            )) {
                                ForEach(RelayConnection.ConnectionMode.allCases, id: \.self) { mode in
                                    Text(mode.rawValue).tag(mode)
                                }
                            }
                            .pickerStyle(.menu)
                            .frame(width: 180)
                        }
                        HStack {
                            Text("Status").font(.caption)
                            Spacer()
                            Circle().fill(relay.isConnected ? .green : .red).frame(width: 6, height: 6)
                            Text(relay.isConnected ? "Connected" : "Disconnected")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if relay.mode == .relay {
                            HStack {
                                TextField("ws://host:8375", text: $relayURL)
                                    .textFieldStyle(.roundedBorder).font(.caption)
                                Button("Connect") { relay.connectRelay(to: relayURL) }
                                    .font(.caption)
                            }
                        }
                        if relay.mode == .direct {
                            Text("Polling herdr CLI every 2s")
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    .padding(4)
                }

                // Remote Hosts
                GroupBox("Remote Hosts (SSH)") {
                    VStack(alignment: .leading, spacing: 8) {
                        if relay.remotes.isEmpty {
                            Text("No remotes configured").font(.caption2).foregroundStyle(.tertiary)
                        }
                        ForEach(relay.remotes, id: \.self) { remote in
                            HStack {
                                Image(systemName: "server.rack").font(.caption2)
                                Text(remote).font(.caption)
                                Spacer()
                                Button { relay.removeRemote(remote) } label: {
                                    Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                                }.buttonStyle(.plain)
                            }
                        }
                        HStack {
                            TextField("user@host", text: $newRemote)
                                .textFieldStyle(.roundedBorder).font(.caption)
                            Button("Add") {
                                relay.addRemote(newRemote)
                                newRemote = ""
                            }
                            .font(.caption).disabled(newRemote.isEmpty)
                        }
                        Text("Requires SSH key auth (no password)")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                    .padding(4)
                }

                // General
                GroupBox("General") {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("Launch at Login", isOn: $launchAtLogin)
                            .toggleStyle(.switch).controlSize(.small)
                    }
                    .padding(4)
                }

                // Updates
                GroupBox("Updates") {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("Current: v\(updater.currentVersion)").font(.caption)
                            Spacer()
                            if updater.updateAvailable {
                                Text("v\(updater.latestVersion ?? "?") available").font(.caption).foregroundStyle(.green)
                            }
                        }
                        HStack {
                            if updater.updateAvailable {
                                Button("Install Update") { updater.performUpdate() }
                                    .disabled(updater.isUpdating)
                            }
                            Spacer()
                            Button("Check Now") { updater.lastCheck = nil; updater.checkForUpdates() }
                                .font(.caption).disabled(updater.isChecking)
                        }
                        if let status = updater.status {
                            Text(status).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .padding(4)
                }
            }
            .padding(12)
        }
    }
}

// MARK: - Agent Row

struct AgentRow: View {
    let agent: Agent

    private var color: Color {
        switch agent.status {
        case .blocked: .red
        case .working: .green
        case .idle, .unknown: .gray
        }
    }

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(agent.project.isEmpty ? agent.name : agent.project)
                    .font(.body)
                HStack(spacing: 4) {
                    Text(agent.name).font(.caption2).foregroundStyle(.secondary)
                    if agent.host != "local" {
                        Text("@\(agent.host)").font(.caption2).foregroundStyle(.orange)
                    }
                }
            }
            Spacer()
            if agent.status == .blocked {
                Image(systemName: "exclamationmark.bubble.fill").foregroundStyle(.red).font(.caption)
            }
        }
        .padding(.vertical, 4).padding(.horizontal, 8)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
    }
}

// MARK: - Approval Panel

struct ApprovalPanel: View {
    let agent: Agent
    let relay: RelayConnection
    let onDismiss: () -> Void
    @State private var customResponse = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Button { onDismiss() } label: {
                    Image(systemName: "chevron.left")
                }
                .buttonStyle(.plain)
                Text("\(agent.name) — \(agent.project)").font(.headline)
                Spacer()
            }
            .padding(.horizontal, 12).padding(.top, 8)

            ScrollView {
                Text(agent.prompt ?? "Waiting…")
                    .font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
            .padding(.horizontal, 12)

            if let options = agent.options {
                VStack(spacing: 6) {
                    ForEach(options, id: \.self) { option in
                        Button { respond(option) } label: {
                            Text(option).frame(maxWidth: .infinity)
                        }
                        .controlSize(.regular)
                        .buttonStyle(.borderedProminent)
                        .tint(tint(for: option))
                    }
                }
                .padding(.horizontal, 12)
            }

            HStack {
                TextField("Custom response…", text: $customResponse)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { if !customResponse.isEmpty { respond(customResponse) } }
                Button("Send") { respond(customResponse) }
                    .disabled(customResponse.isEmpty)
            }
            .padding(.horizontal, 12).padding(.bottom, 8)
        }
    }

    private func respond(_ text: String) {
        relay.send(response: ResponseMessage(pane_id: agent.id, text: text))
        agent.status = .working
        agent.prompt = nil
        agent.options = nil
        onDismiss()
    }

    private func tint(for option: String) -> Color {
        if option.contains("yes") || option.contains("approve") { return .green }
        if option.contains("no") || option.contains("exit") || option.contains("cancel") { return .red }
        return .accentColor
    }
}
