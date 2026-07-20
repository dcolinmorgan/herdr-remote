import SwiftUI

/// The content view that lives inside the notch panel.
/// Two states: collapsed pill (agent count + status dots) and expanded card (full agent list + approvals).
struct NotchContentView: View {
    let relay: RelayConnection
    let controller: NotchPanelController
    @State private var selectedAgent: Agent?
    @State private var showSettings = false
    @State private var hovered = false

    private var blocked: [Agent] { relay.agents.filter { $0.status == .blocked } }
    private var working: [Agent] { relay.agents.filter { $0.status == .working } }
    private var idle: [Agent] { relay.agents.filter { $0.status == .idle || $0.status == .unknown } }

    var body: some View {
        Group {
            if controller.isExpanded {
                expandedView
            } else {
                collapsedPill
            }
        }
        .clipShape(NotchShape(cornerRadius: controller.isExpanded ? 16 : 14))
        .onHover { hovering in
            hovered = hovering
            if !hovering && controller.isExpanded && selectedAgent == nil && !showSettings {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                    if !hovered { controller.collapse() }
                }
            }
        }
    }

    // MARK: - Collapsed Pill

    private var collapsedPill: some View {
        HStack(spacing: 6) {
            // Connection indicator
            Circle()
                .fill(relay.isConnected ? Color.green : Color.red)
                .frame(width: 6, height: 6)
                .shadow(color: relay.isConnected ? .green.opacity(0.5) : .red.opacity(0.5), radius: 3)

            // Agent status summary
            if !blocked.isEmpty {
                HStack(spacing: 3) {
                    Image(systemName: "exclamationmark.circle.fill")
                        .font(.system(size: 10))
                        .foregroundStyle(.red)
                        .symbolEffect(.pulse, isActive: true)
                    Text("\(blocked.count)")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundStyle(.red)
                }
                .transition(.scale.combined(with: .opacity))
            }

            if !working.isEmpty {
                HStack(spacing: 3) {
                    PulsingDot(color: .green)
                    Text("\(working.count)")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundStyle(.primary)
                }
                .transition(.scale.combined(with: .opacity))
            }

            if relay.agents.isEmpty {
                Text("herdr")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
            } else {
                Text("·")
                    .foregroundStyle(.tertiary)
                Text("\(relay.agents.count)")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            // Chevron hint
            Image(systemName: "chevron.down")
                .font(.system(size: 8, weight: .bold))
                .foregroundStyle(.tertiary)
                .opacity(hovered ? 1 : 0)
                .animation(.easeOut(duration: 0.2), value: hovered)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.ultraThinMaterial)
        .contentShape(Rectangle())
        .onTapGesture { controller.expand() }
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: relay.agents.count)
    }

    // MARK: - Expanded View

    private var expandedView: some View {
        VStack(spacing: 0) {
            // Header bar (still looks like the pill, acts as collapse handle)
            expandedHeader
                .onTapGesture { controller.collapse() }

            Divider().opacity(0.3)

            // Content area
            if showSettings {
                NotchSettingsView(relay: relay, onDismiss: { showSettings = false })
            } else if let agent = selectedAgent {
                NotchApprovalView(agent: agent, relay: relay) {
                    selectedAgent = nil
                }
            } else {
                agentListView
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.ultraThinMaterial)
    }

    private var expandedHeader: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(relay.isConnected ? Color.green : Color.red)
                .frame(width: 6, height: 6)

            Text("herdr")
                .font(.system(size: 12, weight: .semibold))

            Spacer()

            if !blocked.isEmpty {
                Label("\(blocked.count) blocked", systemImage: "exclamationmark.circle.fill")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.red)
            }

            Text("\(relay.agents.count) agents")
                .font(.system(size: 10))
                .foregroundStyle(.secondary)

            Button { showSettings.toggle() } label: {
                Image(systemName: "gear")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)

            Image(systemName: "chevron.up")
                .font(.system(size: 8, weight: .bold))
                .foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }

    // MARK: - Agent List

    private var agentListView: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 8) {
                // Blocked agents hoisted to top (like Vibe Island)
                if !blocked.isEmpty {
                    NotchSectionHeader(title: "Needs you", color: .red, count: blocked.count)
                    ForEach(blocked) { agent in
                        NotchAgentCard(agent: agent, relay: relay, style: .blocked)
                            .onTapGesture { selectedAgent = agent }
                    }
                }

                if !working.isEmpty {
                    NotchSectionHeader(title: "Working", color: .green, count: working.count)
                    ForEach(working) { agent in
                        NotchAgentCard(agent: agent, relay: relay, style: .working)
                    }
                }

                if !idle.isEmpty {
                    NotchSectionHeader(title: "Idle", color: .gray, count: idle.count)
                    ForEach(idle) { agent in
                        NotchAgentCard(agent: agent, relay: relay, style: .idle)
                    }
                }

                if relay.agents.isEmpty {
                    VStack(spacing: 8) {
                        Image(systemName: relay.isConnected ? "checkmark.circle" : "wifi.slash")
                            .font(.title2)
                            .foregroundStyle(.tertiary)
                        Text(relay.isConnected ? "No agents running" : "Connecting…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
    }
}

// MARK: - Section Header

struct NotchSectionHeader: View {
    let title: String
    let color: Color
    let count: Int

    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 5, height: 5)
            Text(title)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary)
            Spacer()
            Text("\(count)")
                .font(.system(size: 9, design: .monospaced))
                .foregroundStyle(.tertiary)
        }
        .padding(.top, 4)
    }
}

// MARK: - Agent Card

enum AgentCardStyle { case blocked, working, idle }

struct NotchAgentCard: View {
    let agent: Agent
    let relay: RelayConnection
    let style: AgentCardStyle
    @State private var hovered = false

    private var accentColor: Color {
        switch style {
        case .blocked: .red
        case .working: .green
        case .idle: .gray
        }
    }

    var body: some View {
        HStack(spacing: 8) {
            // Status indicator
            RoundedRectangle(cornerRadius: 2)
                .fill(accentColor)
                .frame(width: 3, height: 24)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(agent.name)
                        .font(.system(size: 11, weight: .medium))
                    if !agent.project.isEmpty {
                        Text("·").foregroundStyle(.tertiary)
                        Text(agent.project)
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                if style == .blocked, let prompt = agent.prompt {
                    Text(prompt.components(separatedBy: .newlines).last ?? "Waiting…")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }

            Spacer()

            // Quick actions (visible on hover or always for blocked)
            if style == .blocked || hovered {
                HStack(spacing: 4) {
                    if style == .blocked {
                        Button {
                            relay.send(response: ResponseMessage(pane_id: agent.id, text: "yes, single permission"))
                        } label: {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 14))
                                .foregroundStyle(.green)
                        }
                        .buttonStyle(.plain)
                        .help("Allow")
                    }

                    Button { relay.focusPane(agent.id) } label: {
                        Image(systemName: "arrow.up.right.square")
                            .font(.system(size: 12))
                            .foregroundStyle(.blue)
                    }
                    .buttonStyle(.plain)
                    .help("Jump to terminal")
                }
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(hovered ? Color.primary.opacity(0.06) : Color.primary.opacity(0.03))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(style == .blocked ? accentColor.opacity(0.3) : Color.clear, lineWidth: 1)
        )
        .contentShape(Rectangle())
        .onHover { hovered = $0 }
    }
}

// MARK: - Approval View (inline in notch)

struct NotchApprovalView: View {
    let agent: Agent
    let relay: RelayConnection
    let onDismiss: () -> Void
    @State private var customResponse = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Back + agent info
            HStack(spacing: 6) {
                Button { onDismiss() } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 10, weight: .bold))
                }
                .buttonStyle(.plain)

                Text(agent.name)
                    .font(.system(size: 11, weight: .semibold))
                Text("·").foregroundStyle(.tertiary)
                Text(agent.project)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                Spacer()

                Button { relay.interruptPane(agent.id) } label: {
                    Image(systemName: "stop.circle.fill")
                        .foregroundStyle(.red)
                        .font(.system(size: 12))
                }
                .buttonStyle(.plain)
                .help("Interrupt (^C)")
            }
            .padding(.horizontal, 14)
            .padding(.top, 6)

            // Prompt / diff content
            ScrollView {
                VStack(alignment: .leading, spacing: 1) {
                    if let prompt = agent.prompt {
                        ForEach(Array(prompt.components(separatedBy: .newlines).enumerated()), id: \.offset) { _, line in
                            DiffLine(text: line)
                        }
                    } else {
                        Text("Waiting for input…")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.tertiary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .textSelection(.enabled)
            }
            .frame(maxHeight: 180)
            .background(Color.black.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.primary.opacity(0.08), lineWidth: 0.5)
            )
            .padding(.horizontal, 12)

            // Option buttons (like Vibe Island's clickable choices)
            if let options = agent.options {
                HStack(spacing: 6) {
                    ForEach(options, id: \.self) { option in
                        Button { respond(option) } label: {
                            Text(shortLabel(option))
                                .font(.system(size: 10, weight: .medium))
                                .lineLimit(1)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 5)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(tint(for: option))
                        .controlSize(.small)
                    }
                }
                .padding(.horizontal, 12)
            }

            // Custom response
            HStack(spacing: 6) {
                TextField("Reply…", text: $customResponse)
                    .textFieldStyle(.plain)
                    .font(.system(size: 11))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
                    .onSubmit { if !customResponse.isEmpty { respond(customResponse) } }

                Button { respond(customResponse) } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 16))
                        .foregroundStyle(.blue)
                }
                .buttonStyle(.plain)
                .disabled(customResponse.isEmpty)
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 10)
        }
    }

    private func respond(_ text: String) {
        relay.send(response: ResponseMessage(pane_id: agent.id, text: text))
        agent.status = .working
        agent.prompt = nil
        agent.options = nil
        onDismiss()
    }

    private func shortLabel(_ option: String) -> String {
        if option.contains("single permission") { return "Allow" }
        if option.contains("always allow") { return "Trust" }
        if option.contains("tab to edit") || option.contains("no") { return "Deny" }
        if option.contains("approve all") { return "Approve All" }
        if option.contains("exit") || option.contains("cancel") { return "Cancel" }
        return String(option.prefix(12))
    }

    private func tint(for option: String) -> Color {
        if option.contains("yes") || option.contains("approve") || option.contains("single") { return .green }
        if option.contains("no") || option.contains("exit") || option.contains("cancel") { return .red }
        if option.contains("trust") || option.contains("always") { return .blue }
        return .accentColor
    }
}

// MARK: - Settings (compact for notch)

struct NotchSettingsView: View {
    let relay: RelayConnection
    let onDismiss: () -> Void
    @AppStorage("launchAtLogin") private var launchAtLogin = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Button { onDismiss() } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 10, weight: .bold))
                }
                .buttonStyle(.plain)
                Text("Settings")
                    .font(.system(size: 11, weight: .semibold))
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.top, 6)

            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    // Mode
                    HStack {
                        Text("Mode").font(.system(size: 10)).foregroundStyle(.secondary)
                        Spacer()
                        Text(relay.mode.rawValue)
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.primary)
                    }

                    // Connection status
                    HStack {
                        Text("Status").font(.system(size: 10)).foregroundStyle(.secondary)
                        Spacer()
                        Circle().fill(relay.isConnected ? .green : .red).frame(width: 6, height: 6)
                        Text(relay.isConnected ? "Connected" : "Disconnected")
                            .font(.system(size: 10))
                    }

                    Divider().opacity(0.3)

                    // Launch at login
                    Toggle("Launch at Login", isOn: $launchAtLogin)
                        .toggleStyle(.switch)
                        .controlSize(.mini)
                        .font(.system(size: 10))

                    Divider().opacity(0.3)

                    // Remotes
                    Text("Remote Hosts").font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                    if relay.remotes.isEmpty {
                        Text("None configured").font(.system(size: 9)).foregroundStyle(.tertiary)
                    }
                    ForEach(relay.remotes, id: \.self) { remote in
                        HStack {
                            Text(remote).font(.system(size: 10, design: .monospaced))
                            Spacer()
                            Button { relay.removeRemote(remote) } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .font(.system(size: 10))
                                    .foregroundStyle(.red)
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    Divider().opacity(0.3)

                    // Quit
                    Button("Quit Herdi") {
                        NSApplication.shared.terminate(nil)
                    }
                    .font(.system(size: 10))
                    .foregroundStyle(.red)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 6)
            }
        }
    }
}

// MARK: - Custom Shapes

/// A shape that mimics the macOS notch with rounded bottom corners
struct NotchShape: Shape {
    var cornerRadius: CGFloat

    var animatableData: CGFloat {
        get { cornerRadius }
        set { cornerRadius = newValue }
    }

    func path(in rect: CGRect) -> Path {
        Path(roundedRect: rect, cornerRadius: cornerRadius, style: .continuous)
    }
}

// MARK: - Pulsing Dot

struct PulsingDot: View {
    let color: Color
    @State private var pulse = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 6, height: 6)
            .scaleEffect(pulse ? 1.3 : 1.0)
            .opacity(pulse ? 0.7 : 1.0)
            .animation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true), value: pulse)
            .onAppear { pulse = true }
    }
}

// MARK: - Diff Line (syntax-colored like Vibe Island)

struct DiffLine: View {
    let text: String

    private var lineType: LineType {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("+") && !trimmed.hasPrefix("+++") { return .added }
        if trimmed.hasPrefix("-") && !trimmed.hasPrefix("---") { return .removed }
        if trimmed.hasPrefix("@@") { return .hunk }
        return .context
    }

    private enum LineType {
        case added, removed, hunk, context
    }

    var body: some View {
        Text(text)
            .font(.system(size: 10, design: .monospaced))
            .foregroundStyle(foregroundColor)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 4)
            .padding(.vertical, 1)
            .background(backgroundColor)
    }

    private var foregroundColor: Color {
        switch lineType {
        case .added: .green
        case .removed: .red
        case .hunk: .cyan.opacity(0.8)
        case .context: .primary.opacity(0.75)
        }
    }

    private var backgroundColor: Color {
        switch lineType {
        case .added: .green.opacity(0.1)
        case .removed: .red.opacity(0.1)
        case .hunk: .cyan.opacity(0.05)
        case .context: .clear
        }
    }
}
