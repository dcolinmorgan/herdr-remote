import Foundation
import Network
import Observation

enum ConnectionState: Equatable {
    case disconnected
    case connecting
    case connected
    case reconnecting(attempt: Int)
}

@Observable
final class RelayConnection {
    var agents: [Agent] = []
    var connectionState: ConnectionState = .disconnected
    var hostAddress: String = ""
    var paneHistory: [String: String] = [:]

    var isConnected: Bool { connectionState == .connected }

    private var task: URLSessionWebSocketTask?
    private var browser: NWBrowser?
    private var pathMonitor: NWPathMonitor?
    private let session = URLSession(configuration: .default)
    private var reconnectAttempt = 0
    private var reconnectTask: Task<Void, Never>?

    init() {
        startBrowsing()
        startPathMonitor()
    }

    deinit {
        pathMonitor?.cancel()
        browser?.cancel()
    }

    // MARK: - Network Path Monitor

    private func startPathMonitor() {
        pathMonitor = NWPathMonitor()
        pathMonitor?.pathUpdateHandler = { [weak self] path in
            guard let self else { return }
            DispatchQueue.main.async {
                if path.status == .satisfied, !self.isConnected, !self.hostAddress.isEmpty {
                    self.connect(to: self.hostAddress)
                }
            }
        }
        pathMonitor?.start(queue: .global(qos: .utility))
    }

    // MARK: - Bonjour Discovery

    func startBrowsing() {
        let params = NWParameters()
        params.includePeerToPeer = true
        browser = NWBrowser(for: .bonjour(type: "_herdi._tcp", domain: nil), using: params)
        browser?.browseResultsChangedHandler = { [weak self] results, _ in
            guard let result = results.first else { return }
            if case let .service(name, type, domain, _) = result.endpoint {
                self?.resolve(name: name, type: type, domain: domain)
            }
        }
        browser?.start(queue: .main)
    }

    private func resolve(name: String, type: String, domain: String) {
        let connection = NWConnection(to: .service(name: name, type: type, domain: domain, interface: nil), using: .tcp)
        connection.stateUpdateHandler = { [weak self] state in
            if case .ready = state,
               let endpoint = connection.currentPath?.remoteEndpoint,
               case let .hostPort(host, port) = endpoint {
                let addr = "\(host)".replacingOccurrences(of: "%.*", with: "", options: .regularExpression)
                DispatchQueue.main.async {
                    self?.connect(to: "ws://\(addr):\(port)")
                }
                connection.cancel()
            }
        }
        connection.start(queue: .global())
    }

    // MARK: - WebSocket

    func connect(to urlString: String) {
        guard let url = URL(string: urlString) else { return }
        hostAddress = urlString
        reconnectTask?.cancel()
        task?.cancel()
        connectionState = .connecting
        task = session.webSocketTask(with: url)
        task?.resume()
        connectionState = .connected
        reconnectAttempt = 0
        listen()
    }

    func disconnect() {
        reconnectTask?.cancel()
        task?.cancel(with: .normalClosure, reason: nil)
        connectionState = .disconnected
    }

    func send(response: ResponseMessage) {
        guard let data = try? JSONEncoder().encode(response) else { return }
        task?.send(.string(String(data: data, encoding: .utf8)!)) { _ in }
    }

    func fetchHistory(for paneId: String) {
        let msg = ["type": "read_pane", "pane_id": paneId]
        guard let data = try? JSONSerialization.data(withJSONObject: msg) else { return }
        task?.send(.string(String(data: data, encoding: .utf8)!)) { _ in }
    }

    private func listen() {
        task?.receive { [weak self] result in
            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self?.handle(text)
                case .data(let data):
                    self?.handle(String(data: data, encoding: .utf8) ?? "")
                @unknown default:
                    break
                }
                self?.listen()
            case .failure:
                DispatchQueue.main.async {
                    self?.scheduleReconnect()
                }
            }
        }
    }

    private func scheduleReconnect() {
        guard !hostAddress.isEmpty else {
            connectionState = .disconnected
            return
        }
        reconnectAttempt += 1
        connectionState = .reconnecting(attempt: reconnectAttempt)
        let delay = min(Double(1 << min(reconnectAttempt, 5)), 30.0) // 1, 2, 4, 8, 16, 30 cap
        reconnectTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled else { return }
            connect(to: hostAddress)
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let msg = try? JSONDecoder().decode(AgentMessage.self, from: data) else { return }

        DispatchQueue.main.async { [self] in
            switch msg.type {
            case "agents":
                guard let list = msg.agents else { return }
                for a in list {
                    upsertAgent(a)
                }
                let activeIds = Set(list.map(\.pane_id))
                agents.removeAll { !activeIds.contains($0.id) }
                updateAgentCounts()

            case "agent_update":
                if let update = msg.agentData {
                    upsertAgent(update)
                    updateAgentCounts()
                }

            case "blocked":
                if let pid = msg.pane_id,
                   let agent = agents.first(where: { $0.id == pid }) {
                    agent.prompt = msg.prompt
                    agent.options = msg.options
                    agent.status = .blocked
                    HapticManager.shared.blocked()
                    NotificationManager.shared.notifyBlocked(agent: agent.name, project: agent.project)
                }

            case "pane_content":
                if let pid = msg.pane_id, let content = msg.prompt {
                    paneHistory[pid] = content
                }

            default:
                break
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

    private func updateAgentCounts() {
        let blocked = agents.filter { $0.status == .blocked }.count
        let working = agents.filter { $0.status == .working }.count
        let idle = agents.filter { $0.status == .idle || $0.status == .unknown }.count
        LiveActivityManager.shared.update(blocked: blocked, working: working, idle: idle)
        let defaults = UserDefaults(suiteName: "group.com.dcolinmorgan.herdi")
        defaults?.set(blocked, forKey: "blocked_count")
        defaults?.set(working, forKey: "working_count")
        defaults?.set(idle, forKey: "idle_count")
    }
}
