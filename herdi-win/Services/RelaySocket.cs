using System.Net;
using System.Net.WebSockets;
using System.Text;
using Herdi.Models;

namespace Herdi.Services;

/// <summary>
/// One WebSocket to one relay: connect, pump frames, reconnect with backoff.
///
/// Split out of <see cref="RelayConnection"/> when the client learned to watch several
/// relays at once. Nothing in the loop below is new — it was already written per
/// connection, it just lived in a class that assumed there could only ever be one of them,
/// with the socket, the cancellation source and the attempt counter as fields beside the
/// agent list. Owning that trio here is what makes N of them possible: each relay backs
/// off on its own schedule, and a tunnel that is down does not delay the local relay's
/// reconnect or take the whole list offline.
/// </summary>
public sealed class RelaySocket : IDisposable
{
    private readonly string _token;
    private readonly Action<Action> _post;
    private ClientWebSocket? _socket;
    private CancellationTokenSource? _cts;
    private int _reconnectAttempt;
    private bool _disposed;

    public RelaySocket(string url, string token, Action<Action> post)
    {
        Url = url;
        Label = Describe(url);
        _token = token;
        _post = post;
    }

    /// <summary>
    /// The configured URL, verbatim. Doubles as the source key every agent from this relay
    /// is tagged with, so it has to be the stored string rather than anything normalised:
    /// the settings list is what a later <see cref="RelayConnection.Connect"/> diffs
    /// against to decide which sockets survive.
    ///
    /// It carries no token. <see cref="SettingsStore.SplitToken"/> takes a `?token=` out of
    /// one before it is ever stored, precisely because of the sentence above — this string
    /// ends up inside <see cref="Agent.Id"/>, and from there in every toast's launch
    /// argument. The secret goes on the handshake header instead.
    /// </summary>
    public string Url { get; }

    /// <summary>
    /// What a row calls this relay. The URL's authority — `127.0.0.1:8375`,
    /// `herdr.example.com` — which is the part that differs between two relays; the scheme
    /// and path are the same on almost every pair and would spend a row's width saying so.
    /// </summary>
    public string Label { get; }

    public bool IsConnected { get; private set; }

    public string? LastError { get; private set; }

    /// <summary>
    /// Whether this socket is already the connection the settings now ask for. The token is
    /// compared as well as the URL because it is captured at construction and sent on the
    /// handshake — a socket that survived a settings save carrying the old one would keep
    /// reconnecting with it, and a relay that had just been given a token would answer 401
    /// until the app was restarted.
    /// </summary>
    public bool Matches(string url, string token) =>
        string.Equals(Url, url, StringComparison.Ordinal) &&
        string.Equals(_token, token, StringComparison.Ordinal);

    /// <summary>Raised on the posting context for every decoded frame.</summary>
    public event Action<RelaySocket, ServerMessage>? MessageReceived;

    /// <summary>
    /// Raised whenever <see cref="IsConnected"/> or <see cref="LastError"/> moved, so the
    /// owner can recompute the aggregate it publishes to the UI.
    /// </summary>
    public event Action<RelaySocket>? StateChanged;

    public void Start()
    {
        // Started twice would leave two loops racing on _socket for the life of the process.
        if (_cts is not null || _disposed) return;
        _cts = new CancellationTokenSource();
        _reconnectAttempt = 0;
        _ = RunLoopAsync(_cts.Token);
    }

    /// <summary>
    /// Stop reconnecting and close politely. Async void for the same reason the single
    /// connection's Disconnect was: closing a socket is a round trip nobody waits on, and
    /// the flag that stops the loop is set synchronously before it.
    /// </summary>
    public async void Stop()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = null;

        var socket = _socket;
        _socket = null;
        SetState(false, LastError);

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
    }

    /// <summary>
    /// Connect, pump messages, and reconnect with exponential backoff capped at 30s —
    /// same schedule as herdi-mac's scheduleReconnect (min(2^min(attempt,5), 30)).
    /// </summary>
    private async Task RunLoopAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested && !_disposed)
        {
            using (var socket = new ClientWebSocket())
            {
                try
                {
                    // Asked for so a refused handshake can be told apart from an unreachable
                    // one below; without it the status code is not kept.
                    socket.Options.CollectHttpResponseDetails = true;
                    if (!string.IsNullOrEmpty(_token))
                    {
                        // The relay accepts either an Authorization header or ?token=
                        // (herdr_relay.py:1926). The header keeps the secret out of logs --
                        // a tunnel in front of the relay records query strings and not
                        // headers -- and out of this socket's Url, which is a source key.
                        socket.Options.SetRequestHeader("Authorization", "Bearer " + _token);
                    }
                    _socket = socket;

                    await socket.ConnectAsync(new Uri(Url), token);
                    SetState(true, null);
                    _reconnectAttempt = 0;

                    await PumpAsync(socket, token);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    SetState(false, Explain(ex, socket.HttpStatusCode));
                }
            }

            if (token.IsCancellationRequested || _disposed) break;

            SetState(false, LastError);
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

    /// <summary>
    /// What to put in front of the operator when a connection attempt fails.
    ///
    /// A relay that refuses the handshake answers 401, which .NET reports as "The server
    /// returned status code '401' when status code '101' was expected" — true, and no help at
    /// all. Now that every relay carries its own token, this is also the only place that knows
    /// <em>which</em> token was refused; the tray prefixes the message with this relay's label.
    /// Everything else is passed through as the runtime described it.
    /// </summary>
    private string Explain(Exception ex, HttpStatusCode status) => status switch
    {
        HttpStatusCode.Unauthorized => _token.Length == 0
            ? "needs a token — set one for this relay in Settings"
            : "token rejected — check this relay's token in Settings",
        _ => ex.Message,
    };

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
            if (msg is not null) _post(() => MessageReceived?.Invoke(this, msg));
        }
    }

    public async void Send(string json)
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
            SetState(IsConnected, ex.Message);
        }
    }

    /// <summary>
    /// Publish connection state on the posting context, and only when it moved: the owner
    /// re-derives its aggregate on every one of these, and a reconnect loop that is failing
    /// fires the same pair several times a minute.
    /// </summary>
    private void SetState(bool connected, string? error)
    {
        _post(() =>
        {
            if (IsConnected == connected && LastError == error) return;
            IsConnected = connected;
            LastError = error;
            StateChanged?.Invoke(this);
        });
    }

    /// <summary>
    /// Authority of a relay URL, falling back to the whole string when it will not parse —
    /// an unparseable URL is exactly the one whose row needs to be recognisable, since it
    /// is the one that will never connect.
    /// </summary>
    public static string Describe(string url)
    {
        if (Uri.TryCreate(url, UriKind.Absolute, out var parsed) && parsed.Authority.Length > 0)
        {
            return parsed.Authority;
        }
        return url;
    }

    public void Dispose()
    {
        _disposed = true;
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = null;
        _socket?.Dispose();
        _socket = null;
    }
}
