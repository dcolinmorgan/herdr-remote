import SwiftUI

struct ApprovalView: View {
    @Environment(RelayConnection.self) private var relay
    @Environment(\.dismiss) private var dismiss
    let agent: Agent
    @State private var customResponse = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                ScrollView {
                    Text(agent.prompt ?? "Waiting for approval…")
                        .font(.system(.body, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding()
                }
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))

                if agent.isMultiSelect, let promptId = agent.promptId {
                    VStack(spacing: 10) {
                        ForEach(agent.multiOptions, id: \.self) { option in
                            Button {
                                toggle(option, promptId: promptId)
                            } label: {
                                Label(
                                    option,
                                    systemImage: agent.selectedOptions.contains(option)
                                        ? "checkmark.square.fill" : "square"
                                )
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.vertical, 12)
                            }
                            .buttonStyle(.bordered)
                        }
                        Button("Submit") { submit(promptId: promptId) }
                            .buttonStyle(.borderedProminent)
                            .frame(maxWidth: .infinity)
                    }
                } else if let options = agent.options {
                    VStack(spacing: 10) {
                        ForEach(options, id: \.self) { option in
                            Button {
                                respond(option)
                            } label: {
                                Text(option)
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 12)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(tint(for: option))
                        }
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
            .navigationTitle("\(agent.name) — \(agent.project)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func respond(_ text: String) {
        HapticManager.shared.sent()
        relay.send(response: ResponseMessage(pane_id: agent.id, prompt_id: agent.promptId, text: text))
        agent.status = .working
        agent.prompt = nil
        agent.promptId = nil
        agent.options = nil
        dismiss()
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
        dismiss()
    }

    private func tint(for option: String) -> Color {
        if option.contains("yes") || option.contains("approve") { return .green }
        if option.contains("no") || option.contains("exit") || option.contains("cancel") { return .red }
        return .blue
    }
}
