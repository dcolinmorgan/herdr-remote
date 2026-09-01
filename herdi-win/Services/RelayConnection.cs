using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using Herdi.Models;

namespace Herdi.Services;

/// <summary>One relay's connection state, for the tray's Relays submenu.</summary>
public sealed record RelayStatus(string Url, string Label, bool IsConnected, string? Error);

/// <summary>
/// The app's single source of agent state, over either transport. Port of herdi-mac's
/// RelayConnection (Sources/RelayConnection.swift), including both of its modes: a
/// WebSocket to the relay, or <see cref="HerdrPoller"/> driving the herdr CLI here —
/// locally and over SSH.
///
/// Both modes share one merge path (<see cref="Upsert"/>), so grouping, toasts and the
/// answered-elsewhere retraction behave identically whichever is active. The mac app
/// keeps two separate merge loops and they have drifted apart.
///
/// Relay mode watches <em>every</em> configured relay at once rather than one at a time.
/// It used to hold a single socket, so a second relay meant editing the URL in Settings —
/// which is not switching between two views of one herd, it is throwing one herd away. The
/// panes of all of them land in one list and are triaged together, because NEEDS YOU is
/// the ordering that matters and a blocked agent does not become less urgent for being on
/// the other relay. Which relay a row came from is a tag on the row
/// (<see cref="Agent.SourceLabel"/>), shown only once there is more than one.
///
/// Three things follow from that and each was a bug the single-socket shape could not have:
///
/// - <b>Pane ids are only unique within one herdr.</b> Every relay hands out `w1:p1`, so
///   <see cref="Agent.Id"/> carries the source key and <see cref="Agent.PaneId"/> is what
///   goes back on the wire.
/// - <b>A snapshot speaks for its own relay only.</b> The `agents` message is a complete
///   list, but complete for the relay that sent it; the sweep that drops vanished panes is
///   scoped to that source or the relays would delete each other's rows every poll.
/// - <b>Connected is "any", not "all".</b> One unreachable tunnel must not make the dot red
///   while three relays are answering; it names itself in the tray instead.
/// </summary>
public sealed class RelayConnection : INotifyPropertyChanged, IDisposable
{
    private readonly Action<Action> _post;
    private readonly SettingsStore _settings;
    private readonly HerdrCli _cli;
    private readonly HerdrPoller _direct;
    private readonly List<RelaySocket> _sockets = new();

    public RelayConnection(SettingsStore settings, Action<Action>? post = null)
    {
        _settings = settings;
        // Marshals collection/property mutations onto the UI thread. Falls back to
        // inline execution so the class stays usable without a WPF Dispatcher.
        _post = post ?? (a => a());
        _hostAddress = DescribeRelays(settings.RelayUrls);
        _cli = new HerdrCli(settings);
        _direct = new HerdrPoller(settings, _cli, _post);
        _direct.Polled += OnPolled;
    }

    /// <summary>Which transport is live. Set by <see cref="Connect"/> from the settings.</summary>
    public ConnectionMode Mode { get; private set; }

    public ObservableCollection<Agent> Agents { get; } = new();

    private bool _isConnected;
    public bool IsConnected { get => _isConnected; private set => Set(ref _isConnected, value); }

    private string _hostAddress;
    public string HostAddress { get => _hostAddress; private set => Set(ref _hostAddress, value); }

    private string? _lastError;
    public string? LastError { get => _lastError; private set => Set(ref _lastError, value); }

    /// <summary>
    /// Per-relay state, in the configured order. Empty in direct mode. The tray lists these
    /// individually because the aggregate above deliberately hides which relay is down, and
    /// "one of your three relays is unreachable" is not something a single dot can say.
    /// </summary>
    public IReadOnlyList<RelayStatus> Relays =>
        _sockets.Select(s => new RelayStatus(s.Url, s.Label, s.IsConnected, s.LastError)).ToList();

    /// <summary>Raised when an agent newly enters the blocked state (drives the toast).</summary>
    public event Action<Agent>? AgentBlocked;

    /// <summary>Raised when a blocked agent is answered elsewhere, so a stale toast can be pulled.</summary>
    public event Action<Agent>? AgentUnblocked;

    /// <summary>
    /// Raised when an agent that was working goes idle — it finished. The counterpart of
    /// <see cref="AgentBlocked"/>: one says the agent needs you before it can continue, this
    /// one says it no longer needs anything.
    /// </summary>
    public event Action<Agent>? AgentFinished;

    /// <summary>Raised for `pane_content` replies to <see cref="ReadPane"/>.</summary>
    public event Action<string, string>? PaneContentReceived;

    // --- Connection lifecycle

    /// <summary>
    /// Start (or restart) whichever transport the settings ask for. Called again after the
    /// settings dialog saves, which is also when a mode switch takes effect.
    /// </summary>
    public void Connect(string? urlOverride = null)
    {
        _direct.Stop();
        // An explicit URL is only ever passed to force a relay connection.
        var mode = urlOverride is not null ? ConnectionMode.Relay : _settings.Mode;
        if (mode != Mode)
        {
            // The two transports namespace pane ids differently and may not even cover the
            // same hosts, so nothing the old one reported can be reconciled with the new.
            Agents.Clear();
            IsConnected = false;
        }
        Mode = mode;

        if (Mode == ConnectionMode.Direct)
        {
            StopSockets();
            // The herdr path setting may have changed under us.
            _cli.Refresh();
            HostAddress = DescribeDirectSources();
            LastError = null;
            IsConnected = false;
            _direct.Start();
            return;
        }

        var urls = urlOverride is not null
            ? new List<string> { urlOverride }
            : _settings.RelayUrls.ToList();
        ReconcileSockets(urls, _settings.RelayToken);
        RefreshAggregate();
    }

    /// <summary>
    /// Bring the live sockets in line with the configured URLs.
    ///
    /// Unchanged relays keep the socket they already have rather than being torn down and
    /// rebuilt. This is not only about churn: <see cref="Connect"/> runs on <em>every</em>
    /// settings save, including one that touched nothing but the panel's opacity, and
    /// restarting a healthy relay there would drop its connection and re-run the whole
    /// backoff for a colour change.
    /// </summary>
    private void ReconcileSockets(IReadOnlyList<string> urls, string token)
    {
        var kept = new List<RelaySocket>();
        foreach (var url in urls)
        {
            if (string.IsNullOrWhiteSpace(url)) continue;
            var existing = _sockets.FirstOrDefault(s => s.Matches(url, token));
            if (existing is not null && !kept.Contains(existing))
            {
                kept.Add(existing);
                continue;
            }
            var socket = new RelaySocket(url, token, _post);
            socket.MessageReceived += Handle;
            socket.StateChanged += _ => RefreshAggregate();
            kept.Add(socket);
        }

        foreach (var gone in _sockets.Where(s => !kept.Contains(s)))
        {
            gone.MessageReceived -= Handle;
            gone.Stop();
            gone.Dispose();
        }

        _sockets.Clear();
        _sockets.AddRange(kept);

        // A relay that is no longer configured leaves nothing behind: its panes are not
        // "vanished from a snapshot" — nobody is going to send one — so the per-source
        // sweep in ApplySnapshot would never reach them.
        var live = new HashSet<string>(_sockets.Select(s => s.Url), StringComparer.Ordinal);
        for (var i = Agents.Count - 1; i >= 0; i--)
        {
            if (!live.Contains(Agents[i].SourceId)) Agents.RemoveAt(i);
        }

        foreach (var socket in _sockets) socket.Start();
        ApplySourceVisibility();
    }

    private void StopSockets()
    {
        foreach (var socket in _sockets)
        {
            socket.MessageReceived -= Handle;
            socket.Stop();
            socket.Dispose();
        }
        _sockets.Clear();
    }

    /// <summary>
    /// A row only names its relay once there is more than one to tell apart. Applied to
    /// every agent, not just new ones: adding a second relay has to light the tag up on the
    /// panes of the first, and removing it has to take the tag back off.
    /// </summary>
    private void ApplySourceVisibility()
    {
        var show = _sockets.Count > 1;
        foreach (var agent in Agents) agent.ShowSource = show;
    }

    /// <summary>
    /// Fold every relay's state into the one dot, one line and one error the UI binds to.
    /// Connected is <em>any</em>: with three relays and one tunnel down, two thirds of the
    /// herd is live and a red dot would be a lie about the other two.
    /// </summary>
    private void RefreshAggregate()
    {
        if (Mode == ConnectionMode.Direct) return;

        IsConnected = _sockets.Any(s => s.IsConnected);
        HostAddress = DescribeRelays(_sockets.Select(s => s.Url).ToList(), _sockets.Count(s => s.IsConnected));

        // Whose error to show: the first relay that is down and has something to say. With
        // one relay that is exactly what it used to be; with several the label is prefixed,
        // because "connection refused" on its own does not say which relay refused it.
        var failing = _sockets.FirstOrDefault(s => !s.IsConnected && !string.IsNullOrWhiteSpace(s.LastError));
        LastError = failing is null
            ? null
            : _sockets.Count > 1 ? $"{failing.Label}: {failing.LastError}" : failing.LastError;
    }

    /// <summary>
    /// What the tray prints under the status line. One relay names itself in full — the URL
    /// is short and it is the thing you would check. Several would not fit and would not be
    /// worth the width, so they become a count with how many are answering.
    /// </summary>
    private static string DescribeRelays(IReadOnlyList<string> urls, int? connected = null)
    {
        if (urls.Count == 0) return "no relay configured";
        if (urls.Count == 1) return urls[0];
        return connected is null
            ? $"{urls.Count} relays"
            : $"{urls.Count} relays · {connected} connected";
    }

    /// <summary>Human-readable summary of what direct mode is polling, for the tray.</summary>
    public string DescribeDirectSources()
    {
        var parts = new List<string>();
        if (_cli.LocalBinary is not null) parts.Add("local");
        var remotes = _settings.Remotes;
        if (remotes.Count > 0) parts.Add(remotes.Count == 1 ? remotes[0] : $"{remotes.Count} hosts");
        return parts.Count == 0 ? "direct · nothing configured" : "direct · " + string.Join(" + ", parts);
    }

    public void Disconnect()
    {
        _direct.Stop();
        StopSockets();
        _post(() => IsConnected = false);
    }

    // --- Inbound message handling (mirrors handleWS in RelayConnection.swift)

    private void Handle(RelaySocket source, ServerMessage msg)
    {
        switch (msg.Type)
        {
            case "agents":
                // A relay snapshot never carries prompts, and the relay follows it with a
                // `blocked` message per newly blocked pane, so newly-blocked is ignored here.
                ApplySnapshot(source.Url, source.Label, msg.Agents);
                break;

            case "agent_update":
                if (msg.AgentUpdate is not null) Upsert(source.Url, source.Label, msg.AgentUpdate, out _);
                break;

            case "blocked":
            {
                if (msg.PaneId is null) break;
                var agent = Find(source.Url, msg.PaneId);
                if (agent is null) break;
                agent.Prompt = msg.Prompt;
                agent.PromptId = msg.PromptId;
                agent.Options = msg.Options;
                agent.Interaction = msg.Interaction;
                agent.IsMultiSelect = msg.Multi;
                Replace(agent.MultiOptions, msg.MultiOptions);
                Replace(agent.SelectedOptions, msg.SelectedOptions);
                agent.Status = AgentStatus.Blocked;
                // `update: true` means the prompt was merely refreshed — don't re-toast.
                if (!msg.IsUpdate) AgentBlocked?.Invoke(agent);
                break;
            }

            case "pane_content":
                if (msg.PaneId is not null && msg.Content is not null)
                {
                    // Raised as the composite id: two relays can each be showing a `w1:p1`,
                    // and the pane view compares this against the agent it has open.
                    // Composed rather than looked up, so a read that lands before the
                    // snapshot introducing its pane still reaches the view.
                    PaneContentReceived?.Invoke(
                        Agent.ComposeId(source.Url, msg.PaneId), msg.Content);
                }
                break;
        }
    }

    /// <summary>
    /// Merge a complete list of panes and drop whatever is no longer in it.
    /// </summary>
    /// <param name="sourceId">
    /// The source this snapshot speaks for. The sweep below is scoped to it: a relay's
    /// `agents` message is complete for that relay and says nothing whatever about the
    /// panes of any other, so an unscoped sweep would have every relay delete every other
    /// relay's rows on each snapshot and the list would flicker between them.
    /// </param>
    /// <param name="sourceLabel">What a row calls that source.</param>
    /// <param name="snapshot">Every pane the source knows about.</param>
    /// <param name="hostsCovered">
    /// Hosts this snapshot actually speaks for, or null when it speaks for all of them (a
    /// relay `agents` message does). Panes on a host that is absent are left alone: a
    /// failed poll says nothing about that host's panes, and dropping them would make a
    /// flapping SSH connection empty and refill the list every couple of seconds — and
    /// re-toast every blocked agent on it each time it came back.
    /// </param>
    /// <returns>The agents that entered the blocked state in this snapshot.</returns>
    private List<Agent> ApplySnapshot(
        string sourceId,
        string sourceLabel,
        IEnumerable<AgentData> snapshot,
        ISet<string>? hostsCovered = null)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var newlyBlocked = new List<Agent>();

        foreach (var data in snapshot)
        {
            var agent = Upsert(sourceId, sourceLabel, data, out var becameBlocked);
            seen.Add(agent.Id);
            if (becameBlocked) newlyBlocked.Add(agent);
        }

        for (var i = Agents.Count - 1; i >= 0; i--)
        {
            var agent = Agents[i];
            if (!string.Equals(agent.SourceId, sourceId, StringComparison.Ordinal)) continue;
            if (seen.Contains(agent.Id)) continue;
            if (hostsCovered is not null && !hostsCovered.Contains(agent.Host)) continue;
            Agents.RemoveAt(i);
        }

        return newlyBlocked;
    }

    private Agent Upsert(string sourceId, string sourceLabel, AgentData data, out bool becameBlocked)
    {
        var existing = Find(sourceId, data.PaneId);
        var status = AgentStatusParser.Parse(data.Status);

        if (existing is not null)
        {
            var wasBlocked = existing.Status == AgentStatus.Blocked;
            var wasWorking = existing.Status == AgentStatus.Working;
            becameBlocked = status == AgentStatus.Blocked && !wasBlocked;
            existing.Name = data.Agent;
            existing.Status = status;
            existing.Project = data.Project;
            existing.Cwd = data.Cwd;
            existing.Host = data.Host ?? "local";
            existing.SourceLabel = sourceLabel;
            // Answered from another client: drop the prompt and retract the toast.
            if (wasBlocked && status != AgentStatus.Blocked)
            {
                existing.ClearPrompt();
                AgentUnblocked?.Invoke(existing);
            }

            // Finished. Strictly Idle, not merely "no longer Working": Unknown is what a
            // status we could not parse becomes, so treating it as finished would fire a
            // notification every time a poll came back garbled. Blocked is not finished
            // either — AgentBlocked already speaks for that one.
            if (wasWorking && status == AgentStatus.Idle) AgentFinished?.Invoke(existing);

            return existing;
        }

        becameBlocked = status == AgentStatus.Blocked;
        var agent = new Agent(
            data.PaneId,
            data.Agent,
            status,
            data.Project,
            data.Cwd,
            data.Host ?? "local",
            sourceId,
            sourceLabel)
        {
            ShowSource = _sockets.Count > 1,
        };
        Agents.Add(agent);
        return agent;
    }

    // --- Direct mode

    /// <summary>
    /// Apply one poll cycle. Where the relay pushes a `blocked` message carrying the
    /// prompt, direct mode only learns the status from `pane list` — so the prompt is read
    /// afterwards, and the toast waits until there is something to show in it.
    /// </summary>
    private void OnPolled(PollResult result)
    {
        // A cycle already in flight when the mode changed must not touch the agent list.
        if (Mode != ConnectionMode.Direct) return;

        IsConnected = result.Reachable;
        LastError = result.Error;

        // Direct mode is one source by construction — the poller has already merged every
        // configured host into this one result — so it needs no label of its own.
        var newlyBlocked = ApplySnapshot(
            Agent.DirectSource, string.Empty, result.Agents, result.HostsAnswered);
        foreach (var agent in newlyBlocked) _ = FillPromptAsync(agent);
    }

    private async Task FillPromptAsync(Agent agent)
    {
        var (prompt, options) = await _direct.ReadPromptAsync(agent);
        _post(() =>
        {
            // Reading takes an SSH round trip, in which the prompt may have been answered
            // from the terminal itself.
            if (agent.Status != AgentStatus.Blocked) return;
            agent.Prompt = prompt;
            agent.Options = options;
            AgentBlocked?.Invoke(agent);
        });
    }

    private async Task ReadPaneDirectAsync(Agent agent, int lines)
    {
        var content = await _direct.ReadPaneAsync(agent, lines);
        _post(() => PaneContentReceived?.Invoke(agent.Id, content));
    }

    /// <summary>Surface a failed direct-mode command the same way a socket error surfaces.</summary>
    private async Task ReportAsync(Task<HerdrResult> command)
    {
        var result = await command;
        if (!result.Ok && result.Error is not null) _post(() => LastError = result.Error);
    }

    /// <summary>Find by the composite <see cref="Agent.Id"/> — what a toast carries.</summary>
    public Agent? Find(string id) => Agents.FirstOrDefault(a => a.Id == id);

    /// <summary>
    /// Find by the pair a wire message arrives as. The pane id alone is not enough: every
    /// herdr numbers its own panes from w1:p1, so two relays routinely report the same one.
    /// </summary>
    public Agent? Find(string sourceId, string paneId) =>
        Agents.FirstOrDefault(a =>
            string.Equals(a.SourceId, sourceId, StringComparison.Ordinal) &&
            string.Equals(a.PaneId, paneId, StringComparison.Ordinal));

    private static void Replace(ObservableCollection<string> target, List<string>? source)
    {
        target.Clear();
        if (source is null) return;
        foreach (var item in source) target.Add(item);
    }

    // --- Outbound commands

    /// <summary>
    /// Answer a permission prompt. Only the relay's SAFE_RESPONSES values survive a
    /// `respond`; anything else is routed through `agent_prompt` so free-form text
    /// isn't silently dropped with "response not in allowlist". Direct mode has no such
    /// allowlist to satisfy — that guard belongs to the relay, not to herdr — so the text
    /// goes to the pane as typed.
    /// </summary>
    public void Respond(Agent agent, string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return;

        if (Mode == ConnectionMode.Direct)
        {
            _ = ReportAsync(_direct.RespondAsync(agent, text.Trim()));
        }
        else
        {
            Send(agent, Protocol.SafeResponses.Contains(text.Trim())
                ? Protocol.Respond(agent.PaneId, agent.PromptId, text)
                : Protocol.AgentPrompt(agent.PaneId, text));
        }

        agent.Status = AgentStatus.Working;
        agent.ClearPrompt();
    }

    /// <summary>
    /// Send free-form text to an agent that is not waiting on a permission prompt — the
    /// pane view's input box. This is `agent_prompt` in both modes: the relay's handler
    /// runs `herdr agent prompt` (herdr_relay.py:617), and direct mode runs the same verb
    /// itself, so a message submits the way herdr intends rather than being typed into
    /// the pane and hoping Enter takes.
    /// </summary>
    public void SendPrompt(Agent agent, string text)
    {
        var trimmed = text.Trim();
        if (trimmed.Length == 0) return;
        if (trimmed.Length > Protocol.MaxPromptLength) trimmed = trimmed[..Protocol.MaxPromptLength];

        if (Mode == ConnectionMode.Direct) _ = ReportAsync(_direct.PromptAsync(agent, trimmed));
        else Send(agent, Protocol.AgentPrompt(agent.PaneId, trimmed));
    }

    /// <summary>Send ^C to the pane. The relay's key allowlist spells this "C-c".</summary>
    public void Interrupt(Agent agent)
    {
        if (Mode == ConnectionMode.Direct) _ = ReportAsync(_direct.InterruptAsync(agent));
        else Send(agent, Protocol.SendKeys(agent.PaneId, Protocol.InterruptKey));
    }

    public void ReadPane(Agent agent, int lines = 30)
    {
        if (Mode == ConnectionMode.Direct) _ = ReadPaneDirectAsync(agent, lines);
        else Send(agent, Protocol.ReadPane(agent.PaneId, lines));
    }

    /// <summary>
    /// Toggle one option of a multi-select question. NOTE: the current relay has no
    /// handler for `question_toggle` — herdi-mac, herdi-ios, the web app and the TUI
    /// all send it and it is silently ignored. Kept for parity so this client works
    /// the moment the relay grows support. Relay mode only, as on macOS: multi-select is a
    /// relay-protocol notion with no herdr CLI verb behind it.
    /// </summary>
    public void ToggleQuestionOption(Agent agent, string option)
    {
        if (agent.PromptId is null || Mode == ConnectionMode.Direct) return;
        Send(agent, Protocol.QuestionToggle(agent.PaneId, agent.PromptId, option));
        if (agent.SelectedOptions.Contains(option)) agent.SelectedOptions.Remove(option);
        else agent.SelectedOptions.Add(option);
    }

    /// <summary>Submit a multi-select question. Same caveats as above.</summary>
    public void SubmitQuestion(Agent agent)
    {
        if (agent.PromptId is null || Mode == ConnectionMode.Direct) return;
        Send(agent, Protocol.QuestionSubmit(agent.PaneId, agent.PromptId));
        agent.Status = AgentStatus.Working;
        agent.ClearPrompt();
    }

    /// <summary>
    /// Send to the relay this agent came from, and to no other. Routing by the agent rather
    /// than by "the socket" is the whole of what multi-relay costs at the outbound end: an
    /// `agent_prompt` for `w1:p1` broadcast to every relay would land on a different pane on
    /// each of them.
    /// </summary>
    private void Send(Agent agent, string json)
    {
        var socket = _sockets.FirstOrDefault(s =>
            string.Equals(s.Url, agent.SourceId, StringComparison.Ordinal));
        if (socket is null)
        {
            // The relay was removed from Settings while its pane was still on screen.
            LastError = $"{agent.SourceLabel} is no longer configured";
            return;
        }
        socket.Send(json);
    }

    public void Dispose()
    {
        _direct.Dispose();
        StopSockets();
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
