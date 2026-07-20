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
    let controller: PanelWindowController
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
                        working: working
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

            // Right wing: agent count
            HStack(spacing: 4) {
                if !expanded {
                    Text("\(relay.agents.count)")
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.4))
                }
            }
            .padding(.trailing, 10)
        }
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

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 8) {
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
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .frame(maxHeight: 320)
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

            // Quick-action buttons
            if let options = agent.options {
                HStack(spacing: 6) {
                    ForEach(options, id: \.self) { option in
                        ApprovalButton(label: shortLabel(option), tint: tint(for: option)) {
                            respond(option)
                        }
                    }
                }
                .padding(.horizontal, 12)
            }

            // Custom text input
            HStack(spacing: 6) {
                TextField("Reply…", text: $customResponse)
                    .textFieldStyle(.plain)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 6)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(.white.opacity(0.05))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(.white.opacity(0.08), lineWidth: 0.5)
                    )
                    .onSubmit { if !customResponse.isEmpty { respond(customResponse) } }

                Button { respond(customResponse) } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 18))
                        .foregroundStyle(customResponse.isEmpty ? .white.opacity(0.2) : .blue)
                }
                .buttonStyle(.plain)
                .disabled(customResponse.isEmpty)
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 12)
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
        if option.contains("tab to edit") || option.lowercased().starts(with: "no") { return "Deny" }
        if option.contains("approve all") { return "Approve All" }
        if option.contains("exit") || option.contains("cancel") { return "Cancel" }
        return String(option.prefix(14))
    }

    private func tint(for option: String) -> Color {
        if option.contains("yes") || option.contains("approve") || option.contains("single") { return .green }
        if option.contains("no") || option.contains("exit") || option.contains("cancel") { return .red }
        if option.contains("trust") || option.contains("always") { return .blue }
        return .white.opacity(0.6)
    }
}

// MARK: - Approval Button

private struct ApprovalButton: View {
    let label: String
    let tint: Color
    let action: () -> Void
    @State private var pressed = false

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(tint)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(tint.opacity(pressed ? 0.2 : 0.1))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(tint.opacity(0.3), lineWidth: 0.5)
                )
        }
        .buttonStyle(.plain)
        .onHover { pressed = $0 }
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
