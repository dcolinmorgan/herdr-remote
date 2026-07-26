# Quick Start

Get mobile notifications + approval for your herdr agents in 60 seconds.

## 1. Start the relay (on your Mac)

```bash
git clone https://github.com/dcolinmorgan/herdr-remote
cd herdr-remote/relay
uv run herdr_relay.py
```

## 2. Expose it (pick one)

```bash
# Cloudflare tunnel (free, instant):
cloudflared tunnel --url http://localhost:8375
# → gives you https://something.trycloudflare.com
```

## 3. Install the plugin (on any machine with herdr)

```bash
herdr plugin install dcolinmorgan/herdr-push
export HERDR_RELAY="https://your-tunnel.trycloudflare.com"
launchctl setenv HERDR_RELAY "$HERDR_RELAY"
herdr server reload-config
```

## 4. Monitor

**Web app** (phone):
Open [herdr-remote.pages.dev](https://herdr-remote.pages.dev), tap ⚙, paste your tunnel URL.

**Telegram:** send `/start` for a clickable dashboard of every running agent. Select an agent, then reply to the generated output prompt. Finished and blocked notifications also provide **Open output & reply**, and larger herds include Previous and Next buttons.

**Menu bar app** (macOS):
Download from [Releases](https://github.com/dcolinmorgan/herdr-remote/releases).

**Telegram bot**:
```bash
export HERDR_TG_TOKEN="your-token" HERDR_TG_CHAT_ID="your-id"
uv run herdr_telegram.py
```

**Terminal TUI**:
```bash
uv run herdr_tui.py
```

## 5. Test

```bash
herdr plugin action invoke herdr.push test
```

You should see a test agent appear on your dashboard.
