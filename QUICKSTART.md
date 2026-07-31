# Quick Start

Get mobile notifications + approval for your herdr agents in 60 seconds.

## 1. Install persistent local services

```bash
git clone https://github.com/dcolinmorgan/herdr-remote
cd herdr-remote/relay
./install-service.sh
```

The installer creates restartable user services for the relay and, optionally, Telegram. Choose `none` for the Cloudflare tunnel when you only need Telegram; the bot connects to the relay over localhost.

## 2. Configure Telegram

1. Open `@BotFather` in Telegram and send `/newbot`.
2. Choose Telegram setup in the installer and paste the token when prompted.
3. Open the new bot and send `/start`. For a private group, add the bot and send `/start@your_bot`.
4. Select the discovered chat and accept the test message.

Credentials are stored in `~/.config/herdr-remote/secrets.env` with owner-only permissions. The machine needs outbound internet access to Telegram, but no public IP, webhook, or tunnel.

## 3. Optional remote web and agent access

Cloudflare is only needed when a browser or agent outside your local network must connect directly to the relay:

```bash
cloudflared tunnel --url http://localhost:8375
# → gives you https://something.trycloudflare.com
```

On a remote machine with herdr:

```bash
herdr plugin install dcolinmorgan/herdr-push
export HERDR_RELAY="https://your-tunnel.trycloudflare.com"
herdr server reload-config
```

## 4. Monitor

**Telegram:** send `/status`, then `/agents`, `/read`, or `/reply` to your bot.

**Telegram:** send `/start` for a clickable dashboard of every running agent. Select an agent, then reply to the generated output prompt. Finished and blocked notifications also provide **Open output & reply**, and larger herds include Previous and Next buttons.

**Web app** (phone): open [herdr-remote.pages.dev](https://herdr-remote.pages.dev), tap ⚙, and paste the tunnel URL.

**Menu bar app** (macOS): download from [Releases](https://github.com/dcolinmorgan/herdr-remote/releases).

**Terminal TUI**:
```bash
uv run herdr_tui.py
```

## 5. Test

```bash
herdr plugin action invoke herdr.push test
```

You should see a test agent appear on your dashboard.
