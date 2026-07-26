import Foundation

enum AgentStatus: String, Codable {
    case working, blocked, idle, unknown
}

@Observable
final class Agent: Identifiable {
    let id: String // pane_id
    var name: String
    var status: AgentStatus
    var project: String
    var cwd: String
    var host: String
    var prompt: String?
    var options: [String]?

    init(id: String, name: String, status: AgentStatus, project: String, cwd: String, host: String = "local") {
        self.id = id
        self.name = name
        self.status = status
        self.project = project
        self.cwd = cwd
        self.host = host
    }
}

struct AgentMessage: Decodable {
    let type: String
    let agents: [AgentData]?
    let pane_id: String?
    let agent: String?
    let agentData: AgentData?
    let project: String?
    let prompt: String?
    let options: [String]?

    struct AgentData: Decodable {
        let pane_id: String
        let agent: String
        let status: String
        let cwd: String
        let project: String
        let host: String?
    }

    private enum CodingKeys: String, CodingKey {
        case type, agents, pane_id, agent, project, prompt, options
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        type = try values.decode(String.self, forKey: .type)
        agents = try? values.decode([AgentData].self, forKey: .agents)
        pane_id = try? values.decode(String.self, forKey: .pane_id)
        project = try? values.decode(String.self, forKey: .project)
        prompt = try? values.decode(String.self, forKey: .prompt)
        options = try? values.decode([String].self, forKey: .options)
        if type == "agent_update" {
            agentData = try? values.decode(AgentData.self, forKey: .agent)
            agent = nil
        } else {
            agent = try? values.decode(String.self, forKey: .agent)
            agentData = nil
        }
    }
}

struct ResponseMessage: Codable {
    let type = "respond"
    let pane_id: String
    let text: String
}
