# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

herdr-remote is a multi-client system for monitoring and approving [herdr](https://herdr.dev) AI agents remotely. It provides a WebSocket relay that bridges the herdr CLI with phone, desktop, Telegram, and terminal clients.

## Architecture

```
Clients (web/mac/ios/telegram/tui)
        │ WebSocket
        ▼
   relay (:8375)  ←── Cloudflare tunnel (public wss://)
        │
        ▼
   herdr CLI (local or SSH to HERDR_REMOTES)
```

The relay (`relay/herdr_relay.py`) is the central hub: it polls herdr for agent state, accepts push events via HTTP POST and UDP, and broadcasts to connected WebSocket clients. Clients send `respond`, `read_pane`, `send_keys`, and `send_text` messages back through the relay to control agents.

## Components

| Path | What | Language |
|------|------|----------|
| `relay/herdr_relay.py` | WebSocket+HTTP relay server | Python (websockets, zeroconf) |
| `relay/herdr_telegram.py` | Telegram bot client | Python (python-telegram-bot) |
| `relay/herdr_tui.py` | Terminal TUI client | Python (textual) |
| `web/index.html` | Mobile/desktop web app (single file) | HTML/CSS/JS |
| `demo-worker/` | Cloudflare Worker mock relay for demos | JS |
| `herdi-mac/` | macOS menu bar app | Swift (SPM) |
| `herdi-ios/` | iOS app with widgets + Live Activities | Swift (XcodeGen) |
| `herdi-win/` | Windows tray app + top-edge island | C# (.NET 8 / WPF) |

## Running Components

All Python scripts use [PEP 723 inline metadata](https://peps.python.org/pep-0723/) — `uv run` handles dependency installation automatically.

```bash
# Relay (main server)
uv run relay/herdr_relay.py

# Full setup with Cloudflare tunnel
relay/start.sh

# Telegram bot
HERDI_TG_TOKEN="..." HERDI_TG_CHAT_ID="..." uv run relay/herdr_telegram.py

# Terminal TUI
uv run relay/herdr_tui.py

# Demo worker (Cloudflare)
cd demo-worker && npx wrangler dev

# macOS app
cd herdi-mac && ./build.sh

# iOS app (generate Xcode project)
cd herdi-ios && xcodegen generate

# Windows app (needs the .NET 8 SDK; `dotnet build` also works off-Windows
# for compile checking thanks to EnableWindowsTargeting)
cd herdi-win && ./build.ps1
```

## Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `HERDR_RELAY_PORT` | Relay WebSocket port (default: 8375) |
| `HERDR_RELAY_TOKEN` | Optional shared secret for auth |
| `HERDR_REMOTES` | Comma-separated SSH targets to poll |
| `HERDR_BIN` | Path to herdr binary (default: `/opt/homebrew/bin/herdr`) |
| `HERDR_RELAY` | Relay URL used by clients (default: `ws://127.0.0.1:8375`) |

## Web App

The web app is a single self-contained HTML file (`web/index.html`) with inline CSS and JS — no build step. It's deployed to Cloudflare Pages. It includes 11 color themes, a mobile terminal keyboard, PWA support, and agent-icon detection.

## WebSocket Protocol

Messages are JSON with a `type` field:

**Server → Client:** `agents` (complete state snapshot), `agent_update` (single-pane state merge), `blocked` (approval prompt), `pane_content` (terminal read)

**Client → Server:** `respond` (send text to agent), `read_pane` (request terminal content), `send_keys` (send key sequences), `send_text` (raw text without newline), `agent_prompt` (submit free-form text via `herdr agent prompt`), `get_history`, `create_tab`, `push_subscribe`/`push_unsubscribe`

### Relay-side constraints clients must respect

Easy to get wrong — three of the existing clients do:

- **`respond` is allowlisted.** Only the 12 values in `SAFE_RESPONSES` (`herdr_relay.py:90`) are accepted; anything else returns `response not in allowlist`. Free-form replies must use `agent_prompt` (≤10000 chars) or `send_text` (≤1000). The mac/iOS approval cards send custom text as `respond`, so their custom-reply box does not work against the relay.
- **Interrupt is `C-c`.** `SAFE_KEYS` (`herdr_relay.py:91`) accepts `C-c`, not `Ctrl+c`, and `keys` must be an array. The mac app's `Ctrl+c` only works because it invokes the local CLI rather than the relay.
- **`question_toggle`/`question_submit` have no relay handler.** The web app, TUI, mac and iOS clients all send them; the relay ignores both, so multi-select questions cannot be answered from any client until it grows support.

## Deployment

- Web app: Cloudflare Pages (push to main deploys `web/`)
- Demo worker: `npx wrangler deploy` from `demo-worker/`
- macOS app: `herdi-mac/build.sh` produces `dist/Herdi.app`
