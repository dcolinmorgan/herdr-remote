using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Net.WebSockets;
using System.Runtime.CompilerServices;
using System.Text;
using Herdi.Models;

namespace Herdi.Services;

/// <summary>
/// The app's single source of agent state, over either transport. Port of herdi-mac's
/// RelayConnection (Sources/RelayConnection.swift), including both of its modes: a
/// WebSocket to the relay, or <see cref="HerdrPoller"/> driving the herdr CLI here —
/// locally and over SSH.
///
/// Both modes share one merge path (<see cref="Upsert"/>), so grouping, toasts and the
/// answered-elsewhere retraction behave identically whichever is active. The mac app
/// keeps two separate merge loops and they have drifted apart.
/// </summary>
public sealed class RelayConnection : INotifyPropertyChanged, IDisposable
{
    private readonly Action<Action> _post;
    private readonly SettingsStore _settings;
    private readonly HerdrCli _cli;
    private readonly HerdrPoller _direct;
    private ClientWebSocket? _socket;
    private CancellationTokenSource? _cts;
    private int _reconnectAttempt;
    private bool _disposed;

    public RelayConnection(SettingsStore settings, Action<Action>? post = null)
    {
        _settings = settings;
        // Marshals collection/property mutations onto the UI thread. Falls back to
        // inline execution so the class stays usable without a WPF Dispatcher.
        _post = post ?? (a => a());
        _hostAddress = settings.RelayUrl;
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

    /// <summary>Raised when an agent newly enters the blocked state (drives the toast).</summary>
    public event Action<Agent>? AgentBlocked;

    /// <summary>Raised when a blocked agent is answered elsewhere, so a stale toast can be pulled.</summary>
    public event Action<Agent>? AgentUnblocked;

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
            // The herdr path setting may have changed under us.
            _cli.Refresh();
            _cts?.Cancel();
            HostAddress = DescribeDirectSources();
            LastError = null;
            _direct.Start();
            return;
        }

        var url = urlOverride ?? _settings.RelayUrl;
        if (string.IsNullOrWhiteSpace(url)) return;
        HostAddress = url;
        _reconnectAttempt = 0;
        _ = RunLoopAsync(url);
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

    public async void Disconnect()
    {
        _direct.Stop();
        _cts?.Cancel();
        var socket = _socket;
        _socket = null;
        if (socket is { State: WebSocketState.Open })
        {
            try
            {
                await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None);
            }
            catch (Exception)
            {
                // Closing a socket that is already torn down is not actionable.
            }
        }
        socket?.Dispose();
        _post(() => IsConnected = false);
    }

    /// <summary>
    /// Connect, pump messages, and reconnect with exponential backoff capped at 30s —
    /// same schedule as herdi-mac's scheduleReconnect (min(2^min(attempt,5), 30)).
    /// </summary>
    private async Task RunLoopAsync(string url)
    {
        _cts?.Cancel();
        _cts = new CancellationTokenSource();
        var token = _cts.Token;

        while (!token.IsCancellationRequested && !_disposed)
        {
            try
            {
                using var socket = new ClientWebSocket();
                if (!string.IsNullOrEmpty(_settings.RelayToken))
                {
                    // The relay accepts either an Authorization header or ?token=
                    // (herdr_relay.py:386). The header keeps the secret out of logs.
                    socket.Options.SetRequestHeader("Authorization", "Bearer " + _settings.RelayToken);
                }
                _socket = socket;

                await socket.ConnectAsync(new Uri(url), token);
                _post(() =>
                {
                    IsConnected = true;
                    LastError = null;
                });
                _reconnectAttempt = 0;

                await PumpAsync(socket, token);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                _post(() =>
                {
                    IsConnected = false;
                    LastError = ex.Message;
                });
            }

            if (token.IsCancellationRequested || _disposed) break;

            _post(() => IsConnected = false);
            _reconnectAttempt++;
            var delaySeconds = Math.Min(1 << Math.Min(_reconnectAttempt, 5), 30);
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(delaySeconds), token);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private async Task PumpAsync(ClientWebSocket socket, CancellationToken token)
    {
        var buffer = new byte[16 * 1024];
        var builder = new StringBuilder();

        while (socket.State == WebSocketState.Open && !token.IsCancellationRequested)
        {
            WebSocketReceiveResult result;
            do
            {
                result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), token);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    return;
                }
                builder.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
            }
            while (!result.EndOfMessage);

            var text = builder.ToString();
            builder.Clear();
            var msg = ServerMessage.Parse(text);
            if (msg is not null) _post(() => Handle(msg));
        }
    }

    // --- Inbound message handling (mirrors handleWS in RelayConnection.swift)

    private void Handle(ServerMessage msg)
    {
        switch (msg.Type)
        {
            case "agents":
                // A relay snapshot never carries prompts, and the relay follows it with a
                // `blocked` message per newly blocked pane, so newly-blocked is ignored here.
                ApplySnapshot(msg.Agents);
                break;

            case "agent_update":
                if (msg.AgentUpdate is not null) Upsert(msg.AgentUpdate, out _);
                break;

            case "blocked":
            {
                if (msg.PaneId is null) break;
                var agent = Find(msg.PaneId);
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
                    PaneContentReceived?.Invoke(msg.PaneId, msg.Content);
                }
                break;
        }
    }

    /// <summary>
    /// Merge a complete list of panes and drop whatever is no longer in it.
    /// </summary>
    /// <param name="snapshot">Every pane the source knows about.</param>
    /// <param name="hostsCovered">
    /// Hosts this snapshot actually speaks for, or null when it speaks for all of them (a
    /// relay `agents` message does). Panes on a host that is absent are left alone: a
    /// failed poll says nothing about that host's panes, and dropping them would make a
    /// flapping SSH connection empty and refill the list every couple of seconds — and
    /// re-toast every blocked agent on it each time it came back.
    /// </param>
    /// <returns>The agents that entered the blocked state in this snapshot.</returns>
    private List<Agent> ApplySnapshot(IEnumerable<AgentData> snapshot, ISet<string>? hostsCovered = null)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var newlyBlocked = new List<Agent>();

        foreach (var data in snapshot)
        {
            seen.Add(data.PaneId);
            var agent = Upsert(data, out var becameBlocked);
            if (becameBlocked) newlyBlocked.Add(agent);
        }

        for (var i = Agents.Count - 1; i >= 0; i--)
        {
            var agent = Agents[i];
            if (seen.Contains(agent.Id)) continue;
            if (hostsCovered is not null && !hostsCovered.Contains(agent.Host)) continue;
            Agents.RemoveAt(i);
        }

        return newlyBlocked;
    }

    private Agent Upsert(AgentData data, out bool becameBlocked)
    {
        var existing = Find(data.PaneId);
        var status = AgentStatusParser.Parse(data.Status);

        if (existing is not null)
        {
            var wasBlocked = existing.Status == AgentStatus.Blocked;
            becameBlocked = status == AgentStatus.Blocked && !wasBlocked;
            existing.Name = data.Agent;
            existing.Status = status;
            existing.Project = data.Project;
            existing.Cwd = data.Cwd;
            existing.Host = data.Host ?? "local";
            // Answered from another client: drop the prompt and retract the toast.
            if (wasBlocked && status != AgentStatus.Blocked)
            {
                existing.ClearPrompt();
                AgentUnblocked?.Invoke(existing);
            }
            return existing;
        }

        becameBlocked = status == AgentStatus.Blocked;
        var agent = new Agent(
            data.PaneId,
            data.Agent,
            status,
            data.Project,
            data.Cwd,
            data.Host ?? "local");
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

        var newlyBlocked = ApplySnapshot(result.Agents, result.HostsAnswered);
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

    public Agent? Find(string paneId) => Agents.FirstOrDefault(a => a.Id == paneId);

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
            Send(Protocol.SafeResponses.Contains(text.Trim())
                ? Protocol.Respond(agent.Id, agent.PromptId, text)
                : Protocol.AgentPrompt(agent.Id, text));
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
        else Send(Protocol.AgentPrompt(agent.Id, trimmed));
    }

    /// <summary>Send ^C to the pane. The relay's key allowlist spells this "C-c".</summary>
    public void Interrupt(Agent agent)
    {
        if (Mode == ConnectionMode.Direct) _ = ReportAsync(_direct.InterruptAsync(agent));
        else Send(Protocol.SendKeys(agent.Id, Protocol.InterruptKey));
    }

    public void ReadPane(Agent agent, int lines = 30)
    {
        if (Mode == ConnectionMode.Direct) _ = ReadPaneDirectAsync(agent, lines);
        else Send(Protocol.ReadPane(agent.Id, lines));
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
        Send(Protocol.QuestionToggle(agent.Id, agent.PromptId, option));
        if (agent.SelectedOptions.Contains(option)) agent.SelectedOptions.Remove(option);
        else agent.SelectedOptions.Add(option);
    }

    /// <summary>Submit a multi-select question. Same caveats as above.</summary>
    public void SubmitQuestion(Agent agent)
    {
        if (agent.PromptId is null || Mode == ConnectionMode.Direct) return;
        Send(Protocol.QuestionSubmit(agent.Id, agent.PromptId));
        agent.Status = AgentStatus.Working;
        agent.ClearPrompt();
    }

    private async void Send(string json)
    {
        var socket = _socket;
        if (socket is not { State: WebSocketState.Open }) return;
        try
        {
            await socket.SendAsync(
                new ArraySegment<byte>(Encoding.UTF8.GetBytes(json)),
                WebSocketMessageType.Text,
                true,
                CancellationToken.None);
        }
        catch (Exception ex)
        {
            _post(() => LastError = ex.Message);
        }
    }

    public void Dispose()
    {
        _disposed = true;
        _direct.Dispose();
        _cts?.Cancel();
        _cts?.Dispose();
        _socket?.Dispose();
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
