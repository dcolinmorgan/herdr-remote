# herdi-win

Windows desktop client for [herdr-remote](../README.md) — a tray icon plus a Dynamic
Island-style capsule pinned to the top edge of the screen, with native toast
notifications when an agent needs you.

Port of [`herdi-mac`](../herdi-mac), both of its sources: a WebSocket to the relay, or
polling the herdr CLI directly over SSH.

```
┌─ collapsed ────────────────────────┐
│    ═════╡ ● ⚠2 ●3  5 ╞═════        │  top edge of the primary display
└────────────────────────────────────┘
              ↓ hover 0.45s
┌─ expanded ─────────────────────────┐
│  ● ▏ herdr ▕                       │
│  ─────────────────────────────────  │
│  ▌NEEDS YOU                     1   │
│  ▌ claude   relay — Do you want…    │
│  ▌WORKING                       3   │
│  ▌ codex    web                     │
│  ▌IDLE                          1   │
└────────────────────────────────────┘
```

## Build

Requires the [.NET 8 SDK](https://dotnet.microsoft.com/download) on Windows.

```powershell
cd herdi-win
.\build.ps1                 # self-contained single exe (~72 MB, no runtime needed)
.\build.ps1 -Framework      # ~3 MB, requires the .NET 8 Desktop Runtime
.\build.ps1 -Zip            # also produce the release asset the updater looks for
.\build.ps1 -Arch win-arm64 # ARM64
```

Output lands in `dist\<arch>\Herdi.exe`.

`dotnet build` also works from Linux/macOS for compile checking — the project sets
`EnableWindowsTargeting`. Running it obviously needs Windows.

## Setup

1. Launch `Herdi.exe`. It sits in the tray; there is no main window.
2. Tray → **Settings…** → pick a source:
   - **Relay (WebSocket)** — enter the relay URL (`ws://127.0.0.1:8375` locally, or the
     `wss://` tunnel URL) and the token if the relay requires one.
   - **Direct (herdr CLI + SSH)** — enter one SSH target per line, e.g. `user@devbox`.
     No relay needed. See [Direct mode](#direct-mode) for the auth requirement.
3. Tray → **Launch at Login** to start with Windows.

First run writes a Start Menu shortcut named *Herdi*. **Don't delete it** — Windows
resolves this app's notification identity (AppUserModelID) through that shortcut, and
toasts stop working without it.

## What it does

| Capability | Notes |
|---|---|
| Relay mode | WebSocket to the relay, which does its own polling and SSH |
| Direct mode | polls `herdr pane list` here — locally and over SSH — with no relay |
| Remote agents | either mode; the row shows `⇄` and the host it lives on |
| Blocked-agent toast | Title, body, sound — **plus** permission buttons and a reply box |
| Answer from the toast | Approve / Deny inline, or type a reply, without opening the island |
| Top capsule | Hover to expand, blocked agents auto-open the approval card |
| Session list | NEEDS YOU / WORKING / IDLE, blocked hoisted to the top |
| Approval card | diff-highlighted prompt, mapped response buttons, custom reply |
| Interrupt | ^C to the pane |
| Tray icon | turns red while any agent is blocked |
| Reconnect | exponential backoff capped at 30s |
| Launch at login | per-user `Run` registry key |
| Self-update | GitHub Releases, same repo and 10-minute throttle as the mac app |
| Token storage | DPAPI-encrypted (CurrentUser) in `%LOCALAPPDATA%\herdr-remote\settings.json` |
| Fullscreen awareness | hides while another app is fullscreen or presenting |
| Keyboard | `Ctrl+Y` Allow · `Ctrl+T` Trust · `Ctrl+N` Deny · `Esc` back |

The toast is deliberately richer than the macOS one: `sendNotification` on macOS
(`herdi-mac/Sources/RelayConnection.swift:433`) posts text and a sound with no actions,
so answering there always means opening the panel.

## Direct mode

Reads agent state without a relay: every 2 s it runs `herdr pane list` on each configured
host and merges the results, then reads the pane of anything newly blocked to fill the
approval card. Answering, interrupting and reading a pane go back out the same way
(`pane send-text` + `send-keys Enter`, `pane send-keys C-c`, `pane read`).

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

**No "Jump to terminal" button.** On macOS this shells out to `herdr workspace focus` +
`herdr tab focus`. The relay protocol has no equivalent message, and focusing a window is
only meaningful on the machine running herdr — which in direct mode is an SSH host, not
this one. The row's interrupt button is kept.

**No notch.** The capsule shape is drawn rather than measured from
`screen.auxiliaryTopLeftArea`, and it sits on the primary display's top edge.

**Springs become easing curves.** WPF has no spring animation, so each macOS spring maps
to the closest-feeling easing: overshoot (`BackEase`) for expand and pop, none
(`CubicEase`) for collapse.

**Icon font avoided.** Response buttons use plain Unicode symbols instead of SF Symbols'
Windows analogue. Segoe MDL2 Assets and Segoe Fluent Icons differ in glyph coverage
between Windows 10 and 11; text symbols render the same on both.

**One settings dialog.** The mac app spreads the same choices across its status menu (a
Direct/Relay toggle, an add-remote sheet) plus `UserDefaults` keys it never surfaces, and
it has no way to type a relay URL at all. Here the source, the relay URL and token, the
SSH hosts and the herdr path live in one dialog, and the choice is remembered across
launches.

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

## Layout

| Path | Corresponds to |
|---|---|
| `App.xaml.cs` | `HerdiMacApp.swift` (app delegate, wiring) |
| `Models/Agent.cs` | `Agent.swift` |
| `Models/Protocol.cs` | the wire protocol + relay allowlists |
| `Services/RelayConnection.cs` | `RelayConnection.swift` (both modes, one merge path) |
| `Services/HerdrCli.cs` | `runHerdr` + `runSSH` + `resolveHerdrPath` |
| `Services/HerdrPoller.cs` | `pollHerdr` + `readPaneForBlocked` + `detectOptions` |
| `Services/ConnectionMode.cs` | `RelayConnection.ConnectionMode` |
| `Services/ToastService.cs` | `sendNotification` + toast actions |
| `Services/ShortcutHelper.cs` | — (no macOS equivalent needed) |
| `Services/TrayIconHost.cs` | `NSStatusItem` + `rebuildMenu` |
| `Services/Updater.cs` | `Updater.swift` |
| `Services/SettingsStore.cs` | `UserDefaults` + Keychain |
| `Views/IslandWindow.xaml` | `NotchPanel.swift` |
| `Views/SessionListView.xaml` | `SessionListContent` |
| `Views/ApprovalCardView.xaml` | `ApprovalCard` |
| `Controls/IslandShape.cs` | `NotchPanelShape` |
| `ViewModels/ResponseAction.cs` | `ResponseButtonGrid.mapOption` |

## Status

Compiles clean (`dotnet build`, zero warnings) and publishes to a single exe.
**Not yet run on Windows** — it was written and compile-verified on Linux, so the
runtime behaviour that cannot be checked by the compiler is unverified: toast delivery
and COM activation, the AUMID shortcut, capsule placement across multi-monitor and
mixed-DPI setups, and the hover feel.
