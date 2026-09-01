# herdr-remote

Agent dashboard for [herdr](https://herdr.dev) -- menu bar, phone, Telegram. Zero config locally, free tunnel for remote.

**[Try the live demo](https://herdr-demo.pages.dev)**

## Install (10 seconds)

Download [Herdi.app](https://github.com/dcolinmorgan/herdr-remote/releases/latest) and drag to Applications.

Monitors all your local herdr agents automatically -- no relay, no config, no account.

```bash
curl -sL https://github.com/dcolinmorgan/herdr-remote/releases/latest/download/Herdi-0.7.0.dmg -o /tmp/Herdi.dmg && open /tmp/Herdi.dmg
```

## What you get

- **Live agent timeline** -- who worked when, who blocked, who finished
- **One-tap approvals** from phone, menu bar, or Telegram
- **Daily activity digest** -- `/digest` in Telegram shows working time + block count
- **Terminal interaction** -- read output, send commands, interrupt agents remotely
- **Notifications** -- know instantly when agents need you or finish
- **Light, dark, or system** -- the web app is Claude Code's own palette, following your phone's appearance or pinned either way in Settings

## Screenshots

| Menu Bar App | Settings |
|:--:|:--:|
| ![Menu bar](public/mac_main.png) | ![Settings](public/mac_settings.png) |

| Agent List | Terminal View |
|:--:|:--:|
| ![Agent list](public/herdr-remote-menu.png) | ![Terminal](public/herdr-remote-quick-menu.png) |

## Remote monitoring (phone/Telegram)

The Python relay, web dashboard, TUI, and Telegram client run on macOS, Linux, and Windows.

### macOS/Linux

For monitoring agents across machines or from your phone:

```bash
herdr plugin install dcolinmorgan/herdr-push
cd herdr-remote/relay && ./start.sh
```

Open [herdr-demo.pages.dev](https://herdr-demo.pages.dev) on your phone, paste the tunnel URL.

### Windows

With Git, [uv](https://docs.astral.sh/uv/), and `herdr` installed:

```powershell
git clone https://github.com/dcolinmorgan/herdr-remote
Set-Location herdr-remote

herdr plugin link .
herdr plugin list

./relay/start.ps1
```

The launcher starts a local-only relay on `127.0.0.1:8375` by default. Set
`HERDR_RELAY_TOKEN` before enabling a tunnel or binding beyond loopback. Use
`HERDR_REMOTES` for a comma-separated list of SSH targets and `HERDR_BIN` only
when `herdr` is not available on `PATH`.

For the desktop client -- a tray icon plus a capsule at the top of the screen, with
toast notifications you can approve or reply to inline -- build
[`herdi-win`](herdi-win/README.md) with the .NET 8 SDK:

```powershell
Set-Location herdi-win
./build.ps1
./dist/win-x64/Herdi.exe
```

### Security

The relay validates WebSocket origins to prevent drive-by attacks from malicious
webpages. On a tokenless loopback relay, only connections from `localhost` or
`127.0.0.1` origins are accepted. To allow additional origins (e.g., a Cloudflare
Access hostname), set `HERDR_TRUSTED_ORIGINS`:

```bash
export HERDR_TRUSTED_ORIGINS="https://herdr.example.com"
```

## Telegram Bot

For an automatically restarting relay and Telegram bot:

```bash
cd relay
./install-service.sh
```

Choose Telegram setup when prompted. Create the bot with `@BotFather` using `/newbot`, send `/start` to the bot (or `/start@your_bot` in a private group), and select the discovered chat. Telegram connects to the relay over localhost, so this setup does **not** require Cloudflare Tunnel; the Mac only needs outbound internet access to Telegram.

The installer creates user services on macOS or Linux, enables relay authentication for new installs, and stores credentials in `~/.config/herdr-remote/secrets.env` with mode `0600`. On macOS:

```bash
launchctl print "gui/$(id -u)/com.herdr-remote.relay"
launchctl print "gui/$(id -u)/com.herdr-remote.telegram"
```

Manual foreground setup remains available:

```bash
export HERDR_TG_TOKEN="your-token"
export HERDR_TG_CHAT_ID="your-chat-id"
uv run relay/herdr_telegram.py
```

| Command | Action |
|---------|--------|
| `/start` | Show the clickable agent dashboard |
| `/agents` | List all with status |
| `/read` | Read agent output |
| `/reply` | Read + respond in one flow |
| `/send` | Send text to an agent |
| `/trust` | Trust all tools for blocked agent |
| `/interrupt` | Send Ctrl+C |
| `/digest` | Today's activity summary |

The `/start`, `/read`, `/reply`, `/send`, `/interrupt`, and `/trust` pickers keep every eligible agent reachable. Normal herds appear in one list; larger herds include Previous and Next buttons. Selecting an agent opens a reply prompt containing its recent output; reply to that prompt to send text safely to the pane.

Finished and blocked notifications include **Open output & reply**. You can also reply directly to the notification to send a follow-up without returning to the agent list. Blocked notifications retain their one-tap approval controls.

## Architecture

```
                    ┌──────────────────────────────┐
                    │  macOS Menu Bar (Herdi.app)   │ <- zero config
                    └──────────────────────────────┘

┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Web App     │  │  Telegram    │  │  TUI         │
│  (phone)     │  │  Bot         │  │  (terminal)  │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                  │                  │
       └───── WebSocket ──┴──────────────────┘
                   │
        ┌──────────┴──────────┐
        │   relay (:8375)     │  <- Cloudflare tunnel
        └──────────┬──────────┘
                   │
     ┌─────────────┼─────────────┐
     │ local poll  │ herdr-push  │
     │ (herdr CLI) │ (HTTP POST) │
     └──────┬──────┘──────┬──────┘
         ┌──┴──┐     ┌────┴────┐
         │herdr│     │herdr    │
         │local│     │remote   │
         └─────┘     └─────────┘
```

## Terminal TUI

```bash
uv run relay/herdr_tui.py
```

## Token Auth

`install-service.sh` generates and persists a relay token for new managed installs. For foreground use:

```bash
export HERDR_RELAY_TOKEN="$(openssl rand -hex 32)"
uv run relay/herdr_relay.py
```

On Windows PowerShell:

```powershell
$env:HERDR_RELAY_TOKEN = [guid]::NewGuid().ToString("N")
uv run relay/herdr_relay.py
```

## Requirements

- macOS 14+ (menu bar app)
- Windows 10 1809+ (tray app, relay/web/TUI/Telegram)
- Python 3.10+ with [uv](https://docs.astral.sh/uv/) (relay/TUI/bot)
- `cloudflared` (for remote access)
- herdr 0.7+
- Zero-dep plugin: [`herdr-push`](https://github.com/dcolinmorgan/herdr-push)

## Changelog

### v0.7.0

- **Notch panel** — Dynamic Island-style agent status in the MacBook notch; see working/waiting/blocked at a glance without switching windows

### v0.6.0

- **Workspace drill-down** — agents grouped by workspace/space; blocked "Needs you" agents hoisted to top of dashboard before workspace cards
- **Prettier cards** — shadcn-style: 12px radius, subtle borders, hover lift/shadow, `active:scale(0.99)`, cwd display, chevron navigation
- **Web Push (VAPID)** — subscribe in Settings; get notified when agents block even with tab closed; auto-clears when agent unblocks
- **Structured audit log** — all write actions (respond, send_text, send_keys) logged as JSONL to `~/Library/Logs/herdr-remote/audit.log`
- **Push collapse + TTL** — offline devices get only the latest notification (Topic: `herdr-herd`, TTL: 6h), not a burst of stale alerts
- **Count pills** — workspace cards show pane/tab counts at a glance

### v0.5.0

Telegram bot (`/agents /read /send /reply /trust /interrupt`), demo bot, linux setup script.
