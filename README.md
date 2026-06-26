# herdr-remote

Monitor and approve [herdr](https://herdr.dev) agents from your phone, menu bar, or Telegram — no SSH required.

```
herdr plugin install dcolinmorgan/herdr-push
./relay/start.sh
# → open herdr-remote.pages.dev on your phone
```

## Features

- 📱 **Web app** — approve blocked agents from your phone with one tap
- 🖥️ **macOS menu bar app** — see agent status at a glance, approve from desktop
- 💬 **Telegram bot** — approve from your watch/phone via inline buttons
- 🖲️ **Terminal TUI** — kanban dashboard in a herdr pane
- 🎨 **11 themes** — dark, herdr, light, sand, clay, dune, nord, rose, dracula, kanagawa, midnight
- 🔑 **Token auth** — shared secret protects your relay
- 🔌 **Zero-dep plugin** — `herdr-push` uses only `curl`, nothing to install

## Screenshots

| Agent List | Terminal View |
|:--:|:--:|
| ![Agent list with status and icons](landing-ui.jpeg) | ![Terminal interaction from phone](chat-ui.jpeg) |

## Web App

**[herdr-remote.pages.dev](https://herdr-remote.pages.dev)**

- Tap any agent to open a live terminal view
- Special mobile keyboard: Tab, Esc, ^C, y/n + floating arrow d-pad
- Agent icons: Kiro, Codex, Claude, Grok, Pi auto-detected
- Context menu (⋯): open terminal, approve, read output, interrupt
- Quick-action buttons for blocked agents (yes/trust/no)
- Browser notifications when agents block
- Works as PWA — add to Home Screen for app-like experience + Apple Watch notifications

## Quick Start

### 1. Start the relay

```bash
git clone https://github.com/dcolinmorgan/herdr-remote
cd herdr-remote/relay
./start.sh
```

Prints a `wss://` tunnel URL. No account needed (free Cloudflare quick tunnel).

### 2. Open on your phone

Go to [herdr-remote.pages.dev](https://herdr-remote.pages.dev) → tap ⚙ → paste the URL → Connect.

### 3. Monitor remote machines

On any machine running herdr:

```bash
herdr plugin install dcolinmorgan/herdr-push
echo "HERDR_RELAY=https://your-tunnel-url" > "$(herdr plugin config-dir herdr.push)/.env"
herdr plugin action invoke herdr.push test
```

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Web App     │  │  Mac Menu    │  │  Telegram    │
│  (phone)     │  │  Bar App     │  │  Bot         │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                  │                  │
       └───── WebSocket ──┴──────────────────┘
                   │
        ┌──────────┴──────────┐
        │   relay (:8375)     │  ← Cloudflare tunnel
        │   WS + HTTP POST    │
        └──────────┬──────────┘
                   │
     ┌─────────────┼─────────────┐
     │ local poll  │ herdr-push  │
     │ (herdr CLI) │ (HTTP POST) │
     │             │             │
  ┌──┴──┐    ┌────┴────┐   ┌────┴────┐
  │herdr│    │herdr    │   │herdr    │
  │local│    │remote A │   │remote B │
  └─────┘    └─────────┘   └─────────┘
```

## Persistent Tunnel (optional)

Quick tunnels change URL on restart. For a stable URL:

```bash
# Create a named tunnel (one-time)
cloudflared tunnel create herdr-remote
cloudflared tunnel route dns herdr-remote relay.yourdomain.com

# Run it
cloudflared tunnel --config ~/.cloudflared/config-herdr-remote.yml run
```

## macOS Menu Bar App

```bash
cd herdi-mac
./build.sh
cp -r dist/Herdi.app /Applications/
```

Or download from [Releases](https://github.com/dcolinmorgan/herdr-remote/releases).

## Telegram Bot

```bash
export HERDR_TG_TOKEN="your-token"
export HERDR_TG_CHAT_ID="your-chat-id"
uv run relay/herdr_telegram.py
```

Approve agents from your Apple Watch via Telegram inline buttons.

## Terminal TUI

```bash
uv run relay/herdr_tui.py
```

## Token Auth

```bash
export HERDR_RELAY_TOKEN="$(openssl rand -hex 16)"
uv run relay/herdr_relay.py
```

Enter the same token in the web app Settings. Connections without the token are rejected.

## Requirements

- Python 3.10+ with [uv](https://docs.astral.sh/uv/)
- `cloudflared` (for remote access)
- herdr 0.7+
