import SwiftUI

// MARK: - Hover Interaction State Machine

enum HoverPhase {
    case collapsed, prehover, expanded
}

private enum HoverTiming {
    static let expandDelay: TimeInterval = 0.45
    static let collapseDelay: TimeInterval = 0.5
    static let prehoverWidthDelta: CGFloat = 6
    static let prehoverScale: CGFloat = 1.003
}

// MARK: - NotchPanelView (root view inside the panel)

struct NotchPanelView: View {
    let relay: RelayConnection
    @ObservedObject var controller: PanelWindowController
    let hasNotch: Bool
    let notchHeight: CGFloat
    let notchW: CGFloat
    let screenWidth: CGFloat

    @State private var hoverPhase: HoverPhase = .collapsed
    @State private var hoverTimer: Timer?
    @State private var isHovered = false

    private var blocked: [Agent] { relay.agents.filter { $0.status == .blocked } }
    private var working: [Agent] { relay.agents.filter { $0.status == .working } }
    private var idle: [Agent] { relay.agents.filter { $0.status == .idle || $0.status == .unknown } }
    private var isActive: Bool { !relay.agents.isEmpty }

    private var shouldShowExpanded: Bool {
        controller.surface.isExpanded
    }

    /// Panel width adapts to state
    private var panelWidth: CGFloat {
        let maxWidth = min(580, screenWidth - 40)
        if !isActive { return notchW + 60 }
        if shouldShowExpanded { return maxWidth }
        // Collapsed: notch width + wings for status indicators
        let wing: CGFloat = 50
        let blockedExtra: CGFloat = blocked.isEmpty ? 0 : 20
        let prehoverExtra: CGFloat = hoverPhase == .prehover ? HoverTiming.prehoverWidthDelta : 0
        return notchW + wing * 2 + blockedExtra + prehoverExtra
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 0) {
                // Compact bar (always present, sits at notch height)
                if isActive {
                    CompactBar(
                        relay: relay,
                        expanded: shouldShowExpanded,
                        notchHeight: notchHeight,
                        blocked: blocked,
                        working: working,
                        onShowUpdate: {
                            withAnimation(NotchAnimation.open) {
                                controller.surface = .sessionList
                            }
                        }
                    )
                    .frame(height: notchHeight)
                } else {
                    IdleBar(relay: relay, notchHeight: notchHeight)
                        .frame(height: notchHeight)
                }

                // Expanded content below notch
                if shouldShowExpanded {
                    Divider()
                        .background(.white.opacity(0.15))
                        .padding(.horizontal, 12)

                    expandedContent
                        .transition(.blurFade.combined(with: .move(edge: .top)))
                }
            }
            .frame(width: panelWidth)
            .clipped()
            .background(
                NotchPanelShape(
                    topExtension: shouldShowExpanded ? 14 : 3,
                    bottomRadius: shouldShowExpanded ? 24 : 12,
                    minHeight: notchHeight
                )
                .fill(.black)
            )
            .scaleEffect(hoverPhase == .prehover ? HoverTiming.prehoverScale : 1, anchor: .top)
            .contentShape(Rectangle())
            .onHover { hovering in handleHover(hovering) }

            Spacer()
                .allowsHitTesting(false)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .animation(NotchAnimation.open, value: controller.surface)
    }

    // MARK: - Expanded Content

    @ViewBuilder
    private var expandedContent: some View {
        switch controller.surface {
        case .approval(let agentId):
            if let agent = relay.agents.first(where: { $0.id == agentId }) {
                ApprovalCard(agent: agent, relay: relay) {
                    withAnimation(NotchAnimation.close) {
                        controller.surface = .collapsed
                    }
                }
                .transition(.blurFade.combined(with: .scale(scale: 0.96, anchor: .top)))
            }
        case .sessionList:
            SessionListContent(
                relay: relay,
                blocked: blocked,
                working: working,
                idle: idle,
                onSelectAgent: { agent in
                    withAnimation(NotchAnimation.pop) {
                        controller.surface = .approval(agentId: agent.id)
                    }
                },
                onJump: { relay.focusPane($0.id) }
            )
            .transition(.blurFade.combined(with: .move(edge: .top)))
        case .collapsed:
            EmptyView()
        }
    }

    // MARK: - Hover Logic

    private func handleHover(_ hovering: Bool) {
        // During approval interaction, don't collapse
        if case .approval = controller.surface { return }

        isHovered = hovering
        if hovering {
            // Immediate prehover acknowledgement
            withAnimation(NotchAnimation.micro) { hoverPhase = .prehover }
            // Delayed full expansion
            hoverTimer?.invalidate()
            hoverTimer = Timer.scheduledTimer(withTimeInterval: HoverTiming.expandDelay, repeats: false) { _ in
                Task { @MainActor in
                    guard isHovered else { return }
                    hoverPhase = .expanded
                    withAnimation(NotchAnimation.open) {
                        controller.surface = .sessionList
                    }
                }
            }
        } else {
            // Reverse prehover immediately
            withAnimation(NotchAnimation.micro) { hoverPhase = .collapsed }
            // Delayed collapse for grace period
            hoverTimer?.invalidate()
            hoverTimer = Timer.scheduledTimer(withTimeInterval: HoverTiming.collapseDelay, repeats: false) { _ in
                Task { @MainActor in
                    guard !isHovered else { return }
                    hoverPhase = .collapsed
                    withAnimation(NotchAnimation.close) {
                        controller.surface = .collapsed
                    }
                }
            }
        }
    }
}

// MARK: - Compact Bar (notch-level, always visible)

private struct CompactBar: View {
    let relay: RelayConnection
    let expanded: Bool
    let notchHeight: CGFloat
    let blocked: [Agent]
    let working: [Agent]
    let onShowUpdate: () -> Void
    private let updater = Updater.shared

    var body: some View {
        HStack(spacing: 6) {
            // Left wing: status dot + counts
            HStack(spacing: 5) {
                Circle()
                    .fill(relay.isConnected ? Color.green : Color.red)
                    .frame(width: 7, height: 7)
                    .shadow(color: relay.isConnected ? .green.opacity(0.6) : .red.opacity(0.6), radius: 3)

                if !blocked.isEmpty {
                    HStack(spacing: 2) {
                        Image(systemName: "exclamationmark.circle.fill")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.red)
                        Text("\(blocked.count)")
                            .font(.system(size: 11, weight: .bold, design: .monospaced))
                            .foregroundStyle(.red)
                    }
                }

                if !working.isEmpty && !expanded {
                    HStack(spacing: 2) {
                        PulsingDot(color: .green, size: 6)
                        Text("\(working.count)")
                            .font(.system(size: 11, weight: .medium, design: .monospaced))
                            .foregroundStyle(.white.opacity(0.8))
                    }
                }
            }
            .padding(.leading, 10)

            if expanded {
                Spacer()
                Text("herdr")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                Spacer()
            } else {
                Spacer()
            }

            // Right wing: update badge + agent count
            HStack(spacing: 4) {
                if updater.updateAvailable && !expanded {
                    Button(action: onShowUpdate) {
                        Image(systemName: "arrow.down.circle.fill")
                            .font(.system(size: 10))
                            .foregroundStyle(.cyan)
                    }
                    .buttonStyle(.plain)
                    .help("Show update")
                }
                if !expanded {
                    Text("\(relay.agents.count)")
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.4))
                }
            }
            .padding(.trailing, 10)
        }
        .onAppear { updater.checkForUpdates() }
    }
}

// MARK: - Idle Bar (no agents running)

private struct IdleBar: View {
    let relay: RelayConnection
    let notchHeight: CGFloat

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(relay.isConnected ? Color.green.opacity(0.5) : Color.red.opacity(0.5))
                .frame(width: 5, height: 5)
            Text("herdr")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(.white.opacity(0.3))
        }
    }
}

// MARK: - Session List (expanded content)

private struct SessionListContent: View {
    let relay: RelayConnection
    let blocked: [Agent]
    let working: [Agent]
    let idle: [Agent]
    let onSelectAgent: (Agent) -> Void
    let onJump: (Agent) -> Void
    private let updater = Updater.shared

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 8) {
                // Update banner
                if updater.updateAvailable {
                    UpdateBanner(updater: updater)
                }

                // Blocked: hoisted to top with urgency
                if !blocked.isEmpty {
                    SectionHeader(title: "NEEDS YOU", color: .red, count: blocked.count)
                    ForEach(blocked) { agent in
                        AgentSessionRow(agent: agent, style: .blocked, relay: relay)
                            .onTapGesture { onSelectAgent(agent) }
                    }
                }

                // Working
                if !working.isEmpty {
                    SectionHeader(title: "WORKING", color: .green, count: working.count)
                    ForEach(working) { agent in
                        AgentSessionRow(agent: agent, style: .working, relay: relay)
                            .onTapGesture { onJump(agent) }
                    }
                }

                // Idle
                if !idle.isEmpty {
                    SectionHeader(title: "IDLE", color: .gray, count: idle.count)
                    ForEach(idle) { agent in
                        AgentSessionRow(agent: agent, style: .idle, relay: relay)
                            .onTapGesture { onJump(agent) }
                    }
                }

                if relay.agents.isEmpty {
                    VStack(spacing: 6) {
                        Text(relay.isConnected ? "No agents" : "Connecting…")
                            .font(.system(size: 11))
                            .foregroundStyle(.white.opacity(0.4))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 30)
                }

                // Version footer
                HStack {
                    Spacer()
                    Text("v\(updater.currentVersion)")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.2))
                    Spacer()
                }
                .padding(.top, 4)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .frame(maxHeight: 320)
    }
}

// MARK: - Update Banner

private struct UpdateBanner: View {
    let updater: Updater
    @State private var hovered = false

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.down.circle.fill")
                .font(.system(size: 14))
                .foregroundStyle(.cyan)

            VStack(alignment: .leading, spacing: 1) {
                Text("Update available")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                Text("v\(updater.currentVersion) → v\(updater.latestVersion ?? "?")")
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.5))
            }

            Spacer()

            if updater.isUpdating {
                ProgressView()
                    .scaleEffect(0.6)
                    .frame(width: 16, height: 16)
            } else {
                Button { updater.performUpdate() } label: {
                    Text("Install")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(
                            RoundedRectangle(cornerRadius: 6)
                                .fill(.cyan)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(.cyan.opacity(hovered ? 0.12 : 0.06))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(.cyan.opacity(0.2), lineWidth: 0.5)
        )
        .onHover { hovered = $0 }
    }
}

// MARK: - Section Header

private struct SectionHeader: View {
    let title: String
    let color: Color
    let count: Int

    var body: some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 1)
                .fill(color)
                .frame(width: 3, height: 10)
            Text(title)
                .font(.system(size: 9, weight: .bold, design: .monospaced))
                .foregroundStyle(color.opacity(0.7))
            Spacer()
            Text("\(count)")
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .foregroundStyle(.white.opacity(0.3))
        }
        .padding(.top, 4)
    }
}

// MARK: - Agent Session Row

private enum RowStyle { case blocked, working, idle }

private struct AgentSessionRow: View {
    let agent: Agent
    let style: RowStyle
    let relay: RelayConnection
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
            // Accent bar
            RoundedRectangle(cornerRadius: 1.5)
                .fill(accentColor)
                .frame(width: 3, height: 28)

            // Agent info
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(agent.name)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.9))
                    if agent.host != "local" {
                        Image(systemName: "network")
                            .font(.system(size: 8))
                            .foregroundStyle(.green.opacity(0.6))
                    }
                }
                HStack(spacing: 4) {
                    Text(agent.project.isEmpty ? agent.cwd : agent.project)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.4))
                        .lineLimit(1)
                    if style == .blocked, let prompt = agent.prompt {
                        Text("— \(prompt.components(separatedBy: .newlines).last ?? "")")
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundStyle(.red.opacity(0.6))
                            .lineLimit(1)
                    }
                }
            }

            Spacer()

            // Actions
            if hovered || style == .blocked {
                HStack(spacing: 4) {
                    if style == .blocked,
                       agent.options?.contains("yes, single permission") == true {
                        Button {
                            relay.send(response: ResponseMessage(
                                pane_id: agent.id,
                                prompt_id: agent.promptId,
                                text: "yes, single permission"
                            ))
                        } label: {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 14))
                                .foregroundStyle(.green)
                        }
                        .buttonStyle(.plain)
                        .help("Allow")
                    }

                    Button { relay.focusPane(agent.id) } label: {
                        Image(systemName: "arrow.up.forward.square")
                            .font(.system(size: 12))
                            .foregroundStyle(.blue.opacity(0.8))
                    }
                    .buttonStyle(.plain)
                    .help("Jump to terminal")

                    if style == .working || style == .blocked {
                        Button { relay.interruptPane(agent.id) } label: {
                            Image(systemName: "stop.circle")
                                .font(.system(size: 11))
                                .foregroundStyle(.red.opacity(0.6))
                        }
                        .buttonStyle(.plain)
                        .help("Interrupt (^C)")
                    }
                }
                .transition(.opacity)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(hovered ? .white.opacity(0.06) : .white.opacity(0.03))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(style == .blocked ? accentColor.opacity(0.25) : .clear, lineWidth: 0.5)
        )
        .contentShape(Rectangle())
        .onHover { hovered = $0 }
        .animation(NotchAnimation.micro, value: hovered)
    }
}

// MARK: - Approval Card (inline permission/question answering)

private struct ApprovalCard: View {
    let agent: Agent
    let relay: RelayConnection
    let onDismiss: () -> Void
    @State private var customResponse = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Header
            HStack(spacing: 8) {
                Button { onDismiss() } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.white.opacity(0.6))
                }
                .buttonStyle(.plain)

                RoundedRectangle(cornerRadius: 2)
                    .fill(.red)
                    .frame(width: 3, height: 14)

                Text(agent.name)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white)
                Text("·")
                    .foregroundStyle(.white.opacity(0.3))
                Text(agent.project)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.5))

                Spacer()

                Button { relay.interruptPane(agent.id) } label: {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(.red.opacity(0.7))
                }
                .buttonStyle(.plain)
                .help("Interrupt (^C)")
            }
            .padding(.horizontal, 14)
            .padding(.top, 10)

            // Prompt / diff content
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let prompt = agent.prompt {
                        ForEach(Array(prompt.components(separatedBy: .newlines).enumerated()), id: \.offset) { _, line in
                            DiffLine(text: line)
                        }
                    } else {
                        Text("Waiting for input…")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.white.opacity(0.3))
                            .padding(8)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(6)
            }
            .frame(maxHeight: 160)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(.white.opacity(0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(.white.opacity(0.06), lineWidth: 0.5)
            )
            .padding(.horizontal, 12)

            if agent.isMultiSelect, let promptId = agent.promptId {
                VStack(spacing: 6) {
                    ForEach(agent.multiOptions, id: \.self) { option in
                        Button {
                            toggle(option, promptId: promptId)
                        } label: {
                            HStack {
                                Image(systemName: agent.selectedOptions.contains(option) ? "checkmark.square.fill" : "square")
                                Text(option)
                                Spacer()
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.plain)
                    }
                    Button {
                        submit(promptId: promptId)
                    } label: {
                        Label("Submit", systemImage: "checkmark.circle.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding(.horizontal, 12)
            } else {
                ResponseButtonGrid(options: agent.options) { response in
                    respond(response)
                }
                .padding(.horizontal, 12)
            }

            // Custom text input
            HStack(spacing: 6) {
                TextField("Custom reply…", text: $customResponse)
                    .textFieldStyle(.plain)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(.white.opacity(0.05))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(.white.opacity(0.1), lineWidth: 0.5)
                    )
                    .onSubmit { if !customResponse.isEmpty { respond(customResponse) } }

                Button { respond(customResponse) } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 20))
                        .foregroundStyle(customResponse.isEmpty ? .white.opacity(0.15) : .blue)
                }
                .buttonStyle(.plain)
                .disabled(customResponse.isEmpty)
                .keyboardShortcut(.return, modifiers: .command)
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 12)
        }
    }

    private func respond(_ text: String) {
        relay.send(response: ResponseMessage(pane_id: agent.id, prompt_id: agent.promptId, text: text))
        agent.status = .working
        agent.prompt = nil
        agent.promptId = nil
        agent.options = nil
        onDismiss()
    }

    private func toggle(_ option: String, promptId: String) {
        relay.toggleQuestionOption(paneId: agent.id, promptId: promptId, option: option)
        if let index = agent.selectedOptions.firstIndex(of: option) {
            agent.selectedOptions.remove(at: index)
        } else {
            agent.selectedOptions.append(option)
        }
    }

    private func submit(promptId: String) {
        relay.submitQuestion(paneId: agent.id, promptId: promptId)
        agent.status = .working
        agent.prompt = nil
        agent.promptId = nil
        agent.multiOptions = []
        agent.selectedOptions = []
        onDismiss()
    }
}

// MARK: - Response Button Grid

/// Clean, icon-labeled buttons for common agent responses.
/// Maps raw option strings to clear UI with icons and keyboard shortcuts.
private struct ResponseButtonGrid: View {
    let options: [String]?
    let onRespond: (String) -> Void

    private var buttons: [ResponseAction] {
        guard let options else { return [] }
        return options.map { mapOption($0) }
    }

    var body: some View {
        HStack(spacing: 8) {
            ForEach(buttons) { btn in
                ResponseButton(action: btn) { onRespond(btn.rawValue) }
            }
        }
    }

    private func mapOption(_ option: String) -> ResponseAction {
        let lower = option.lowercased()

        // Permission responses
        if lower.contains("single permission") || lower == "y" || lower == "yes" {
            return ResponseAction(label: "Allow", icon: "checkmark", tint: .green, shortcut: "⌘Y", rawValue: option)
        }
        if lower.contains("always allow") || lower.contains("trust") {
            return ResponseAction(label: "Trust", icon: "shield.checkered", tint: .blue, shortcut: "⌘T", rawValue: option)
        }
        if lower.contains("tab to edit") || lower.starts(with: "no") || lower == "n" {
            return ResponseAction(label: "Deny", icon: "xmark", tint: .red, shortcut: "⌘N", rawValue: option)
        }

        // Batch responses
        if lower.contains("approve all") {
            return ResponseAction(label: "Approve All", icon: "checkmark.circle.fill", tint: .green, shortcut: "⌘A", rawValue: option)
        }
        if lower.contains("configure individually") {
            return ResponseAction(label: "Configure", icon: "slider.horizontal.3", tint: .orange, shortcut: nil, rawValue: option)
        }

        // Flow control
        if lower.contains("continue") || lower.contains("proceed") {
            return ResponseAction(label: "Continue", icon: "play.fill", tint: .green, shortcut: "⌘↩", rawValue: option)
        }
        if lower.contains("edit") || lower.contains("modify") {
            return ResponseAction(label: "Edit", icon: "pencil", tint: .orange, shortcut: "⌘E", rawValue: option)
        }
        if lower.contains("retry") || lower.contains("again") {
            return ResponseAction(label: "Retry", icon: "arrow.clockwise", tint: .blue, shortcut: "⌘R", rawValue: option)
        }
        if lower.contains("skip") {
            return ResponseAction(label: "Skip", icon: "forward.fill", tint: .gray, shortcut: nil, rawValue: option)
        }
        if lower.contains("exit") || lower.contains("cancel") || lower.contains("abort") {
            return ResponseAction(label: "Cancel", icon: "xmark.circle", tint: .red, shortcut: "⌘.", rawValue: option)
        }

        // Fallback
        let shortLabel = String(option.prefix(16))
        return ResponseAction(label: shortLabel, icon: "circle", tint: .white.opacity(0.6), shortcut: nil, rawValue: option)
    }
}

private struct ResponseAction: Identifiable {
    let label: String
    let icon: String
    let tint: Color
    let shortcut: String?
    let rawValue: String

    var id: String { rawValue }
}

private struct ResponseButton: View {
    let action: ResponseAction
    let onTap: () -> Void
    @State private var hovered = false
    @State private var pressed = false

    var body: some View {
        Button {
            withAnimation(.easeOut(duration: 0.08)) { pressed = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                pressed = false
                onTap()
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: action.icon)
                    .font(.system(size: 10, weight: .semibold))
                Text(action.label)
                    .font(.system(size: 10, weight: .semibold))
                if let shortcut = action.shortcut {
                    Text(shortcut)
                        .font(.system(size: 8, weight: .medium))
                        .foregroundStyle(action.tint.opacity(0.5))
                }
            }
            .foregroundStyle(hovered ? .white : action.tint)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(hovered ? action.tint.opacity(0.25) : action.tint.opacity(0.08))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(action.tint.opacity(hovered ? 0.5 : 0.2), lineWidth: 0.5)
            )
            .scaleEffect(pressed ? 0.95 : 1)
        }
        .buttonStyle(.plain)
        .onHover { hovered = $0 }
        .animation(NotchAnimation.micro, value: hovered)
        .animation(.easeOut(duration: 0.08), value: pressed)
    }
}

// MARK: - Diff Line

private struct DiffLine: View {
    let text: String

    private enum LineType { case added, removed, hunk, context }
    private var lineType: LineType {
        let t = text.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("+") && !t.hasPrefix("+++") { return .added }
        if t.hasPrefix("-") && !t.hasPrefix("---") { return .removed }
        if t.hasPrefix("@@") { return .hunk }
        return .context
    }

    var body: some View {
        Text(text)
            .font(.system(size: 10, design: .monospaced))
            .foregroundStyle(fgColor)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 4)
            .padding(.vertical, 0.5)
            .background(bgColor)
    }

    private var fgColor: Color {
        switch lineType {
        case .added: .green
        case .removed: .red
        case .hunk: .cyan.opacity(0.7)
        case .context: .white.opacity(0.6)
        }
    }

    private var bgColor: Color {
        switch lineType {
        case .added: .green.opacity(0.08)
        case .removed: .red.opacity(0.08)
        case .hunk: .cyan.opacity(0.03)
        case .context: .clear
        }
    }
}

// MARK: - Pulsing Dot

struct PulsingDot: View {
    let color: Color
    var size: CGFloat = 6
    @State private var pulse = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: size, height: size)
            .scaleEffect(pulse ? 1.3 : 1.0)
            .opacity(pulse ? 0.7 : 1.0)
            .animation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true), value: pulse)
            .onAppear { pulse = true }
    }
}

// MARK: - NotchPanelShape (squircle with shoulder wings extending into notch)

private struct NotchPanelShape: Shape {
    var topExtension: CGFloat
    var bottomRadius: CGFloat
    var minHeight: CGFloat = 0

    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { AnimatablePair(topExtension, bottomRadius) }
        set {
            topExtension = newValue.first
            bottomRadius = newValue.second
        }
    }

    func path(in rect: CGRect) -> Path {
        let ext = topExtension
        let maxY = max(rect.maxY, rect.minY + minHeight)
        let br = min(bottomRadius, rect.width / 4, (maxY - rect.minY) / 2)
        // Squircle factor for Apple-style continuous curvature corners
        let k: CGFloat = 0.62

        var p = Path()
        // Top: extends into notch area via shoulder wings
        p.move(to: CGPoint(x: rect.minX - ext, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX + ext, y: rect.minY))
        // Right shoulder (smooth curve from top-edge to side)
        p.addCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY + ext),
            control1: CGPoint(x: rect.maxX + ext * 0.35, y: rect.minY),
            control2: CGPoint(x: rect.maxX, y: rect.minY + ext * 0.35)
        )
        // Right side
        p.addLine(to: CGPoint(x: rect.maxX, y: maxY - br))
        // Bottom-right squircle
        p.addCurve(
            to: CGPoint(x: rect.maxX - br, y: maxY),
            control1: CGPoint(x: rect.maxX, y: maxY - br * (1 - k)),
            control2: CGPoint(x: rect.maxX - br * (1 - k), y: maxY)
        )
        // Bottom
        p.addLine(to: CGPoint(x: rect.minX + br, y: maxY))
        // Bottom-left squircle
        p.addCurve(
            to: CGPoint(x: rect.minX, y: maxY - br),
            control1: CGPoint(x: rect.minX + br * (1 - k), y: maxY),
            control2: CGPoint(x: rect.minX, y: maxY - br * (1 - k))
        )
        // Left side
        p.addLine(to: CGPoint(x: rect.minX, y: rect.minY + ext))
        // Left shoulder
        p.addCurve(
            to: CGPoint(x: rect.minX - ext, y: rect.minY),
            control1: CGPoint(x: rect.minX, y: rect.minY + ext * 0.35),
            control2: CGPoint(x: rect.minX - ext * 0.35, y: rect.minY)
        )
        p.closeSubpath()
        return p
    }
}

// MARK: - Blur + Fade Transition

private struct BlurFadeModifier: ViewModifier {
    let active: Bool
    func body(content: Content) -> some View {
        content
            .blur(radius: active ? 5 : 0)
            .opacity(active ? 0 : 1)
    }
}

extension AnyTransition {
    static var blurFade: AnyTransition {
        .modifier(
            active: BlurFadeModifier(active: true),
            identity: BlurFadeModifier(active: false)
        )
    }
}
