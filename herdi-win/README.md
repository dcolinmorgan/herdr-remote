# herdi-win

Windows desktop client for [herdr-remote](../README.md) — a tray icon plus a Dynamic
Island-style capsule pinned to the top edge of the screen, with native toast
notifications when an agent needs you.

Port of [`herdi-mac`](../herdi-mac), relay mode only.

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
2. Tray → **Relay Settings…** → enter the relay URL (`ws://127.0.0.1:8375` locally, or
   the `wss://` tunnel URL) and the token if the relay requires one.
3. Tray → **Launch at Login** to start with Windows.

First run writes a Start Menu shortcut named *Herdi*. **Don't delete it** — Windows
resolves this app's notification identity (AppUserModelID) through that shortcut, and
toasts stop working without it.

## What it does

| Capability | Notes |
|---|---|
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

## Deliberate differences from herdi-mac

**Direct mode is not implemented.** The mac app can poll the local `herdr` CLI and SSH
into `HERDR_REMOTES` directly, bypassing the relay. This client is relay-only, which
also sidesteps a pile of platform-specific work: the hardcoded `/usr/bin/ssh` path, the
`/opt/homebrew/bin/sshpass` password-auth path (no Windows equivalent), and Keychain.

**No "Jump to terminal" button.** On macOS this shells out to
`herdr workspace focus` + `herdr tab focus` locally. The relay protocol has no
equivalent message, and with a remote relay the herdr window is on another machine
anyway. The row's interrupt button is kept.

**No notch.** The capsule shape is drawn rather than measured from
`screen.auxiliaryTopLeftArea`, and it sits on the primary display's top edge.

**Springs become easing curves.** WPF has no spring animation, so each macOS spring maps
to the closest-feeling easing: overshoot (`BackEase`) for expand and pop, none
(`CubicEase`) for collapse.

**Icon font avoided.** Response buttons use plain Unicode symbols instead of SF Symbols'
Windows analogue. Segoe MDL2 Assets and Segoe Fluent Icons differ in glyph coverage
between Windows 10 and 11; text symbols render the same on both.

**Relay settings UI added.** The mac app has no way to type a relay URL — it only
toggles Direct/Relay and reads `hostAddress` from `UserDefaults`. A relay-only client
needs somewhere to paste the tunnel URL.

## Protocol constraints this client respects

Read out of `relay/herdr_relay.py` while porting; the other clients get some of these
wrong:

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
| `Services/RelayConnection.cs` | `RelayConnection.swift` (relay half) |
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
