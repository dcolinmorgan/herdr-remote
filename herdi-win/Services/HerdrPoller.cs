using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;
using Herdi.Models;

namespace Herdi.Services;

/// <summary>One completed poll cycle across every configured host.</summary>
/// <param name="Agents">Every pane running an agent, from every host that answered.</param>
/// <param name="HostsAnswered">
/// Which hosts the pane list actually speaks for, labelled as <see cref="Agent.Host"/> is.
/// A host missing from here failed this cycle, and its panes must not be read as gone.
/// </param>
/// <param name="Reachable">True when at least one host answered.</param>
/// <param name="Error">Why hosts failed, or null when all of them answered.</param>
public sealed record PollResult(
    List<AgentData> Agents, HashSet<string> HostsAnswered, bool Reachable, string? Error);

/// <summary>
/// Polls `herdr pane list` on the local machine and over SSH, so agent state can be read
/// without a relay. Direct half of herdi-mac's RelayConnection (pollHerdr,
/// Sources/RelayConnection.swift:94).
///
/// Prompt extraction follows the relay rather than the mac app: read 50 lines, drop the
/// terminal chrome, keep the last 20, cap at 500 characters (herdr_relay.py:275). The mac
/// app keeps 6 unfiltered lines, which means the same blocked pane reads differently
/// there than through the relay — switching modes here shows the same card.
/// </summary>
public sealed partial class HerdrPoller : IDisposable
{
    /// <summary>Matches the relay's POLL_INTERVAL and the mac app's pollTimer.</summary>
    private static readonly TimeSpan Interval = TimeSpan.FromSeconds(2);

    /// <summary>
    /// The prompt preview budget. Only the last of these has to agree with the relay,
    /// which cuts to <c>content[-500:]</c> in blocked_message (herdr_relay.py): taking the
    /// other end would keep the scrollback and drop the question, and with it the options
    /// <see cref="DetectOptions"/> parses back out of the same text. How much each side
    /// reads to get there differs on purpose -- the relay fetches 100 lines for a pane
    /// view, this fetches 50 for a toast -- because the 500-character cut lands well
    /// inside both.
    /// </summary>
    private const int PromptReadLines = 50;
    private const int PromptKeepLines = 20;
    private const int PromptMaxChars = 500;

    private static readonly string[] PaneListArgs = { "pane", "list" };

    /// <summary>TOOL_OPTIONS (herdr_relay.py:71).</summary>
    private static readonly string[] ToolOptions =
        { "yes, single permission", "trust, always allow", "no (tab to edit)" };

    /// <summary>SUBAGENT_OPTIONS (herdr_relay.py:72).</summary>
    private static readonly string[] SubagentOptions =
        { "approve all pending", "configure individually", "exit (cancel subagents)" };

    /// <summary>
    /// Terminal chrome that is not part of the prompt. Port of CHROME_RE (herdr_relay.py:73).
    ///
    /// Source-generated rather than RegexOptions.Compiled. Compiled builds the matcher with
    /// Reflection.Emit on first use, which means a dynamic assembly and a JIT pass held for
    /// the life of the process - a poor trade for a pattern that only runs when a pane turns
    /// out to be blocked. The generator does the same work at build time for nothing at run
    /// time.
    /// </summary>
    [GeneratedRegex(@"^[\s─━═_—│|◔◑◕●]+$|Kiro\s[·•]|esc to cancel|type to queue|^\s*[◔◑◕●]\s+(Shell|Bash)")]
    private static partial Regex Chrome();

    private readonly SettingsStore _settings;
    private readonly HerdrCli _cli;
    private readonly Action<Action> _post;
    private CancellationTokenSource? _cts;

    public HerdrPoller(SettingsStore settings, HerdrCli cli, Action<Action> post)
    {
        _settings = settings;
        _cli = cli;
        _post = post;
    }

    /// <summary>Raised on the UI thread after each cycle, successful or not.</summary>
    public event Action<PollResult>? Polled;

    public void Start()
    {
        Stop();
        _cts = new CancellationTokenSource();
        _ = LoopAsync(_cts.Token);
    }

    public void Stop()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = null;
    }

    private async Task LoopAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                var result = await PollOnceAsync(token);
                if (token.IsCancellationRequested) break;
                _post(() => Polled?.Invoke(result));
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                // A poll cycle must never take the loop down with it — the relay's own
                // poll_loop swallows per-cycle failures the same way. The token is checked
                // first so a teardown mid-cycle is not reported as a connection failure,
                // and nothing is posted to a dispatcher that is already going away.
                if (token.IsCancellationRequested) break;
                _post(() => Polled?.Invoke(
                    new PollResult(new List<AgentData>(), new HashSet<string>(), false, ex.Message)));
            }

            try
            {
                await Task.Delay(Interval, token);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private async Task<PollResult> PollOnceAsync(CancellationToken token)
    {
        // Null stands for the local host, exactly as the relay's remote=None does.
        var hosts = new List<string?>();
        if (_cli.LocalBinary is not null) hosts.Add(null);
        foreach (var remote in _settings.Remotes) hosts.Add(remote);

        if (hosts.Count == 0)
        {
            return new PollResult(
                new List<AgentData>(), new HashSet<string>(), false,
                "no herdr binary and no SSH hosts — add one in Settings");
        }

        // Hosts run concurrently: each has its own gate in HerdrCli, and one unreachable
        // remote would otherwise spend its whole connect timeout delaying the others.
        var polls = await Task.WhenAll(hosts.Select(host => PollHostAsync(host, token)));

        var agents = new List<AgentData>();
        var answered = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var failures = new List<string>();
        foreach (var (host, panes, error) in polls)
        {
            if (error is not null)
            {
                failures.Add($"{host}: {error}");
                continue;
            }
            answered.Add(host);
            agents.AddRange(panes);
        }

        return new PollResult(
            agents,
            answered,
            answered.Count > 0,
            failures.Count == 0 ? null : string.Join("; ", failures));
    }

    private async Task<(string Host, List<AgentData> Panes, string? Error)> PollHostAsync(
        string? host, CancellationToken token)
    {
        var label = host ?? "local";
        var result = await _cli.RunAsync(host, PaneListArgs, token);
        return result.Ok
            ? (label, ParsePanes(result.Output, label), null)
            : (label, new List<AgentData>(), result.Error ?? "unreachable");
    }

    /// <summary>
    /// Parse `herdr pane list` output. Same projection as the relay's
    /// get_agents_from_host (herdr_relay.py:226), except remote pane ids are prefixed with
    /// their host: unlike the relay, nothing here keeps a pane-to-host side table, so the
    /// id has to carry the host or two machines' panes could collide under one id.
    /// </summary>
    internal static List<AgentData> ParsePanes(string json, string host)
    {
        var agents = new List<AgentData>();
        if (string.IsNullOrWhiteSpace(json)) return agents;

        try
        {
            // A UTF-8 BOM survives decoding as U+FEFF, which JsonDocument rejects.
            using var document = JsonDocument.Parse(json.Trim().TrimStart('﻿'));
            if (!document.RootElement.TryGetProperty("result", out var result) ||
                !result.TryGetProperty("panes", out var panes) ||
                panes.ValueKind != JsonValueKind.Array)
            {
                return agents;
            }

            var remote = !string.Equals(host, "local", StringComparison.OrdinalIgnoreCase);
            foreach (var pane in panes.EnumerateArray())
            {
                var name = AgentData.Str(pane, "agent");
                var paneId = AgentData.Str(pane, "pane_id");
                if (string.IsNullOrEmpty(name) || string.IsNullOrEmpty(paneId)) continue;

                var cwd = AgentData.Str(pane, "cwd") ?? string.Empty;
                agents.Add(new AgentData(
                    remote ? host + ":" + paneId : paneId,
                    name,
                    AgentData.Str(pane, "agent_status") ?? "unknown",
                    cwd,
                    ProjectName(cwd),
                    host));
            }
        }
        catch (JsonException)
        {
            // Half-written output or an error page instead of JSON: treat as no panes,
            // as both the relay and the mac app do.
        }

        return agents;
    }

    /// <summary>
    /// Prompt and response options for a freshly blocked pane, the pair the relay puts in
    /// its `blocked` message.
    /// </summary>
    public async Task<(string Prompt, IReadOnlyList<string> Options)> ReadPromptAsync(
        Agent agent, CancellationToken token = default)
    {
        // `visible`, as the relay does (PROMPT_READ_SOURCE). Asking `recent` for more rows than
        // the pane shows makes herdr harvest the extra ones by walking the agent's own scroll
        // interface, which moves the operator's terminal; a read fired by a status change may
        // never do that. Live-checked on a 48-row idle claude pane: `--lines 200 --source recent`
        // returned 101 rows of genuine older output (`visible` is exactly its tail), seconds cold
        // and instant warm. 50 rows clears most viewports -- but that one it does not.
        var content = await ReadPaneAsync(agent, PromptReadLines, "visible", token);
        var kept = new List<string>();
        foreach (var line in content.Replace("\r\n", "\n").Split('\n'))
        {
            if (!string.IsNullOrWhiteSpace(line) && !Chrome().IsMatch(line)) kept.Add(line);
        }
        if (kept.Count > PromptKeepLines) kept.RemoveRange(0, kept.Count - PromptKeepLines);

        var prompt = string.Join("\n", kept);
        if (prompt.Length > PromptMaxChars) prompt = prompt[^PromptMaxChars..];
        return (prompt, DetectOptions(prompt));
    }

    /// <summary>
    /// Raw pane content, for the `pane_content` equivalent in direct mode. `recent` is right for
    /// a read the user asked for -- reaching past the viewport is the whole point -- but wrong for
    /// anything automatic, hence the parameter. See ReadPromptAsync.
    /// </summary>
    public async Task<string> ReadPaneAsync(
        Agent agent, int lines, string source = "recent", CancellationToken token = default)
    {
        var result = await _cli.RunAsync(
            HostOf(agent),
            new[] { "pane", "read", PaneIdOf(agent), "--lines", lines.ToString(), "--source", source },
            token);
        return result.Ok ? result.Output : string.Empty;
    }

    /// <summary>
    /// Answer a prompt. Two commands, as the relay does (herdr_relay.py:534): send-text
    /// and then a separate Enter. The mac app appends "\n" to the text instead, which
    /// herdr does not always read as a submit.
    /// </summary>
    public async Task<HerdrResult> RespondAsync(Agent agent, string text, CancellationToken token = default)
    {
        var host = HostOf(agent);
        var paneId = PaneIdOf(agent);

        var sent = await _cli.RunAsync(host, new[] { "pane", "send-text", paneId, text }, token);
        if (!sent.Ok) return sent;
        return await _cli.RunAsync(host, new[] { "pane", "send-keys", paneId, "Enter" }, token);
    }

    /// <summary>
    /// Submit free-form text to an agent that is not sitting on a permission prompt.
    /// `herdr agent prompt` is what the relay's agent_prompt handler runs
    /// (herdr_relay.py:617) precisely because it knows how each agent CLI takes a
    /// submission; typing into the pane and pressing Enter does not always land.
    /// </summary>
    public Task<HerdrResult> PromptAsync(Agent agent, string text, CancellationToken token = default) =>
        _cli.RunAsync(
            HostOf(agent),
            new[] { "agent", "prompt", PaneIdOf(agent), text },
            token);

    /// <summary>
    /// Send ^C. Spelled "C-c" like the relay's SAFE_KEYS, not the "Ctrl+c" the mac app
    /// passes; the relay proves the CLI takes this spelling, and it keeps one constant
    /// across both modes.
    /// </summary>
    public Task<HerdrResult> InterruptAsync(Agent agent, CancellationToken token = default) =>
        _cli.RunAsync(
            HostOf(agent),
            new[] { "pane", "send-keys", PaneIdOf(agent), Protocol.InterruptKey },
            token);

    /// <summary>Null for a local agent, else the SSH target to run the command on.</summary>
    private static string? HostOf(Agent agent) => agent.IsRemote ? agent.Host : null;

    /// <summary>
    /// The pane id as its own host knows it, with the host prefix removed. Matched against
    /// the exact host rather than cut at the first colon — herdr pane ids contain colons
    /// themselves, so the mac app's "drop up to the first colon" truncates local ids.
    /// </summary>
    private static string PaneIdOf(Agent agent)
    {
        var prefix = agent.Host + ":";
        return agent.IsRemote && agent.Id.StartsWith(prefix, StringComparison.Ordinal)
            ? agent.Id[prefix.Length..]
            : agent.Id;
    }

    /// <summary>Port of detect_options (herdr_relay.py:281), including its TOOL_OPTIONS fallback.</summary>
    private static IReadOnlyList<string> DetectOptions(string content)
    {
        var lower = content.ToLowerInvariant();
        // Order matters: a subagent prompt that also quotes the tool wording reads as a
        // tool prompt for the relay, and both modes should offer the same buttons.
        if (lower.Contains("yes, single permission")) return ToolOptions;
        if (lower.Contains("approve all pending")) return SubagentOptions;
        return ToolOptions;
    }

    /// <summary>
    /// os.path.basename(cwd), tolerating the trailing slash that would make it empty.
    /// Remote paths are POSIX, which Path.GetFileName handles alongside Windows ones.
    /// </summary>
    private static string ProjectName(string cwd)
    {
        var trimmed = cwd.TrimEnd('/', '\\');
        return trimmed.Length == 0 ? string.Empty : Path.GetFileName(trimmed);
    }

    public void Dispose() => Stop();
}
