# Remote access

This guide connects your phone to the relay when the phone is not on the same network as the host.
The host is the computer that runs herdr and the relay.

The relay gives keyboard access to your agents. Read [SECURITY.md](SECURITY.md) before you put it on
the internet.

Tested end to end: a macOS host, an Android phone, a named tunnel and Cloudflare Access. The Linux,
Windows and iPhone steps come from the installer, the launcher and the platform documentation. No one
tested them yet.

## Choose a path

| Path | Address | Needs | Use it for |
|---|---|---|---|
| Temp tunnel | random `*.trycloudflare.com`, new on every restart | `cloudflared` only | a quick test |
| Named tunnel | fixed, on your own domain | a domain on Cloudflare DNS, a free Cloudflare account | daily use |
| Named tunnel and Cloudflare Access | the same fixed address, behind an email login | the same, plus a free Zero Trust plan | daily use, recommended |

A temp tunnel breaks a home-screen icon on every restart, because the address changes. For daily use,
pick a named tunnel.

Cloudflare Access puts a login page in front of the relay. Without it, the relay token is the only
check between the internet and your agents.

## The host must stay on

The relay and your agents run on the host. When the host sleeps or shuts down, the phone shows the
relay as offline.

- **macOS:** in System Settings, open Battery or Energy, and stop automatic sleep on power adapter.
  A laptop with its lid closed sleeps unless it has power and an external display.
- **Linux:** disable suspend for the session, or run the relay on a machine that does not sleep.
- **Windows:** in Settings, open System, then Power, and set sleep to Never when plugged in.

The installer on macOS and Linux runs the relay and the tunnel as login services. They start again
after a restart.

## Temp tunnel

Use the steps in [QUICKSTART.md](QUICKSTART.md#3-optional-remote-web-and-agent-access). Then do the
[first visit](#first-visit-from-the-phone) with the `trycloudflare.com` address.

## Named tunnel

### 1. Log in to Cloudflare

```bash
cloudflared tunnel login
```

A browser window opens. Log in, then pick the domain that holds your relay address. This step writes
`~/.cloudflared/cert.pem`.

### 2. Add Cloudflare Access (recommended)

Do this step before step 3. The address then never works without a login.

1. Open the [Zero Trust dashboard](https://one.dash.cloudflare.com). On a first visit, pick a team
   name and the Free plan.
2. Go to Access, then Applications, and add a **Self-hosted** application.
3. Under Destinations, add a **public hostname** with your relay address, for example
   `relay.example.com`. Do not use a private hostname. A private hostname needs the WARP client on
   the phone.
4. Set the application session duration. One month stops a daily login on the phone.
5. Add a policy with the action **Allow**. Include **Emails** with your own address.
6. Keep **One-time PIN** as the login method, and save.

If you reuse a policy from another application, check two settings on it:

- **Include:** a rule such as "Emails ending in" a company domain lets everyone at that domain pass
  Access.
- **Policy session duration:** a fixed value overrides the duration on the application. Pick "Same as
  application session timeout" to let each application keep its own.

### 3. Run the installer (macOS and Linux)

```bash
cd relay && ./install-service.sh
```

Answer the tunnel questions as follows:

| Question | Answer |
|---|---|
| Secure the relay with an access token? | `Y` |
| Tunnel mode [1/2/3] | `1` (named) |
| Tunnel name [herdr-relay] | Enter |
| Hostname | your relay address, for example `relay.example.com` |
| Port 8375 is already in use. Kill it and proceed? | `Y`, if the process is an earlier relay |

On an existing install, the token question is "Secure the existing relay with a token? [y/N]". Its
default is No. Answer `y`, or the tunnel puts a relay with no token on the internet.

The installer creates the tunnel and the DNS record, writes `~/.cloudflared/config-herdr.yml`, and
installs the tunnel as a login service. It saves the token in `~/.config/herdr-remote/secrets.env`.

### 3. Run the launcher (Windows)

Windows has no installer. Create the tunnel by hand, then let `start.ps1` run it:

```powershell
cloudflared tunnel create herdr-relay
cloudflared tunnel route dns herdr-relay relay.example.com
```

Write `%USERPROFILE%\.cloudflared\config-herdr.yml`, with the tunnel id from the create command:

```yaml
tunnel: herdr-relay
credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
ingress:
  - hostname: relay.example.com
    service: http://localhost:8375
  - service: http_status:404
```

Then set `HERDR_RELAY_TOKEN`, `HERDR_TUNNEL_MODE=named` and `HERDR_TUNNEL_NAME=herdr-relay`, and run
`relay\start.ps1`. The launcher refuses to start a tunnel without a token.

### 4. Check the address

From any computer, open `https://relay.example.com`. With Access, you see the Cloudflare login page.
Without Access, you see `Invalid token`, which means the tunnel works and the relay refused a request
with no token.

If the address does not resolve right after setup, your computer may remember an earlier failed
lookup. Try again from the phone on mobile data.

## First visit from the phone

The phone needs a secret link one time. The link is your relay address with the token at the end. It
looks like this:

```text
https://relay.example.com/?token=4f9c...e21a
```

After the first visit, the browser keeps a cookie for one year. Then the plain address works without
the token.

### Show the link as a QR code

The easy way to get the link onto the phone is a QR code on the host screen.

1. On a Mac, install the QR tool once:

   ```bash
   brew install qrencode
   ```

   On Linux, install the `qrencode` package with your package manager.

2. Copy the two lines below into the terminal. Change `relay.example.com` to your relay address
   first. Then press Enter.

   ```bash
   TOKEN=$(sed -n 's/^HERDR_RELAY_TOKEN=//p' ~/.config/herdr-remote/secrets.env)
   qrencode -t ansiutf8 "https://relay.example.com/?token=$TOKEN"
   ```

   A square of black and white blocks appears in the terminal.

3. Point the phone camera at the square. Tap the link that the camera shows.
4. If the link opens in the camera app or in a scanner app, copy it. Paste it into Chrome (Android)
   or Safari (iPhone).
5. If Cloudflare asks, enter your email, then the PIN from the email.
6. Make sure your agents appear.
7. Close the terminal window, or run `clear`. The QR code holds the token.

The QR code is the same as a password. Do not show it in a screenshot, a video call or a chat.

### Without a QR code

Show the token on the host:

```bash
sed -n 's/^HERDR_RELAY_TOKEN=//p' ~/.config/herdr-remote/secrets.env
```

On the phone, type `https://relay.example.com/?token=` and then the token, in one line.

### Which browser keeps the login

The cookie stays in the browser where you opened the link. Each of these needs the token link again:

- **A private or incognito tab.** It deletes all cookies when you close it. The plain address then
  shows `Invalid token`.
- **A different browser,** for example the browser of a QR scanner app.
- **A different address for the same relay.** Each address has its own cookies. Use the same address
  every time.

Use a normal tab in your daily browser. Then add the page to the home screen.

### Android

In Chrome, open the menu and tap **Add to Home screen** or **Install app**. Notifications work in
Chrome without the home-screen icon.

### iPhone

In Safari, tap Share, then **Add to Home Screen**. Notifications work only from the home-screen icon,
on iOS 16.4 or later. Apple also refuses push messages with the default sender, so set
`HERDR_VAPID_SUBJECT` to a real `mailto:` address in `~/.config/herdr-remote/config.env`.

## Limits with Cloudflare Access

Access asks for a browser login. A client that cannot show a login page cannot pass it:

- the Windows app and the macOS app, when they connect to the relay through the tunnel;
- `herdr-push` on a remote host, when `HERDR_RELAY` points to the tunnel.

A client on the host itself connects to `ws://127.0.0.1:8375` and does not go through Access.

Cloudflare offers service tokens and Bypass policies for clients like these. This guide does not
cover them, and no one has tested them with the relay.

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `Invalid token` after the Access login | This browser has no token cookie: a private tab, another browser, or another address | Open the `?token=` link once in a normal tab of that browser |
| No PIN email from Access | Access sends a PIN only to an address that the policy allows | Check the policy Include rule, then the spam folder |
| A second PIN does not arrive | Cloudflare may limit PINs to one address | Wait 5 minutes, or use the browser that already has the login |
| Installer: `Port 8375 is not listening after 3 seconds` | `uv` installs packages on the first start and needs more time | Check `lsof -iTCP:8375 -sTCP:LISTEN`. Set `HERDR_INSTALL_SETTLE_SECONDS=15` for a new run. |
| The phone shows the relay offline | The host sleeps, or the tunnel service stopped | Wake the host. Check `cloudflared tunnel list` for connections. |
