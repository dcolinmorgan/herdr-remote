# herdi-win

Windows desktop client for [herdr-remote](../README.md) — a tray icon whose panel flies
out above the notification area, with native toast notifications when an agent needs you.

Port of [`herdi-mac`](../herdi-mac), both of its sources: a WebSocket to the relay, or
polling the herdr CLI directly over SSH.

```
┌─ session list ─────────────────────┐
│  ● ⚠2 ●3      herdr          5  ✕  │
│  ─────────────────────────────────  │
│  ▌NEEDS YOU                     1   │
│  ▌ claude   relay — Do you want…    │
│  ▌WORKING                       3   │
│  ▌ codex    web                     │
│  ▌IDLE                          1   │
└────────────────────────────────────┘
              ↓ click a row
┌─ pane ─────────────────────────────┐
│  ‹ ▌claude · relay        ⟳    ⊘   │
│  ┌────────────────────────────────┐ │
│  │ ● Running herdr pane list…     │ │
│  └────────────────────────────────┘ │
│  ▏Message this agent…          ↑   │
└────────────────────────────────────┘
                  ▲
   ┌──────────────┴──────────────────┐
   │ ▪ ▪ ▪                  ^ ⌂ 🐕 🕐 │  taskbar — click the tray icon
   └─────────────────────────────────┘
```

Click the tray icon to open it, click it again — or anywhere else, or press `Esc` — to
put it away. Nothing sits on top of your windows while it is closed: the tray icon *is* the
resting state, and it carries a live count so a glance tells you whether opening it is
worth it.

```
🐕      nothing running, or everything idle
🐕③     3 agents working — dark disc, green digit
🐕❶     1 agent waiting on you — red disc, white digit
🐕➕     more than 9; the tooltip has the number
```

Hover for the full breakdown: `Herdi — 2 waiting on you · 3 working · 1 idle`.

## Build

Requires the [.NET 8 SDK](https://dotnet.microsoft.com/download) on Windows.

```powershell
cd herdi-win
.\build.ps1                 # self-contained single exe, no runtime needed
.\build.ps1 -Framework      # small exe, requires the .NET 8 Desktop Runtime
.\build.ps1 -Compress       # smaller exe, double the memory
.\build.ps1 -Zip            # produce BOTH release assets (see below)
.\build.ps1 -Arch win-arm64 # ARM64
```

Output lands in `dist\<arch>\Herdi.exe`. What each mode costs, measured on 0.7.3:

| | exe | memory, flyout open | target machine needs |
|---|---|---|---|
| default | 166 MB | 80 MB | nothing |
| `-Framework` | 25 MB | 80 MB | .NET 8 Desktop Runtime |
| `-Compress` | 70 MB | **160 MB** | nothing |

`-Framework` and the default measure identically, because both map their assemblies off
disk rather than unpacking them; size is the whole of what separates them, and size is what
the built-in updater downloads on every release. Almost all of `-Framework`'s 25 MB is
`Microsoft.Windows.SDK.NET.dll`, the Windows SDK's entire WinRT projection, which this app
loads for one namespace (`Windows.UI.Notifications`).

`-Compress` saves 96 MB of download and costs 80 MB of memory for as long as the process
runs. See [Memory](#memory) for why. It is refused alongside `-Zip`: a release asset decides
what every install of it costs to run, and that is not a bill to hand people invisibly.

### Releases carry both

`-Zip` publishes a pair per architecture, and **both must be uploaded**:

| Asset | What it is | Who takes it |
|---|---|---|
| `Herdi-win-x64-<version>.zip` | self-contained, 166 MB | first install; anyone with no .NET |
| `Herdi-win-x64-<version>-fdd.zip` | framework-dependent, 25 MB | every update of an install that has the runtime |

They are not interchangeable, so the updater does not guess. The deployment mode is stamped
into the assembly at build time (`HerdiDeployment` in `Herdi.Win.csproj`) and
`Updater.IsUsableAsset` matches on it *and* on the running architecture — replacing a
self-contained install with the `-fdd` asset would leave it unable to start on a machine
with no .NET 8 Desktop Runtime, and the old `*win*.zip` match would happily have handed an
x64 install the arm64 build.

Publishing only the `-fdd` asset strands every self-contained install whose machine has no
runtime. Publishing only the other just makes every update six times larger than it needs
to be.

`dotnet build` also works from Linux/macOS for compile checking — the project sets
`EnableWindowsTargeting`. Running it obviously needs Windows.

## Memory

It sits in the tray all day beside the editors and browsers you actually came to use, so
what it costs while doing nothing is part of what it is.

| | private bytes |
|---|---|
| Tray icon, relay connection, polling, toasts | ~20 MB |
| With the flyout open | ~80 MB |

It used to be ~300 MB at rest, and none of that was ever this app's own data: the managed
heap measures 4.9 MB either way, which ruled the code out before any of it was read. Three
things were paying for it, all of them configuration rather than design:

- **A compressed single-file bundle.** A compressed bundle cannot be memory-mapped, so the
  runtime decompresses every assembly it loads into private memory that is never shared,
  never paged out and never returned — 74 of them here. Uncompressed, the same assemblies
  are mapped straight out of the bundle. This is the trade `-Compress` still offers.
- **The flyout being built during startup**, handle forced up front, so the first tray click
  would cost no more than the tenth. For a panel that is hidden by default and on many days
  never opened, that is the wrong way round; it is built on first use now, and kept
  afterwards because it is hidden rather than closed.
- **A D3D device the flyout cannot use.** `AllowsTransparency` makes it a layered window and
  WPF has no hardware path for those — it rasterises them in software and hands the result
  to `UpdateLayeredWindow` regardless. It still stands up its composition engine and loads
  the display driver's user-mode DLL the first time any window is shown, which measured
  170 MB for a 608 px card that never draws a frame through it. `RenderMode.SoftwareOnly`
  skips it, and nothing here can tell the difference.

`HERDI_RENDER=hardware` puts the GPU path back — worth trying only if the flyout ever
renders *wrongly* rather than merely slowly, which would mean something in the card does
depend on the accelerated rasteriser after all.

`InvariantGlobalization` is not among these, though it looks like it should be: it drops
~30 MB of ICU data, compiles clean, and then throws *"Cannot find non-neutral culture
related to 'en-us'"* the first time a window with a binding is shown, because WPF resolves
every binding's culture through `XmlLanguage.GetSpecificCulture()`.

## Setup

1. Launch `Herdi.exe`. It sits in the tray; there is no main window.
2. Tray → **Settings…** → pick a source:
   - **Relay (WebSocket)** — enter one relay URL per line (`ws://127.0.0.1:8375` locally,
     or the `wss://` tunnel URL) and the token if a relay requires one. Every line is
     connected at once; see [Several relays at once](#several-relays-at-once).
   - **Direct (herdr CLI + SSH)** — enter one SSH target per line, e.g. `user@devbox`.
     No relay needed. See [Direct mode](#direct-mode) for the auth requirement.
3. Tray → **Settings…** → **Panel Appearance** to set the panel's colour and how
   see-through it is. The panel comes out while you drag so you can see it against
   whatever is behind it, and Cancel puts it back.
4. Tray → **Launch at Login** to start with Windows.

First run writes a Start Menu shortcut named *Herdi*. **Don't delete it** — Windows
resolves this app's notification identity (AppUserModelID) through that shortcut, and
toasts stop working without it.

## What it does

| Capability | Notes |
|---|---|
| Relay mode | WebSocket to **every** configured relay at once, each doing its own polling and SSH |
| Relay tag | which relay a row came from, shown on the row once there is more than one |
| Direct mode | polls `herdr pane list` here — locally and over SSH — with no relay |
| Remote agents | either mode; the row shows `⇄` and the host it lives on |
| Blocked-agent toast | Title, body, sound — **plus** permission buttons and a reply box |
| Agent-finished toast | when a working agent goes idle, with a box to say what's next; own sound, one per agent |
| Answer from the toast | Approve / Deny inline, or type a reply, without opening the panel |
| Tray flyout | click the icon to open, again (or away, or `Esc`) to close; nothing on screen in between |
| Taskbar aware | anchors to the notification-area corner for a taskbar on any of the four edges, at any DPI |
| Appearance | panel colour and opacity, previewed on the real panel |
| Session list | NEEDS YOU / WORKING / IDLE, blocked hoisted to the top |
| Approval card | diff-highlighted prompt, mapped response buttons, custom reply |
| Pane view | click any row for its live terminal, and a box to message the agent |
| Row menu | right-click: answer, open terminal, interrupt, copy pane id |
| Interrupt | ^C to the pane, on the agents it can stop |
| Tray icon | live count badge: red while agents are blocked, green while they work |
| Tray tooltip | the full breakdown — `2 waiting on you · 3 working · 1 idle` |
| Reconnect | exponential backoff capped at 30s, per relay |
| Launch at login | per-user `Run` registry key |
| Notify When Finished | tray toggle; on by default |
| Self-update | GitHub Releases, same repo and 10-minute throttle as the mac app |
| Token storage | DPAPI-encrypted (CurrentUser) in `%LOCALAPPDATA%\herdr-remote\settings.json` |
| Fullscreen awareness | a blocked agent does not pop the panel while another app is fullscreen or presenting |
| Keyboard | `Ctrl+Y` Allow · `Ctrl+T` Trust · `Ctrl+N` Deny · `Esc` back / close · `Enter` send |

The toast is deliberately richer than the macOS one: `sendNotification` on macOS
(`herdi-mac/Sources/RelayConnection.swift:433`) posts text and a sound with no actions,
so answering there always means opening the panel.

### Two notifications, saying different things

`AgentBlocked` fires when an agent needs you before it can continue; `AgentFinished` fires on
the `Working → Idle` transition, when it no longer needs anything. Neither client this was
ported from has the second one — the macOS and iOS apps notify only on blocked — and an agent
you set going and walked away from is exactly the case where being told it is *done* is worth
as much as being told it is stuck.

The two toasts are shaped differently on purpose:

| | Blocked | Finished |
|---|---|---|
| `scenario` | `reminder` — stays until dismissed | default — a stale "done" notice is clutter |
| Sound | `Notification.Default` | `Notification.IM`, so they differ by ear |
| Tag | one for all, newest replaces | per pane, so several finishing agents stack |
| Actions | Allow / Deny / Trust + reply | reply only — nothing to pick from |
| Panel | pops (unless fullscreen) | does not; the toast's click opens it |

"Finished" is strictly `Idle`, never merely *not* `Working`. `Unknown` is what an unparseable
status becomes, so counting it would fire a notification every time a poll came back garbled,
and `Blocked` has its own toast already. Panes that vanish from a poll are removed rather than
called finished — a closed pane is not a completed one, and there would be nothing to open.

Volume is the risk with this one, so **Notify When Finished** in the tray menu turns it off,
and the per-pane tag means an agent whose status flaps between working and idle refreshes one
toast instead of producing a stream of them.

## Several relays at once

Relay mode connects to **every** URL in Settings and merges their agents into one list.

It used to hold a single URL, so reaching a second relay meant editing the first one out —
which is not switching between two views of one herd, it is throwing one herd away. A
laptop watching a work relay and a home one, or a tunnel beside the loopback relay it
tunnels to, had to pick one and stop seeing the other.

The panes of all of them are triaged together into the same NEEDS YOU / WORKING / IDLE
sections rather than grouped per relay. `NEEDS YOU` is the ordering that matters, and a
blocked agent does not become less urgent for being on the other relay. Which relay a row
came from is a muted tag beside the agent's name, and it appears **only once there is more
than one** — with a single relay it would print the same string on every row.

What the shape costs, and how each is paid for:

| | |
|---|---|
| Every herdr numbers its own panes, so two relays both report `w1:p1` | `Agent.Id` carries the source key; `Agent.PaneId` is the half that goes on the wire, and a test refuses any relay message built from the composite |
| An `agents` snapshot is complete **for its own relay** and silent about the rest | the sweep that drops vanished panes is scoped to the source that sent it, or the relays would delete each other's rows on every snapshot |
| One relay down | `IsConnected` is *any*, not *all* — a dead tunnel must not blank three live relays. Tray → **Relays** names each one with `●` / `○`, and the tray error line reads `1 of 3 relays unreachable` |
| Reconnect | per relay, on its own backoff, so an unreachable one does not delay the others |
| A relay removed from Settings | its panes go with it: nobody is going to send a snapshot for it, so the per-source sweep could never reach them |
| A settings save that changed nothing about a relay | keeps the socket it already has. `Connect()` runs on **every** save, including one that touched only the panel's opacity, and rebuilding a healthy connection there would re-run the whole backoff for a colour change |

**One token, every relay.** The `Relay Token` field is sent to all of them. That is not the
limitation it looks like: a relay with no `HERDR_RELAY_TOKEN` set skips the check entirely
(`herdr_relay.py:384`), so handing one to a loopback relay that wants none is harmless — the
canonical pair, a tokenless local relay beside a token-guarded tunnel, works as it stands.
Two relays that each require a *different* token is the case it does not cover.

An older `settings.json` is read unchanged: the single `relayUrl` key becomes the first
entry of `relayUrls` on load and is dropped from the file on the next save, the same
migration `islandExpandedOpacity` already gets.

## Type

The whole UI is monospace — `Cascadia Mono`, then `Consolas` for Windows 10, then
`Courier New`. This is the web client's decision (`CLAUDE.md`, *Web App*) applied here: the
app is a window onto a terminal, and a proportional shell around a monospace pane reads as
two programs sharing one card. The chrome was Segoe UI, so a 580px card carried three faces
at once — Segoe for the row names, the mono stack for the locations and section headers,
and the mono stack again for the pane's own output.

Two details are load-bearing:

- **`Cascadia Mono`, not `Cascadia Code`.** They are separate families and only Mono is
  guaranteed to be installed by Windows Terminal. Code adds programming ligatures, which is
  the last thing a status row wants.
- **The face is set on the window, not only in the styles.** `PlainButton` sets no font, so
  Cancel, Save and Install would have stayed on the WPF default; property inheritance from
  the window root is what reaches them. `TextOptions.TextFormattingMode="Display"` goes with
  it on the settings dialog — the island already had it — because monospace stems at 10-12px
  land on half pixels under WPF's default `Ideal` metrics.

`MonoFont` and `UiFont` name the same stack today and are still two keys: one is the chrome
and one is terminal content, so a terminal face with wider glyph coverage can be dropped
into the second without dragging the first along.

## Direct mode

Reads agent state without a relay: every 2 s it runs `herdr pane list` on each configured
host and merges the results, then reads the pane of anything newly blocked to fill the
approval card. Answering, messaging, interrupting and reading a pane go back out the same
way (`pane send-text` + `send-keys Enter`, `agent prompt`, `pane send-keys C-c`,
`pane read`).

The SSH terms are the relay's, not the mac app's — `ssh -o ConnectTimeout=5 -o
BatchMode=yes <host> $HERDR_REMOTE_BIN …`, a 15 s command timeout, and one command at a
time per host — so any host the relay can poll works here unchanged. Prompt extraction
follows the relay too (read 50 lines, drop terminal chrome, keep the last 20, cap at 500
chars), which means a blocked pane looks the same whichever source is active. The mac app
keeps 6 unfiltered lines instead and therefore renders it differently.

| | Relay mode | Direct mode |
|---|---|---|
| Needs a relay running | yes | no |
| Remote hosts come from | relay's `HERDR_REMOTES` | this client's settings |
| Multi-select questions | sent (relay ignores them today) | unavailable — no CLI verb |
| Free-text reply | `agent_prompt`, to dodge `SAFE_RESPONSES` | straight to the pane |
| Pane view | `read_pane` / `agent_prompt` | `pane read` / `agent prompt` |
| Auth | relay token (DPAPI-encrypted) | SSH key or `ssh-agent` |

**Key auth only.** `BatchMode=yes` forbids every prompt, so the host must accept a key or
an agent identity. Windows has no `sshpass`, and the mac app's password path
(`/opt/homebrew/bin/sshpass -p <password> …`) puts the secret in a command line that any
process on the machine can read, so it is not ported. Host list lives in plaintext in
`settings.json` — a hostname is not a secret, and it matches what the mac app does with
`herdi_remotes` in `UserDefaults`.

`ssh.exe` is found on `PATH`, then at `%SystemRoot%\System32\OpenSSH\ssh.exe`. A local
`herdr` is looked up as the Settings override → `HERDR_BIN` → `PATH`, and **not finding
one is normal** — Windows is usually the machine watching, not the one running agents, so
the local host is simply skipped instead of raising the hard error the mac app does.

## Deliberate differences from herdi-mac

**A pane view instead of "Jump to terminal".** On macOS a row tap shells out to
`herdr workspace focus` + `herdr tab focus`. The relay protocol has no equivalent
message, and focusing a window is only meaningful on the machine running herdr — which
here is usually an SSH host, not this one. Reading that pane and submitting to it work
from anywhere, so a row opens the terminal inside the panel instead: `read_pane` every
2 s while it is on screen, and an input box that goes out as `agent_prompt`. Blocked rows
still open the approval card.

**No notch, so it is a tray flyout rather than a top-edge capsule.** macOS anchors its
panel to the notch — hardware, with nothing underneath it — so it can live at the top edge
permanently and open on hover. Windows has no notch. A capsule pinned to the top edge sits
over whatever window owns that strip (tab strips, title bars, the ribbon), and hover is the
wrong trigger for something the pointer crosses on its way somewhere else. Windows already
has an always-visible status surface for a background app — the tray icon, which is here
anyway and already carries the state (red while blocked, counts in the tooltip) — so it
takes the collapsed island's job, and the panel becomes the flyout that hangs off it, the
shape Windows uses for its own network and volume panels.

What follows from that:

- **Click, not hover.** Left-click the icon to open, again to close. There is no collapsed
  state to widen, no expand delay, and no cursor polling: the flyout is either up or absent.
- **Nothing on top of your windows while it is closed.** `WS_EX_TRANSPARENT` click-through
  went with the capsule — a hidden window intercepts nothing, so it does not need to be
  made transparent to input.
- **Clicking away dismisses it, even mid-approval.** macOS exempts an open approval card
  from its global click monitor. Here it cannot be: the flyout is topmost, borderless and
  has no taskbar button, so one that refuses to leave when it loses focus is one with no
  way out. The toast carries the same buttons and reply box, and the flyout is one click
  away again.
- **Anchored to the taskbar, wherever it is.** `ABM_GETTASKBARPOS` gives the bar's edge and
  rectangle, the notification area is always at its trailing end, and the result is clamped
  into that display's work area — so a bar docked left, right, top or bottom, on a
  non-primary display, at any DPI, all land correctly. `Shell_NotifyIconGetRect` would give
  the icon's own rectangle, but it needs the window handle and id WinForms' `NotifyIcon`
  keeps private, and it reports the overflow chevron once the user hides the icon.
- **One opacity instead of two.** With no resting capsule there is no resting opacity to
  tune. A `settings.json` from an older build has its expanded value carried over and its
  collapsed one dropped.

**A blocked agent announces itself without stealing focus.** macOS pops the approval card
outright (`observeBlockedAgents`, `HerdiMacApp.swift:180`), which it can afford because a
notch panel covers nothing when it opens. Here the flyout appears without taking the
keyboard — `SetForegroundWindow` is restricted on Windows precisely because pulling focus
out from under a typist is hostile — and takes itself away again after 12 s if nobody
comes. The pointer settling on it, a click, or a keystroke cancels that. Suppressed
entirely while `SHQueryUserNotificationState` reports a game, a presentation or Focus
Assist, which is where Windows holds its own toasts back.

**Springs become easing curves.** WPF has no spring animation, so each macOS spring maps
to the closest-feeling easing — `CubicEase` out for the flyout's slide-and-fade entrance,
quicker on the way out. The entrance is shorter than the notch panel's expansion (180 ms
against 420): a window appearing is not a shape growing.

**Icon font avoided.** Response buttons use plain Unicode symbols instead of SF Symbols'
Windows analogue. Segoe MDL2 Assets and Segoe Fluent Icons differ in glyph coverage
between Windows 10 and 11; text symbols render the same on both.

**One settings dialog, and more than one relay.** The mac app spreads the same choices
across its status menu (a Direct/Relay toggle, an add-remote sheet) plus `UserDefaults`
keys it never surfaces, and it has no way to type a relay URL at all. Here the source, the
relay URLs and token, the SSH hosts, the herdr path and the panel's appearance live in one
dialog, and the choices are remembered across launches. The relay field is a *list*, and
every entry is connected at once — see [Several relays at once](#several-relays-at-once);
macOS and iOS still hold a single relay each. The appearance controls apply to the live panel while the
dialog is open — bringing it out if it is closed, since translucency can only be judged
against what is behind it — and are rolled back on Cancel. Opacity is clamped to ≥ 20 % so
a mis-drag cannot make the panel unreadable, and the dialog is reached from the tray rather
than from the panel, so nothing set here can lock you out.

**Multi-select is relay-only.** `question_toggle` / `question_submit` are relay-protocol
messages with no herdr CLI verb behind them, so the checkboxes are inert in direct mode
rather than pretending to work. Same restriction as macOS, which guards both on
`mode == .relay`.

## Protocol constraints this client respects

Read out of `relay/herdr_relay.py` while porting; the other clients get some of these
wrong. These are relay-mode rules — they guard the relay, not herdr, so direct mode is not
bound by them:

- **`respond` is allowlisted.** Only the 12 values in `SAFE_RESPONSES`
  (`herdr_relay.py:90`) are accepted; anything else is rejected with
  *"response not in allowlist"*. Free-form replies therefore go out as `agent_prompt`
  (≤10 000 chars). The mac and iOS approval cards send custom text as `respond`, so
  their custom-reply box cannot work against the relay.
- **Interrupt is `C-c`, not `Ctrl+c`.** `SAFE_KEYS` (`herdr_relay.py:91`) lists `C-c`.
  The mac app's `"Ctrl+c"` spelling only works because it talks to the local CLI
  instead of the relay.
- **`question_toggle` / `question_submit` are unhandled.** The relay has no branch for
  either message. The web app, TUI, mac and iOS clients all send them and are silently
  ignored. This client sends them too, for parity, so multi-select starts working the
  moment the relay grows support — but the checkbox path is inert today.

## Why not the Windows App SDK

`AppNotificationBuilder` is the nicer API and handles unpackaged registration in one
call, but it was rejected for two reasons:

1. Its build requires Windows-only native tools (`mt.exe`, `makepri.exe`), so the
   project could not be compile-checked outside Windows at all.
2. Framework-dependent mode makes users install the Windows App Runtime; self-contained
   mode drags in WinUI, ML and AI subpackages for what is ultimately one toast.

The plain WinRT projection that ships with the `net8.0-windows10.0.19041.0` target has
zero NuGet dependencies. The cost is doing the identity work by hand:
[`ShortcutHelper`](Services/ShortcutHelper.cs) writes the AUMID and ToastActivatorCLSID
onto a Start Menu shortcut, and [`ToastService`](Services/ToastService.cs) registers a
COM activator so button presses land in the running process instead of spawning a
second copy.

### When nothing appears

Notifications have an unusual number of ways to produce *silence* rather than an error, so
they get more instrumentation than the rest of the app:

- **A malformed payload is dropped without a word.** Two bugs of exactly this kind shipped
  and were the reason no toast ever appeared on Windows: `scenario` was set to
  `urgentReminder`, which is not one of the four values that exist (`reminder`, `alarm`,
  `incomingCall`, `urgent`), and `<audio>` was placed after `<actions>` when the
  [`toast` element](https://learn.microsoft.com/en-us/uwp/schemas/tiles/toastschema/element-toast)
  is an ordered sequence of `visual, audio?, commands?, actions?, header?`. Either one alone
  is enough for the platform to discard the notification silently.
- **The OS may simply have them switched off.** `ToastNotifier.Setting` is the only place
  Windows says so; `DeliveryBlocked` translates it (`DisabledForApplication`,
  `DisabledForUser`, `DisabledByGroupPolicy`) into something readable.
- **Setup can fail per machine** — the shortcut write, the CLSID registration, or the toast
  call itself, none of which may take the app down since notifications are meant to degrade
  rather than crash it. `SetupError` records which, with the HRESULT, because the useful ones
  are numbers: `0x80070490` (element not found) means the AUMID shortcut is not resolving,
  the usual first-run failure.

`Problem` folds the last two together and `TrayIconHost` shows it as a tray-menu line, the
same treatment `IslandViewModel.ConnectionError` gets.

Beyond that, every attempt is recorded in
[`DiagnosticLog`](Services/DiagnosticLog.cs) — `%LOCALAPPDATA%\herdr-remote\herdi.log` — with
the payload and HRESULT on failure. A tray app has no console and no window, so a subsystem
that fails quietly by design has no other way to account for itself.

## Layout

| Path | Corresponds to |
|---|---|
| `App.xaml.cs` | `HerdiMacApp.swift` (app delegate, wiring) |
| `Models/Agent.cs` | `Agent.swift` |
| `Models/Protocol.cs` | the wire protocol + relay allowlists |
| `Services/RelayConnection.cs` | `RelayConnection.swift` (both modes, one merge path, N relays) |
| `Services/RelaySocket.cs` | — (one WebSocket; macOS has only ever had one) |
| `Services/HerdrCli.cs` | `runHerdr` + `runSSH` + `resolveHerdrPath` |
| `Services/HerdrPoller.cs` | `pollHerdr` + `readPaneForBlocked` + `detectOptions` |
| `Services/ConnectionMode.cs` | `RelayConnection.ConnectionMode` |
| `Services/ToastService.cs` | `sendNotification` + toast actions |
| `Services/ShortcutHelper.cs` | — (no macOS equivalent needed) |
| `Services/TrayIconHost.cs` | `NSStatusItem` + `rebuildMenu` |
| `Services/TrayIconRenderer.cs` | — (macOS swaps SF Symbols; no count on the status item) |
| `Services/TaskbarInfo.cs` | — (macOS anchors to the notch; nothing to locate) |
| `Services/Updater.cs` | `Updater.swift` |
| `Services/SettingsStore.cs` | `UserDefaults` + Keychain |
| `Services/IslandAppearance.cs` | — (macOS hides the panel in the notch; nothing to tune) |
| `Views/IslandWindow.xaml` | `NotchPanel.swift` |
| `Views/SessionListView.xaml` | `SessionListContent` |
| `Views/ApprovalCardView.xaml` | `ApprovalCard` |
| `Views/PaneView.xaml` | — (stands in for `onJump` / `focusPane`) |
| `ViewModels/ResponseAction.cs` | `ResponseButtonGrid.mapOption` |

## Status

Compiles clean (`dotnet build`, zero warnings) and publishes to a single exe. Written and
compile-verified on Linux, so everything the compiler cannot check is unverified until it
runs on Windows: COM activation, the AUMID shortcut, flyout placement
against a taskbar on each of the four edges and across multi-monitor and mixed-DPI setups,
whether a tray click that dismisses the flyout is reliably swallowed rather than reopening
it, the slide-in feel, and the settings dialog's retemplated sliders and colour picker.

Toast delivery was the one thing this bit: the payload is now validated against the
published `toast` element schema — well-formedness, `scenario` against its four legal values,
child-element order, and the five-action cap — which is what caught the two silent-drop bugs
described above. That the platform then *shows* it still needs a real machine.

The badge geometry *was* checked: the disc fraction, ring width, digit size and the colour
pairing were each chosen by rendering the real .ico frames at 16 / 20 / 24 / 32 px through an
equivalent rasteriser and reading the result, against both a dark and a light taskbar. Three
things came out of that which a swatch would not have given:

- A green disc on the glyph's own green turns to mush by 20 px, hence the working badge being
  a dark disc carrying a *green digit* rather than the obvious way round.
- 0.66 of the icon buries the sheep and 0.52 is illegible; 0.64 keeps both.
- The digit's em wants to be about the disc's full inner diameter (0.98), not the 0.82 it
  started at — a digit is only its cap height, so a smaller em was giving up legibility for
  nothing. That in turn forced the point-centred `DrawString` overload: the rectangle one
  clips, and at this size the line box is taller than the disc.

What is still unverified is GDI+ itself — its antialiasing, and whether Segoe UI Bold hints
the same way at these pixel sizes as the font the preview was rendered with. Vertical
centring is left to `StringAlignment.Center` with no nudge, which is what the preview
showed; if digits sit high on real hardware it is one constant.
