using System.Text.Json;

namespace Herdi.Models;

/// <summary>
/// Wire protocol for the herdr relay. Parsing is hand-rolled with JsonDocument
/// because the `agent` field is polymorphic: an object on `agent_update`, a plain
/// string elsewhere (same reason herdi-mac writes a custom Decodable init).
/// </summary>
public static class Protocol
{
    // --- Client -> server message types the relay actually handles.
    // Verified against relay/herdr_relay.py: respond, agent_event, read_pane,
    // get_history, send_keys, send_text, agent_prompt, create_tab,
    // push_subscribe, push_unsubscribe.
    public const string TypeRespond = "respond";
    public const string TypeAgentPrompt = "agent_prompt";
    public const string TypeSendKeys = "send_keys";
    public const string TypeSendText = "send_text";
    public const string TypeReadPane = "read_pane";
    public const string TypeQuestionToggle = "question_toggle";
    public const string TypeQuestionSubmit = "question_submit";

    /// <summary>
    /// The relay's SAFE_RESPONSES allowlist (herdr_relay.py:90). A `respond` whose
    /// text is outside this set is rejected with "response not in allowlist", so
    /// free-form replies must go through `agent_prompt` instead.
    /// </summary>
    public static readonly HashSet<string> SafeResponses = new(StringComparer.OrdinalIgnoreCase)
    {
        "y", "n", "a", "yes", "no", "trust",
        "yes, single permission",
        "trust, always allow",
        "no (tab to edit)",
        "approve all pending",
        "configure individually",
        "exit (cancel subagents)",
    };

    /// <summary>
    /// The relay's SAFE_KEYS allowlist (herdr_relay.py:91). Note interrupt is
    /// "C-c" here — NOT the "Ctrl+c" spelling herdi-mac passes to the local CLI.
    /// </summary>
    public const string InterruptKey = "C-c";

    public static string Respond(string paneId, string? promptId, string text) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = TypeRespond,
            ["pane_id"] = paneId,
            ["prompt_id"] = promptId,
            ["text"] = text,
        });

    public static string AgentPrompt(string paneId, string text) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = TypeAgentPrompt,
            ["pane_id"] = paneId,
            ["text"] = text,
        });

    public static string SendKeys(string paneId, params string[] keys) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = TypeSendKeys,
            ["pane_id"] = paneId,
            ["keys"] = keys,
        });

    public static string ReadPane(string paneId, int lines = 30) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = TypeReadPane,
            ["pane_id"] = paneId,
            ["lines"] = lines.ToString(),
            ["format"] = "text",
        });

    public static string QuestionToggle(string paneId, string promptId, string option) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = TypeQuestionToggle,
            ["pane_id"] = paneId,
            ["prompt_id"] = promptId,
            ["option"] = option,
        });

    public static string QuestionSubmit(string paneId, string promptId) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = TypeQuestionSubmit,
            ["pane_id"] = paneId,
            ["prompt_id"] = promptId,
        });
}

/// <summary>A pane snapshot as carried by `agents` and `agent_update`.</summary>
public sealed record AgentData(
    string PaneId,
    string Agent,
    string Status,
    string Cwd,
    string Project,
    string? Host)
{
    public static AgentData? From(JsonElement e)
    {
        if (e.ValueKind != JsonValueKind.Object) return null;
        var paneId = Str(e, "pane_id");
        if (string.IsNullOrEmpty(paneId)) return null;
        return new AgentData(
            paneId,
            Str(e, "agent") ?? string.Empty,
            Str(e, "status") ?? "unknown",
            Str(e, "cwd") ?? string.Empty,
            Str(e, "project") ?? string.Empty,
            Str(e, "host"));
    }

    internal static string? Str(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
}

/// <summary>A decoded server -> client message.</summary>
public sealed class ServerMessage
{
    public string Type { get; init; } = string.Empty;
    public List<AgentData> Agents { get; init; } = new();
    public AgentData? AgentUpdate { get; init; }
    public string? PaneId { get; init; }
    public string? Prompt { get; init; }
    public string? PromptId { get; init; }
    public List<string>? Options { get; init; }
    public List<string>? MultiOptions { get; init; }
    public List<string>? SelectedOptions { get; init; }
    public string? Interaction { get; init; }
    public bool Multi { get; init; }
    /// <summary>True when this `blocked` is a refresh of a prompt already shown — suppresses a repeat toast.</summary>
    public bool IsUpdate { get; init; }
    public string? Content { get; init; }

    public static ServerMessage? Parse(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return null;
            var type = AgentData.Str(root, "type");
            if (string.IsNullOrEmpty(type)) return null;

            var agents = new List<AgentData>();
            if (root.TryGetProperty("agents", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    var parsed = AgentData.From(item);
                    if (parsed is not null) agents.Add(parsed);
                }
            }

            // `agent` is an object on agent_update, a string elsewhere.
            AgentData? update = null;
            if (type == "agent_update" && root.TryGetProperty("agent", out var agentEl))
            {
                update = AgentData.From(agentEl);
            }

            return new ServerMessage
            {
                Type = type,
                Agents = agents,
                AgentUpdate = update,
                PaneId = AgentData.Str(root, "pane_id"),
                Prompt = AgentData.Str(root, "prompt"),
                PromptId = AgentData.Str(root, "prompt_id"),
                Options = StrList(root, "options"),
                MultiOptions = StrList(root, "multi_options"),
                SelectedOptions = StrList(root, "selected_options"),
                Interaction = AgentData.Str(root, "interaction"),
                Multi = Bool(root, "multi"),
                IsUpdate = Bool(root, "update"),
                Content = AgentData.Str(root, "content"),
            };
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static List<string>? StrList(JsonElement e, string name)
    {
        if (!e.TryGetProperty(name, out var v) || v.ValueKind != JsonValueKind.Array) return null;
        var list = new List<string>();
        foreach (var item in v.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.String)
            {
                var s = item.GetString();
                if (s is not null) list.Add(s);
            }
        }
        return list;
    }

    private static bool Bool(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;
}
