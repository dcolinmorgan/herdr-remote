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

The mac and Windows clients can also skip the relay entirely. Their **direct** mode runs the CLI itself — `herdr pane list` locally and `ssh <target> herdr pane list` per configured host — on the same SSH terms as the relay (`ConnectTimeout=5`, `BatchMode=yes`, `HERDR_REMOTE_BIN`). The host list is per client: `herdi_remotes` in `UserDefaults` on macOS, `%LOCALAPPDATA%\herdr-remote\settings.json` on Windows. Nothing in this mode touches the relay, so none of the relay constraints below apply to it.

One relay constraint does reach them, because it is herdr's, not the relay's: **an
automatic read must pass `--source visible`.** `recent` past the viewport is a
*harvesting* read — herdr walks the agent's own scroll interface to fetch the rest,
moving the operator's terminal to do it, and it only works while the agent is idle.
The relay reads `visible` for exactly this reason (`PROMPT_READ_SOURCE`).

- **Omitting `--format` gets you the harvesting one.** Verified on a 48-row idle claude
  pane: `--lines 200 --source recent` with no `--format`, and with `--format text`, both
  return 137 rows of real older output; `--format ansi` returns the 37 on screen, same as
  `visible`. The direct-mode clients pass no `--format`, so `--source visible` is the only
  thing keeping them off that path.
- **The harvest caches.** Cold it is seconds; re-reading the same rows is instant. Timing a
  second read tells you nothing about what the first one cost.

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
| `herdi-win/` | Windows tray app + tray flyout panel | C# (.NET 8 / WPF) |

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
# ./build.ps1 -Framework is 25 MB against the default's 166 MB for identical memory;
# ./build.ps1 -Compress halves the download and doubles the memory. See herdi-win/README.md.
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
| `HERDR_SESSION` | Boot-time default herdr session; a client can override it per source at runtime via `session_switch` |
| `HERDI_RENDER` | Windows client only: `hardware` restores WPF's GPU path (default is software — see `herdi-win/README.md#memory`) |

Runtime session overrides (per source) are persisted to `active_sessions.json` inside `HERDR_LOG_DIR`, so they survive relay restarts.

## Web App

The web app is a single self-contained HTML file (`web/index.html`) with inline CSS and JS — no build step. It's deployed to Cloudflare Pages. It includes 11 color themes, a mobile terminal keyboard, PWA support, and agent-icon detection.

## WebSocket Protocol

Messages are JSON with a `type` field:

**Server → Client:** `agents` (complete state snapshot), `agent_update` (single-pane state merge), `blocked` (approval prompt), `pane_content` (terminal read), `sessions` (per-source herdr session lists and the active selection)

An `agents` entry carries, per pane: `pane_id`, `agent`, `label`, `workspace_label`, `status`,
`cwd`, `project`, `host`, `remote`, `workspace_id`, `tab_id`, and `title` — herdr's terminal title,
which is **live activity, not a session name**. A working claude sets it to what it is doing;
`activity_title` drops the harness's own banner, which is all an idle or done pane leaves there.

**Client → Server:** `respond` (send text to agent), `read_pane` (request terminal content), `send_keys` (send key sequences), `send_text` (raw text without newline), `agent_prompt` (submit free-form text via `herdr agent prompt`), `session_switch` (point one source at a herdr session; `session: null` follows herdr's default), `get_history`, `create_tab`, `push_subscribe`/`push_unsubscribe`

### Relay-side constraints clients must respect

Easy to get wrong — three of the existing clients do:

- **`respond` is allowlisted.** Only the 12 values in `SAFE_RESPONSES` (`herdr_relay.py:90`) are accepted; anything else returns `response not in allowlist`. Free-form replies must use `agent_prompt` (≤10000 chars) or `send_text` (≤1000). The mac/iOS approval cards send custom text as `respond`, so their custom-reply box does not work against the relay.
- **Keys use herdr's `+` grammar, validated by `key_is_allowed`, and `keys` must be a non-empty array.** Bare specials (`Enter` `Escape` `Tab` `Space` `Backspace` `Up`…`F12`), single characters, and `ctrl+`/`shift+`/`alt+` chords all pass — special names case-insensitively, so `esc` and `shift+tab` are fine. `C-c` also passes: live-verified as the one tmux-style spelling herdr 0.8.0 still aliases to interrupt (`C-u`, `M-x`, `BTab` do not). `BSpace`, `Insert` and `Delete` are rejected by herdr in any spelling.
- **`PageUp`, `PageDown`, `Home` and `End` are sent as bytes, not as keys.** herdr's own validator refuses every spelling of them (re-probed on 0.8.2: `PgUp`, `pageup`, `Page_Up` and `ctrl+Home` all answer `unsupported key`), so `key_escape_sequence` turns them into the CSI bytes a terminal emits and the relay ships that through `pane send-text` instead — `pane send-text` is a byte channel and passes ESC verbatim. Modified forms are computed, not enumerated: xterm's `1 + shift(1) + alt(2) + ctrl(4)`, so `ctrl+Home` is `ESC[1;5H` and `shift+PageUp` is `ESC[5;2~`. A mixed `keys` array keeps its order — consecutive keys of one kind travel in one CLI call, so `[Escape, PageUp, PageDown, Enter]` becomes send-keys / send-text / send-keys, in that order. Clients still just send the key name.
- **`question_toggle`/`question_submit` have no relay handler.** The web app, TUI, mac and iOS clients all send them; the relay ignores both, so multi-select questions cannot be answered from any client until it grows support.

## Deployment

- Web app: Cloudflare Pages (push to main deploys `web/`)
- Demo worker: `npx wrangler deploy` from `demo-worker/`
- macOS app: `herdi-mac/build.sh` produces `dist/Herdi.app`
