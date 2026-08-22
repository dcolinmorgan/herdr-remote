#!/usr/bin/env python3
"""Reader for an agent's own conversation transcript.

Why files and not the terminal: an agent TUI runs on the alternate screen, so herdr retains no
scrollback for it (`scroll.max_offset_from_bottom` is 0 on every agent pane), and the one path that
does reach older rows -- a `recent` + text read, which walks the agent's own mouse-scroll interface
-- costs ~31ms per line, only works while the agent is idle, and visibly scrolls the operator's
terminal. The agent writes its own transcript anyway, with real message boundaries and timestamps,
so that is what we read.

Currently only Claude's JSONL is understood. Adding a harness means adding a locate+parse pair and
one line in HARNESSES -- nothing else in here or in the relay is claude-specific.
"""
import glob
import json
import os
import re
import shlex
import subprocess
import threading

# The session ref herdr reports is `{kind: "id", value: "<uuid>"}`. Everything downstream of this
# regex participates in a filesystem path or a remote shell word, so nothing that isn't a uuid is
# ever allowed past it.
UUID_RE = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")

COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)
SHELL_WRAP_RE = re.compile(
    r"</?(?:local-command-stdout|local-command-stderr|bash-input|bash-stdout|bash-stderr)>"
)

DEFAULT_LIMIT = 200
MAX_LIMIT = 2000
# Characters of turn text one page may carry, plus a rough per-turn JSON overhead.
PAGE_TEXT_BUDGET = 64 * 1024
TURN_OVERHEAD = 120
TEXT_LIMIT = 4000
TOOL_TEXT_LIMIT = 200
CACHE_SIZE = 4
REMOTE_TIMEOUT = 25


def _int_env(name, default):
    """An int from the environment, falling back rather than refusing to start the relay."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _roots_env(name, default):
    raw = os.environ.get(name, "")
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or default


ENABLED = os.environ.get("HERDR_TRANSCRIPT", "1").strip().lower() not in {"0", "false", "no", "off"}
LOCAL_ROOTS = _roots_env("HERDR_CLAUDE_ROOTS", [os.path.expanduser("~/.claude/projects")])
# Remote roots stay unexpanded: they are shell words for the remote host, whose $HOME is not ours.
REMOTE_ROOTS = _roots_env("HERDR_REMOTE_CLAUDE_ROOTS", ["$HOME/.claude/projects"])
MAX_BYTES = _int_env("HERDR_TRANSCRIPT_MAX_BYTES", 64 * 1024 * 1024)
TAIL_BYTES = _int_env("HERDR_TRANSCRIPT_TAIL_BYTES", 8 * 1024 * 1024)
# Remote is stingier than local: the bytes cross a network, and the biggest transcript on this
# machine is 33MB. A tail means remote history is recency-bounded, which the payload says out loud
# through `file_truncated` so the UI can too.
REMOTE_TAIL_BYTES = _int_env("HERDR_TRANSCRIPT_REMOTE_TAIL_BYTES", 4 * 1024 * 1024)


# ---------------------------------------------------------------------------- text helpers


def clip(text, limit):
    """Strip ANSI, normalise newlines, and cap length. Returns (text, truncated)."""
    clean = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(clean) <= limit:
        return clean, False
    return clean[:limit], True


def _first_line(text):
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


# ---------------------------------------------------------------------------- claude parser
#
# Row-type disposition, from the actual distribution in a 2747-row session on this machine
# (1036 assistant, 724 user, 190 each of mode/permission-mode/ai-title/last-prompt, 128 attachment,
# 74 file-history-*, 17 system, 8 misc):
#
#   assistant  -> text blocks become `assistant`, tool_use blocks become a one-line `tool` turn,
#                 thinking blocks are dropped. An isApiErrorMessage row is a `note`, not the agent
#                 talking.
#   user       -> see _parse_user: 683 of those 724 rows are tool_result traffic that folds into
#                 the tool turn it answers; the interesting minority is a few dozen real messages.
#   system     -> only compact_boundary and away_summary carry content a reader wants; the rest is
#                 timing metadata (turn_duration) or hook noise.
#   ai-title   -> not a turn; the last one wins as the session title.
#   everything else -> dropped. An unknown `type` is dropped rather than raised, so a format drift
#                 in claude costs a few turns instead of the whole panel.


def _turn(row, role, text, index, limit=TEXT_LIMIT):
    uuid = row.get("uuid")
    uuid = uuid if isinstance(uuid, str) else ""
    body, truncated = clip(text, limit)
    return {
        "uuid": uuid if index == 0 else f"{uuid}#{index}",
        "role": role,
        "text": body,
        "ts": row.get("timestamp") or "",
        "truncated": truncated,
    }


def _tool_summary(block):
    name = block.get("name")
    name = name if isinstance(name, str) and name else "tool"
    args = block.get("input")
    detail = ""
    if isinstance(args, dict):
        for key in ("command", "file_path", "path", "pattern", "query", "url", "description",
                    "prompt", "notebook_path", "skill"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                detail = " ".join(value.split())
                break
        else:
            try:
                detail = " ".join(json.dumps(args, ensure_ascii=False).split())
            except (TypeError, ValueError):
                detail = ""
    return f"{name}({detail})" if detail else name


def _fold_tool_result(block, tool_turns):
    """Attach a tool_result to the tool_use turn it answers instead of making it its own turn."""
    turn = tool_turns.get(block.get("tool_use_id"))
    if turn is None:
        return
    body = block.get("content")
    if isinstance(body, list):
        body = "\n".join(
            piece.get("text", "") for piece in body
            if isinstance(piece, dict) and piece.get("type") == "text"
        )
    if not isinstance(body, str):
        body = ""
    marker = "!" if block.get("is_error") else "→"
    head = _first_line(body)
    if not head and not block.get("is_error"):
        return
    turn["text"], turn["truncated"] = clip(f"{turn['text']} {marker} {head or 'error'}", TOOL_TEXT_LIMIT)


def _parse_assistant(row, turns, tool_turns):
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return
    role = "note" if row.get("isApiErrorMessage") else "assistant"
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                turns.append(_turn(row, role, text, index))
        elif kind == "tool_use":
            turn = _turn(row, "tool", _tool_summary(block), index, limit=TOOL_TEXT_LIMIT)
            turns.append(turn)
            tool_id = block.get("id")
            if isinstance(tool_id, str) and tool_id:
                tool_turns[tool_id] = turn
        # thinking / redacted_thinking: deliberately dropped -- it is not part of the conversation
        # a person is scrolling back through, and it is the bulk of the bytes.


def classify_user_text(text):
    """(role, text) for a string-content user row, or (None, "") when the row is not a turn.

    Claude wraps a lot of machinery in the user channel. Rows flagged isMeta are handled by the
    caller; these are the tagged envelopes that carry no flag.
    """
    stripped = text.strip()
    # A slash command. The tags come in either order (<command-message> can precede
    # <command-name>), so search rather than test the prefix.
    match = COMMAND_NAME_RE.search(stripped)
    if match:
        name = match.group(1).strip()
        args_match = COMMAND_ARGS_RE.search(stripped)
        args = args_match.group(1).strip() if args_match else ""
        rendered = f"{name} {args}".strip()
        return ("user", rendered) if rendered else (None, "")
    if stripped.startswith(("<local-command-stdout>", "<local-command-stderr>",
                            "<bash-stdout>", "<bash-stderr>")):
        body = SHELL_WRAP_RE.sub("", stripped).strip()
        return ("note", body) if body else (None, "")
    if stripped.startswith("<bash-input>"):
        body = SHELL_WRAP_RE.sub("", stripped).strip()
        return ("user", f"! {body}") if body else (None, "")
    if stripped.startswith("<task-notification>"):
        match = SUMMARY_RE.search(stripped)
        return "note", (match.group(1).strip() if match else "task notification")
    # Written for the model, never shown in the agent's own transcript view.
    if stripped.startswith(("<system-reminder>", "<local-command-caveat>")):
        return None, ""
    return "user", stripped


def _parse_user(row, turns, tool_turns):
    # isMeta marks an injected envelope -- a caveat block, an image placeholder, a skill body --
    # rather than something a person typed. Verified across 60 transcripts on this machine: every
    # isMeta user row was injected, and no real message carried the flag.
    if row.get("isMeta"):
        return
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if row.get("isCompactSummary"):
        if isinstance(content, str) and content.strip():
            turns.append(_turn(row, "note", content, 0))
        return
    if isinstance(content, list):
        spoken = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                _fold_tool_result(block, tool_turns)
            elif block.get("type") == "text":
                piece = block.get("text")
                if isinstance(piece, str) and piece.strip():
                    spoken.append(piece)
        if spoken:
            # "[Request interrupted by user]" and friends arrive on this shape; the flag is what
            # tells them apart from a person typing.
            role = "note" if row.get("interruptedMessageId") else "user"
            turns.append(_turn(row, role, "\n".join(spoken), 0))
        return
    if not isinstance(content, str) or not content.strip():
        return
    role, text = classify_user_text(content)
    if role:
        turns.append(_turn(row, role, text, 0))


def _parse_system(row, turns):
    if row.get("subtype") not in {"compact_boundary", "away_summary"}:
        return
    content = row.get("content")
    if isinstance(content, str) and content.strip():
        turns.append(_turn(row, "note", content, 0))


def parse_claude(lines):
    """(turns, title) from an iterable of JSONL lines. Oldest first."""
    turns = []
    tool_turns = {}
    seen_rows = set()
    title = ""
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            # A transcript being appended to has a torn last line. That is a normal state.
            continue
        if not isinstance(row, dict):
            continue
        if row.get("isSidechain"):
            continue  # subagent traffic, not this conversation
        # Real transcripts replay rows: one session here has 591 of 2602 rows written twice, same
        # uuid, same timestamp, same content (a resumed session re-appending what it loaded). The
        # uuid is the row's identity, so the second copy is a duplicate, not a second turn -- and
        # deduping here is also what keeps a turn id usable as a pagination cursor.
        row_uuid = row.get("uuid")
        if isinstance(row_uuid, str) and row_uuid:
            if row_uuid in seen_rows:
                continue
            seen_rows.add(row_uuid)
        row_type = row.get("type")
        if row_type == "assistant":
            _parse_assistant(row, turns, tool_turns)
        elif row_type == "user":
            _parse_user(row, turns, tool_turns)
        elif row_type == "system":
            _parse_system(row, turns)
        elif row_type == "ai-title":
            candidate = row.get("aiTitle")
            if isinstance(candidate, str) and candidate.strip():
                title = candidate.strip()
    for index, turn in enumerate(turns):
        if not turn["uuid"] or turn["uuid"].startswith("#"):
            turn["uuid"] = f"turn-{index}"
    return turns, title


# ---------------------------------------------------------------------------- locating


def locate_claude(session_value, roots=None):
    """The transcript path for a session uuid, or None.

    A glob on the uuid rather than deriving the project directory from the pane's cwd: the rule is
    real (every `/`, `.` and `_` becomes `-`) but the pane's cwd is the shell's, while claude's
    project directory is fixed at ITS startup cwd, and the two drift. The uuid is globally unique
    and the glob measured 0.7ms.
    """
    for root in (roots if roots is not None else LOCAL_ROOTS):
        matches = sorted(glob.glob(os.path.join(os.path.expanduser(root), "*", f"{session_value}.jsonl")))
        if matches:
            return matches[0]
    return None


# A remote root has to reach the far shell unquoted -- `$HOME/.claude/projects` is the default and
# our $HOME is not theirs -- so it is constrained instead of quoted. It comes from the relay's own
# environment, never from a client, and a root that does not fit is dropped rather than sent.
REMOTE_ROOT_RE = re.compile(r"\A[A-Za-z0-9_./~$-]+\Z")


def remote_probe_script(session_value, roots, expected_size, tail_bytes):
    """A POSIX script that answers NOFILE / CACHED / SIZE+tail in one round trip.

    Only `ls`, `wc`, `tail` and `head` -- no python on the far side. `wc -c < file` is read through
    `set --` because BSD wc pads its output with spaces and GNU wc does not.

    `expected_size` is what our cache last saw. When the file has not grown, the answer is CACHED
    and the bytes never move; when it has, we pay one transfer. Pagination is therefore cheap even
    for a remote pane, which matters because "load older" is a per-click round trip.
    """
    safe_roots = [root for root in roots if REMOTE_ROOT_RE.match(root)]
    if not safe_roots:
        raise ValueError("no usable remote transcript root")
    candidates = " ".join(f"{root}/*/{session_value}.jsonl" for root in safe_roots)
    shortcut = (
        f'[ "$s" = "{int(expected_size)}" ] && {{ echo CACHED; exit 0; }}; '
        if expected_size > 0 else ""
    )
    return (
        f'f=$(ls -1 {candidates} 2>/dev/null | head -1); '
        '[ -n "$f" ] || { echo NOFILE; exit 0; }; '
        'set -- $(wc -c < "$f"); s=$1; '
        + shortcut +
        'echo "SIZE $s"; '
        f'tail -c {int(tail_bytes)} "$f"'
    )


def _default_remote_runner(remote, script, ssh_args=()):
    cmd = ["ssh", *ssh_args, remote, "sh -c " + shlex.quote(script)]
    proc = subprocess.run(cmd, capture_output=True, timeout=REMOTE_TIMEOUT)
    return proc.returncode, proc.stdout


# ---------------------------------------------------------------------------- reading


def drop_partial_line(blob):
    """A tail starts mid-line. Drop that fragment rather than hand the parser a torn JSON row."""
    cut = blob.find(b"\n")
    return blob[cut + 1:] if cut != -1 else b""


def _decode(blob):
    return blob.decode("utf-8", "replace").splitlines()


def read_local(path):
    """(lines, truncated) for a local transcript, reading only the tail of a huge one."""
    size = os.path.getsize(path)
    if size <= MAX_BYTES:
        with open(path, "rb") as handle:
            return _decode(handle.read()), False
    with open(path, "rb") as handle:
        handle.seek(-min(TAIL_BYTES, size), os.SEEK_END)
        blob = handle.read()
    return _decode(drop_partial_line(blob)), True


# ---------------------------------------------------------------------------- cache
#
# Parsed turns, not raw bytes: the largest session on this machine is 33MB of JSONL but only 0.25MB
# of turns, and the raw text is dropped as soon as it is parsed, so peak memory is one file.
# Invalidated on size (plus mtime locally). A transcript is append-only, so a size that has not
# moved means nothing was added -- which is also what lets the remote probe answer CACHED.

_cache = {}
_cache_order = []
_cache_lock = threading.Lock()


def cache_get(key, fingerprint):
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None or entry[0] != fingerprint:
            return None
        _cache_order.remove(key)
        _cache_order.append(key)
        return entry[1]


def cache_put(key, fingerprint, value):
    with _cache_lock:
        if key in _cache:
            _cache_order.remove(key)
        _cache[key] = (fingerprint, value)
        _cache_order.append(key)
        while len(_cache_order) > CACHE_SIZE:
            _cache.pop(_cache_order.pop(0), None)


def cache_peek_size(key):
    """The size our cached parse was made from, for the remote CACHED shortcut. 0 when unknown."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return 0
        fingerprint = entry[0]
        return fingerprint[0] if isinstance(fingerprint, tuple) and fingerprint else 0


def cache_clear():
    with _cache_lock:
        _cache.clear()
        _cache_order.clear()


# ---------------------------------------------------------------------------- pagination


def paginate(turns, limit, before, include_tools):
    """(page, total, has_more) anchored on the newest turn, walking backwards from `before`.

    Two ceilings, whichever bites first: `limit` turns and PAGE_TEXT_BUDGET characters. The budget
    exists because turn counts and payload sizes are only loosely related -- 200 turns measured
    97KB of JSON on one session here and 324KB on another -- and a phone opening a panel should not
    wait on a third of a megabyte. Whatever the budget cuts is still reachable through `has_more`.
    """
    visible = turns if include_tools else [turn for turn in turns if turn["role"] != "tool"]
    total = len(visible)
    end = total
    if before:
        index = next((i for i, turn in enumerate(visible) if turn["uuid"] == before), None)
        if index is not None:
            end = index
        # Unknown cursor (file rewritten, stale client): fall back to the newest page. The user
        # asked for "older" and we owe them something, not a blank panel.
    floor = max(0, end - max(1, limit))
    start, used = end, 0
    while start > floor:
        cost = len(visible[start - 1]["text"]) + TURN_OVERHEAD
        if used + cost > PAGE_TEXT_BUDGET and start < end:
            break  # always yield at least the newest turn, however long it is
        used += cost
        start -= 1
    return visible[start:end], total, start > 0


# ---------------------------------------------------------------------------- entry point


def _unavailable(reason, agent=""):
    return {
        "messages": [], "total": 0, "has_more": False, "title": "",
        "agent": agent, "file_truncated": False, "unavailable": reason,
    }


HARNESSES = {"claude": (locate_claude, parse_claude)}


def history(session, remote=None, limit=DEFAULT_LIMIT, before=None, include_tools=False,
            agent="", ssh_args=(), remote_runner=None, log=None):
    """The history payload body for one pane's session ref.

    `session` is the raw `agent_session` record herdr reports, straight out of the relay's
    pane_session_map -- clients never see or send a session uuid, they send a pane_id.
    """
    if not ENABLED:
        return _unavailable("disabled", agent)
    if not isinstance(session, dict):
        return _unavailable("no-session", agent)
    # kind "path" (a harness that hands over an absolute file) needs a containment check against
    # the configured roots before it may be opened. Claude never uses it; wire it up with the
    # harness that does. A ref we cannot make sense of is "no session", whatever the harness.
    if session.get("kind") != "id":
        if log and session:
            log.info("transcript: session ref kind %r not supported", session.get("kind"))
        return _unavailable("no-session", agent)
    value = session.get("value")
    if not isinstance(value, str) or not UUID_RE.match(value):
        return _unavailable("no-session", agent)
    harness = session.get("agent") or agent
    entry = HARNESSES.get(harness)
    if entry is None:
        # A pane running a harness this relay cannot parse is a different sentence from a pane
        # with no session at all, and the UI should be able to say which.
        if log:
            log.info("transcript: no reader for harness %r", harness)
        return _unavailable("unsupported", harness)
    locate, parse = entry

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))
    try:
        if remote:
            parsed = _load_remote(value, parse, remote, ssh_args, remote_runner)
        else:
            parsed = _load_local(value, locate, parse)
    except Exception:
        if log:
            log.exception("transcript read failed for session %s (remote=%s)", value, remote)
        return _unavailable("error", agent)
    if parsed is None:
        return _unavailable("no-log", agent)

    turns, title, file_truncated = parsed
    page, total, has_more = paginate(turns, limit, before, include_tools)
    return {
        "messages": page,
        "total": total,
        "has_more": has_more,
        "title": title,
        "agent": harness,
        "file_truncated": file_truncated,
        "unavailable": None,
    }


def _load_local(value, locate, parse):
    path = locate(value)
    if not path:
        return None
    stat = os.stat(path)
    key = (None, path)
    fingerprint = (stat.st_size, stat.st_mtime_ns)
    cached = cache_get(key, fingerprint)
    if cached is not None:
        return cached
    lines, file_truncated = read_local(path)
    turns, title = parse(lines)
    parsed = (turns, title, file_truncated)
    cache_put(key, fingerprint, parsed)
    return parsed


def _load_remote(value, parse, remote, ssh_args, remote_runner):
    """Only the script's path shape is claude-specific; the parse comes from the harness table."""
    key = (remote, value)
    runner = remote_runner or _default_remote_runner
    script = remote_probe_script(value, REMOTE_ROOTS, cache_peek_size(key), REMOTE_TAIL_BYTES)
    returncode, blob = runner(remote, script, ssh_args)
    if returncode != 0:
        raise OSError(f"ssh transcript read failed on {remote} with exit {returncode}")
    header, _, body = blob.partition(b"\n")
    header = header.strip()
    if header == b"NOFILE":
        return None
    if header == b"CACHED":
        cached = cache_get(key, (cache_peek_size(key),))
        if cached is not None:
            return cached
        # The cache was evicted between the probe and now. Ask again without the shortcut.
        script = remote_probe_script(value, REMOTE_ROOTS, 0, REMOTE_TAIL_BYTES)
        returncode, blob = runner(remote, script, ssh_args)
        if returncode != 0:
            raise OSError(f"ssh transcript read failed on {remote} with exit {returncode}")
        header, _, body = blob.partition(b"\n")
        header = header.strip()
        if header == b"NOFILE":
            return None
    if not header.startswith(b"SIZE "):
        raise ValueError(f"unexpected transcript frame from {remote}: {header[:40]!r}")
    size = int(header.split()[1])
    file_truncated = len(body) < size
    if file_truncated:
        body = drop_partial_line(body)
    turns, title = parse(_decode(body))
    parsed = (turns, title, file_truncated)
    cache_put(key, (size,), parsed)
    return parsed
