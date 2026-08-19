namespace Herdi.Services;

/// <summary>
/// Where agent state comes from. Mirrors herdi-mac's RelayConnection.ConnectionMode
/// (Sources/RelayConnection.swift:14).
/// </summary>
public enum ConnectionMode
{
    /// <summary>WebSocket to the herdr relay, which does its own polling and SSH.</summary>
    Relay,

    /// <summary>Poll the herdr CLI here — locally and over SSH — with no relay involved.</summary>
    Direct,
}
