import Foundation
import Network
import Observation
import UserNotifications

@Observable
final class RelayConnection {
    var agents: [Agent] = []
    var isConnected = false
    var hostAddress = "ws://127.0.0.1:8375"
    var mode: ConnectionMode = .direct
    var herdrError: String? = nil  // Surfaces binary-not-found etc.

    enum ConnectionMode: String, CaseIterable {
        case direct = "Direct (herdr CLI)"
        case relay = "Relay (WebSocket)"
    }

    private var task: URLSessionWebSocketTask?
    private let session = URLSession(configuration: .default)
    private var pollTimer: Timer?
    private var reconnectAttempt = 0
    private var reconnecting = false
    private var herdrPath: String = ""
    var remotes: [String] = [] // SSH targets, e.g. ["user@host"]

    init() {
        herdrPath = resolveHerdrPath()
        // Load saved remotes
        if let saved = UserDefaults.standard.stringArray(forKey: "herdi_remotes") {
            remotes = saved
        }
        startDirect()
    }

    /// Resolve herdr binary: UserDefaults override → HERDR_BIN env → PATH lookup → common locations
    private func resolveHerdrPath() -> String {
        // 1. UserDefaults override (set via Settings)
        if let custom = UserDefaults.standard.string(forKey: "herdi_herdr_path"),
           !custom.isEmpty, FileManager.default.isExecutableFile(atPath: custom) {
            return custom
        }
        // 2. HERDR_BIN environment variable
        if let envPath = ProcessInfo.processInfo.environment["HERDR_BIN"],
           FileManager.default.isExecutableFile(atPath: envPath) {
            return envPath
        }
        // 3. Resolve via PATH using /usr/bin/which
        let whichProcess = Process()
        whichProcess.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        whichProcess.arguments = ["herdr"]
        let pipe = Pipe()
        whichProcess.standardOutput = pipe
        whichProcess.standardError = FileHandle.nullDevice
        do {
            try whichProcess.run()
            whichProcess.waitUntilExit()
            if whichProcess.terminationStatus == 0 {
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                if let path = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
                   !path.isEmpty, FileManager.default.isExecutableFile(atPath: path) {
                    return path
                }
            }
        } catch {}
        // 4. Common install locations
        let commonPaths = [
            "/opt/homebrew/bin/herdr",
            "/usr/local/bin/herdr",
            NSString(string: "~/.local/bin/herdr").expandingTildeInPath,
            NSString(string: "~/bin/herdr").expandingTildeInPath
        ]
        for path in commonPaths {
            if FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }
        // Not found — return empty and set error in pollHerdr
        return ""
    }

    // MARK: - Direct Mode (polls herdr CLI)

    func startDirect() {
        mode = .direct
        task?.cancel(with: .normalClosure, reason: nil)
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.pollHerdr()
        }
        pollHerdr() // immediate first poll
    }

    private func pollHerdr() {
        DispatchQueue.global(qos: .utility).async { [self] in
            // Check if herdr binary is found
            if herdrPath.isEmpty {
                DispatchQueue.main.async { [self] in
                    isConnected = false
                    herdrError = "herdr not found. Install herdr or set path in Settings."
                    agents = []
                }
                return
            }
            
            // Local
            var allAgents = parseAgents(from: runHerdr("pane", "list"), host: "local")

            // Remotes via SSH
            for remote in remotes {
                let result = runSSH(remote, "herdr", "pane", "list")
                allAgents += parseAgents(from: result, host: remote)
            }

            DispatchQueue.main.async { [self] in
                isConnected = true
                herdrError = nil  // Clear any previous error
                var seen = Set<String>()
                for a in allAgents {
                    seen.insert(a.id)
                    if let existing = agents.first(where: { $0.id == a.id }) {
                        if existing.status != a.status {
                            if a.status == .blocked && existing.status != .blocked {
                                readPaneForBlocked(existing, remote: a.host == "local" ? nil : a.host)
                            }
                            existing.status = a.status
                        }
                        if existing.project != a.project { existing.project = a.project }
                        if existing.host != a.host { existing.host = a.host }
                    } else {
                        let agent = Agent(id: a.id, name: a.name, status: a.status, project: a.project, cwd: a.cwd, host: a.host)
                        agents.append(agent)
                        if a.status == .blocked { readPaneForBlocked(agent, remote: a.host == "local" ? nil : a.host) }
                    }
                }
                agents.removeAll { !seen.contains($0.id) }
            }
        }
    }

    private struct ParsedAgent {
        let id: String, name: String, status: AgentStatus, project: String, cwd: String, host: String
    }

    private struct PaneLocation {
        let workspaceId: String
        let tabId: String
    }

    private func parseAgents(from output: String, host: String) -> [ParsedAgent] {
        guard let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let resultObj = json["result"] as? [String: Any],
              let panes = resultObj["panes"] as? [[String: Any]] else { return [] }

        return panes.compactMap { p in
            guard let agent = p["agent"] as? String, !agent.isEmpty else { return nil }
            let paneId = (host == "local" ? "" : "\(host):") + (p["pane_id"] as? String ?? "")
            let status = AgentStatus(rawValue: p["agent_status"] as? String ?? "unknown") ?? .unknown
            let cwd = p["cwd"] as? String ?? ""
            return ParsedAgent(id: paneId, name: agent, status: status, project: (cwd as NSString).lastPathComponent, cwd: cwd, host: host)
        }
    }

    private func parsePaneLocation(from output: String) -> PaneLocation? {
        guard let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let result = json["result"] as? [String: Any],
              let pane = result["pane"] as? [String: Any],
              let workspaceId = pane["workspace_id"] as? String,
              let tabId = pane["tab_id"] as? String else { return nil }
        return PaneLocation(workspaceId: workspaceId, tabId: tabId)
    }

    private func runSSH(_ remote: String, _ args: String...) -> String {
        let process = Process()
        let password = KeychainHelper.getPassword(for: remote)

        if let password, FileManager.default.fileExists(atPath: "/opt/homebrew/bin/sshpass") {
            // Use sshpass for password auth
            process.executableURL = URL(fileURLWithPath: "/opt/homebrew/bin/sshpass")
            process.arguments = ["-p", password, "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", remote] + args
        } else {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
            process.arguments = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes", remote] + args
        }

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else { return "" }
            return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        } catch { return "" }
    }

    func addRemote(_ remote: String, password: String? = nil) {
        guard !remote.isEmpty, !remotes.contains(remote) else { return }
        remotes.append(remote)
        UserDefaults.standard.set(remotes, forKey: "herdi_remotes")
        if let password, !password.isEmpty {
            KeychainHelper.setPassword(password, for: remote)
        }
    }

    func removeRemote(_ remote: String) {
        remotes.removeAll { $0 == remote }
        UserDefaults.standard.set(remotes, forKey: "herdi_remotes")
        KeychainHelper.deletePassword(for: remote)
    }

    private func readPaneForBlocked(_ agent: Agent, remote: String? = nil) {
        // Extract the real pane_id (strip host prefix if present)
        let paneId = agent.id.contains(":") && remote != nil
            ? String(agent.id.drop(while: { $0 != ":" }).dropFirst())
            : agent.id

        DispatchQueue.global(qos: .utility).async { [self] in
            // `visible`, not `recent`, as the relay does (PROMPT_READ_SOURCE). `recent` past the
            // pane's viewport makes herdr harvest the extra rows by walking the agent's own
            // scroll interface, which moves the operator's terminal -- something a read fired by
            // a status change must never do. 20 rows sits inside most viewports, so this is
            // usually a no-op; on a pane split down under 20 rows it is not. The prompt is on
            // screen by definition, so nothing is given up either way.
            let raw: String
            if let remote {
                raw = runSSH(remote, "herdr", "pane", "read", paneId, "--lines", "20", "--source", "visible")
            } else {
                raw = runHerdr("pane", "read", paneId, "--lines", "20", "--source", "visible")
            }
            let lines = raw.components(separatedBy: .newlines)
                .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
                .suffix(6)
            let content = lines.joined(separator: "\n")
            let options = detectOptions(content)

            DispatchQueue.main.async {
                agent.prompt = String(content.prefix(500))
                agent.options = options
                self.sendNotification(agent: agent.name, project: agent.project)
            }
        }
    }

    private func detectOptions(_ text: String) -> [String] {
        let lower = text.lowercased()
        if lower.contains("yes, single permission") {
            return ["yes, single permission", "trust, always allow", "no (tab to edit)"]
        }
        if lower.contains("approve all pending") {
            return ["approve all pending", "configure individually", "exit (cancel subagents)"]
        }
        return ["yes, single permission", "trust, always allow", "no (tab to edit)"]
    }

    private func runHerdr(_ args: String...) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: herdrPath)
        process.arguments = Array(args)
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        } catch {
            return ""
        }
    }

    // MARK: - Relay Mode (WebSocket)

    func connectRelay(to urlString: String) {
        guard let url = URL(string: urlString) else { return }
        mode = .relay
        hostAddress = urlString
        pollTimer?.invalidate()
        pollTimer = nil
        reconnecting = false
        task?.cancel(with: .normalClosure, reason: nil)
        task = session.webSocketTask(with: url)
        task?.resume()
        reconnectAttempt = 0
        listen()
    }

    func disconnect() {
        task?.cancel(with: .normalClosure, reason: nil)
        pollTimer?.invalidate()
        isConnected = false
    }

    func send(response: ResponseMessage) {
        if mode == .direct {
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                let paneId = response.pane_id
                // Check if this is a remote agent (id starts with "host:")
                if let agent = agents.first(where: { $0.id == paneId }), agent.host != "local" {
                    let realId = String(paneId.drop(while: { $0 != ":" }).dropFirst())
                    _ = runSSH(agent.host, "herdr", "pane", "send-text", realId, response.text + "\n")
                } else {
                    _ = runHerdr("pane", "send-text", paneId, response.text + "\n")
                }
            }

        } else {
            guard let data = try? JSONEncoder().encode(response) else { return }
            task?.send(.string(String(data: data, encoding: .utf8)!)) { _ in }
        }
    }

    func toggleQuestionOption(paneId: String, promptId: String, option: String) {
        guard mode == .relay,
              let data = try? JSONEncoder().encode(
                QuestionToggleMessage(pane_id: paneId, prompt_id: promptId, option: option)
              ) else { return }
        task?.send(.string(String(data: data, encoding: .utf8)!)) { _ in }
    }

    func submitQuestion(paneId: String, promptId: String) {
        guard mode == .relay,
              let data = try? JSONEncoder().encode(
                QuestionSubmitMessage(pane_id: paneId, prompt_id: promptId)
              ) else { return }
        task?.send(.string(String(data: data, encoding: .utf8)!)) { _ in }
    }

    func focusPane(_ paneId: String) {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            if let agent = agents.first(where: { $0.id == paneId }), agent.host != "local" {
                let prefix = agent.host + ":"
                let remotePaneId = paneId.hasPrefix(prefix) ? String(paneId.dropFirst(prefix.count)) : paneId
                let output = runSSH(agent.host, "herdr", "pane", "get", remotePaneId)
                guard let location = parsePaneLocation(from: output) else { return }
                _ = runSSH(agent.host, "herdr", "workspace", "focus", location.workspaceId)
                _ = runSSH(agent.host, "herdr", "tab", "focus", location.tabId)
                return
            }

            let output = runHerdr("pane", "get", paneId)
            guard let location = parsePaneLocation(from: output) else { return }
            _ = runHerdr("workspace", "focus", location.workspaceId)
            _ = runHerdr("tab", "focus", location.tabId)
        }
    }

    func interruptPane(_ paneId: String) {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            _ = runHerdr("pane", "send-keys", paneId, "Ctrl+c")
        }
    }

    private func listen() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                DispatchQueue.main.async { if !self.isConnected { self.isConnected = true } }
                switch message {
                case .string(let text): self.handleWS(text)
                case .data(let data): self.handleWS(String(data: data, encoding: .utf8) ?? "")
                @unknown default: break
                }
                self.listen()
            case .failure:
                DispatchQueue.main.async {
                    self.isConnected = false
                    self.scheduleReconnect()
                }
            }
        }
    }

    private func scheduleReconnect() {
        guard !reconnecting, mode == .relay else { return }
        reconnecting = true
        reconnectAttempt += 1
        let delay = min(Double(1 << min(reconnectAttempt, 5)), 30.0)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, !self.isConnected else { return }
            self.reconnecting = false
            self.connectRelay(to: self.hostAddress)
        }
    }

    private func handleWS(_ text: String) {
        guard let data = text.data(using: .utf8),
              let msg = try? JSONDecoder().decode(AgentMessage.self, from: data) else { return }
        DispatchQueue.main.async { [self] in
            switch msg.type {
            case "agents":
                guard let list = msg.agents else { return }
                var seen = Set<String>()
                for a in list {
                    seen.insert(a.pane_id)
                    upsertAgent(a)
                }
                agents.removeAll { !seen.contains($0.id) }
            case "agent_update":
                if let update = msg.agentData { upsertAgent(update) }
            case "blocked":
                if let pid = msg.pane_id, let agent = agents.first(where: { $0.id == pid }) {
                    agent.prompt = msg.prompt
                    agent.promptId = msg.prompt_id
                    agent.options = msg.options
                    agent.multiOptions = msg.multi_options ?? []
                    agent.selectedOptions = msg.selected_options ?? []
                    agent.interaction = msg.interaction
                    agent.isMultiSelect = msg.multi ?? false
                    agent.status = .blocked
                    if msg.update != true {
                        sendNotification(agent: agent.name, project: agent.project)
                    }
                }
            default: break
            }
        }
    }

    private func upsertAgent(_ data: AgentMessage.AgentData) {
        if let existing = agents.first(where: { $0.id == data.pane_id }) {
            existing.name = data.agent
            existing.status = AgentStatus(rawValue: data.status) ?? .unknown
            existing.project = data.project
            existing.cwd = data.cwd
            existing.host = data.host ?? "local"
            return
        }
        agents.append(Agent(
            id: data.pane_id, name: data.agent,
            status: AgentStatus(rawValue: data.status) ?? .unknown,
            project: data.project, cwd: data.cwd, host: data.host ?? "local"
        ))
    }

    private func sendNotification(agent: String, project: String) {
        let center = UNUserNotificationCenter.current()
        let content = UNMutableNotificationContent()
        content.title = "Agent Blocked"
        content.body = "\(agent) needs input in \(project)"
        content.sound = .default
        center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }
}
