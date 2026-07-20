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
    var promptId: String?
    var multiOptions: [String] = []
    var selectedOptions: [String] = []
    var interaction: String?
    var isMultiSelect = false

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
    let prompt_id: String?
    let multi_options: [String]?
    let selected_options: [String]?
    let interaction: String?
    let multi: Bool?
    let update: Bool?

    struct AgentData: Decodable {
        let pane_id: String
        let agent: String
        let status: String
        let cwd: String
        let project: String
        let host: String?
    }

    private enum CodingKeys: String, CodingKey {
        case type, agents, pane_id, agent, project, prompt, options, prompt_id
        case multi_options, selected_options, interaction, multi, update
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        type = try values.decode(String.self, forKey: .type)
        agents = try? values.decode([AgentData].self, forKey: .agents)
        pane_id = try? values.decode(String.self, forKey: .pane_id)
        project = try? values.decode(String.self, forKey: .project)
        prompt = try? values.decode(String.self, forKey: .prompt)
        options = try? values.decode([String].self, forKey: .options)
        prompt_id = try? values.decode(String.self, forKey: .prompt_id)
        multi_options = try? values.decode([String].self, forKey: .multi_options)
        selected_options = try? values.decode([String].self, forKey: .selected_options)
        interaction = try? values.decode(String.self, forKey: .interaction)
        multi = try? values.decode(Bool.self, forKey: .multi)
        update = try? values.decode(Bool.self, forKey: .update)
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
    let prompt_id: String?
    let text: String
}

struct QuestionToggleMessage: Codable {
    let type = "question_toggle"
    let pane_id: String
    let prompt_id: String
    let option: String
}

struct QuestionSubmitMessage: Codable {
    let type = "question_submit"
    let pane_id: String
    let prompt_id: String
}
