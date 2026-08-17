using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Net.WebSockets;
using System.Runtime.CompilerServices;
using System.Text;
using Herdi.Models;

namespace Herdi.Services;

/// <summary>
/// WebSocket client for the herdr relay. Port of herdi-mac's RelayConnection
/// (Sources/RelayConnection.swift), relay mode only — the Direct/SSH polling half
/// is deliberately not carried over.
/// </summary>
public sealed class RelayConnection : INotifyPropertyChanged, IDisposable
{
    private readonly Action<Action> _post;
    private readonly SettingsStore _settings;
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
    }

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

    public void Connect(string? urlOverride = null)
    {
        var url = urlOverride ?? _settings.RelayUrl;
        if (string.IsNullOrWhiteSpace(url)) return;
        HostAddress = url;
        _reconnectAttempt = 0;
        _ = RunLoopAsync(url);
    }

    public async void Disconnect()
    {
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
            {
                var seen = new HashSet<string>();
                foreach (var data in msg.Agents)
                {
                    seen.Add(data.PaneId);
                    Upsert(data);
                }
                for (var i = Agents.Count - 1; i >= 0; i--)
                {
                    if (!seen.Contains(Agents[i].Id)) Agents.RemoveAt(i);
                }
                break;
            }

            case "agent_update":
                if (msg.AgentUpdate is not null) Upsert(msg.AgentUpdate);
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

    private void Upsert(AgentData data)
    {
        var existing = Find(data.PaneId);
        var status = AgentStatusParser.Parse(data.Status);

        if (existing is not null)
        {
            var wasBlocked = existing.Status == AgentStatus.Blocked;
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
            return;
        }

        Agents.Add(new Agent(
            data.PaneId,
            data.Agent,
            status,
            data.Project,
            data.Cwd,
            data.Host ?? "local"));
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
    /// isn't silently dropped with "response not in allowlist".
    /// </summary>
    public void Respond(Agent agent, string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return;
        var payload = Protocol.SafeResponses.Contains(text.Trim())
            ? Protocol.Respond(agent.Id, agent.PromptId, text)
            : Protocol.AgentPrompt(agent.Id, text);
        Send(payload);
        agent.Status = AgentStatus.Working;
        agent.ClearPrompt();
    }

    /// <summary>Send ^C to the pane. The relay's key allowlist spells this "C-c".</summary>
    public void Interrupt(Agent agent) => Send(Protocol.SendKeys(agent.Id, Protocol.InterruptKey));

    public void ReadPane(Agent agent, int lines = 30) => Send(Protocol.ReadPane(agent.Id, lines));

    /// <summary>
    /// Toggle one option of a multi-select question. NOTE: the current relay has no
    /// handler for `question_toggle` — herdi-mac, herdi-ios, the web app and the TUI
    /// all send it and it is silently ignored. Kept for parity so this client works
    /// the moment the relay grows support.
    /// </summary>
    public void ToggleQuestionOption(Agent agent, string option)
    {
        if (agent.PromptId is null) return;
        Send(Protocol.QuestionToggle(agent.Id, agent.PromptId, option));
        if (agent.SelectedOptions.Contains(option)) agent.SelectedOptions.Remove(option);
        else agent.SelectedOptions.Add(option);
    }

    /// <summary>Submit a multi-select question. Same unhandled-by-relay caveat as above.</summary>
    public void SubmitQuestion(Agent agent)
    {
        if (agent.PromptId is null) return;
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
