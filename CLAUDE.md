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
| `web/` | Mobile/desktop web app: `index.html` + `app.css` + `js/*.js`, no build step | HTML/CSS/JS |
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
| `HERDR_SHELL_PANES` | Set to `1` to list, read and **write** the panes with no agent in them (default off — writing to one is arbitrary command execution; see SECURITY.md) |
| `HERDR_TRANSCRIPT` | Set to `0` to refuse every `get_history` with `unavailable: "disabled"` |
| `HERDR_CLAUDE_ROOTS` | Comma-separated roots to search for claude transcripts (default `~/.claude/projects`) |
| `HERDR_PI_ROOTS` | Comma-separated roots holding pi session logs; a pane's path ref must sit inside one (default `~/.pi/agent/sessions`) |
| `HERDR_REMOTE_CLAUDE_ROOTS` | Same, as remote shell words (default `$HOME/.claude/projects`) |
| `HERDR_TRANSCRIPT_MAX_BYTES` / `HERDR_TRANSCRIPT_TAIL_BYTES` | Read only the last N bytes of a transcript past this size (default 64MB / 8MB) |
| `HERDR_TRANSCRIPT_REMOTE_TAIL_BYTES` | Bytes of a remote transcript to fetch per read (default 4MB) |
| `HERDI_RENDER` | Windows client only: `hardware` restores WPF's GPU path (default is software — see `herdi-win/README.md#memory`) |

Runtime session overrides (per source) are persisted to `active_sessions.json` inside `HERDR_LOG_DIR`, so they survive relay restarts.

## Web App

`web/` is `index.html` (markup) + `app.css` + ten scripts under `web/js/`, and **still has no build
step** — plain `<script src>` tags, loaded in the order they are listed. It's deployed to Cloudflare
Pages. It carries a mobile terminal keyboard, PWA support, and agent-icon detection.

Three things about that layout are load-bearing:

- **They are not ES modules, and the order matters.** 77 inline `on*` handlers in the markup and in
  rendered template strings call these functions by name, so everything has to stay on the global
  scope; `type="module"` would put each file in its own and break all 77 at once. The scripts share
  one scope exactly as the single `<script>` block did, which is why `tests/run.sh` step 9c looks for
  duplicate `function` names across *all* of them rather than per file.
- **The paths are relative** (`js/state.js`, not `/js/state.js`). The browser tests open the app
  from a `file://` URI, where an absolute path is the filesystem root.
- **The relay serves `web/` as a directory** (`web_asset`), not from a list of filenames — see
  below. Adding a file needs no relay change.

`web/js/` in load order: `state` (globals, settings, theme, socket) → `markdown` → `diff` →
`triage` → `spaces` (grouping and naming lookups) → `cards` (what a row is called and how it
renders) → `mirror` (the terminal reconciler) → `nav` → `palette` → `push`.

**The palette is Claude Code's own ground, by way of collie** (`collie/web/src/index.css`): a neutral
grey ramp on `#0a0a0a`, with the four status hues tuned against each ground rather than one set
reused for both. The values are collie's oklch rasterized, because the `theme-color` metas, the web
manifest and `ANSI_COLORS` are all hex and a token that disagreed with a meta shows as a seam under
the URL bar. It is written **once**, as a block of `light-dark()` pairs resolved by the root's own
`color-scheme`; the other shape — a light palette plus a dark `@media` copy — has to restate every
value, and then a third time for an explicit pin. That is what makes the Settings switch two lines
of CSS (`:root[data-theme="light"|"dark"] { color-scheme: … }`), and it puts native UI — scrollbars,
form controls, the caret, the iOS keyboard — on the right side of the theme for free. The cost is a
floor of Chrome 123 / Safari 17.5, next to the `color-mix()` already load-bearing throughout the
file.

- **Auto is the *absence* of a pin**, so it works with JavaScript disabled entirely. What JS owns is
  the part CSS cannot do: taking a **stale** pin back off — Dark → Auto leaving `data-theme` stamped
  is the bug the two-way write exists to stop — and the two `theme-color` metas, which carry `media`
  attributes and therefore follow the OS rather than the pin, so a pinned reader is given the pinned
  colour in *both*. The choice is a **bare string** in `localStorage.herdr_theme`, written by
  `setTheme` and read by a script in `<head>` before first paint; `JSON.stringify` there would store
  `"dark"` *with* the quotes and the anti-flash would silently never fire again. Three exclusive
  choices are a `radiogroup`, not three `aria-pressed` toggles announcing three independent
  switches, and the selected one fills with the inverted neutral the session view's toggles already
  use — blue would read as the *selection* colour the chips own.
- **`--on-accent` is the text ON a saturated fill.** A saturated token is dark in the light theme
  and light in the dark one, so no single literal serves both: the `color: #fff` these controls
  carried measured **2.6:1** against the dark theme's blue. The pair measures ≥6.6:1 on all four
  hues in both themes.
- **The mirror is dark under both themes** and carries its own `color-scheme: dark`. `ANSI_COLORS`
  is VS Code's Dark+ set — the same 16 collie ships — authored for a dark ground, and it sits beside
  truecolor an agent emits that no palette can re-theme. That `color-scheme` also puts the tokens
  used *inside* the pane (the search highlight) on their dark halves, and stops a light theme from
  handing dark output a light scrollbar.
- **The UI is monospace**, because the app is a window onto a terminal and a proportional shell
  around a monospace pane read as two programs sharing a screen. `--font-mono` is the system stack;
  the bundled **Hack Nerd Font is deliberately not in it** — 982KB, and today nothing fetches it
  until a session opens, since a `display: none` element loads no font. It stays first in
  `--font-term`, where its Nerd Font glyphs are the reason it is shipped at all. Every measured
  layout assertion in `tests/test_web_*.py` — the key labels that must not clip at 320px, the herd
  row that gives up the project before the tab, the chrome budgets — passes unchanged under it.

`tests/test_web_theme.py` measures all of it in a browser: the two grounds, a pin beating the OS in
both directions, the stale pin coming off, the pre-paint stamp surviving a reload, the metas, the
contrast on each saturated fill, and "monospace" as two equal-length strings of different glyphs
measuring the same width.

The history panel renders a conversation, not a log: a person's turn is a tinted bubble, the
agent's is full-width markdown, a tool call is one compact row, and a file edit opens into its
diff. Two renderers do that work, and both build **DOM nodes, never HTML strings** — every
character of transcript text lands in a `textContent`, which is the same boundary `ansiFragment`
holds and is what makes the escaping provable rather than remembered.

- `mdFragment` is a hand-rolled markdown subset (no build step, and the CSP blocks every CDN).
  What it supports is measured, not guessed: across the 2,572 assistant text blocks (838KB) in
  the 25 largest transcripts here, inline code appears in 43.8%, bold 32.6%, bullets 11.2%,
  headings 10.3%, **GFM tables 8.7% — more often than fenced code at 5.2%** — ordered lists 5.7%,
  rules 2.5%, italics 2.4%, quotes 1.4%, links 1.3%, strikethrough 0.1% (unsupported).
  Deliberately absent: `_underscore_` emphasis, which would mangle `snake_case_identifiers`; and
  `*emphasis*` requires non-space just inside both delimiters so `rename *.ts to *.tsx` survives.
  Blocks are flat, links are `https?:`/`mailto:` only, and headings become `.md-h1`…`.md-h6` divs
  rather than real `h1`s so an agent's `#` cannot outrank the panel's own title.
- `diffFragment` colours `+`/`-`/context lines and moves the marker into its own gutter cell, so
  the code keeps its real indentation instead of being shifted a column.
- A turn is stamped in the **reader's** zone, not the file's. Claude writes every transcript
  timestamp in UTC — all 4,450 rows sampled here end in `Z` — and this was `ts.slice(11, 16)`, five
  characters lifted straight out of the string, so a turn made at 17:08 in UTC+8 read 09:08.
  `turnStamp` parses it and formats through `toLocaleTimeString`; the **date rides along when the
  turn is not from today**, because paging back is the whole point of the panel and a bare `18:51`
  cannot say which day it belongs to. A string the platform will not parse falls back to the old
  slice, and an empty fallback yields no stamp rather than a blank one.
- Renderer behaviour is tested in a real browser: `tests/test_web_history.py` loads
  `web/index.html` over `file://` with playwright and asserts the DOM (skipped, not failed, where
  playwright or chromium is missing; `tests/run.sh` step 9d runs every `tests/test_web_*.py` in one
  process with playwright on the path, which is why each file shares **one** browser at module
  scope — a class that started a second playwright instance is the contention that makes
  `page.goto` time out). `WebHistoryPanelTests` pins the page to `Asia/Shanghai` and `en-GB` and
  reopens it as `America/Los_Angeles`, because "the reader's zone" is exactly the claim and the
  runner's own clock would make it a test of the runner.

The panel's header is **one row**, and the filter opens *in place of* the conversation title
(`toggleHistoryFind`) rather than beside it. It was two rows — a title bar over a filter bar,
measured **80px of a 390×844 screen, 9.5%, spent before a single turn had rendered** — and the
filter, which is only wanted while you are looking for something, paid for its input box
permanently. It is 35px now, and the same 35px in both states: every child of that flex row is
pinned to 22px, because the row's height is set by its tallest child and an input even two pixels
taller than a chip would make the header jump every time the filter opened. Closing the filter
**drops the needle** — one still hiding turns while its input is off screen would read as a
conversation with pieces missing — and a fresh page closes it, so a needle cannot survive into the
next conversation.

The list carries **`overscroll-behavior: contain`**, for the same reason `.term-content` does: at
the top of it a downward drag chained to the document and handed Chrome its pull-to-refresh, which
reloads the whole app — losing the open session, the panel, and however far back you had paged. The
two are asserted together so they cannot drift apart.

**A selection has to survive a page that rebuilds itself, and that is a question about blast
radius.** Measured in chromium, on every way there is to update text under one:

| what you do | what happens to a range inside |
|---|---|
| `el.replaceChildren(…)` | collapses to `(el, 0)` — the top of the buffer |
| `node.data = next` | collapses to `(node, 0)` — the DOM spec's `replaceData` |
| `node.appendData(extra)` | untouched |

So no node can be rewritten without moving what is anchored in it, and the only question is how
little has to be rewritten. `ansiFragment` emits one span per styled **run**, and a run spans
newlines — a pane with no colour in it is *one text node holding the whole buffer* — so a change on
the last line moved a caret sitting on line 3 to the top of the output. **That is the reported bug:**
a touch drag leaves a **caret** behind, the tick rebuilt the buffer, the caret came back at
`(el, 0)`, and the reader's next drag highlighted the first line. Freezing cannot fix it, because
freezing on a caret would stop the mirror for good on the first tap. Collie polls the same mirror
and carries no selection code at all (its one `getSelection` is about not stealing focus), because
React renders one keyed node per line: a change on line 7 never touches line 3's node.

**So the mirror is one span per line** (`mirrorLineNodes`), and a tick is a reconcile
(`mirrorPatch`):

- **identical content touches no DOM** — most ticks, since an idle pane repeats itself every 3s and
  the old code rebuilt the buffer 20 times a minute for nothing;
- **a buffer that scrolled by k lines keeps the nodes of the lines that stayed** (`mirrorShift`,
  verified in full before it is acted on and capped at 64, because a jump of more than a screenful
  is a repaint) — which is the only way a range survives a pane that is actually working;
- **a line that only grew takes `appendData`**, the one range-safe mutation, so it may land mid-drag;
- **a rewritten line rebuilds itself** and nothing else, so a caret ends up at the start of its own
  line rather than at the top of the buffer;
- **a caret in a line being deleted is dropped**, not left to fall back to `(el, 0)` — which is
  exactly where the next drag would extend from.

The newline between two lines is a text node **between** the spans rather than inside one, so
`el.textContent` is byte-identical to the old flat render — `doSearch` counts offsets in it — and
the boxes are asserted equal to it rather than read off the CSS. Cost on a 1000-line coloured
buffer (the deepest a herdr read reaches): identical tick **0.1ms** against the old 3.9ms, a repaint
14.3ms against 3.9ms; at the default 200 lines, 0.1ms and 3.9ms against 0.9ms. The common tick got
40× cheaper and the worst one 4× dearer.

**The mirror carries scrollback, and paging back holds it.** Every read is `recent`. It used to be
decided per pane — `recent` wherever `scrollback` was non-zero, `visible` where it was 0 — on the
belief that an agent pane never has a ring, because its TUI runs on the alternate screen. That is a
distinction without a difference: measured on this host (herdr 0.8.2, all 35 live panes, ansi,
`recent` 200 against `visible` at the pane's own height), **every agent pane reports no ring, so the
two reads come back byte-identical**. Where they do differ — shell panes holding a ring, +1.4KB to
+52KB — the extra bytes *are* the scrollback the reader opened the pane to see, so `visible` there
is not a saving but a missing feature.

Nor can the choice be split by read rather than by pane. `visible` returns the rendered grid and
nothing else, and `mirrorPatch` reconciles the **whole** buffer — so priming one `recent` read on
open and then following with `visible` would show the history for exactly one tick before the 3s
pass deleted every line the viewport no longer holds. The follow read is bounded at
`PANE_LINES_BASE` instead, which costs nothing extra: `loadMore` stops the tick before `paneLines`
can grow past it.

Two rules fall out of it, and each was its own leak:

- **Only follow mode auto-refreshes.** The tick reused whatever `paneLines` had grown to, so one
  tap on "load more" put a 600-line read on a timer and the ceiling put a 1000-line one there:
  **125.7KB per tick, 42KB/s**, re-fetching output the reader had already scrolled away from — and
  the next tick would have replaced their page anyway. Held mode sends nothing; `followPane` (the
  refresh button) is the way back, and it is the *only* way back, which is why that button is no
  longer a bare re-read — in held mode a re-read would just fetch the same page again.
- **Only a real switch resets the reading state.** `openTerminal` is re-entered on every `blocked`
  event for the pane already in front of you, and it reset `paneLines` unconditionally. With a
  follow flag beside it that becomes: the reader is pulled back to the live screen the moment
  their agent asks a question — the one moment they are most likely to be reading. `paneLines`,
  `paneFollowing` and `userScrolledUp` now sit inside the same `activePane !== paneId` guard the
  panels do.

**A real selection still stops the tick outright** (`selectionInside`), because a line the reader is
selecting inside can still be the line that gets rewritten. It is checked in four places:
`mirrorTick`, which then does not even *send* the read (a herdr call, an SSH round trip on a remote
host, for content it may not render); the `pane_content` handler, for a read in flight when the drag
started and for a manual refresh; `loadMore`, whose answer puts hundreds of lines *in front* of what
is on screen; and `render`'s list write — there **after** the name maps and the sibling strips, so
only the list holds still. Nothing is queued: the tick repeats.

**Only a *vertical* arrival at the top asks for more lines.** `scroll` says nothing about which axis
moved, and `scrollTop === 0` is true for the whole of a sideways drag — permanently true when the
output is shorter than the box. Measured: one wheel right took the read from 200 lines to 600 and
the next to 1000, each answer a wholesale different content, and `loadMore` reaches `refreshPane`
directly so the tick's own guard never saw it.

**A switch empties the mirror** (`clearPaneMirror`), because until the new pane's read lands there
is nothing true to put there. The read is a relay round trip — milliseconds locally, an SSH hop and
up to seconds on a remote host — and for that whole window the buffer on screen was the output of
the pane you *left*, sitting under the new pane's title and beside its filled chip, with nothing
saying it was stale. Three things go with it, and each is its own bug otherwise: `__mirror`, the
element property `mirrorPatch` reconciles against, which now describes another pane; any **range
inside that output**, because `selectionInside` guards the mirror and cannot tell a stale drag from
a live one — it would have refused the new pane's first read and left the mirror empty until the
reader tapped somewhere; and the scroll. Only on a **real switch**: `openTerminal` is re-entered on
every `blocked` event for the pane already in front of you, and blanking there would blink the
output away every time an agent asked a question.

`tests/test_web_selection.py` measures all of it, the collapse table included.

The history panel asks for tool turns **by default** (`history_.tools` starts `true`). The relay's
own default is still `include_tools: false` — this is the web client's choice, because a tool call
is most of what an agent's turn consists of and hiding them rendered a conversation with holes in
it. It costs reach: tool turns spend page slots and characters from the same budget as the prose,
so a page reaches less far back. The `Tools` chip is the way to prose-only.

The key pad and the panel layering are geometry, so `tests/test_web_keys.py` measures them rather
than reading the CSS. The arrow keys sit in a `grid-template-areas` inverted T — `up` shares its
column with `down`, and the empty cell is above `left`, where a keyboard has nothing either — and
the test asserts the boxes, not the rule. The pad is **seven columns and two rows**, sized against
measurements at 390×844 across four revisions: four rows of 44px was 271px closed / 415px with
presets open, five columns and three rows was 205 / 301, seven columns with the pad switch and the
presets disclosure sharing one line was 121 / 201, and trimming every key's own height and padding
brought it to **111 / 183**. Twelve keys need thirteen cells because of the arrows' empty corner,
which makes seven the narrowest grid that fits two rows; `Enter` spans both rows, and no label clips
down to a 320px viewport (40px a cell). The same pass took the digit pad from 3×3 of 52px keys
(164px, taller than the keys pad above it) to **one row of nine** (73px), and the quick dock from
two labelled sections of 44px buttons to a 3×2 grid — colour already said which two were the
confirm pair. Tests hold the dock under 14% / 22.5% of the screen, count the rows, and check every
label for clipping, so none of it can grow back quietly.

Two things were found by measuring rather than reading, both in the same trim pass:

- **The input row is a flex box, so an inline `padding` on any child governs the whole row.** It
  stayed 60px after `.term-input button:last-child`'s padding was cut, because the `/` and Send
  buttons carried theirs inline where no rule reaches — every child stretches to the tallest. Both
  moved into `.term-cmd` / `.term-send`, the row is 43px, and a test refuses any inline `padding`
  under `.term-input` so the trap cannot be re-set.
- **`key-green` / `key-blue` / `key-red` had no CSS at all.** The single-letter form of the three
  approval answers rendered as 11×19px native buttons in Chrome's own grey, unthemed, in the middle
  of a dark dock — unhittable, and invisible as a group. `#actionKeys` is now a 3-column grid of
  30px keys tinted from the same accents as the answers above them; it costs the dock 14px while a
  pane is blocked.
Panes with no agent in them are **not in the herd list**, which is agents only. Two thirds of the
panes on a real host are these — 20 of 30 — they carry no `status` at all, and triaging them would
bury ten agents under twenty rows that can never be anything but Recent. They are reached by picking
a space (which groups by tab and shows both kinds together) and from the sibling strip inside a
session. A terminal's dot is **hollow** rather than a fifth shade competing with the four buckets,
which is the same thing `worstTriage` says by returning null for a set holding only these.

**The herd list is `triage`, the one ordering the whole page agrees on**: `Needs you` → `Ready ·
unseen` → `Working` → `Recent`, tested in `tests/test_web_spaces.py`. Ported from Collie's
`lib/triage.ts`, and the point of it is one classifier — `bucketOf` — that the rows, the space chips
and the tab chips all route through, so a chip and the row it stands for cannot come to disagree
about what a colour means.

- **`Ready · unseen` is the section that could not exist before** the relay kept timestamps: an agent
  that finished while you weren't looking. It is a **comparison, not a flag** — `status === "done" &&
  last_active_at > last_seen_at` — so opening the pane clears it with no bookkeeping on either side.
- **The dot is the bucket's colour, not the status's.** `done` means two different things depending
  on whether you have looked at it, and only the bucket knows which. Orange for `ready` sits where it
  belongs on red → orange → green → grey and leaves blue meaning *selection*, which is all it means
  anywhere else on this page.
- **Only `Recent` folds, and only `Recent` inverts.** Collapsing an alert defeats the alert, and an
  attention section is ordered by urgency, which does not invert. The three above it have **no
  controls at all**, and that absence is what marks the fourth as the one you may put away. Both
  preferences persist, because a phone reopens this page constantly.
- **Sorting:** the attention sections by `last_active_at` desc, `Recent` by `last_seen_at` desc.
- **The no-timestamp path is free.** Every comparator returns 0, `Array.prototype.sort` is stable, so
  each section keeps the order the relay already sent and `Ready` is simply empty. No feature
  detection, no branch — which is what keeps an older relay, and `demo-worker`, working untouched.

**Picking a space groups its panes by tab** (`groupPanesByTab`) — agents *and* bare shells, in
herdr's own order inside each tab, because that is the one view where "what is in this tab" is the
question being asked and a tab is a thing with a shape. It goes through `panesInSpace` for that
order (see the session view below); merging the relay's two arrays here, which is what it did, put
every agent ahead of every terminal, so a terminal split *between* two agents was drawn last.
**Empty tabs render**
(`(empty tab)`): a freshly created tab holds a shell the relay may not have listed yet, and hiding
the tab would leave nowhere to go and start an agent in it. A pane whose tab `tab list` has not
caught up with — the poll race right after a create — lands in a trailing `…` group rather than
vanishing.

**The Spaces and Tabs strips keep their sideways scroll across a snapshot.** They live inside
`#agents`, which `render()` rewrites with `innerHTML` on every `agents` message — every two seconds
— and that throws away the very elements holding the offset, so a space you had scrolled the strip
to reach walked back off the screen under your thumb. `render()` now saves and restores it around
the write (`chipStripScroll` / `restoreChipStripScroll`), keyed by **what the strip is**
(`data-strip="spaces"|"tabs"`) rather than by its position in the list: the herd draws one strip and
the space view draws two, and picking a space is exactly the moment you have just scrolled the
Spaces row. The value is assigned rather than clamped by hand, so a strip that lost chips since the
last snapshot lands at its own new end. It is the same bug the sibling row has, and
`tests/test_web_spaces.py`'s `WebStripScrollTests` measures all three rows together, with the guard
that stops the fix from switching the sibling row's anchor off altogether beside them.

**What a row is called is two questions, so two functions and an explicit scope** (`paneParts` /
`paneTitleInTab`), not one function guessing from whatever heading happens to be above it:

- In the **herd** the title carries the two things that *locate* a piece of work, the space and the
  tab, **as separate spans rather than a joined string**. At 390px tail-truncating the join eats the
  tab name and leaves every row reading `herdr-remote-dev · d…`, where the characters that survive
  are the ones every row in that space shares. Separate spans let the **project** give up width
  first and the tab — the only discriminator — survive; `test_the_project_gives_up_width_before_the_tab_does`
  measures it rather than reading the CSS.
- The project is the **space's label**, never `p.project`: the relay sets that to `basename(cwd)`,
  which is a per-pane fact and the very thing `informativeCwd` decides whether to show on line two.
- In a **space's own view** both are already established by the heading, so repeating them says
  nothing — and worse, two panes in one tab would become indistinguishable. There the pane's own name
  leads and the cwd sits beneath.
- **`paneName` is `label || pane_id`, and never `project`.** On the real host every card in
  `tmp-workspace` was called `tmp-workspace`, and so was the heading above them. The id is the only
  field that always separates two siblings, and the same string feeds `data-agent-name` (the rename
  prefill) and the row's `aria-label`, so what a pane is *called* cannot drift from what it announces.
- **`informativeCwd`** drops the cwd when its basename equals the space label — a space is almost
  always named after its directory, so that line spent itself repeating line one. What is left when
  everything drops out is the **pane id**: measured here, three agents share one tab of one space
  whose directory *is* the space's name, so their label, tab and cwd are all empty or identical and
  all three rows read `tuyaos-ai-qemu` with an empty second line.
- **`meaningfulTabLabel`** drops a *positional* tab label when the space has only one tab — herdr
  labels an unlabelled tab `"1"`, and `billing · 1` reads as a bug rather than a name. With two or
  more the number stays: weak, but the only thing telling two panes in one project apart.
- Relatedly, **a tab has been renamed when its label is not a bare integer** — *not* when it differs
  from `number`. Live on this host, `wT:t4` has label `"2"` and number `4`, because herdr's label is
  the tab's **position** in its space while the number is a separate counter; comparing the two
  called that a rename and rendered a heading reading `2` beside one reading `Tab 1`.

**The session view says the two levels below the space in ONE list** (`renderSiblings` →
`renderSibStrip` / `sibGroup`, where Collie's `TabStrip` + `PaneStrip` used to be ported side by
side): every pane in this space, in herdr's order, grouped by tab, each group led by its tab's name
and separated from the next by a 1px rule — read left to right the way a multiplexer's own status
line is. The space above them is the level left to the herd list, which is a tap on Back. The row is
DOM nodes, rebuilt from every `agents` snapshot — so a terminal appearing beside the open pane shows
up without a reopen. The **names are absent in a single-tab space** (6 of the 10 agent panes measured
sit in one, and each would have paid a chip to be told the name of the only tab there is), and the
**row itself goes, border and all, when the space holds one pane** — there is nothing to switch to,
and it would cost the output 33px to say so. A tab whose panes this client cannot name draws no
group, which is also what keeps the row honest with `HERDR_SHELL_PANES` off, where it becomes the
tabs that hold agents.

**Every pane in the space is one tap, and no chip is rationed.** What this replaced went through
three shapes. Two rows of 33px, and 4 of the 10 agent panes measured paid for both — for a row that
is at most three chips beside a row that is at most five. Then one row of **two scrollers**, which
bought back 33px (3.9% of a 390×844 screen) and turned it into a **width fight**: 2 tabs measure
135px of a 370px row and 3 measure 253px, **68%**, against a pane chip that can be 178px on its own
(a 32vw activity title plus the dot, the id tag and the padding), so a multi-tab space had to ration
the tabs to 45% and still starved the level the reader came for. One list ends the fight — the widest
chip can have the whole row — and it also ends the *two taps* it took to reach a pane in another tab
(a tab chip, then whatever pane `tabLandingPane` decided to land you on). The tab's name is still
tappable, and still lands on the neediest pane in it, because "take me to what needs me over there"
is a different question from "take me to that pane".

**The tab's name is `position: sticky` inside its own group, which is what makes one scroller safe.**
In a shared scroller the pane chips come *after* the tab chips, so the scroll that reveals the open
pane drove the tab level off the left edge: measured at 390px, opening a pane of `ble-dev` in a 3-tab
space left `ble-verify` standing alone there — a *sibling* tab dressed as the breadcrumb of the one
you were in — and in a 2-tab space it left the tail of a pane id and no tab level at all. A name that
sticks **inside its own group** cannot be the wrong one: a sticky element is held by its containing
block, so each name is pinned only while its own panes are on screen and the next group pushes it out
rather than stacking on top of it. Swept across the whole scroll range in a browser, the name at the
left edge always belongs to the group that owns that edge. Two consequences are load-bearing:
- **The pinned name is painted over the chips scrolling beneath it,** so it needs an opaque
  background — and the rule has to be `.term-sib.sib-tab`, because `.term-sib`'s own
  `background: none` matches at the same specificity and comes later in the file. A see-through
  pinned chip renders as two names on top of each other. `aria-current`'s fill still wins over both.
- **The reveal must not place the open pane under it.** `scrollSibsToOpenPane` therefore measures
  from the **group** box, which never sticks (a stuck name's rect is where it is *painted*, not where
  it sits in the row), and either fits the name in the flow beside the pane — the common case, since
  a name leads its own tab's panes rather than following every tab in the space — or places the pane
  to the right of the name's own width. Measured over all 42 panes on this host: the open pane is
  fully on screen and its own tab's name is visible in every one.

**Colour is deliberately not the channel for "which tab".** The dot is the triage bucket (red →
orange → green → grey) and a filled chip is the selection, everywhere on this page; a third scale
keyed to the tab would collide with both. The name says it, the 1px rule says where a group ends, and
the squarer corner says a name is not a pane chip — none of which costs a hue or a written heading (a
`Tabs` / `Panes` heading measured 40px of the row, more than 4 of the 5 rows that scrolled on the
real host overflowed by).

**A sideways scroll is the reader's, and it survives a rebuild they did not ask for.** The row is
rebuilt from every `agents` snapshot, and a rebuild costs it its position: `replaceChildren` empties
it, its `scrollWidth` collapses, and the browser answers by clamping `scrollLeft` to 0. The
auto-scroll then pulled the open chip back into view from there, so a reader scrolling to read the
name of a pane at the far end was snapped back to their own pane **every two seconds**.
`renderSiblings` therefore carries the offset across the rebuild, and the auto-scroll is
**anchored**: `sibsAnchoredTo` fires it once per pane, dropped only where "new" is meant —
`openTerminal`'s real-switch branch (which covers entering a session from the list, where
`activePane` is null) and the row disappearing. A **re-entry clears nothing**, so the `blocked` event
that re-enters `openTerminal` for the pane already in front of you cannot drag the row back at the
one moment you are most likely to be reading it. The reveal looks for **`[data-sib-id]`**, not for a
bare `aria-current`: the tab's name carries that attribute too and is pinned to the left edge, so
matching it would report every row as already placed. The placing call runs **after** the view is
displayed, because `renderSiblings` fires while the view is still `display:none`, where every rect is
zero, no chip can be found to be off screen and `scrollLeft` cannot be written either — which is why
a row with no box is left unanchored rather than counted as placed. The row sits in **normal flow**
under the header, which is why the absolutely-positioned history panel covers it exactly as it
already covered the output and `positionHistoryPanel` is untouched. The search bar is in that same
flow *after* it, so opening search pushes the output down rather than hiding it.

**The row is in herdr's own order, which is `order` and nothing else** (`paneOrder` →
`panesInSpace`, which `groupPanesByTab` goes through too, so the space view and the session row
cannot disagree about two panes of one tab). The relay passes through each pane's index in
`pane list`, which is the order the panes are laid out on the operator's screen — see the protocol
section for why neither array position nor the pane id can stand in for it. Sorting the ids, which
is what this did, put the live `w6:t1` in exactly the wrong order: pH, p15, p12 at the desk against
p12, p15, pH on the phone. A missing `order` sorts **last, stably**, so an older relay and
`demo-worker` keep the arrays as they arrived and the one pane that can lack it on a current relay —
an agent a `blocked` push appended — is back in place on the next snapshot. `tabLandingPane` breaks
its ties the same way, so a tap on a tab's name lands on the chip a reader would have picked.

**The chrome above the output is measured, and it was 20% of the phone.** 69px of app header, of
which the session view covered the bottom 20 — its `top` was a hardcoded `49px` against a header
sized by a 44px button, so both header buttons were clipped through the whole of a session — then
55px of session header, itself 55 because `back` carried a `1.4rem` font-size around a 20px icon,
text metrics for a button with no text in it. Then the two sibling rows. **170px, 20.1%, before a
single line of a pane had rendered.** It is **116px, 13.7%** now: the app header is a *height*
(`--header-h`, the one place the number is written, and what `.terminal-view`'s `top` is computed
from, so the two cannot drift again), the session header pins every child to 28px the way the
history bar does, and the two levels share one row and one list. `tests/test_web_spaces.py` measures
all three against the screen rather than reading the CSS.

The row this all grew out of was also flat, and that is the shape it has come back to -- but it
listed the *other* panes in the workspace tagged `Tab` and `Space`, each chip named
`label || pane_id`. Three things were wrong with it, and each is now a rule:

- **A pane is named by what it can still say for itself** (`paneChipName`, Collie's
  `paneDisplayName`): the operator's label, then — for an agent — the activity `title` it is
  reporting, then the harness name; for a terminal, its cwd basename, then `shell`. **28 of the 30
  panes on the measured host carry no label at all**, so `label || pane_id` *was* the pane id: the
  row read `w6:pH  w6:pQ  w6:pR`, three chips whose names differ by one character. `project` is
  never in it — the relay sets that to `basename(cwd)`, which by construction every pane in one
  worktree shares. The pane id's **suffix** rides along as a muted tag, because a name is routinely
  shared by every chip in the row (three shells in `herdr`, three claudes in one tab) and the suffix
  is the only part that separates them.
- **The pane you are standing in is in the row, filled.** The old row listed the others, so a row of
  chips had no *you are here* in it. Blue is selection everywhere else on this page, and this is a
  selection; the open pane and its tab carry it, and `aria-current` is what the CSS fills off.
- **A tab is reachable by name.** Tapping one lands on `tabLandingPane` — the neediest agent in it
  by `bucketOf`, and failing that its first terminal — so the tap goes to whatever would have been
  highest in the herd list. A tab with no pane the client can name is **not a chip**: there is no CLI
  for pointing at an empty tab, and that is also what keeps the row honest with `HERDR_SHELL_PANES`
  off, where it becomes the tabs holding agents. The tab you are in is inert rather than
  re-entering `openTerminal` and closing the panel you have open.

A chip carries the same dot its card does, from the same `bucketOf`/`worstTriage` the herd uses, so
the row needs no legend and the two places cannot disagree about a pane. **Neither group is labelled
in words**: measured at 390×844, a `Tabs` / `Panes` label cost 40px of the row including its gap,
and 4 of the 5 rows that scrolled on the real host overflowed by *less* than that (3px, 13px, 17px,
41px) — so the label is on each group's `aria-label`, where it is still announced and costs nothing,
and the distinction is carried by shape (a tab chip is squarer), by the id tag a tab has not got,
and — now that they share a row — by the 1px rule between them, which is drawn only when there is
something on both sides of it.
A chip's own height is **fixed at 22px rather than padded**, because a name taken from an activity
title carries CJK, whose glyphs are taller than latin at the same size, and the row grew 2px whenever
one appeared; the name is capped at `min(32vw, 260px)` with the whole of it in the tooltip, since one
205px title chip would otherwise be most of a 390px row.

Every toggle in the session view says whether its panel is open through **`aria-pressed`**, and the
CSS fills the chip off that attribute alone — `setPressed` is the only writer, so the pixels and the
screen reader cannot drift apart. Search, History and the two dock buttons used to render pixel
identical either way; they now fill with the *inverted neutral* (`--text` on `--bg`), which is
unmistakably lit, carries no semantics of its own to clash with the orange/red keys, and is the one
pair of colours guaranteed to contrast in all eleven themes — a blue fill sat next to the blue Send
button and read as a second Send. Blue fill is reserved for *selection* (the Keys/123 switch, the
`Tools` filter), and `refresh` deliberately has **no** pressed state at all: it fires and returns,
and the contrast with the two chips beside it is what marks those two as toggles. Mutually
exclusive pairs are restated together rather than one at a time (`showDock`, and `toggleSearch`
routing history's close through `navClose` instead of hiding the element and orphaning its history
entry). Settings and Timeline are siblings of the session view, which is
`position: fixed; z-index: 50` over an opaque background below the 768px breakpoint: a panel
opened from inside a session used to render in normal flow *underneath* it, present and
unreachable. `openPanel` records what the panel covered in `panelReturn` and deactivates the
session view; `hidePanel` restores exactly that, which is also why closing a panel no longer
reveals the agent list under a live session. The test proves it with `elementFromPoint` at the
panel's own centre, at both phone and desktop widths.

**There is one socket, and a handler belongs to the socket that installed it.** `close()` is
asynchronous, so the socket being replaced fires its `onclose` *after* the replacement is already
live. While the departing socket kept its handlers, that late close reported offline and scheduled
a reconnect, which then closed the **healthy** socket, whose own close scheduled the next one:
switching relays started a permanent offline -> connecting -> live cycle every 3s that a reload
cleared and the next switch started again. So `connect()` detaches the old socket's four handlers
before closing it, and each new socket's handlers begin `if (ws !== sock) return` — which also
keeps a snapshot still in flight from the relay you are leaving out of `handleMessage`, where it
would merge that relay's panes into the next one's state. There is likewise **one pending
reconnect** (`reconnectTimer`, cleared at the top of `connect()`): two chains each closing the
other's socket is the same loop by another route. `tests/test_web_connection.py` measures the
ordering in a browser against a stand-in socket; four of its five tests fail on the code this
replaced, and the fifth — a dropped socket still reconnecting — is what stops the guard from
becoming a way of switching reconnection off.

## WebSocket Protocol

Messages are JSON with a `type` field:

**Server → Client:** `agents` (complete state snapshot, plus the `spaces` hierarchy), `agent_update` (single-pane state merge), `blocked` (approval prompt), `pane_content` (terminal read), `sessions` (per-source herdr session lists and the active selection), `history` (transcript turns, or `unavailable` with a reason), `command_result` / `tab_created` (did the mutation land), `error`
**Server → Client:** `agents` (complete state snapshot, plus the `spaces` hierarchy and the `panes` list), `agent_update` (single-pane state merge), `blocked` (approval prompt), `pane_content` (terminal read), `history` (transcript turns, or `unavailable` with a reason), `command_result` / `tab_created` (did the mutation land), `error`

**Client → Server:** `respond` (send text to agent), `read_pane` (request terminal content), `send_keys` (send key sequences), `send_text` (raw text without newline), `agent_prompt` (submit free-form text via `herdr agent prompt`), `session_switch` (point one source at a herdr session; `session: null` follows herdr's default), `get_history`, `focus`, `create_tab`, `rename_tab`, `close_tab`, `rename_agent`, `push_subscribe`/`push_unsubscribe`

An `agents` entry carries, per pane: `pane_id`, `agent`, `label`, `workspace_label`, `title` (herdr's terminal title —
live activity, not a session name: a working claude sets it to what it is doing, and
`activity_title` drops the harness's own banner, which is all an idle or done pane leaves there.
The durable title comes back from `get_history`), `status`, `cwd`, `project`, `host`, `remote`,
`workspace_id`, `tab_id`, `focused` (the one pane per host herdr itself has in front),
`scrollback` + `viewport_rows` (from herdr's `scroll`), `has_session` (this pane names an agent
transcript), `order` (see below), and `last_active_at` / `last_seen_at` (epoch **milliseconds**,
because every client that will compare them is JavaScript). The session ref itself stays
server-side in `pane_session_map`.

**`order` is the pane's index in its host's own `pane list`, and it is the only thing that says
where a pane sits on the operator's screen.** herdr answers `pane list` in the order the panes are
laid out — verified against `pane layout` on every tab of this host, the same split-tree walk, top
left first — and nothing else in the record implies it. The ids will not do: `pane swap` and
`pane move` rearrange panes, and the suffix is a creation counter in a base wider than ten, so
`w6:t1` reads pH, p15, p12 at the desk and sorts to p12, p15, pH. Splitting one `pane list` into
`agents` + `panes` also throws away the interleaving, which is how a terminal split between two
agents ends up drawn last. Per host, since a client only ever orders panes within one tab of one
machine. A relay older than this sends none, and a client must then keep the arrays as they
arrived rather than invent an order — the web app sorts missing last, stably, which is the same
thing. An `agent_update` carries no `order` (it is a merge, and the snapshot beside it has one),
and an agent a `blocked` push appends before its first snapshot has none for one poll.

The same `agents` message carries `spaces` — `{workspaces: [...], tabs: [...]}` from
`herdr workspace list` and `herdr tab list`. `pane list` gives every pane a `workspace_id` and a
`tab_id`, but only the ids; the operator's label, their numbering, which one is focused, and the
**total** pane count live in those two lists alone. Each entry is tagged with `host`/`remote`.
Refreshed every `SPACES_POLL_INTERVAL` pane polls, immediately on connect, and immediately after
any message that moves the hierarchy — two extra CLI calls per host (4ms each locally, one SSH
round trip each remotely) against something that only changes when someone creates, closes,
renames or focuses. A failed read keeps the last good hierarchy rather than blanking clients.

The same message carries **`panes`** — the panes with no agent in them, which is most of them:
30 panes on this host, 10 of which hold an agent. They are a separate array rather than `agents`
entries because six clients render that array and every one of them assumes its entries are
agents; a shell pane would show up in all of them as a card with an empty harness name. Each entry
is `pane_id`, `label`, `cwd`, `project`, `host`, `remote`, `workspace_id`, `tab_id`, `focused`,
`scrollback`, `viewport_rows`, `order` — no `status` (herdr reports `agent_status: "unknown"` for
all of them), no `title` (there is no such field on a non-agent pane) and no `has_session`. They
come out of the **same `pane list`** the poll already runs, so listing them costs nothing — which
is also why their `order` is comparable with an agent entry's: the two arrays are one list, indexed
before it was split.

Two things are true of a shell pane and not of an agent pane:

- **It has a real scrollback ring, and reading it is cheap.** Shell panes here report 0 to 9258.
  A 400-line `recent` read on one measured **10ms end to end through the relay**, against the
  multi-second harvest the same request triggers on an idle agent pane. Scrollback is worth
  offering here. It is no longer what *distinguishes* the two kinds — on herdr 0.8.2 most agent
  panes report a ring too (see the read-semantics section) — but the cost of reading it still is.
- **Writing to it is a command.** `respond` on a shell pane skips the question detector entirely —
  there is nothing to detect — and sends `pane send-text` followed by `Enter`. No harness stands
  between the text and the shell. That is why the whole feature is behind `HERDR_SHELL_PANES` and
  why the audit line is `respond_shell` rather than `respond`.

**`focus` on a shell pane is a walk, not a command.** `agent focus` climbs to the tab and
workspace holding an agent pane; there is no equivalent for a pane without one, and `pane focus`
only steps to a *neighbour* by `--direction`. So `focus_shell_pane` focuses the tab, then reads
`pane layout` (every pane's rect plus `focused_pane_id`) and steps one neighbour at a time,
re-reading after each step — herdr's notion of "the pane to the right" is its own, and a route
plotted from the first layout would land elsewhere and report success. A step that changes nothing
stops the walk instead of looping, and `PANE_WALK_LIMIT` (6) bounds it either way. Measured on a
throwaway two-pane tab: **32ms** end to end through the relay, about six CLI calls.

`walk_direction` picks its axis by **row overlap, not by comparing dx to dy**: rects are in
terminal cells, a cell is about twice as tall as it is wide, and the raw comparison calls a
side-by-side pair a vertical move on splits that look square on screen.

**`pane process-info` is fetched on request, never on a timer.** 20 shell panes here share only 12
distinct `cwd` basenames, so eight of them are indistinguishable from a sibling by directory alone
— `process-info` separates zsh from vim from the build that has been running an hour. It costs
2.5ms locally but it is **one call per pane**, which is one SSH round trip per pane, so it never
enters the poll. `read_pane` takes an optional `process: true` and answers with
`process: {name, cmdline}`; a client asks when it *opens* a pane, not on every mirror refresh.

### The relay is single-threaded; every herdr call is not

Nothing that blocks may be awaited inline. A herdr call is a subprocess — a few ms locally, but a
read past the viewport runs to seconds and an SSH call to the 15s timeout — and for its whole
duration an inline caller serves no other client, runs no poll tick and sends no broadcast. The
same applies to `send_web_push`, whose `pywebpush` POSTs are `requests` under the hood against
endpoints the relay does not control.

So the boundary is explicit at each call site: `await asyncio.to_thread(read_pane, …)`, not
`read_pane(…)`. Measured with eight clients each reading a different pane at the same instant,
against a herdr stand-in whose every read costs 0.5s: **4050ms of wall clock in a clean 506ms
staircase before, 513ms after** — the eighth client used to wait four seconds for a half-second
call. On real local reads (3ms each) the staircase is still exactly there, just cheap; it is the
SSH and scrollback-harvest paths that make it hurt.

Two tests hold the boundary, because the failure is silent — everything still works, the relay
just stops answering anyone else while it runs:

- `test_a_slow_herdr_call_does_not_stall_the_event_loop` puts a 0.3s subprocess under `_poll_once`
  and counts how many times a 5ms ticker got scheduled. Inline it is exactly 0.
- `test_no_blocking_call_is_awaited_inline_from_async_code` builds the call graph over the relay's
  own sync functions, seeds it with `subprocess.run` / `transcript.history` / `transcript_ssh`, and
  fails on any of them called straight from an `async def` — naming file, line and holder. It
  unwraps `asyncio.to_thread(fn, …)` for `fn` only, so `to_thread(f, read_pane(x))` is still caught.

`_invoke_herdr`'s SSH branch is the only shared state involved (`_remote_locks`, behind
`_remote_locks_guard`), so the worker threads need no further synchronising. `_deliver_push` works
off a snapshot of `push_subscriptions` and drops dead ones **by value**, since a `push_subscribe`
arriving mid-flight would invalidate an index computed before it.

### Pane activity: what moved, and what you have looked at

herdr's pane records carry **no timestamps at all**, so the relay derives and owns two per pane and
ships them on every `agents` entry *and* every `panes` entry:

| field | meaning |
|-------|---------|
| `last_active_at` | the last agent status transition this relay observed |
| `last_seen_at` | the last time a client opened or drove the pane through this relay |

They exist so a client can answer the one question a status alone cannot: **did this finish while I
wasn't looking?** That is a *comparison*, not a stored flag — `status == "done" && last_active_at >
last_seen_at` — which is why opening the pane clears it with no bookkeeping on either side: the read
bumps `last_seen_at` and the row leaves that section on the next snapshot.

The rules are all rules about not lying to the operator:

- **A first sighting seeds `active_at == seen_at`.** Only transitions observed *after* the relay
  first saw a pane may mark it unread, so a fresh client never opens on a wall of alerts for work
  already dealt with at the desk — the same rule the blocked-push path already follows by never
  firing on a first sighting.
- **Only a status change bumps `active_at`,** tracked against the ledger's *own* status memory rather
  than `last_statuses` (which the blocked-push logic owns and updates on its own schedule; two
  features reading one dict would be coupled by call order).
- **`SEEN_ON` is the single chokepoint** for `seen_at`, applied ahead of every handler so a new one
  cannot forget: `read_pane`, `get_history`, `respond`, `send_keys`, `send_text`, `agent_prompt`,
  `question_toggle`, `question_submit`. **`focus` is deliberately absent** — it moves herdr's own
  cursor at the desk without the client reading anything, and `seen` is about what *you* looked at
  through the relay. An unknown pane id is ignored rather than seeded, so bogus ids cannot grow the
  file.
- **Keyed by `(host, pane_id)`,** unlike the other pane maps: every herdr numbers its own panes, and
  this is the one such map written to disk, where a collision would stick.
- **Forgetting rides the existing stale sweep** in `update_pane_maps` rather than a second reconcile
  policy beside it — that sweep already decides when a caller's picture is complete enough to drop
  anything, and it covers **shell panes**, which a removal event derived from an agent status map
  never would.
- **`activity.json` in `LOG_DIR`**, written temp-file-plus-rename (a half file would parse as nothing
  and silently cost everyone's unread column), pruned at 30 days on load, and **debounced 10s**: an
  open pane's 3s mirror tick marks it seen every tick, which is free in memory and one write per tick
  forever on disk. Every field is re-validated on load, including that `True` is not a timestamp —
  it is an `int` in python and would sort a pane unread for good.
- **Both fields absent reads as "nothing known".** A relay older than this ships neither and a client
  must treat that as "no unseen section", not as "everything is unread".

### Static assets: the relay serves `web/` as a directory

`web_asset()` resolves a request path to a file under `web/` by **extension**, replacing a
hand-maintained `path -> (filename, mime)` table. The table was a standing bug rather than a list:
a file committed to `web/` is public on Cloudflare Pages immediately, but over the relay it 404s
until someone remembers two more lines in two different places — so a missing asset only ever
appeared for the people on a tunnel.

- **Cache-Control is split by extension, deliberately.** `.woff2/.png/.svg/.txt` get a year
  immutable; `.css/.js` get `no-cache`, because they change under a fixed name on every deploy and
  a year of immutable would pin returning browsers to whatever JavaScript they saw first.
- **`.html` is absent from the table on purpose.** `index.html` is served further up, behind
  `HERDR_RELAY_TOKEN` when one is set. Static assets are exempt from that token — a browser fetches
  the stylesheet and the scripts before it can authenticate — so putting `.html` here would turn
  that exemption into a way past the token.
- **It cannot be talked out of `web/`.** Every path segment must be a plain name (rejecting `""`,
  `.`, `..` and anything holding a separator — the server has already percent-decoded, so `%2e%2e`
  is covered), and the resolved path is re-checked to be inside `web/`, which is what stops a
  symlink pointing out of the tree. `tests/test_web_assets.py` covers both halves.

### Relay-side constraints clients must respect

- **`respond` is allowlisted.** Only the 12 values in `SAFE_RESPONSES` (`herdr_relay.py:90`) are accepted; anything else returns `response not in allowlist`. Free-form replies must use `agent_prompt` (≤10000 chars) or `send_text` (≤1000). The mac/iOS approval cards send custom text as `respond`, so their custom-reply box does not work against the relay.
- **Keys use herdr's `+` grammar, validated by `key_is_allowed`, and `keys` must be a non-empty array.** Bare specials (`Enter` `Escape` `Tab` `Space` `Backspace` `Up`…`F12`), single characters, and `ctrl+`/`shift+`/`alt+` chords all pass — special names case-insensitively, so `esc` and `shift+tab` are fine. `C-c` also passes: live-verified as the one tmux-style spelling herdr 0.8.0 still aliases to interrupt (`C-u`, `M-x`, `BTab` do not). `BSpace`, `Insert` and `Delete` are rejected by herdr in any spelling.
- **`PageUp`, `PageDown`, `Home` and `End` are sent as bytes, not as keys.** herdr's own validator refuses every spelling of them (re-probed on 0.8.2: `PgUp`, `pageup`, `Page_Up` and `ctrl+Home` all answer `unsupported key`), so `key_escape_sequence` turns them into the CSI bytes a terminal emits and the relay ships that through `pane send-text` instead — `pane send-text` is a byte channel and passes ESC verbatim. Modified forms are computed, not enumerated: xterm's `1 + shift(1) + alt(2) + ctrl(4)`, so `ctrl+Home` is `ESC[1;5H` and `shift+PageUp` is `ESC[5;2~`. A mixed `keys` array keeps its order — consecutive keys of one kind travel in one CLI call, so `[Escape, PageUp, PageDown, Enter]` becomes send-keys / send-text / send-keys, in that order. Clients still just send the key name.
- **`question_toggle`/`question_submit` have no relay handler.** The web app, TUI, mac and iOS clients all send them; the relay ignores both, so multi-select questions cannot be answered from any client until it grows support.
- **Workspace and tab ids are only unique within one host.** Every herdr numbers its own spaces
  w1, w2, … so a client that watches more than one host must send `host` alongside
  `workspace_id`/`tab_id`. `resolve_space` serves an id with no host while it is unambiguous and
  refuses it when two hosts share it, rather than mutating a tab on the wrong machine. The relay
  also refuses ids it has never listed — `create_tab` used to hand whatever the client sent
  straight to the CLI, and always to the local host.
- **`focus` says what to focus by which id it carries.** `{pane_id}` → `herdr agent focus`, which
  walks up to the tab and workspace holding it; `{tab_id}` → `tab focus`; `{workspace_id}` →
  `workspace focus`. There is no CLI for focusing an arbitrary *non-agent* pane — `pane focus`
  only steps to a neighbour by `--direction` — so a shell pane will need `tab focus` plus a walk.
- **Labels a client writes into herdr's UI are cleaned, not trusted.** `rename_tab` and
  `rename_agent` (and `create_tab`'s optional `label`) go through `clean_label`: control
  characters collapse to spaces, and an empty, over-64-char, or leading-dash label is refused
  rather than handed to a CLI that would read it as a flag. `rename_agent` calls
  `herdr agent rename`; typing `/rename x` at the pane instead just lands literal text in the
  agent's composer.
- **`read_pane` picks its own source.** `source` ∈ `visible | recent | recent-unwrapped | detection`
  (default `recent`), `lines` is clamped to 1000, `format` ∈ `text | ansi`. Optional
  `process: true` adds `process: {name, cmdline}` to the reply at the cost of one extra CLI call
  — ask on open, not on every refresh.
- **A shell pane is addressable only when `HERDR_SHELL_PANES` is on.** With it off they are not in
  `known_panes`, so every message naming one is refused as an unknown pane — that is the whole
  gate, there is no second check per message. With it on, `respond` takes free text there (it
  becomes a command), and `focus` walks instead of calling `agent focus`.
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

### herdr read semantics the relay is built on (live-probed on herdr 0.8.0 / protocol 19; re-probed on 0.8.2 where a bullet says so)

- **`pane.read` is clamped at ~1000 lines, silently.** 1000, 1500 and 5000 all return the same 1000
  rows with `truncated` unchanged. There is no offset/paging parameter, so 1000 lines back from the
  bottom is the deepest any single read can reach.
- **An agent pane may or may not have scrollback, and no client may assume either way.** This
  said the opposite — every agent pane reports 0, because its TUI runs on the alternate screen —
  and it was true of herdr 0.8.0. Re-probed on **0.8.2: 9 of the 10 agent panes on this host
  report a ring** (151, 434, 594, 938, 1262, 1267, 1400, 1662, 8439) and exactly one reports 0.
  The field is still the right thing to read instead of probing; what it answers is "could a
  scrollback read return anything here", which is all `canLoadMore` wants. It is **not** a way to
  tell an agent pane from a terminal, and the web mirror's read source used to be picked off it
  believing otherwise — see the follow/held rule in the Web App section.
- **`recent` + `format: text` on an idle agent pane HARVESTS**: herdr walks the agent's own
  mouse-scroll interface, which measured 6.2s for 200 lines and 12.7s for 400 (~31ms/line), only
  works while the agent is idle, isn't deterministic, and visibly scrolls the operator's terminal up
  and back. `format: ansi` and `source: visible|detection` never harvest and return instantly.
  **Anything on a timer must therefore read `visible`** (`PROMPT_READ_SOURCE`); a scrollback read has to be user-initiated.
- **The hierarchy commands exist and are separate from `pane list`** (checked on herdr 0.8.2):
  `workspace list|create|get|focus|rename|close`, `tab list|create|get|focus|rename|close`, and
  `agent … focus|rename`. `workspace list` reports `label`, `number`, `focused`, `tab_count`,
  `pane_count`, `active_tab_id` and, for a git workspace, a `worktree` block; `tab list` reports
  `label` (the tab's number as a string until someone renames it), `number`, `focused` and
  `pane_count`. Measured on this host: 10 workspaces and 26 panes, of which 9 are agent panes —
  so `pane list` alone hides two thirds of the panes and three workspaces entirely.
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
