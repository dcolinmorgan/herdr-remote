import SwiftUI

struct AgentDetailView: View {
    @Environment(RelayConnection.self) private var relay
    let agent: Agent
    @State private var customResponse = ""

    var body: some View {
        VStack(spacing: 0) {
            // Header info
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Circle().fill(statusColor).frame(width: 10, height: 10)
                    Text(agent.status.rawValue.capitalized)
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Text(agent.project).font(.title2.bold())
                Text(agent.cwd).font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding()

            Divider()

            // Scrollable pane content
            ScrollView {
                Text(relay.paneHistory[agent.id] ?? agent.prompt ?? "No output captured")
                    .font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
            .background(.ultraThinMaterial)

            // Approval controls (if blocked)
            if agent.status == .blocked {
                Divider()
                VStack(spacing: 10) {
                    if agent.isMultiSelect, let promptId = agent.promptId {
                        ForEach(agent.multiOptions, id: \.self) { option in
                            Button {
                                toggle(option, promptId: promptId)
                            } label: {
                                Label(
                                    option,
                                    systemImage: agent.selectedOptions.contains(option)
                                        ? "checkmark.square.fill" : "square"
                                )
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                            }
                            .buttonStyle(.bordered)
                        }
                        Button("Submit") { submit(promptId: promptId) }
                            .buttonStyle(.borderedProminent)
                    } else if let options = agent.options {
                        ForEach(options, id: \.self) { option in
                            Button {
                                respond(option)
                            } label: {
                                Text(option).frame(maxWidth: .infinity).padding(.vertical, 10)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(tint(for: option))
                        }
                    }
                    HStack {
                        TextField("Custom response…", text: $customResponse)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit { if !customResponse.isEmpty { respond(customResponse) } }
                        Button("Send") { respond(customResponse) }
                            .disabled(customResponse.isEmpty)
                    }
                }
                .padding()
            }
        }
        .navigationTitle(agent.name)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { relay.fetchHistory(for: agent.id) }
    }

    private var statusColor: Color {
        switch agent.status {
        case .blocked: .red
        case .working: .green
        case .idle, .unknown: .gray
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

    private func toggle(_ option: String, promptId: String) {
        relay.toggleQuestionOption(paneId: agent.id, promptId: promptId, option: option)
        if let index = agent.selectedOptions.firstIndex(of: option) {
            agent.selectedOptions.remove(at: index)
        } else {
            agent.selectedOptions.append(option)
        }
    }

    private func submit(promptId: String) {
        HapticManager.shared.sent()
        relay.submitQuestion(paneId: agent.id, promptId: promptId)
        agent.status = .working
        agent.prompt = nil
        agent.promptId = nil
        agent.multiOptions = []
        agent.selectedOptions = []
    }

    private func tint(for option: String) -> Color {
        if option.contains("yes") || option.contains("approve") { return .green }
        if option.contains("no") || option.contains("exit") || option.contains("cancel") { return .red }
        return .blue
    }
}
