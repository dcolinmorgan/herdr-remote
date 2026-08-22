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
| `relay/transcript.py` | Agent transcript reader behind `get_history` | Python (stdlib only) |
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
| `HERDR_SSH_CONTROL_PATH` | Override the SSH multiplexing control socket (default `<log dir>/ssh-%C`; skipped on Windows, or when the path would exceed the AF_UNIX limit) |
| `HERDR_BIN` | Path to herdr binary (default: `/opt/homebrew/bin/herdr`) |
| `HERDR_RELAY` | Relay URL used by clients (default: `ws://127.0.0.1:8375`) |
| `HERDR_SESSION` | Boot-time default herdr session; a client can override it per source at runtime via `session_switch` |
| `HERDR_TRANSCRIPT` | Set to `0` to refuse every `get_history` with `unavailable: "disabled"` |
| `HERDR_CLAUDE_ROOTS` | Comma-separated roots to search for claude transcripts (default `~/.claude/projects`) |
| `HERDR_REMOTE_CLAUDE_ROOTS` | Same, as remote shell words (default `$HOME/.claude/projects`) |
| `HERDR_TRANSCRIPT_MAX_BYTES` / `HERDR_TRANSCRIPT_TAIL_BYTES` | Read only the last N bytes of a transcript past this size (default 64MB / 8MB) |
| `HERDR_TRANSCRIPT_REMOTE_TAIL_BYTES` | Bytes of a remote transcript to fetch per read (default 4MB) |
| `HERDI_RENDER` | Windows client only: `hardware` restores WPF's GPU path (default is software — see `herdi-win/README.md#memory`) |

Runtime session overrides (per source) are persisted to `active_sessions.json` inside `HERDR_LOG_DIR`, so they survive relay restarts.

## Web App

The web app is a single self-contained HTML file (`web/index.html`) with inline CSS and JS — no build step. It's deployed to Cloudflare Pages. It includes 11 color themes, a mobile terminal keyboard, PWA support, and agent-icon detection.

## WebSocket Protocol

Messages are JSON with a `type` field:

**Server → Client:** `agents` (complete state snapshot), `agent_update` (single-pane state merge), `blocked` (approval prompt), `pane_content` (terminal read), `sessions` (per-source herdr session lists and the active selection), `history` (transcript turns, or `unavailable` with a reason)

**Client → Server:** `respond` (send text to agent), `read_pane` (request terminal content), `send_keys` (send key sequences), `send_text` (raw text without newline), `agent_prompt` (submit free-form text via `herdr agent prompt`), `session_switch` (point one source at a herdr session; `session: null` follows herdr's default), `get_history`, `create_tab`, `push_subscribe`/`push_unsubscribe`

An `agents` entry carries, per pane: `pane_id`, `agent`, `label`, `workspace_label`, `status`,
`cwd`, `project`, `host`, `remote`, `workspace_id`, `tab_id`, `scrollback` + `viewport_rows` (from
herdr's `scroll`), `has_session` (this pane names an agent transcript), and `title` — herdr's
terminal title, which is **live activity, not a session name**. A working claude sets it to what it
is doing; `activity_title` drops the harness's own banner, which is all an idle or done pane leaves
there. The session ref itself stays server-side in `pane_session_map`.

### Relay-side constraints clients must respect

- **`respond` is allowlisted.** Only the 12 values in `SAFE_RESPONSES` (`herdr_relay.py:90`) are accepted; anything else returns `response not in allowlist`. Free-form replies must use `agent_prompt` (≤10000 chars) or `send_text` (≤1000). The mac/iOS approval cards send custom text as `respond`, so their custom-reply box does not work against the relay.
- **Keys use herdr's `+` grammar, validated by `key_is_allowed`, and `keys` must be a non-empty array.** Bare specials (`Enter` `Escape` `Tab` `Space` `Backspace` `Up`…`F12`), single characters, and `ctrl+`/`shift+`/`alt+` chords all pass — special names case-insensitively, so `esc` and `shift+tab` are fine. `C-c` also passes: live-verified as the one tmux-style spelling herdr 0.8.0 still aliases to interrupt (`C-u`, `M-x`, `BTab` do not). `BSpace`, `Insert` and `Delete` are rejected by herdr in any spelling.
- **`PageUp`, `PageDown`, `Home` and `End` are sent as bytes, not as keys.** herdr's own validator refuses every spelling of them (re-probed on 0.8.2: `PgUp`, `pageup`, `Page_Up` and `ctrl+Home` all answer `unsupported key`), so `key_escape_sequence` turns them into the CSI bytes a terminal emits and the relay ships that through `pane send-text` instead — `pane send-text` is a byte channel and passes ESC verbatim. Modified forms are computed, not enumerated: xterm's `1 + shift(1) + alt(2) + ctrl(4)`, so `ctrl+Home` is `ESC[1;5H` and `shift+PageUp` is `ESC[5;2~`. A mixed `keys` array keeps its order — consecutive keys of one kind travel in one CLI call, so `[Escape, PageUp, PageDown, Enter]` becomes send-keys / send-text / send-keys, in that order. Clients still just send the key name.
- **`question_toggle`/`question_submit` have no relay handler.** The web app, TUI, mac and iOS clients all send them; the relay ignores both, so multi-select questions cannot be answered from any client until it grows support.
- **`read_pane` picks its own source.** `source` ∈ `visible | recent | recent-unwrapped | detection`
  (default `recent`), `lines` is clamped to 1000, `format` ∈ `text | ansi`.
- **`get_history` reads the agent's own transcript, not the terminal.** Request:
  `{pane_id, limit?, before?, include_tools?}` — `limit` defaults to 200 and is capped at 2000,
  `before` is a turn `uuid` from an earlier response (page towards older), `include_tools` defaults
  to false. Response: `{messages, total, has_more, title, agent, file_truncated, unavailable}`,
  where each message is `{uuid, role, text, ts, truncated}` and `role` ∈
  `user | assistant | note | tool`. Turns come back oldest-first.
  - A `tool` turn carries more: `tool` (the name), `target` (the one argument worth showing —
    `command` for Bash, `file_path` for Edit/Read/Write, else the first of `TOOL_TARGET_KEYS`),
    and on a failure `error: true` plus `result` (the first line of the tool_result). `text` is
    unchanged and still the whole one-line summary, because the macOS, iOS, Windows and TUI
    clients render that string and know none of these fields.
  - **A file edit carries its diff.** `Edit`, `MultiEdit` and `Write` ship `diff` plus `added` /
    `removed` counts, and `diff_clipped` when the body is only the head of the change. Both sides
    were always in the transcript — an Edit's input holds them verbatim — they just never survived
    parsing. **The diff has no `@@` header and no line numbers**: `old_string` is a *fragment* of
    the file, so every number difflib produces counts from the fragment and would not match the
    editor the reader is about to open. A jump between hunks is a bare `...` row. The counts are
    of the whole change even when the body is clipped, so a client can say "+200, showing 40".
  - Ceilings are `DIFF_MAX_LINES` (40) and `DIFF_MAX_CHARS` (2000). Measured over the 1,840 Edit
    calls in the 25 largest transcripts here: median 10 lines / 494 chars, p90 40 lines / 1.9KB,
    max 321 lines / 14KB. A `Write`'s content is one side only — median 90 lines, up to 1,529 —
    so it is always the head of the file. A diff spends from the same `PAGE_TEXT_BUDGET` as the
    prose, which is why `include_tools: false` (the default) costs nothing.
  - The session uuid never crosses the wire. Clients send a `pane_id`; the relay resolves it
    through `pane_session_map` and validates the ref before it touches a path.
  - A page is bounded by turn count **and** by ~128K characters, whichever bites first — measured
    200-turn pages ranged from 97KB to 324KB of JSON. Whatever the budget cuts is still reachable
    through `has_more`.
  - `include_tools: false` filters tool turns out *before* pagination, so they neither show nor
    consume a slot; on the biggest session here 674 of 794 turns were tool calls.
  - An unknown `before` degrades to the newest page rather than an empty one.
  - `unavailable` ∈ `no-session | no-log | unsupported | disabled | error`; clients must render the
    reason rather than "no history for this pane". `file_truncated` means only the tail of the file
    was read (a remote host, or a file past `HERDR_TRANSCRIPT_MAX_BYTES`) — say so instead of
    implying the conversation starts there.

### herdr read semantics the relay is built on (live-probed, herdr 0.8.0 / protocol 19)

- **`pane.read` is clamped at ~1000 lines, silently.** 1000, 1500 and 5000 all return the same 1000
  rows with `truncated` unchanged. There is no offset/paging parameter, so 1000 lines back from the
  bottom is the deepest any single read can reach.
- **An agent pane has NO scrollback.** Every agent pane reports `scroll.max_offset_from_bottom: 0`
  (its TUI runs on the alternate screen); shell panes on the primary screen report thousands. A
  client can read that field instead of probing.
- **`recent` + `format: text` on an idle agent pane HARVESTS**: herdr walks the agent's own
  mouse-scroll interface, which measured 6.2s for 200 lines and 12.7s for 400 (~31ms/line), only
  works while the agent is idle, isn't deterministic, and visibly scrolls the operator's terminal up
  and back. `format: ansi` and `source: visible|detection` never harvest and return instantly.
  **Anything on a timer must therefore read `visible`** (`PROMPT_READ_SOURCE`); a scrollback read
  has to be user-initiated.
- **`herdr agent history` does not exist.** `herdr agent` has
  list/get/read/send-keys/prompt/rename/focus/wait/attach/start/explain. Conversation history comes
  from the agent's own transcript (`~/.claude/projects/<mangled-cwd>/<session-uuid>.jsonl` for
  Claude), keyed by the `agent_session` ref on the pane record.

### Transcript reader (`relay/transcript.py`)

Claude's JSONL is the only format understood; adding a harness is a locate+parse pair plus one line
in `HARNESSES`. Live-measured on the 196 transcripts on this machine (327MB, largest file 33.4MB):

- **Found by uuid, not by deriving the path.** `glob(<root>/*/<uuid>.jsonl)` measured 0.7ms. The
  `cwd → directory` mangling rule is real (`/`, `.` and `_` all become `-`) but the pane's cwd is
  the *shell's*, while claude's project directory is fixed at *its* startup cwd; they drift.
- **Cost:** largest file cold 227ms (read + parse), warm 1ms — the cache holds parsed turns
  (0.25MB for that session), never the raw 33MB, and is invalidated on size (+mtime locally).
- **Rows are dropped generously.** `thinking` blocks, `attachment`/`file-history-*`/`mode` rows,
  `isMeta` envelopes, `isSidechain` subagent traffic, and any unknown `type`. A format drift in
  claude costs a few turns, not the panel.
- **Replayed rows are deduped by row uuid.** One real transcript here writes 591 of its 2602 rows
  twice (a resumed session re-appending what it loaded); without the dedupe those turns render
  twice and a turn uuid is not a usable cursor.
- **A `tool_result` folds into the `tool_use` turn it answers** instead of becoming its own turn:
  683 of one session's 724 `user` rows were tool_result traffic.
- **Remote panes are read over SSH in one round trip** (`ls`/`wc`/`tail`/`head`, no python needed on
  the far side), framed as `NOFILE` / `CACHED` / `SIZE <n>` + the file's tail. The relay offers the
  size it already has, so paging a remote pane usually moves no bytes. Remote history is therefore
  **recency-bounded** (`HERDR_TRANSCRIPT_REMOTE_TAIL_BYTES`, default 4MB) and says so through
  `file_truncated`.

## Deployment

- Web app: Cloudflare Pages (push to main deploys `web/`)
- Demo worker: `npx wrangler deploy` from `demo-worker/`
- macOS app: `herdi-mac/build.sh` produces `dist/Herdi.app`
