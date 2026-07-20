import SwiftUI

struct AgentListView: View {
    @Environment(RelayConnection.self) private var relay
    @State private var selectedAgent: Agent?
    @State private var showSettings = false

    private var blocked: [Agent] { relay.agents.filter { $0.status == .blocked } }
    private var working: [Agent] { relay.agents.filter { $0.status == .working } }
    private var idle: [Agent] { relay.agents.filter { $0.status == .idle || $0.status == .unknown } }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 16) {
                    if !blocked.isEmpty {
                        Section { agentCards(blocked) } header: { sectionHeader("Needs You", color: .red) }
                    }
                    if !working.isEmpty {
                        Section { agentCards(working) } header: { sectionHeader("Working", color: .green) }
                    }
                    if !idle.isEmpty {
                        Section { agentCards(idle) } header: { sectionHeader("Idle", color: .gray) }
                    }
                    if relay.agents.isEmpty {
                        ContentUnavailableView("No Agents", systemImage: "antenna.radiowaves.left.and.right",
                            description: Text(relay.isConnected ? "Waiting for herdr agents…" : "Not connected to relay"))
                    }
                }
                .padding()
            }
            .navigationTitle("herdi")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gear") }
                }
                ToolbarItem(placement: .topBarLeading) {
                    connectionIndicator
                }
            }
            .sheet(item: $selectedAgent) { agent in
                ApprovalView(agent: agent).environment(relay)
            }
            .sheet(isPresented: $showSettings) {
                SettingsView().environment(relay)
            }
            .navigationDestination(for: String.self) { agentId in
                if let agent = relay.agents.first(where: { $0.id == agentId }) {
                    AgentDetailView(agent: agent).environment(relay)
                }
            }
        }
    }

    @ViewBuilder
    private var connectionIndicator: some View {
        switch relay.connectionState {
        case .connected:
            Circle().fill(.green).frame(width: 8, height: 8)
        case .connecting, .reconnecting:
            ProgressView().scaleEffect(0.5)
        case .disconnected:
            Circle().fill(.red).frame(width: 8, height: 8)
        }
    }

    private func agentCards(_ agents: [Agent]) -> some View {
        ForEach(agents) { agent in
            SwipeableAgentCard(agent: agent, relay: relay) {
                if agent.status == .blocked {
                    selectedAgent = agent
                }
            }
        }
    }

    private func sectionHeader(_ title: String, color: Color) -> some View {
        HStack {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(title).font(.headline).foregroundStyle(.secondary)
            Spacer()
        }
    }
}

// MARK: - Swipeable Card

struct SwipeableAgentCard: View {
    let agent: Agent
    let relay: RelayConnection
    let onTap: () -> Void
    @State private var offset: CGFloat = 0
    private let threshold: CGFloat = 80

    var body: some View {
        ZStack {
            // Background actions
            HStack {
                // Leading (approve)
                if agent.status == .blocked {
                    HStack {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.title2).foregroundStyle(.white)
                        Spacer()
                    }
                    .padding(.leading, 20)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color.green, in: RoundedRectangle(cornerRadius: 10))
                }
                Spacer()
                // Trailing (reject)
                if agent.status == .blocked {
                    HStack {
                        Spacer()
                        Image(systemName: "xmark.circle.fill")
                            .font(.title2).foregroundStyle(.white)
                    }
                    .padding(.trailing, 20)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color.red, in: RoundedRectangle(cornerRadius: 10))
                }
            }

            // Card
            NavigationLink(value: agent.id) {
                AgentCard(agent: agent)
            }
            .buttonStyle(.plain)
            .offset(x: offset)
            .gesture(
                agent.status == .blocked
                ? DragGesture()
                    .onChanged { value in offset = value.translation.width }
                    .onEnded { value in
                        if value.translation.width > threshold, let first = agent.options?.first {
                            respond(first)
                        } else if value.translation.width < -threshold, let last = agent.options?.last {
                            respond(last)
                        }
                        withAnimation(.spring(response: 0.3)) { offset = 0 }
                    }
                : nil
            )
            .simultaneousGesture(TapGesture().onEnded { onTap() })
        }
    }

    private func respond(_ text: String) {
        HapticManager.shared.sent()
        relay.send(response: ResponseMessage(pane_id: agent.id, prompt_id: agent.promptId, text: text))
        agent.status = .working
        agent.prompt = nil
        agent.promptId = nil
        agent.options = nil
    }
}

// MARK: - Card

struct AgentCard: View {
    let agent: Agent

    private var statusColor: Color {
        switch agent.status {
        case .blocked: .red
        case .working: .green
        case .idle, .unknown: .gray
        }
    }

    var body: some View {
        HStack(spacing: 12) {
            Circle().fill(statusColor).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                Text(agent.project.isEmpty ? agent.name : agent.project)
                    .font(.body.weight(.medium))
                HStack(spacing: 4) {
                    Text(agent.name).font(.caption).foregroundStyle(.secondary)
                    if agent.host != "local" {
                        Text("@\(agent.host)").font(.caption2).foregroundStyle(.orange)
                    }
                }
            }
            Spacer()
            if agent.status == .blocked {
                Image(systemName: "exclamationmark.bubble.fill")
                    .foregroundStyle(.red)
            }
        }
        .padding(12)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
    }
}
