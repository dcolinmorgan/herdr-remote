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
- **11 themes** -- dark, herdr, light, sand, clay, dune, nord, rose, dracula, kanagawa, midnight

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

By default `start.sh` uses a free Cloudflare quick tunnel. If that doesn't
work for you (or you'd rather run your own rendezvous host), see
[AWS reverse tunnel](#aws-reverse-tunnel) below.

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

## AWS reverse tunnel

An alternative to Cloudflare Tunnel: a small EC2 host you control
terminates TLS and forwards to the relay over a reverse SSH tunnel the
Mac opens outbound. Nothing inbound is ever opened on the Mac or the
home router, and the relay stays loopback-only, exactly as with
Cloudflare Tunnel.

1. Deploy the rendezvous host (one-time, by hand — this repo only ships
   the definition, it does not apply it): see
   [`infra/aws-tunnel/README.md`](infra/aws-tunnel/README.md) for the
   CloudFormation template, cost (~$4/mo), and deploy steps.
2. Generate a tunnel-only SSH keypair and add its public half as the
   stack's `TunnelPublicKey` parameter (see that README).
3. Add to `~/.config/herdr-remote/config.env`:
   ```bash
   HERDR_TUNNEL_MODE=aws
   HERDR_AWS_HOST=herdr-remote.example.com
   HERDR_AWS_SSH_KEY=/Users/you/.ssh/herdr-remote-tunnel
   ```
4. `cd relay && ./install-service.sh` (or `./start.sh` for a foreground
   run) — it detects `HERDR_TUNNEL_MODE=aws` and supervises
   `tunnel-aws.sh` instead of `cloudflared`. Install
   [`autossh`](https://formulae.brew.sh/formula/autossh) for fast
   reconnects after a dropped link; a plain-`ssh` retry loop is used
   otherwise.
5. Open `https://<HERDR_AWS_HOST>` on your phone.
6. To take it down again, use `relay/herdr-remote stop`, not a plain `kill`:
   the supervised services restart a killed process within seconds. `stop`
   stops the launchd/systemd services and then re-checks past that restart
   delay, printing "Stopped" only once it has confirmed the relay port is
   closed and no tunnel process remains. If it cannot confirm that, it says so
   and exits non-zero — until then, assume the endpoint is still reachable.

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
        │   relay (:8375)     │  <- Cloudflare tunnel, or AWS reverse tunnel
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
- Windows 10+ (relay/web/TUI/Telegram; no tray app)
- Python 3.10+ with [uv](https://docs.astral.sh/uv/) (relay/TUI/bot)
- `cloudflared` (for remote access via Cloudflare Tunnel), or `autossh` +
  the AWS rendezvous host (see [AWS reverse tunnel](#aws-reverse-tunnel))
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
