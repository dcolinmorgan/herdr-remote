#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets>=14.0", "zeroconf>=0.80.0", "pywebpush>=2.0.0", "py-vapid>=1.9.0"]
# ///
"""herdr-remote relay — polls herdr, accepts push events (HTTP POST + WebSocket + UDP), broadcasts to clients."""
import asyncio, hashlib, json, logging, os, re, shlex, shutil, signal, socket, subprocess, threading, time

try:
    from websockets.asyncio.server import serve
except ImportError:
    from websockets.server import serve
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from logging.handlers import RotatingFileHandler
import sys

try:
    from agent_state import complete_agent_update_message
except ModuleNotFoundError:
    from importlib.util import module_from_spec, spec_from_file_location

    _agent_state_spec = spec_from_file_location(
        "herdr_remote_agent_state",
        os.path.join(os.path.dirname(__file__), "agent_state.py"),
    )
    _agent_state_module = module_from_spec(_agent_state_spec)
    _agent_state_spec.loader.exec_module(_agent_state_module)
    complete_agent_update_message = _agent_state_module.complete_agent_update_message

try:
    import transcript
except ModuleNotFoundError:
    from importlib.util import module_from_spec, spec_from_file_location

    _transcript_spec = spec_from_file_location(
        "herdr_remote_transcript",
        os.path.join(os.path.dirname(__file__), "transcript.py"),
    )
    transcript = module_from_spec(_transcript_spec)
    _transcript_spec.loader.exec_module(transcript)

def _get_log_dir():
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/herdr-remote")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local"))
        return os.path.join(base, "herdr-remote", "logs")
    if os.path.isdir("/var/log") and os.access("/var/log", os.W_OK):
        return "/var/log/herdr-remote"
    return os.path.expanduser("~/.local/state/herdr-remote/log")

LOG_DIR = os.environ.get("HERDR_LOG_DIR", _get_log_dir())
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "relay.log")
AUDIT_FILE = os.path.join(LOG_DIR, "audit.log")

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
_file_handler.setFormatter(_formatter)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

log = logging.getLogger("herdr-relay")
log.setLevel(logging.INFO)
log.addHandler(_file_handler)
log.addHandler(_console_handler)
logging.getLogger("websockets").setLevel(logging.WARNING)

HERDR = (
    os.environ.get("HERDR_BIN")
    or shutil.which("herdr")
    or ("herdr" if sys.platform == "win32" else "/opt/homebrew/bin/herdr")
)
REMOTE_HERDR = os.environ.get("HERDR_REMOTE_BIN", "herdr")
# Panes with no agent in them. herdr reports 30 panes on this host and only 10 hold an agent, so
# two thirds of the operator's terminals are invisible to every client. Listing and reading them
# costs nothing extra -- they come out of the same `pane list` the poll already runs -- but
# WRITING to one is arbitrary command execution on the host, with no agent-side approval prompt
# in the way. That is a capability the relay did not have, so it arrives behind a switch rather
# than with an upgrade. See SECURITY.md.
SHELL_PANES = os.environ.get("HERDR_SHELL_PANES", "").strip().lower() not in {
    "", "0", "false", "no", "off",
}
# How many neighbour steps focus_shell_pane will take before giving up (see there).
PANE_WALK_LIMIT = 6
WS_PORT = int(os.environ.get("HERDR_RELAY_PORT", "8375"))
RELAY_HOST = os.environ.get("HERDR_RELAY_HOST", "127.0.0.1")
POLL_INTERVAL = 2
AUTH_TOKEN = os.environ.get("HERDR_RELAY_TOKEN", "")  # Optional: shared secret for relay auth
TRUSTED_ORIGINS = [o.strip().lower() for o in os.environ.get("HERDR_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# Session selection per source. Key is None for local, else the "user@host"
# string from HERDR_REMOTES. Value is a session name, or None to follow
# herdr's own default session.
DEFAULT_LOCAL_SESSION = os.environ.get("HERDR_SESSION") or None
ACTIVE_SESSIONS = {}


def active_session_for(remote=None):
    """Session name for one source, or None for herdr's default session."""
    if remote in ACTIVE_SESSIONS:
        return ACTIVE_SESSIONS[remote]
    return DEFAULT_LOCAL_SESSION if remote is None else None


def _herdr_env(session):
    """Child environment targeting one session.

    Returning the inherited environment is wrong for the default session: the
    relay's own env pins HERDR_SESSION via config.env, and HERDR_SOCKET_PATH is
    present whenever the relay runs inside a herdr pane. Both must be removed.
    """
    env = os.environ.copy()
    if session:
        env["HERDR_SESSION"] = session
        env.pop("HERDR_SOCKET_PATH", None)
    else:
        env.pop("HERDR_SESSION", None)
        env.pop("HERDR_SOCKET_PATH", None)
    return env

# VAPID Web Push
VAPID_PUBLIC_KEY = os.environ.get("HERDR_VAPID_PUBLIC", "")
VAPID_PRIVATE_KEY = os.environ.get("HERDR_VAPID_PRIVATE", "")
VAPID_SUBJECT = os.environ.get("HERDR_VAPID_SUBJECT", "mailto:herdr@localhost")
push_subscriptions = []  # list of PushSubscription dicts
PUSH_SUBS_FILE = os.path.join(LOG_DIR, "push_subs.json")
ACTIVE_SESSIONS_FILE = os.path.join(LOG_DIR, "active_sessions.json")


ACTIVITY_FILE = os.path.join(LOG_DIR, "activity.json")
# Entries untouched for this long are dropped on load -- a backstop for panes whose removal we
# missed (an unclean shutdown). `activity_forget` is the real reaper.
ACTIVITY_PRUNE_AFTER = 30 * 24 * 60 * 60
# At most one disk write per this window. An open pane's mirror tick marks it seen every 3s; in
# memory that is free, on disk it would be a write per tick forever. Losing <=10s of "seen"
# precision to a crash is imperceptible in a feature whose finest unit is "just now".
ACTIVITY_FLUSH_DEBOUNCE = 10.0
# Messages that mean a client is looking at or driving a pane, which is what clears its unread
# state. One place, so a new handler cannot forget. `focus` is absent on purpose: it moves herdr's
# own cursor at the desk without the client reading anything, and `seen` is about what YOU looked
# at through the relay. So are the tab/workspace verbs, which name no pane.
SEEN_ON = frozenset({
    "read_pane", "get_history", "respond", "send_keys", "send_text", "agent_prompt",
    "question_toggle", "question_submit",
})

if RELAY_HOST not in {"127.0.0.1", "localhost", "::1"} and not AUTH_TOKEN:
    raise SystemExit("HERDR_RELAY_TOKEN is required when HERDR_RELAY_HOST binds beyond loopback")

# Remote hosts: comma-separated SSH targets
REMOTES = [r.strip() for r in os.environ.get("HERDR_REMOTES", "").split(",") if r.strip()]

TOOL_OPTIONS = ["yes, single permission", "trust, always allow", "no (tab to edit)"]
SUBAGENT_OPTIONS = ["approve all pending", "configure individually", "exit (cancel subagents)"]
CHROME_RE = re.compile(
    r"^[\s\u2500\u2501\u2550_\u2014\u2502|\u25d4\u25d1\u25d5\u25cf\s]+$"
    r"|Kiro\s[\u00b7\u2022]"
    r"|esc to cancel"
    r"|type to queue"
    r"|^\s*[\u25d4\u25d1\u25d5\u25cf]\s+(Shell|Bash)"
)
QUESTION_OPTION_RE = re.compile(
    r"^(?P<cursor>[\uf054>\u203a\u276f\u25b8\u2192])?\s*"
    r"(?P<marker>[\uf046\uf10c\uf192\uf096\uf14a\u25cb\u25c9\u2610\u2611]|\([ o]\)|\[[ xX]\])\s+"
    r"(?P<label>.+?)\s*$"
)
QUESTION_OTHER = "Other (type your own)"


clients = set()
last_statuses = {}

# --- Pane activity: what moved, and what you have looked at ---
#
# herdr's pane records carry no timestamps at all, so the relay derives and owns both. Two numbers
# per pane are enough for a client to triage a herd:
#   active_at -- the last agent status transition this relay observed
#   seen_at   -- the last time a client opened or drove the pane through this relay
#
# "Unseen" is then a COMPARISON, not a stored flag: an agent is newly-finished-and-unread exactly
# when `status == "done" and active_at > seen_at`. Opening the pane sets seen_at = now and the row
# leaves that section on its own -- nothing to mark read, nothing to keep in sync.
#
# Keyed by (host, pane_id), unlike the maps above: every herdr numbers its own panes, so a bare pane
# id is not unique across the hosts this relay polls, and this is the one such map written to disk,
# where a collision would stick.
pane_activity = {}
# The status this ledger last saw, kept separately from `last_statuses` above -- that one belongs to
# the blocked-push logic and is updated on its own schedule, and two features reading one dict would
# be coupled by call order.
_activity_status = {}
_activity_dirty = False
_activity_flush_task = None
last_blocked_prompts = {}
event_queue = asyncio.Queue()
pane_remote_map = {}
# pane_id -> the raw agent_session ref herdr reports (kind id|path + value). Kept server-side
# rather than broadcast: it is the transcript lookup key, and no client needs to know a session
# uuid to ask for that pane's history.
pane_session_map = {}
known_panes = set()
# pane_id -> the record broadcast for a non-agent pane. Separate from agent_cache because the
# handlers need to tell the two apart: a shell pane has no question to detect, no approval to
# match and no `agent focus` to call.
shell_pane_map = {}
agent_cache = {}
# The tab/workspace hierarchy as herdr reports it, refreshed on its own slower cadence (see
# SPACES_POLL_INTERVAL) and immediately after anything that changes it. `(host, id) -> remote`,
# because ids are only unique within one herdr: every host numbers its own workspaces w1, w2, ...
spaces_cache = {"workspaces": [], "tabs": []}
workspace_remote_map = {}
tab_remote_map = {}
_remote_locks = {}
_remote_locks_guard = threading.Lock()
_session_list_cache = {}  # source -> (monotonic_timestamp, sessions_list)


SAFE_RESPONSES = {
    "y", "n", "a", "yes", "no", "trust",
    "yes, single permission", "trust, always allow", "no (tab to edit)",
    "approve all pending", "configure individually", "exit (cancel subagents)",
}
# Keys the relay will forward, in the grammar herdr actually validates. Live-verified against
# herdr 0.8.0 (protocol 19) on a throwaway session:
#   accepted -- bare specials (Enter Escape Tab Space Backspace BS Up Down Left Right F1..F12),
#               any single character, `+`-joined chords (ctrl+c, shift+tab, alt+Up), and `C-c`,
#               which is the ONE tmux-style spelling herdr still aliases to interrupt;
#   rejected -- C-u, M-x, BTab, BSpace, PageUp, PageDown, Home, End, Insert, Delete.
# `BSpace` used to sit in this set and could never have worked: herdr answers
# `invalid_key: unsupported key BSpace`. Chords are validated by key_is_allowed(), not enumerated
# here, because the web app composes them at runtime (ctrl+/shift+ any key) -- this set is the
# bare-key half of the grammar, and it must stay a self-contained literal expression
# (tests/test_telegram.py evaluates it straight out of the AST).
SAFE_KEYS = {
    "y", "n", "a",
    "Enter", "Escape", "Tab", "Space", "Backspace", "BS",
    "Up", "Down", "Left", "Right",
    "C-c",
} | {str(number) for number in range(10)} | {f"F{index}" for index in range(1, 13)}

# Modifiers herdr accepts in a chord. `cmd`/`super` are also valid upstream but no client sends
# them, so they stay out: an allowlist should not be wider than the UI that feeds it.
SAFE_MODIFIERS = {"ctrl", "shift", "alt"}

# Special key NAMES, lowercased, because herdr matches them case-insensitively -- `shift+tab` and
# `esc` both ack, so a client spelling them that way is not wrong. Single characters stay
# case-sensitive (they are typed literally), which is why they aren't in here.
SAFE_SPECIAL_KEYS = {
    "enter", "escape", "esc", "tab", "space", "backspace", "bs",
    "up", "down", "left", "right",
} | {f"f{index}" for index in range(1, 13)}


# Keys herdr's own validator refuses in EVERY spelling -- live re-checked on herdr 0.8.2, which
# answers `unsupported key PageUp` to PageUp/PgUp/pageup/PgDn/Page_Up alike, and the same for
# Home and End with or without a modifier. No respelling reaches them through `pane send-keys`.
#
# `pane send-text` is a byte channel and passes ESC through verbatim (probed by running `cat -v`
# in a throwaway pane, which then showed `^[[5~`), so the relay delivers these as the CSI bytes a
# terminal would emit for the key. A real TUI reads them AS the key: `less` on a 500-line file
# paged from row 1 to row 70 on ESC[6~ and back to row 1 on ESC[5~.
#
# Modified forms are computed rather than enumerated -- xterm encodes the modifier as
# 1 + shift(1) + alt(2) + ctrl(4), so ctrl+Home is ESC[1;5H and shift+PageUp is ESC[5;2~.
#
# Insert and Delete are refused by herdr too and would be one line each here; they stay out until
# a client asks for them, so this table only covers keys something actually sends.
CSI_MODIFIER_BITS = {"shift": 1, "alt": 2, "ctrl": 4}
CSI_TILDE_KEYS = {"pageup": "5", "pagedown": "6"}
CSI_LETTER_KEYS = {"home": "H", "end": "F"}


def key_escape_sequence(key):
    """The CSI bytes for a key herdr cannot send, or "" when `pane send-keys` should take it.

    Accepts the same `+`-joined grammar as key_is_allowed, so `PageUp`, `pageup` and `ctrl+Home`
    all resolve. An unknown or repeated modifier resolves to "" and is then refused by
    key_is_allowed, rather than silently going out as the unmodified key.
    """
    if not isinstance(key, str) or not key:
        return ""
    *modifiers, base = key.split("+")
    base = base.lower()
    if base not in CSI_TILDE_KEYS and base not in CSI_LETTER_KEYS:
        return ""
    modifiers = [modifier.lower() for modifier in modifiers]
    if len(set(modifiers)) != len(modifiers):
        return ""
    if not all(modifier in CSI_MODIFIER_BITS for modifier in modifiers):
        return ""
    code = 1 + sum(CSI_MODIFIER_BITS[modifier] for modifier in modifiers)
    if base in CSI_TILDE_KEYS:
        number = CSI_TILDE_KEYS[base]
        return f"\x1b[{number}~" if code == 1 else f"\x1b[{number};{code}~"
    letter = CSI_LETTER_KEYS[base]
    return f"\x1b[{letter}" if code == 1 else f"\x1b[1;{code}{letter}"


def key_is_allowed(key):
    """True when herdr's key validator would accept `key` AND the relay is willing to send it.

    Two shapes pass: a bare key (SAFE_KEYS, or any special name in any case), and a `+`-joined
    chord whose modifiers are all in SAFE_MODIFIERS and whose base is a single printable character
    or a special name -- herdr takes `alt+Up`, `shift+tab` and `ctrl+c` alike.

    Bare single characters stay limited to SAFE_KEYS (y/n/a/digits) even though herdr would type
    any of them: send_keys is for control, and free text has its own gated channels.

    A repeated modifier (`ctrl+ctrl+c`) is refused HERE regardless of what herdr does with it --
    it only ever arrives from a client bug, and forwarding a malformed chord into a live terminal
    is not the way to find that out.
    """
    if not isinstance(key, str) or not key:
        return False
    if key in SAFE_KEYS or key.lower() in SAFE_SPECIAL_KEYS:
        return True
    if key_escape_sequence(key):
        return True
    if "+" not in key:
        return False
    *modifiers, base = key.split("+")
    if not modifiers or not base:
        return False
    modifiers = [modifier.lower() for modifier in modifiers]
    if not all(modifier in SAFE_MODIFIERS for modifier in modifiers):
        return False
    if len(set(modifiers)) != len(modifiers):
        return False
    if base.lower() in SAFE_SPECIAL_KEYS:
        return True
    return len(base) == 1 and base.isprintable()


# --- Audit logging ---
_audit_handler = RotatingFileHandler(AUDIT_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
_audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
audit_log = logging.getLogger("herdr-audit")
audit_log.setLevel(logging.INFO)
audit_log.addHandler(_audit_handler)
audit_log.propagate = False


# --- WebSocket Origin Validation (CVE mitigation) ---
# Prevents drive-by attacks from malicious webpages when relay runs without token

def relay_host_is_loopback(host: str) -> bool:
    """Check if host is a loopback address."""
    if not host:
        return False
    host = host.lower()
    return host in {"127.0.0.1", "localhost", "::1", "[::1]"}

def normalized_origin(parsed) -> str:
    """Normalize origin to scheme://host:port for comparison."""
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    # Default ports
    if port is None:
        port = 443 if scheme == "https" else 80
    return f"{scheme}://{host}:{port}"

def trusted_websocket_origin(origin: str) -> bool:
    """
    Check if a WebSocket Origin header should be trusted.
    
    - No Origin (native clients like Telegram bot, macOS app): allowed
    - Token authentication enabled: origin check skipped (token governs access)
    - Origin 'null': rejected (opaque origins, sandboxed iframes)
    - Non-HTTP schemes: allowed (file://, app://, etc. are local)
    - Explicitly trusted origins (HERDR_TRUSTED_ORIGINS): allowed
    - Loopback origins (localhost, 127.0.0.1): allowed on loopback relay
    - Everything else: rejected
    """
    import urllib.parse as urlparse
    
    # Native clients don't send Origin
    if not origin:
        return True
    
    # Token auth takes precedence
    if AUTH_TOKEN:
        return True
    
    # Opaque origin (sandboxed iframe, etc.) - reject
    if origin.lower() == "null":
        return False
    
    try:
        parsed = urlparse.urlsplit(origin)
    except Exception:
        return False
    
    scheme = (parsed.scheme or "").lower()
    
    # Non-HTTP schemes (file://, app://, etc.) are local apps
    if scheme not in {"http", "https"}:
        return True
    
    # Check explicit trusted origins
    if TRUSTED_ORIGINS:
        norm = normalized_origin(parsed)
        if norm in TRUSTED_ORIGINS or origin.lower() in TRUSTED_ORIGINS:
            return True
    
    # On loopback relay, allow loopback origins
    if relay_host_is_loopback(RELAY_HOST):
        return relay_host_is_loopback(parsed.hostname)
    
    return False


def audit(action: str, ip: str, device: str, pane_id: str, detail: str = ""):
    """Append a write action to the audit log as structured JSONL."""
    import datetime
    entry = {
        # Same wire format as before -- `Z`, not `+00:00` -- now that utcnow() is deprecated
        # and warned once per audit() per process into the journal.
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "action": action,
        "paneId": pane_id,
        "ip": ip,
        "device": device,
    }
    if detail:
        entry["detail"] = detail[:120]  # truncate like collie
    audit_log.info(json.dumps(entry, separators=(",", ":")))


# --- Web Push helpers ---
def _load_push_subs():
    global push_subscriptions
    if os.path.isfile(PUSH_SUBS_FILE):
        try:
            with open(PUSH_SUBS_FILE) as f:
                push_subscriptions = json.load(f)
        except Exception:
            push_subscriptions = []


def _save_push_subs():
    with open(PUSH_SUBS_FILE, "w") as f:
        json.dump(push_subscriptions, f)


def _load_active_sessions():
    """Restore session selection. Mirrors _load_push_subs: never raises.

    Values are restricted to str or None: a hand-edited or corrupted entry
    like {"local": 5} would otherwise land in ACTIVE_SESSIONS[None] as-is,
    then _herdr_env(5) sets env["HERDR_SESSION"] = 5 and
    subprocess.run(env=...) raises TypeError -- which run_herdr swallows,
    so the relay silently reports zero agents forever, surviving every
    restart. This is also the one place a persisted value reaches
    _invoke_herdr's remote branch (which interpolates it straight into the
    ssh argv) without ever passing through apply_session_switch's
    get_sessions() allowlist, so gating the type on load is the load-time
    half of keeping that argv interpolation sane.
    """
    if not os.path.isfile(ACTIVE_SESSIONS_FILE):
        return
    try:
        with open(ACTIVE_SESSIONS_FILE) as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            return
    except Exception:
        return
    for key, value in stored.items():
        if value is not None and not isinstance(value, str):
            continue
        ACTIVE_SESSIONS[None if key == "local" else key] = value


# --- Pane activity ledger ---
def _load_activity():
    """Read the ledger, dropping anything malformed and anything past the prune horizon.

    Every field is checked because this file outlives the process that wrote it: a shape change, a
    truncated write or a hand-edit must cost the unread column, not the relay's startup.
    """
    global pane_activity
    if not os.path.isfile(ACTIVITY_FILE):
        return
    try:
        with open(ACTIVITY_FILE) as f:
            raw = json.load(f)
    except Exception as e:
        log.warning("could not read %s: %s", ACTIVITY_FILE, e)
        return
    if not isinstance(raw, dict):
        return
    now = time.time()
    loaded = {}
    for host, panes in raw.items():
        if not isinstance(host, str) or not isinstance(panes, dict):
            continue
        for pane_id, entry in panes.items():
            if not isinstance(pane_id, str) or not isinstance(entry, dict):
                continue
            active, seen = entry.get("active_at"), entry.get("seen_at")
            # bool is an int in python, and `True` as a timestamp would sort every pane unread.
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (active, seen)):
                continue
            if now - max(active, seen) > ACTIVITY_PRUNE_AFTER:
                continue
            loaded[(host, pane_id)] = {"active_at": float(active), "seen_at": float(seen)}
    pane_activity = loaded


def _write_activity():
    """BLOCKING. Temp file plus rename, so a crash mid-write cannot leave a half file behind that
    then fails to parse and silently costs everyone's unread state."""
    payload = {}
    for (host, pane_id), entry in pane_activity.items():
        payload.setdefault(host, {})[pane_id] = entry
    tmp = ACTIVITY_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, ACTIVITY_FILE)
    except Exception as e:
        log.warning("could not persist %s: %s", ACTIVITY_FILE, e)


async def flush_activity():
    """Write now if anything changed. Called on shutdown; otherwise the debounce drives it."""
    global _activity_dirty
    if not _activity_dirty:
        return
    _activity_dirty = False
    await asyncio.to_thread(_write_activity)


async def _activity_flush_later():
    await asyncio.sleep(ACTIVITY_FLUSH_DEBOUNCE)
    await flush_activity()


def _activity_mark_dirty():
    global _activity_dirty, _activity_flush_task
    _activity_dirty = True
    if _activity_flush_task is not None and not _activity_flush_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop yet, or none any more -- flush_activity() still writes when asked
    _activity_flush_task = loop.create_task(_activity_flush_later())


def pane_host(pane_id):
    """The host label a pane belongs to, from what the poll already recorded. The ledger's key half."""
    return pane_remote_map.get(pane_id) or "local"


def activity_ensure(host, pane_id):
    """First sighting: seed active_at = seen_at = now, so the pane starts out exactly `seen`.

    A client must never open on a screen full of unread alerts, so only transitions observed AFTER
    the relay first saw a pane may mark it unread -- the same rule the blocked-push path already
    applies by never firing on a first sighting.
    """
    if (host, pane_id) in pane_activity:
        return
    now = time.time()
    pane_activity[(host, pane_id)] = {"active_at": now, "seen_at": now}
    _activity_mark_dirty()


def activity_note_active(host, pane_id):
    """The agent moved. The only thing that can make a pane unread."""
    held = pane_activity.get((host, pane_id))
    now = time.time()
    pane_activity[(host, pane_id)] = {
        "active_at": now, "seen_at": held["seen_at"] if held else now,
    }
    _activity_mark_dirty()


def activity_note_seen(pane_id):
    """A client opened or drove this pane. Clears its unread state by construction.

    Unknown panes are ignored rather than seeded: a client naming a pane the relay has never listed
    would otherwise grow the file by one entry per bogus id.
    """
    if not pane_id or pane_id not in known_panes:
        return
    key = (pane_host(pane_id), pane_id)
    held = pane_activity.get(key)
    now = time.time()
    pane_activity[key] = {"active_at": held["active_at"] if held else now, "seen_at": now}
    _activity_mark_dirty()


def activity_forget(host, pane_id):
    """The pane is gone. Drop it so a reused pane id cannot inherit a dead pane's history."""
    _activity_status.pop((host, pane_id), None)
    if pane_activity.pop((host, pane_id), None) is not None:
        _activity_mark_dirty()


def activity_note_statuses(agents):
    """Bump active_at wherever a status changed since this ledger last looked."""
    for agent in agents:
        key = (agent.get("host", "local"), agent["pane_id"])
        status = agent.get("status")
        if key in _activity_status and _activity_status[key] != status:
            activity_note_active(*key)
        _activity_status[key] = status


def stamp_activity(records):
    """Put the two timestamps on records about to go out, in MILLISECONDS -- every client that will
    compare them is JavaScript, and a client should not have to know which unit this relay thinks in.
    A pane with no entry carries neither key, and `isUnseen` is false for both absent."""
    for record in records:
        entry = pane_activity.get((record.get("host", "local"), record["pane_id"]))
        if entry:
            record["last_active_at"] = int(entry["active_at"] * 1000)
            record["last_seen_at"] = int(entry["seen_at"] * 1000)



def _save_active_sessions():
    payload = {("local" if k is None else k): v for k, v in ACTIVE_SESSIONS.items()}
    with open(ACTIVE_SESSIONS_FILE, "w") as f:
        json.dump(payload, f)


def _deliver_push(payload, headers):
    """POST one payload to every subscription. BLOCKING -- pywebpush is requests underneath.

    Works off a snapshot of push_subscriptions and drops dead ones BY VALUE: this runs on a
    worker thread now, so a push_subscribe arriving mid-flight would invalidate any index
    computed before it and pop somebody else's subscription.
    """
    try:
        from pywebpush import webpush
    except ImportError:
        log.warning("pywebpush not installed, skipping push")
        return
    dead = []
    for sub in list(push_subscriptions):
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                headers=headers,
            )
        except Exception as e:
            log.warning("Push failed for %.60s: %s", (sub or {}).get("endpoint", "?"), e)
            # 404/410 is the push service saying this subscription is retired, not a transient
            # failure -- anything else keeps its subscription for the next notification.
            if "410" in str(e) or "404" in str(e):
                dead.append(sub)
    for sub in dead:
        try:
            push_subscriptions.remove(sub)
        except ValueError:
            pass
    if dead:
        _save_push_subs()


async def send_web_push(title: str, body: str, url: str = "/", clear: bool = False):
    """Send push notification to all registered subscriptions.

    Uses collapse topic + TTL so offline devices get only the latest.
    If clear=True, sends a clear instruction instead of showing a notification.
    """
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return
    if clear:
        payload = json.dumps({"type": "clear", "tag": "herdr-blocked"})
    else:
        payload = json.dumps({"title": title, "body": body, "url": url})
    headers = {"Topic": "herdr-herd", "TTL": "21600"}  # 6h TTL, collapse key
    await asyncio.to_thread(_deliver_push, payload, headers)

_load_push_subs()
_load_active_sessions()
_load_activity()


def _ssh_base_args():
    """SSH options every remote invocation shares, with connection reuse where it is available.

    The poll loop dials every configured host once per POLL_INTERVAL, and each dial used to be a
    full TCP + auth handshake -- 30 handshakes a minute per host at a 2s interval. ControlMaster
    keeps one connection alive across ticks instead. `%C` hashes user/host/port into a
    fixed-width name so the control socket path stays inside the ~104-byte AF_UNIX limit; if the
    path would still be too long, or we are on Windows (whose OpenSSH has no multiplexing), we
    simply run without it rather than break every remote read.
    """
    base = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes"]
    if sys.platform == "win32":
        return base
    control_path = os.environ.get("HERDR_SSH_CONTROL_PATH") or os.path.join(LOG_DIR, "ssh-%C")
    if len(control_path) > 90:
        log.warning("SSH control path too long (%d chars); running without multiplexing", len(control_path))
        return base
    return base + [
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={control_path}",
        "-o", "ControlPersist=60s",
    ]


SSH_BASE_ARGS = _ssh_base_args()


def _remote_lock(remote):
    """One lock per SSH target, so concurrent readers queue instead of racing the connection."""
    with _remote_locks_guard:
        remote_lock = _remote_locks.get(remote)
        if remote_lock is None:
            remote_lock = threading.Lock()
            _remote_locks[remote] = remote_lock
        return remote_lock


def _invoke_herdr(*args, remote=None):
    """Run one herdr command, locally or over SSH. BLOCKING -- never call this from the loop.

    Every herdr call is a subprocess. Locally that is a few ms, but a read reaching past the
    viewport costs seconds and an SSH call can run to the timeout below, and for that whole time
    an inline caller serves no other client, runs no poll tick and sends no broadcast. Everything
    reachable from async code goes through asyncio.to_thread.

    Only the SSH branch touches shared state (_remote_locks, behind _remote_locks_guard), so the
    worker threads need no further synchronising.
    """
    session = active_session_for(remote)
    if remote:
        cmd = ["ssh", *SSH_BASE_ARGS, remote]
        if session:
            # An env= would not survive ssh; the remote shell applies this.
            cmd.append(f"HERDR_SESSION={session}")
        cmd += [REMOTE_HERDR, *args]
        with _remote_lock(remote):
            return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)

    cmd = [HERDR, *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=15, env=_herdr_env(session),
    )


def transcript_ssh(remote, script, ssh_args=()):
    """Run a transcript probe on a remote host, behind the same per-host lock as everything else.

    Bytes, not text: the reply is a framed header plus a raw tail of the transcript, and the cut
    has to be made on a byte boundary before anything tries to decode it.
    """
    cmd = ["ssh", *ssh_args, remote, "sh -c " + shlex.quote(script)]
    with _remote_lock(remote):
        proc = subprocess.run(cmd, capture_output=True, timeout=transcript.REMOTE_TIMEOUT)
    if proc.returncode != 0:
        log.warning("transcript ssh on %s exited %s: %s", remote, proc.returncode,
                    proc.stderr.decode("utf-8", "replace").strip()[:200])
    return proc.returncode, proc.stdout


def run_herdr_result(*args, remote=None):
    return _invoke_herdr(*args, remote=remote)


def run_herdr(*args, remote=None):
    try:
        return _invoke_herdr(*args, remote=remote).stdout.strip()
    except Exception:
        return ""


def _mutate_herdr(*args, remote=None):
    try:
        return run_herdr_result(*args, remote=remote).returncode == 0
    except Exception:
        return False


def get_workspace_labels(remote=None):
    """Map workspace_id to the workspace name the user chose in herdr."""
    raw = run_herdr("workspace", "list", remote=remote)
    try:
        data = json.loads(raw)
        workspaces = data.get("result", {}).get("workspaces", [])
        return {
            w["workspace_id"]: w.get("label", "")
            for w in workspaces
            if w.get("workspace_id") and w.get("label")
        }
    except (json.JSONDecodeError, KeyError):
        return {}


def activity_title(title, agent):
    """The terminal title, but only when it carries something the cwd does not.

    herdr passes the pane's terminal title straight through, and a claude that is working sets it
    to what it is doing ("fix P0, draft the P1 plan"). Idle and done panes are the problem: of the
    nine agent panes on the host this was measured on, seven reported no title at all and two
    reported the plain banner "Claude Code" -- which is the harness's name, already in the `agent`
    field right beside it, and worth less than the cwd a client would drop to show it. Match the
    banner by prefix so codex and opencode get the same treatment without a per-harness list, and
    so a title that merely mentions the harness ("Claude Code: fix the poll") survives.
    """
    title = (title or "").strip()
    if not title:
        return ""
    flattened = re.sub(r"[^a-z0-9]", "", title.lower())
    if agent and flattened.startswith(re.sub(r"[^a-z0-9]", "", agent.lower())):
        return ""
    return title


def pane_session_ref(pane):
    """The agent-session ref herdr reports for a pane, or None when it can't be trusted.

    herdr keeps reporting the LAST session a pane announced, so relaunching a pane under a
    different harness leaves the previous one's ref behind (a pane running pi still advertising a
    claude uuid). The ref carries its own `agent` name, so compare it against the pane's before
    believing it; a server that omits the field stays permissive.
    """
    session = pane.get("agent_session")
    if not isinstance(session, dict):
        return None
    if session.get("kind") not in {"id", "path"} or not session.get("value"):
        return None
    reported = session.get("agent")
    if reported and reported != pane.get("agent"):
        return None
    return session


def shell_pane_record(pane, host_label, remote, order=0):
    """The payload for a pane with no agent in it.

    Deliberately NOT an `agents` entry. Six clients render that array and every one of them
    assumes its entries are agents; a shell pane would show up in all of them as a card with an
    empty harness name. It also has none of what an agent entry carries -- herdr reports
    `agent_status: "unknown"` for all 20 of them here, there is no session and no terminal title
    field at all. What it has is a cwd, a place in the hierarchy, and one thing an agent pane
    never has: a real scrollback ring.
    """
    scroll = pane.get("scroll") or {}
    return {
        "pane_id": pane["pane_id"],
        # herdr allows a label on any pane but nothing sets one by default -- all 20 here report
        # null, so clients fall back to project/pane_id and `rename_agent` is the way to fix that.
        "label": pane.get("label") or "",
        "cwd": pane.get("cwd", ""),
        "project": os.path.basename(pane.get("cwd", "")),
        "host": host_label,
        "remote": remote,
        "workspace_id": pane.get("workspace_id", ""),
        "tab_id": pane.get("tab_id", ""),
        "focused": bool(pane.get("focused")),
        # The reason scrollback is worth offering here and not on an agent pane: measured 34-693
        # rows on the shell panes of this host against a flat 0 on every agent pane, and a
        # 400-line `recent` read costs 5ms rather than herdr's multi-second harvest, because
        # there is a real ring to read instead of a TUI to walk.
        "scrollback": scroll.get("max_offset_from_bottom", 0),
        "viewport_rows": scroll.get("viewport_rows", 0),
        "order": order,
    }


def list_panes_from_host(remote=None):
    """One `pane list`, split into (agents, shell panes).

    Split here rather than in two functions because the CLI call is the expensive part -- 12ms
    locally, a full SSH round trip remotely -- and the poll runs it every POLL_INTERVAL.

    The split is also what makes `order` necessary. herdr answers `pane list` in the order the
    operator sees the panes at the desk -- verified against `pane layout` on every tab of this
    host, it is the same split-tree walk, top-left first -- and that order is not derivable from
    anything else in the record: `pane swap` and `pane move` exist, and the pane id's suffix is a
    creation counter (`w6:pH` was opened before `w6:p12`), so neither the layout nor even the
    creation order survives sorting the ids as strings. Splitting one list in two throws the
    interleaving away, so each record keeps its index in the list it came from. Per host, since a
    client only ever compares panes within one tab of one machine.
    """
    raw = run_herdr("pane", "list", remote=remote)
    host_label = remote or "local"
    try:
        data = json.loads(raw)
        panes = data.get("result", {}).get("panes", [])
        workspace_labels = get_workspace_labels(remote=remote) if panes else {}
    except (json.JSONDecodeError, KeyError):
        return [], []

    agents, shells = [], []
    for order, p in enumerate(panes):
        if not p.get("agent"):
            if SHELL_PANES and p.get("pane_id"):
                shells.append(shell_pane_record(p, host_label, remote, order))
            continue
        session = pane_session_ref(p)
        if session:
            pane_session_map[p["pane_id"]] = session
        else:
            pane_session_map.pop(p["pane_id"], None)
        # `scroll` says what a scrollback read could ever yield: an agent on the alternate screen
        # reports max_offset_from_bottom 0 (verified across every agent pane on this host), so a
        # client can tell "nothing behind the viewport, don't offer to load older" from "there are
        # 9k lines back there" without a probe read. Shell panes are the ones with a ring; they
        # aren't listed yet, but the field is theirs too.
        scroll = p.get("scroll") or {}
        agents.append({
            "pane_id": p["pane_id"],
            "agent": p.get("agent", ""),
            "label": p.get("label", ""),
            # Names the space, and stands in for panes that have no label.
            "workspace_label": workspace_labels.get(p.get("workspace_id", ""), ""),
            "status": p.get("agent_status", "unknown"),
            "cwd": p.get("cwd", ""),
            "project": os.path.basename(p.get("cwd", "")),
            "host": host_label,
            "remote": remote,
            "workspace_id": p.get("workspace_id", ""),
            "tab_id": p.get("tab_id", ""),
            # A working claude sets its terminal title to what it is doing, so this is live
            # activity rather than a stable session name. Idle and done panes report either
            # nothing or the harness's own banner, which says less than the cwd it would
            # displace on a client, so activity_title drops those.
            "title": activity_title(p.get("terminal_title_stripped"), p.get("agent", "")),
            # Which pane herdr itself has in front. Exactly one pane per host is focused, so a
            # client can mark where the operator actually is, and offer to move them (see the
            # `focus` message) instead of only ever listing.
            "focused": bool(p.get("focused")),
            "scrollback": scroll.get("max_offset_from_bottom", 0),
            "viewport_rows": scroll.get("viewport_rows", 0),
            # Where this pane sits in herdr's own `pane list` -- see the docstring. A client that
            # groups panes by tab has no other way back to the order on screen.
            "order": order,
            # Whether this pane names a transcript at all -- the client's cue for offering a
            # history view. The ref itself stays in pane_session_map.
            "has_session": session is not None,
        })
    return agents, shells


def pane_process(pane_id, remote=None):
    """What is actually running in a pane.

    Shell panes are the ones that need this: measured on this host, 20 of them share only 12
    distinct cwd basenames, so eight are indistinguishable from a sibling by directory alone.
    `pane process-info` separates them -- zsh from vim from the build that has been running an
    hour -- for 2.5ms locally. But it is one call per pane, which is one SSH round trip per pane,
    so it is never done for a list; clients ask for it on the pane they are opening.
    """
    raw = run_herdr("pane", "process-info", "--pane", pane_id, remote=remote)
    try:
        info = json.loads(raw).get("result", {}).get("process_info", {})
        foreground = (info.get("foreground_processes") or [{}])[0]
    except (json.JSONDecodeError, AttributeError, TypeError, IndexError):
        return {}
    if not isinstance(foreground, dict):
        return {}
    name = (foreground.get("name") or "").strip()
    if not name:
        return {}
    # Both are the pane's own process table, so they are as trustworthy as anything else herdr
    # reports -- but they end up in a client's UI, so they get the same length ceiling as a label.
    return {"name": name[:64], "cmdline": (foreground.get("cmdline") or "").strip()[:200]}


def pane_layout(pane_id, remote=None):
    """`pane layout` for the tab holding a pane: every pane's rect plus which one is focused."""
    raw = run_herdr("pane", "layout", "--pane", pane_id, remote=remote)
    try:
        layout = json.loads(raw).get("result", {}).get("layout", {})
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(layout, dict) or not layout.get("panes"):
        return None
    return layout


def walk_direction(current, target):
    """Which way herdr should step to get from one pane's rect towards another's.

    Rects are in terminal cells, and a cell is about twice as tall as it is wide, so comparing a
    raw dx against a raw dy picks the wrong axis on splits that look square on screen. Overlap
    decides it without a fudge factor: two panes that share rows are side by side, whatever the
    numbers say, and only when they share none is the move vertical.
    """
    cx0, cy0 = current.get("x", 0), current.get("y", 0)
    cx1, cy1 = cx0 + current.get("width", 0), cy0 + current.get("height", 0)
    tx0, ty0 = target.get("x", 0), target.get("y", 0)
    tx1, ty1 = tx0 + target.get("width", 0), ty0 + target.get("height", 0)
    if ty0 < cy1 and cy0 < ty1:
        if tx0 >= cx1:
            return "right"
        if tx1 <= cx0:
            return "left"
    if ty0 >= cy1:
        return "down"
    if ty1 <= cy0:
        return "up"
    # Nested or overlapping rects -- a zoomed pane, or a layout this does not model. Fall back to
    # whichever centre is further away, so the walk still makes progress instead of refusing.
    if abs((tx0 + tx1) - (cx0 + cx1)) >= abs((ty0 + ty1) - (cy0 + cy1)):
        return "right" if tx0 + tx1 > cx0 + cx1 else "left"
    return "down" if ty0 + ty1 > cy0 + cy1 else "up"


def focus_shell_pane(pane_id, tab_id, remote=None):
    """Focus a pane herdr has no command for.

    `agent focus` takes a pane and walks up to the tab and workspace holding it. There is no
    equivalent for a pane without an agent: `pane focus` only steps to a *neighbour*, by
    direction. So the tab is focused first, and then the pane is reached one step at a time.

    Each step re-reads the layout rather than plotting the whole route from the first one.
    "The pane to the right" is herdr's notion and not ours, so a route computed up front would
    land somewhere else and report success; re-reading also catches the step that moved nothing
    -- a wall, or a layout walk_direction does not model -- and stops instead of looping.

    Costs one `pane layout` per step plus one `pane focus`: about six CLI calls for a four-pane
    tab, 15ms locally. It is user-initiated, never on a timer.
    """
    if tab_id and not _mutate_herdr("tab", "focus", tab_id, remote=remote):
        return False
    previous = None
    for _ in range(PANE_WALK_LIMIT):
        layout = pane_layout(pane_id, remote=remote)
        if layout is None:
            return False
        focused = layout.get("focused_pane_id")
        if focused == pane_id:
            return True
        if focused == previous:
            log.warning("pane walk stalled on %s heading for %s", focused, pane_id)
            return False
        rects = {p.get("pane_id"): (p.get("rect") or {}) for p in layout.get("panes", [])}
        if focused not in rects or pane_id not in rects:
            return False
        previous = focused
        if not _mutate_herdr("pane", "focus", "--direction",
                             walk_direction(rects[focused], rects[pane_id]),
                             "--pane", focused, remote=remote):
            return False
    log.warning("pane walk gave up after %d steps heading for %s", PANE_WALK_LIMIT, pane_id)
    return False


def get_agents_from_host(remote=None):
    return list_panes_from_host(remote=remote)[0]


def get_all_panes():
    agents, shells = list_panes_from_host(remote=None)
    for remote in REMOTES:
        more_agents, more_shells = list_panes_from_host(remote=remote)
        agents.extend(more_agents)
        shells.extend(more_shells)
    return agents, shells


def get_all_agents():
    return get_all_panes()[0]


def get_sessions(remote=None):
    """List herdr sessions for one source as [{"name", "running"}].

    Cached per source for SESSION_LIST_CACHE_TTL: sessions_message() calls
    this once per source on every client connect, each a blocking
    subprocess (ssh with up to a 15s timeout for remotes) on the event
    loop, and herdr_telegram.py opens a fresh WebSocket per button press
    with no X-Herdr-Remote-Command header -- so every press previously
    paid N+1 of these. apply_session_switch()'s validation also goes
    through this cache, which keeps it checking against the same list the
    user was actually shown rather than a fresher one they never saw.
    """
    cached = _session_list_cache.get(remote)
    if cached is not None and time.monotonic() - cached[0] < SESSION_LIST_CACHE_TTL:
        return cached[1]
    raw = run_herdr("session", "list", remote=remote)
    sessions = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] == "name":
            continue
        sessions.append({"name": parts[0], "running": parts[1] == "running"})
    _session_list_cache[remote] = (time.monotonic(), sessions)
    return sessions


def update_pane_maps(agents, shells=()):
    """Register what the poll just saw, and forget what it didn't.

    `shells` defaults to empty so a caller that only has agents cannot accidentally evict every
    shell pane through the stale sweep below -- passing nothing means "no opinion", not "there
    are none". Callers that list both pass both.
    """
    current_pane_ids = {agent["pane_id"] for agent in agents}
    if shells:
        current_pane_ids |= {pane["pane_id"] for pane in shells}
    else:
        # No shell list means the caller has no opinion about shell panes, not that there are
        # none -- keep the ones already known instead of sweeping every one of them as stale.
        current_pane_ids |= set(shell_pane_map)
    for agent in agents:
        pane_id = agent["pane_id"]
        pane_remote_map[pane_id] = agent.get("remote")
        known_panes.add(pane_id)
        agent_cache[pane_id] = agent
        activity_ensure(agent.get("host", "local"), pane_id)
    for pane in shells:
        pane_id = pane["pane_id"]
        pane_remote_map[pane_id] = pane.get("remote")
        known_panes.add(pane_id)
        shell_pane_map[pane_id] = pane
        activity_ensure(pane.get("host", "local"), pane_id)
    activity_note_statuses(agents)

    stale = known_panes - current_pane_ids
    if stale:
        known_panes.difference_update(stale)
        for pane_id in stale:
            # Before pane_remote_map loses the pane, since that map is what names its host. Reusing
            # this sweep rather than reconciling the ledger separately is deliberate: the guard on
            # `shells` above already decides when the caller has a full enough picture to forget
            # anything, and a second policy beside it would be a second thing to keep true.
            activity_forget(pane_host(pane_id), pane_id)
            pane_remote_map.pop(pane_id, None)
            pane_session_map.pop(pane_id, None)
            last_statuses.pop(pane_id, None)
            last_blocked_prompts.pop(pane_id, None)
            agent_cache.pop(pane_id, None)
            shell_pane_map.pop(pane_id, None)
    # Last, so the records carry whatever this call just seeded or bumped.
    stamp_activity(agents)
    stamp_activity(shells)


POLL_GENERATION = 0


def reset_pane_state():
    """Drop all pane-keyed state and invalidate in-flight polls.

    pane_id is session-local: w1:p1 exists in every session. update_pane_maps
    prunes only panes absent from the new list, so a pane_id present in both
    sessions would carry its state across a switch — suppressing a real blocked
    notification, or letting a command route to the wrong session's agent.

    Also drains event_queue: a pre-switch agent_event dequeued after this call
    must not be able to re-seed state under a stale pane_id. Must only ever
    be called from the event-loop thread; POLL_GENERATION += 1 is not atomic.

    Also clears _session_list_cache: a real switch must always be validated
    and displayed against a freshly read session list, never a pre-switch
    one still inside its TTL.
    """
    global POLL_GENERATION
    known_panes.clear()
    agent_cache.clear()
    pane_remote_map.clear()
    last_statuses.clear()
    last_blocked_prompts.clear()
    _session_list_cache.clear()
    while True:
        try:
            event_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    POLL_GENERATION += 1


def _source_key(host):
    """Map a client-supplied host to a source key, or raise KeyError."""
    if host in (None, "", "local"):
        return None
    if host in REMOTES:
        return host
    raise KeyError(host)


def session_switch_names(host):
    """Session names a switch to `host` may name, or None when the host is unknown.

    BLOCKING -- one `herdr session list`, which is an ssh round trip for a remote. Split out of
    apply_session_switch so a caller on the event loop can read this on a worker thread and still
    run the mutation itself: reset_pane_state drains an asyncio.Queue and bumps POLL_GENERATION,
    neither of which is safe off the loop thread.
    """
    try:
        source = _source_key(host)
    except KeyError:
        return None
    return {entry["name"] for entry in get_sessions(remote=source)}


def apply_session_switch(host, session, ip="", device="", *, names):
    """Point one source at a session. Returns (ok, error_message, changed).

    `names` is the allowlist a named session is checked against -- read it with
    session_switch_names, off the loop. It is keyword-only and has no default on purpose: this
    function must NOT be able to reach a blocking call, and a caller that omits the allowlist
    should fail loudly rather than fall back to reading it here. A falsy `names` therefore
    rejects every named session; only `session=None` (follow herdr's own default) still passes.

    `changed` is False on the no-op path (already-active selection) and on
    any rejection, True only when ACTIVE_SESSIONS was actually mutated.
    Callers must skip the broadcast + re-poll when it's False -- that's the
    expensive part the no-op short-circuit below exists to avoid, and it is
    defeated if the caller runs it anyway.

    Must run on the event-loop thread: reset_pane_state below is not thread-safe.
    """
    try:
        source = _source_key(host)
    except KeyError:
        return False, f"unknown host: {host}", False

    # Re-selecting the already-active session is a no-op: skip the pane-state reset below.
    # `source in ACTIVE_SESSIONS` (not `.get()`) matters here -- a key that
    # has never been set is not the same thing as an explicit None value.
    if source in ACTIVE_SESSIONS and ACTIVE_SESSIONS[source] == session:
        return True, "", False

    if session is not None:
        if not isinstance(session, str):
            # session lands in a set-membership check next; a list/dict is
            # unhashable there and would raise instead of being rejected.
            return False, f"unknown session: {session}", False
        if session not in (names or frozenset()):
            return False, f"unknown session: {session}", False

    ACTIVE_SESSIONS[source] = session
    try:
        _save_active_sessions()
    except Exception:
        # A save failure must not half-apply the switch: pane state still
        # has to reset and the action still has to audit, or stale
        # pane-keyed state survives under the newly active session.
        log.exception("failed to persist active sessions: host=%s session=%s", host, session)
    reset_pane_state()
    audit("session_switch", ip, device, "", f"host={host} session={session}")
    log.info("session switch: host=%s session=%s", host, session)
    return True, "", True


SESSION_REFRESH_EVERY = 15   # poll cycles; 30s at POLL_INTERVAL=2
SESSION_LIST_CACHE_TTL = SESSION_REFRESH_EVERY * POLL_INTERVAL   # 30s


def sessions_message():
    """Per-source session lists and the active selection for each."""
    sources = []
    for source in [None, *REMOTES]:
        sources.append({
            "host": "local" if source is None else source,
            "active": active_session_for(source),
            "sessions": get_sessions(remote=source),
        })
    return {"type": "sessions", "sources": sources}


async def broadcast_sessions():
    gen = POLL_GENERATION
    msg = await asyncio.to_thread(sessions_message)
    # This guard is load-bearing now that the line above yields: sessions_message runs one
    # `herdr session list` per source on a worker thread, so a session_switch CAN land while
    # this message is being built, and the message it would carry is then already wrong.
    #
    # It does NOT cover the real staleness window: broadcast() below awaits
    # ws.send() once per client, so a switch landing mid fan-out can still
    # let a client late in that loop see a pre-switch `active` value. That
    # window is shared by every message type, self-heals within one
    # refresh cycle (~30s), and closing it means touching broadcast()
    # itself.
    if gen != POLL_GENERATION:
        return          # a switch landed while building this; the message is stale
    await broadcast(msg)


# How often the tab/workspace hierarchy is re-read, in poll ticks. `pane list` already carries
# every pane's workspace_id and tab_id, but only the ids: the labels the operator sees, the
# numbering, and which one is focused live in `workspace list` and `tab list`. Two more CLI calls
# per host -- 4ms each locally, one SSH round trip each remotely -- against a hierarchy that only
# changes when someone creates, closes, renames or focuses something. So: its own slower cadence,
# plus a forced refresh after any message that moves it (see spaces_dirty).
SPACES_POLL_INTERVAL = 5
spaces_dirty = True
_spaces_ticks = 0


def get_spaces_from_host(remote=None):
    """The workspaces and tabs one herdr reports, flattened and tagged with their host."""
    host_label = remote or "local"
    workspaces = []
    tabs = []

    raw = run_herdr("workspace", "list", remote=remote)
    try:
        listed = json.loads(raw).get("result", {}).get("workspaces", [])
    except (json.JSONDecodeError, AttributeError):
        listed = []
    for w in listed:
        if not w.get("workspace_id"):
            continue
        worktree = w.get("worktree") or {}
        workspaces.append({
            "workspace_id": w["workspace_id"],
            # herdr's own label -- the repo or directory name the operator named the space, not
            # the basename of some pane's cwd, which is what a client has to guess from `agents`.
            "label": w.get("label", ""),
            "number": w.get("number", 0),
            "focused": bool(w.get("focused")),
            "tab_count": w.get("tab_count", 0),
            # Every pane, agent or not. The relay only lists agent panes, so the difference is
            # exactly how much of this workspace a client cannot see yet.
            "pane_count": w.get("pane_count", 0),
            "active_tab_id": w.get("active_tab_id", ""),
            "repo": worktree.get("repo_name", ""),
            "host": host_label,
            "remote": remote,
        })

    raw = run_herdr("tab", "list", remote=remote)
    try:
        listed = json.loads(raw).get("result", {}).get("tabs", [])
    except (json.JSONDecodeError, AttributeError):
        listed = []
    for t in listed:
        if not t.get("tab_id"):
            continue
        tabs.append({
            "tab_id": t["tab_id"],
            "workspace_id": t.get("workspace_id", ""),
            # Defaults to the tab number as a string, so it is only interesting once someone
            # renames it -- but then it is the only place that name exists.
            "label": t.get("label", ""),
            "number": t.get("number", 0),
            "focused": bool(t.get("focused")),
            "pane_count": t.get("pane_count", 0),
            "host": host_label,
            "remote": remote,
        })

    return workspaces, tabs


def get_all_spaces():
    workspaces, tabs = get_spaces_from_host(remote=None)
    for remote in REMOTES:
        more_workspaces, more_tabs = get_spaces_from_host(remote=remote)
        workspaces.extend(more_workspaces)
        tabs.extend(more_tabs)
    return {"workspaces": workspaces, "tabs": tabs}


def update_space_maps(spaces):
    workspace_remote_map.clear()
    tab_remote_map.clear()
    for w in spaces["workspaces"]:
        workspace_remote_map[(w["host"], w["workspace_id"])] = w["remote"]
    for t in spaces["tabs"]:
        tab_remote_map[(t["host"], t["tab_id"])] = t["remote"]


def refresh_spaces(force=False):
    """Re-read the hierarchy when it is due, and return whatever the cache holds now."""
    global spaces_dirty, _spaces_ticks
    if force or spaces_dirty or _spaces_ticks % SPACES_POLL_INTERVAL == 0:
        spaces = get_all_spaces()
        # An empty result means the CLI call failed (herdr always has at least one workspace);
        # keep the last good hierarchy rather than blanking every client's chip strip.
        if spaces["workspaces"]:
            spaces_cache["workspaces"] = spaces["workspaces"]
            spaces_cache["tabs"] = spaces["tabs"]
            update_space_maps(spaces_cache)
        spaces_dirty = False
    _spaces_ticks += 1
    return spaces_cache


def mark_spaces_dirty():
    """Ask the next poll to re-read the hierarchy instead of waiting out the slow cadence."""
    global spaces_dirty
    spaces_dirty = True


def resolve_space(kind, ident, host=""):
    """Which host owns this workspace/tab id, as (ok, remote, error).

    Ids are unique per herdr, not across hosts: two machines both call their first workspace w1.
    A client that sees more than one host therefore has to say which one it means. Clients that
    send no host are served while the id is unambiguous and refused when it is not -- guessing
    would mutate a tab on the wrong machine.
    """
    table = workspace_remote_map if kind == "workspace" else tab_remote_map
    if not ident:
        return False, None, f"{kind}_id required"
    if host:
        if (host, ident) not in table:
            return False, None, f"unknown {kind}_id"
        return True, table[(host, ident)], ""
    matches = {h: r for (h, i), r in table.items() if i == ident}
    if not matches:
        return False, None, f"unknown {kind}_id"
    if len(matches) > 1:
        return False, None, f"{kind}_id {ident} exists on {', '.join(sorted(matches))}; host required"
    return True, next(iter(matches.values())), ""


# A label a client wants written into herdr's own UI, or "" if it is not one.
MAX_LABEL_LEN = 64


def clean_label(label):
    """Collapse a client-supplied name to something safe to hand a CLI as a positional argument.

    A leading dash would be parsed as a flag, and a newline or control character would be written
    straight into herdr's tab strip, so neither survives.
    """
    label = re.sub(r"[\x00-\x1f\x7f]", " ", str(label or "")).strip()
    if not label or label.startswith("-") or len(label) > MAX_LABEL_LEN:
        return ""
    return label


# Source for every read the relay makes on its own initiative (poll loop, respond, send_keys).
#
# `visible` -- the rendered viewport -- NOT `recent`. In text format a `recent` read of more lines
# than the pane is tall makes herdr harvest an alt-screen agent's scrollback through the agent's
# own mouse-scroll interface. Measured on herdr 0.8.0: 200 lines took 6.2s, 400 took 12.7s
# (~31ms/line), it only works while the agent is idle, it is not even deterministic (a first
# attempt returned the viewport and nothing else), and the operator watches their terminal scroll
# up and snap back once per read. This function runs on every poll tick for every blocked pane and
# again before every respond/send_keys, so it has to be free. `visible` is immune by construction:
# it IS the rendered grid, clamped to it however many lines are asked for.
#
# The conversation history the harvest was reaching for is not this function's job -- see
# get_history: it belongs in the agent's own transcript, which has real message boundaries and
# costs nothing.
PROMPT_READ_SOURCE = "visible"

# Sources herdr accepts on `pane read` (CLI spelling -- the socket wants recent_unwrapped, the CLI
# wants recent-unwrapped), and the line ceiling herdr silently enforces.
READ_SOURCES = {"visible", "recent", "recent-unwrapped", "detection"}
MAX_READ_LINES = 1000


def read_pane(pane_id, remote=None):
    raw = run_herdr("pane", "read", pane_id, "--lines", "100", "--source", PROMPT_READ_SOURCE, remote=remote)
    lines = [l for l in raw.splitlines() if l.strip() and not CHROME_RE.search(l)]
    display_lines = lines[-50:]
    question = detect_question("\n".join(lines))
    if question and question["text"] and question["text"] not in display_lines:
        option_start = next(
            (
                index for index in range(len(display_lines) - 1, -1, -1)
                if QUESTION_OPTION_RE.match(display_lines[index].strip().strip("\u2502|").strip())
            ),
            None,
        )
        if option_start is not None:
            while option_start > 0 and QUESTION_OPTION_RE.match(
                display_lines[option_start - 1].strip().strip("\u2502|").strip()
            ):
                option_start -= 1
        else:
            option_start = 0
        display_lines.insert(option_start, question["text"])
    return "\n".join(display_lines)


def detect_question(text):
    blocks = []
    current = []
    current_start = None
    lines = text.splitlines()
    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip().strip("\u2502|").strip()
        match = QUESTION_OPTION_RE.match(line)
        if not match:
            if current:
                blocks.append((current_start, current))
                current = []
                current_start = None
            continue
        if current_start is None:
            current_start = line_index
        marker = match.group("marker")
        current.append({
            "label": match.group("label").strip(),
            "selected": bool(match.group("cursor")),
            "multi": marker in {"\uf046", "\uf096", "\uf14a", "\u2610", "\u2611", "[ ]", "[x]", "[X]"},
            "checked": marker in {"\uf046", "\uf14a", "\u2611", "[x]", "[X]"},
        })
    if current:
        blocks.append((current_start, current))

    for block_start, block in reversed(blocks):
        has_other = any(option["label"] == QUESTION_OTHER for option in block)
        has_done = any("Done selecting" in option["label"] for option in block)
        if has_other or has_done:
            question_lines = []
            for raw_line in reversed(lines[:block_start]):
                line = raw_line.strip().strip("\u2502|").strip()
                if not line:
                    if question_lines:
                        break
                    continue
                if (
                    "submit" in line.casefold()
                    or re.fullmatch(r"[\W_]*ask[\W_]*", line, re.IGNORECASE)
                    or not any(character.isalnum() for character in line)
                ):
                    if question_lines:
                        break
                    continue
                question_lines.append(line)
            question_text = " ".join(reversed(question_lines))
            return {
                "options": block,
                "selected_index": next(
                    (index for index, option in enumerate(block) if option["selected"]),
                    0,
                ),
                "multi": any(option["multi"] for option in block) or has_done,
                "text": question_text,
            }
    return None


def detect_approval_options(text):
    lower = text.lower()
    if "yes, single permission" in lower:
        return TOOL_OPTIONS
    if "approve all pending" in lower:
        return SUBAGENT_OPTIONS
    return []


def detect_options(text):
    approval_options = detect_approval_options(text)
    if approval_options:
        return approval_options
    question = detect_question(text)
    if not question:
        return []
    return [
        option["label"]
        for option in question["options"]
        if option["label"] != QUESTION_OTHER and "Done selecting" not in option["label"]
    ]


def custom_editor_active(text):
    return "Enter your response:" in text or (
        "Custom answer:" in text and "submit" in text.lower()
    )

def question_prompt_id(pane_id, content):
    question = detect_question(content)
    if not question:
        normalized = " ".join(content.split())
        return hashlib.sha256(f"{pane_id}\n{normalized}".encode("utf-8")).hexdigest()[:20]
    labels = [
        option["label"] for option in question["options"]
        if option["label"] != QUESTION_OTHER and "Done selecting" not in option["label"]
    ]
    signature = json.dumps(
        {
            "pane_id": pane_id,
            "question": question["text"],
            "multi": question["multi"],
            "labels": labels,
        },
        sort_keys=True,
    )
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]


def prompt_matches(pane_id, prompt_id, remote=None):
    if not prompt_id:
        return False
    return question_prompt_id(pane_id, read_pane(pane_id, remote=remote)) == prompt_id


def blocked_message(pane_id, agent, project, host, content):
    question = detect_question(content) if agent == "omp" else None
    options = detect_options(content) if agent == "omp" else detect_approval_options(content)
    return {
        "type": "blocked",
        "pane_id": pane_id,
        "agent": agent,
        "project": project,
        "host": host,
        "prompt": content[-500:],
        "prompt_id": question_prompt_id(pane_id, content),
        "options": [] if question and question["multi"] else options,
        "multi_options": options if question and question["multi"] else [],
        "selected_options": [
            option["label"] for option in question["options"]
            if option["multi"] and option["label"] != QUESTION_OTHER
            and "Done selecting" not in option["label"] and option["checked"]
        ] if question else [],
        "interaction": "omp_question" if question else "prompt",
        "multi": bool(question and question["multi"]),
        "update": False,
    }


def pane_is_omp(pane_id, remote=None):
    return any(
        agent["pane_id"] == pane_id and agent["agent"] == "omp" and agent.get("remote") == remote
        for agent in get_all_agents()
    )


def move_question_cursor(pane_id, question, target_index, remote=None):
    selected_index = question["selected_index"]
    direction = "Down" if target_index >= selected_index else "Up"
    keys = [direction] * abs(target_index - selected_index)
    return not keys or _mutate_herdr("pane", "send-keys", pane_id, *keys, remote=remote)


def toggle_question_option(pane_id, option_label, remote=None):
    if not pane_is_omp(pane_id, remote=remote):
        return False
    question = detect_question(read_pane(pane_id, remote=remote))
    if not question or not question["multi"]:
        return False
    target_index = next((
        index
        for index, option in enumerate(question["options"])
        if option["label"].casefold() == option_label.casefold()
    ), None)
    if target_index is None or not move_question_cursor(pane_id, question, target_index, remote=remote):
        return False
    return _mutate_herdr("pane", "send-keys", pane_id, "Enter", remote=remote)


def submit_multi_question(pane_id, remote=None):
    if not pane_is_omp(pane_id, remote=remote):
        return False
    content = read_pane(pane_id, remote=remote)
    question = detect_question(content)
    if not question or not question["multi"]:
        return False
    done_index = next((
        index
        for index, option in enumerate(question["options"])
        if "Done selecting" in option["label"]
    ), None)
    if done_index is not None:
        if not move_question_cursor(pane_id, question, done_index, remote=remote):
            return False
        return _mutate_herdr("pane", "send-keys", pane_id, "Enter", remote=remote)
    if "Submit" in content and any(
        marker in content for marker in ("\uf14a", "\uf046", "\u2611", "[x]", "[X]")
    ):
        return _mutate_herdr("pane", "send-keys", pane_id, "Tab", "Enter", remote=remote)
    return False


def respond_to_question(pane_id, text, question, remote=None):
    options = question["options"]
    target_index = next(
        (index for index, option in enumerate(options) if option["label"].casefold() == text.casefold()),
        None,
    )
    custom_response = target_index is None
    if custom_response:
        target_index = next(
            (index for index, option in enumerate(options) if option["label"] == QUESTION_OTHER),
            None,
        )
    if target_index is None:
        return False

    selected_index = question["selected_index"]
    direction = "Down" if target_index >= selected_index else "Up"
    keys = [direction] * abs(target_index - selected_index) + ["Enter"]
    if not _mutate_herdr("pane", "send-keys", pane_id, *keys, remote=remote):
        return False
    if not custom_response:
        return True
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        editor_content = read_pane(pane_id, remote=remote)
        if "Enter your response:" in editor_content or (
            "Custom answer:" in editor_content and "submit" in editor_content.lower()
        ):
            break
        time.sleep(0.05)
    else:
        return False
    return _mutate_herdr("pane", "send-text", pane_id, text, remote=remote) and _mutate_herdr(
        "pane", "send-keys", pane_id, "Enter", remote=remote
    )


async def broadcast(msg):
    data = json.dumps(msg)
    dead = set()
    for ws in list(clients):
        try:
            await ws.send(data)
        except (ConnectionClosedError, ConnectionClosedOK):
            dead.add(ws)
        except Exception:
            dead.add(ws)
    if dead:
        log.debug("Removed %d dead client(s)", len(dead))
    clients.difference_update(dead)

async def send_current_snapshot(ws):
    await ws.send(json.dumps(await asyncio.to_thread(sessions_message)))
    agents, shells = await asyncio.to_thread(get_all_panes)
    update_pane_maps(agents, shells)
    # Force the hierarchy read: a client that just connected has no chip strip at all, and
    # waiting out the slow cadence would show it agents filed under ids for a few seconds.
    spaces = await asyncio.to_thread(refresh_spaces, force=True)
    await ws.send(json.dumps(
        {"type": "agents", "agents": agents, "spaces": spaces, "panes": shells}))
    for agent in agents:
        if agent["status"] != "blocked":
            continue
        content = await asyncio.to_thread(read_pane, agent["pane_id"], remote=agent.get("remote"))
        await ws.send(json.dumps(blocked_message(
            agent["pane_id"],
            agent["agent"],
            agent["project"],
            agent.get("host", "local"),
            content,
        )))


async def poll_loop():
    cycle = 0
    while True:
        try:
            await _poll_once()
            if cycle % SESSION_REFRESH_EVERY == 0:
                await broadcast_sessions()
        except Exception:
            log.exception("poll cycle failed; retrying")
        cycle += 1
        await asyncio.sleep(POLL_INTERVAL)


async def _poll_once():
        gen = POLL_GENERATION
        agents, shells = await asyncio.to_thread(get_all_panes)
        update_pane_maps(agents, shells)
        # Always broadcast (even empty list) so clients stay in sync
        spaces = await asyncio.to_thread(refresh_spaces)
        await broadcast(
            {"type": "agents", "agents": agents, "spaces": spaces, "panes": shells})
        if gen != POLL_GENERATION:
            return          # a switch landed; this snapshot is stale
        for a in agents:
            pid, status = a["pane_id"], a["status"]
            if status == "blocked":
                content = await asyncio.to_thread(read_pane, pid, remote=a.get("remote"))
                message = blocked_message(
                    pid,
                    a["agent"],
                    a["project"],
                    a.get("host", "local"),
                    content,
                )
                fingerprint = (
                    message["prompt_id"],
                    tuple(message["selected_options"]),
                    message["prompt"],
                )
                previous = last_blocked_prompts.get(pid)
                if previous != fingerprint:
                    message["update"] = previous is not None and previous[0] == message["prompt_id"]
                    last_blocked_prompts[pid] = fingerprint
                    await broadcast(message)
                    await send_web_push(
                        title=f"\U0001f411 {a['project']} blocked",
                        body=content[:120],
                        url=f"/?pane={pid}",
                    )
                    if gen != POLL_GENERATION:
                        return
            else:
                if last_statuses.get(pid) == "blocked":
                    await send_web_push("", "", clear=True)
                    if gen != POLL_GENERATION:
                        return
                last_blocked_prompts.pop(pid, None)
            last_statuses[pid] = status
async def event_push():
    while True:
        event = await event_queue.get()
        gen = POLL_GENERATION
        pane_id = event.get("pane_id", "")
        update = None
        if pane_id and event.get("type") == "agent_event":
            update = complete_agent_update_message(
                event,
                current=agent_cache.get(pane_id),
                local_hostname=socket.gethostname(),
            )
            if update is None:
                continue
        agent_data = update["agent"] if update else event
        status = agent_data.get("status", "")
        host = agent_data.get("host", "local")
        event_remote = pane_remote_map.get(pane_id)

        if pane_id and event.get("type") == "agent_event":
            agents, shells = await asyncio.to_thread(get_all_panes)
            if status == "blocked" and not any(
                agent["pane_id"] == pane_id for agent in agents
            ):
                agents.append({
                    "pane_id": pane_id,
                    "agent": agent_data.get("agent", ""),
                    "status": status,
                    "cwd": agent_data.get("cwd", ""),
                    "project": agent_data.get("project", ""),
                    "host": host,
                    "remote": event_remote,
                })
            update_pane_maps(agents, shells)
            await broadcast({"type": "agents", "agents": agents, "panes": shells})
            if gen != POLL_GENERATION:
                continue        # a switch landed; this event is stale
            agent_cache[pane_id] = {**agent_cache.get(pane_id, {}), **agent_data}
            if status != "blocked":
                await broadcast(update)

        if status == "blocked" and pane_id:
            remote = pane_remote_map.get(pane_id)
            if remote or host == "local":
                content = await asyncio.to_thread(read_pane, pane_id, remote=remote)
            else:
                content = event.get("prompt", "Agent is blocked")
            message = blocked_message(
                pane_id,
                agent_data.get("agent", ""),
                agent_data.get("project", ""),
                host,
                content or agent_data.get("prompt", "Agent is blocked"),
            )
            # Unreachable with a mismatch today: whichever branch got here
            # did so with no await since the last gen check (the "agents"
            # broadcast above already `continue`s on staleness before this
            # point, and read_pane/blocked_message are synchronous), so gen
            # cannot have changed. Kept as a guard for a future refactor
            # that inserts an await between this check and the writes below.
            if gen != POLL_GENERATION:
                continue        # a switch landed; this event is stale
            last_blocked_prompts[pane_id] = (
                message["prompt_id"],
                tuple(message["selected_options"]),
                message["prompt"],
            )
            await broadcast(message)


WEB_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))

# What the relay serves out of web/, keyed by extension: (content type, Cache-Control).
#
# The split is the interesting half. A font or a raster never changes without changing its name,
# so a year of immutable caching is free. The stylesheet and the scripts DO change under a fixed
# name on every deploy, and the previous table gave the one file it held -- a font -- that same
# year. Handing it to app.css would have pinned every returning browser to whatever JavaScript it
# saw first, with no way to invalidate short of renaming the files.
#
# `.html` is deliberately absent: index.html is served further up, behind the token when one is
# configured, and adding it here would quietly turn the auth exemption below into a way past it.
WEB_ASSET_TYPES = {
    ".css": ("text/css; charset=utf-8", "no-cache"),
    ".js": ("text/javascript; charset=utf-8", "no-cache"),
    ".svg": ("image/svg+xml", "public, max-age=31536000, immutable"),
    ".png": ("image/png", "public, max-age=31536000, immutable"),
    ".woff2": ("font/woff2", "public, max-age=31536000, immutable"),
    ".txt": ("text/plain; charset=utf-8", "public, max-age=31536000, immutable"),
}


def web_asset(request_path):
    """(absolute path, content type, cache policy) for a static file under web/, or None.

    Replaces a hand-maintained `path -> (filename, mime)` table, which was a standing bug rather
    than a list: every file committed to web/ is public on Cloudflare Pages immediately, but over
    the relay it 404s until someone remembers two more lines in two different places -- so a
    missing asset only ever showed up for the people on a tunnel, which is the half nobody tests.
    Splitting the app into modules would have made that table grow a line per file.

    Cannot be talked out of web/. Every segment has to be a plain name, which rejects "", ".",
    ".." and anything carrying a separator -- and the server has already percent-decoded, so that
    covers the %2e%2e spellings too. The resolved path is then checked to still be inside web/,
    which is what catches a symlink pointing out of the tree.
    """
    if not isinstance(request_path, str) or not request_path.startswith("/"):
        return None
    segments = request_path[1:].split("/")
    separators = {os.sep, os.altsep} - {None}
    for segment in segments:
        if segment in ("", ".", "..") or any(sep in segment for sep in separators):
            return None
    entry = WEB_ASSET_TYPES.get(os.path.splitext(segments[-1])[1].lower())
    if entry is None:
        return None
    resolved = os.path.realpath(os.path.join(WEB_DIR, *segments))
    if resolved != WEB_DIR and not resolved.startswith(WEB_DIR + os.sep):
        return None
    if not os.path.isfile(resolved):
        return None
    content_type, cache_control = entry
    return resolved, content_type, cache_control


async def process_request(connection, request):
    """Handle HTTP POST on the same port as WebSocket."""
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    public_paths = {"/sw.js", "/api/vapid-public-key"}
    request_path = (request.path or "/").split("?", 1)[0]

    # Token auth (if configured). Static assets under web/ are exempt because a browser fetches
    # the stylesheet, the scripts and the fonts before anything has authenticated, and the
    # service worker reads its notification icon with no session at all. index.html is NOT one of
    # them -- see web_asset -- so the app itself stays behind the token exactly as before.
    if AUTH_TOKEN and request_path not in public_paths and web_asset(request_path) is None:
        token = None
        for key, value in request.headers.raw_items():
            if key.lower() == "authorization":
                token = value.replace("Bearer ", "")
        # Also check query param ?token=
        if not token and "token=" in (request.path or ""):
            import urllib.parse
            _, qs = request.path.split("?", 1) if "?" in request.path else (request.path, "")
            params = urllib.parse.parse_qs(qs)
            token = params.get("token", [None])[0]
        if token != AUTH_TOKEN:
            headers = Headers([("Content-Type", "text/plain")])
            return Response(401, "Unauthorized", headers, b"Invalid token\n")

    # Check if this is a WebSocket upgrade
    upgrade = None
    origin = None
    for key, value in request.headers.raw_items():
        if key.lower() == "upgrade":
            upgrade = value.lower()
        if key.lower() == "origin":
            origin = value
    if upgrade == "websocket":
        # Validate origin to prevent drive-by attacks from malicious webpages
        if not trusted_websocket_origin(origin):
            log.warning("Rejected WebSocket from untrusted origin: %s", origin)
            headers = Headers([("Content-Type", "text/plain")])
            return Response(403, "Forbidden", headers, b"Untrusted WebSocket origin\n")
        return None  # proceed with WebSocket handshake

    # For CORS preflight
    if request.path and "OPTIONS" in str(request.headers):
        headers = Headers([
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Methods", "POST, OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type"),
        ])
        return Response(204, "No Content", headers, b"")

    # ⚠ EVENT PUSH MUST BE HANDLED FIRST — ORDER IS LOAD-BEARING.
    # A pushed event arrives as `?d=<urlencoded json>` on ANY path.
    # The README shows POST to :8375 without naming a path, so `/` is common.
    # Every static route below `return`s, so if reached first the event is
    # dropped while caller still gets 200. Add new static routes BELOW, never above.
    import urllib.parse as _urlparse
    if "?" in (request.path or ""):
        _, qs = (request.path or "").split("?", 1)
        params = _urlparse.parse_qs(qs)
        if "d" in params:
            try:
                event = json.loads(params["d"][0])  # parse_qs already decodes
                event_queue.put_nowait(event)
                log.debug("push: received event type=%s", event.get("type", "unknown"))
            except Exception as e:
                log.warning("push: unparseable event payload (%d bytes): %s", len(params["d"][0]), e)
            headers = Headers([("Access-Control-Allow-Origin", "*")])
            return Response(200, "OK", headers, b"ok\n")

    # Serve web app for GET / or GET /index.html
    path = (request.path or "/").split("?")[0]
    if path in ("/", "/index.html"):
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
        index_path = os.path.join(web_dir, "index.html")
        if os.path.isfile(index_path):
            with open(index_path, "rb") as f:
                body = f.read()
            headers = Headers([
                ("Content-Type", "text/html; charset=utf-8"),
                ("Cache-Control", "no-cache"),
            ])
            return Response(200, "OK", headers, body)

    # Serve service worker
    if path == "/sw.js":
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
        sw_path = os.path.join(web_dir, "sw.js")
        if os.path.isfile(sw_path):
            with open(sw_path, "rb") as f:
                body = f.read()
            headers = Headers([
                ("Content-Type", "application/javascript"),
                ("Cache-Control", "no-cache"),
                ("Service-Worker-Allowed", "/"),
            ])
            return Response(200, "OK", headers, body)

    # Serve VAPID public key
    if path == "/api/vapid-public-key":
        body = json.dumps({"publicKey": VAPID_PUBLIC_KEY}).encode()
        headers = Headers([
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
        ])
        return Response(200, "OK", headers, body)

    # Serve logo.svg
    if path == "/logo.svg":
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
        svg_path = os.path.join(web_dir, "logo.svg")
        if os.path.isfile(svg_path):
            with open(svg_path, "rb") as f:
                body = f.read()
            headers = Headers([("Content-Type", "image/svg+xml")])
            return Response(200, "OK", headers, body)

    asset = web_asset(path)
    if asset:
        asset_path, content_type, cache_control = asset
        with open(asset_path, "rb") as f:
            body = f.read()
        headers = Headers([
            ("Content-Type", content_type),
            ("Cache-Control", cache_control),
        ])
        return Response(200, "OK", headers, body)

    # Fallback for unmatched paths
    headers = Headers([("Access-Control-Allow-Origin", "*")])
    return Response(404, "Not Found", headers, b"not found\n")


async def handle_client(ws):
    remote_addr = ws.remote_address
    ip = remote_addr[0] if remote_addr else "unknown"
    ua = ws.request.headers.get("User-Agent", "unknown") if ws.request else "unknown"
    origin = ws.request.headers.get("Origin", "") if ws.request else ""
    command_connection = (
        ws.request.headers.get("X-Herdr-Remote-Command") == "1"
        if ws.request
        else False
    )

    device = "unknown"
    ua_lower = ua.lower()
    if "iphone" in ua_lower or "ipad" in ua_lower:
        device = "iOS"
    elif "android" in ua_lower:
        device = "Android"
    elif "macintosh" in ua_lower or "mac os" in ua_lower:
        device = "macOS"
    elif "windows" in ua_lower:
        device = "Windows"
    elif "linux" in ua_lower:
        device = "Linux"
    elif "telegram" in ua_lower or "bot" in ua_lower:
        device = "bot"
    elif "python" in ua_lower:
        device = "script"

    log.info("Client connected: ip=%s device=%s origin=%s", ip, device, origin or "-")
    clients.add(ws)
    connected_at = time.monotonic()
    try:
        if not command_connection:
            await send_current_snapshot(ws)
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            # One place, ahead of every handler, so a new one cannot forget to clear a pane's unread
            # state (see SEEN_ON). Ahead of validation too: a client that named a known pane did
            # look at it, whatever the rest of the message turns out to be.
            if msg_type in SEEN_ON:
                activity_note_seen(msg.get("pane_id", ""))
            if msg_type == "question_toggle":
                pane_id = msg["pane_id"]
                option = msg.get("option", "")
                if pane_id not in known_panes or not option:
                    await ws.send(json.dumps({"type": "error", "message": "invalid question option"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                if not await asyncio.to_thread(
                    prompt_matches, pane_id, msg.get("prompt_id", ""), remote=remote
                ):
                    await ws.send(json.dumps({"type": "error", "message": "question changed; refresh and try again"}))
                    continue
                if not await asyncio.to_thread(toggle_question_option, pane_id, option, remote=remote):
                    await ws.send(json.dumps({"type": "error", "message": "question option toggle failed"}))
            elif msg_type == "question_submit":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                if not await asyncio.to_thread(
                    prompt_matches, pane_id, msg.get("prompt_id", ""), remote=remote
                ):
                    await ws.send(json.dumps({"type": "error", "message": "question changed; refresh and try again"}))
                    continue
                if not await asyncio.to_thread(submit_multi_question, pane_id, remote=remote):
                    await ws.send(json.dumps({"type": "error", "message": "question submission failed"}))
            elif msg_type == "respond":
                pane_id = msg["pane_id"]
                request_id = msg.get("request_id")

                def command_error(message):
                    response = {"type": "error", "message": message}
                    if request_id:
                        response["request_id"] = request_id
                    return response

                if pane_id not in known_panes:
                    await ws.send(json.dumps(command_error("unknown pane_id")))
                    continue
                text = msg.get("text", "").strip()
                if not text or len(text) > 1000:
                    await ws.send(json.dumps(command_error("response empty or too long")))
                    continue
                remote = pane_remote_map.get(pane_id)
                if pane_id in shell_pane_map:
                    # A shell pane has no question to detect, no approval options to match and no
                    # harness to refuse a bad answer: the text IS a command and Enter runs it.
                    # That is what HERDR_SHELL_PANES buys and why it is off by default. The
                    # question guard below would refuse every one of these, so it is skipped
                    # rather than tricked -- and the audit line says which kind of pane it was.
                    log.info("Shell command from %s (%s): pane=%s text=%r", ip, device, pane_id, text)
                    audit("respond_shell", ip, device, pane_id, f"text={text!r}")
                    delivered = await asyncio.to_thread(
                        _mutate_herdr, "pane", "send-text", pane_id, text, remote=remote
                    ) and await asyncio.to_thread(
                        _mutate_herdr, "pane", "send-keys", pane_id, "Enter", remote=remote
                    )
                    await ws.send(json.dumps(
                        {"type": "command_result", "command": "respond", "ok": bool(delivered),
                         **({"request_id": request_id} if request_id else {})}))
                    continue
                content = await asyncio.to_thread(read_pane, pane_id, remote=remote)
                if question_prompt_id(pane_id, content) != msg.get("prompt_id", ""):
                    await ws.send(json.dumps(command_error("prompt changed; refresh and try again")))
                    continue
                question = (
                    detect_question(content)
                    if await asyncio.to_thread(pane_is_omp, pane_id, remote=remote)
                    else None
                )
                log.info("Response from %s (%s): pane=%s text=%r", ip, device, pane_id, text)
                audit("respond", ip, device, pane_id, f"text={text!r}")
                if question:
                    delivered = await asyncio.to_thread(
                        respond_to_question, pane_id, text, question, remote=remote
                    )
                elif custom_editor_active(content) or text.lower() in SAFE_RESPONSES:
                    delivered = await asyncio.to_thread(
                        _mutate_herdr, "pane", "send-text", pane_id, text, remote=remote
                    ) and await asyncio.to_thread(
                        _mutate_herdr, "pane", "send-keys", pane_id, "Enter", remote=remote
                    )
                else:
                    await ws.send(json.dumps({
                        **command_error("free-text response requires a detected question"),
                    }))
                    continue
                if not delivered:
                    await ws.send(json.dumps(command_error("response delivery failed")))
                    continue
                response = {"type": "command_result", "command": "respond", "ok": True}
                if request_id:
                    response["request_id"] = request_id
                await ws.send(json.dumps(response))
            elif msg_type == "session_switch":
                request_id = msg.get("request_id")
                # The allowlist read is one `herdr session list` per call -- an ssh round trip
                # for a remote -- so it goes to a worker thread. The mutation stays here: it
                # drains an asyncio.Queue and bumps POLL_GENERATION, neither safe off the loop.
                names = await asyncio.to_thread(session_switch_names, msg.get("host"))
                ok, err, changed = apply_session_switch(
                    msg.get("host"), msg.get("session"), ip, device, names=names
                )
                if not ok:
                    response = {"type": "error", "message": err}
                    if request_id:
                        response["request_id"] = request_id
                    await ws.send(json.dumps(response))
                else:
                    # A no-op switch (already-active selection) still acks,
                    # but must skip the broadcast and re-poll -- that's the
                    # expensive part `changed` exists to let us avoid.
                    if changed:
                        await broadcast_sessions()
                    if request_id:
                        await ws.send(json.dumps({
                            "type": "command_result",
                            "command": "session_switch",
                            "request_id": request_id,
                            "ok": True,
                        }))
                    if changed:
                        try:
                            await _poll_once()
                        except Exception:
                            log.exception("post-switch poll failed")
            elif msg_type == "agent_event":
                event_queue.put_nowait(msg)
            elif msg_type == "read_pane":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                read_format = msg.get("format", "text")
                if read_format not in {"text", "ansi"}:
                    await ws.send(json.dumps({"type": "error", "message": "invalid pane read format"}))
                    continue
                # herdr clamps pane.read at ~1000 lines and does not say so (`truncated` stays
                # true either way): 999/1000/1500/5000 all came back with the same 1000 rows.
                # Asking for more only buys a bigger request, so refuse to pretend.
                try:
                    lines = max(1, min(int(msg.get("lines", 30)), MAX_READ_LINES))
                except (TypeError, ValueError):
                    await ws.send(json.dumps({"type": "error", "message": "invalid pane read lines"}))
                    continue
                # Clients pick the source: `visible` for a live mirror poll (free, viewport only),
                # `recent`/`recent-unwrapped` when the user explicitly asks for scrollback. Note
                # `recent` + text on an alt-screen agent pane is the multi-second harvest that
                # scrolls the operator's terminal (see PROMPT_READ_SOURCE) -- it is allowed here
                # because it is user-initiated, not because it is cheap.
                read_source = str(msg.get("source", "recent")).replace("_", "-")
                if read_source not in READ_SOURCES:
                    await ws.send(json.dumps({"type": "error", "message": "invalid pane read source"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                content = await asyncio.to_thread(
                    run_herdr,
                    "pane", "read", pane_id, "--lines", str(lines), "--source", read_source,
                    "--format", read_format, remote=remote
                )
                reply = {"type": "pane_content", "pane_id": pane_id, "content": content}
                if msg.get("process"):
                    # One extra CLI call, so it is asked for rather than always sent: a client
                    # wants it when it OPENS a pane, not on every mirror refresh.
                    reply["process"] = await asyncio.to_thread(
                        pane_process, pane_id, remote=remote
                    )
                await ws.send(json.dumps(reply))
            elif msg_type == "get_history":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                # History comes from the agent's own transcript, not from the terminal: an agent
                # TUI runs on the alternate screen, so herdr kept no scrollback for it, and the
                # one read that does reach older rows costs ~31ms per line and scrolls the
                # operator's terminal. What used to stand here called `herdr agent history`, a
                # command that does not exist.
                #
                # The session uuid never crosses the wire in either direction: the client sends a
                # pane_id, the relay looks the ref up in pane_session_map (which it populates from
                # `pane list`), and transcript.history validates it before it touches a path.
                try:
                    limit = int(msg.get("limit", transcript.DEFAULT_LIMIT))
                except (TypeError, ValueError):
                    await ws.send(json.dumps({"type": "error", "message": "invalid history limit"}))
                    continue
                before = msg.get("before")
                if before is not None and not isinstance(before, str):
                    await ws.send(json.dumps({"type": "error", "message": "invalid history cursor"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                pane = agent_cache.get(pane_id) or {}
                # Off the event loop: a cold read of the biggest transcript on this machine (33MB)
                # measured 0.29s, and a remote one is an SSH round trip. Neighbouring handlers
                # block the loop on their subprocess; this one is too slow to join them.
                body = await asyncio.to_thread(
                    transcript.history,
                    pane_session_map.get(pane_id),
                    remote=remote,
                    limit=limit,
                    before=before or None,
                    include_tools=bool(msg.get("include_tools")),
                    agent=pane.get("agent", ""),
                    ssh_args=SSH_BASE_ARGS,
                    remote_runner=transcript_ssh,
                    log=log,
                )
                await ws.send(json.dumps({"type": "history", "pane_id": pane_id, **body}))
            elif msg_type == "send_keys":
                pane_id = msg["pane_id"]
                request_id = msg.get("request_id")

                def command_error(message):
                    response = {"type": "error", "message": message}
                    if request_id:
                        response["request_id"] = request_id
                    return response

                if pane_id not in known_panes:
                    await ws.send(json.dumps(command_error("unknown pane_id")))
                    continue
                keys = msg.get("keys", [])
                if not isinstance(keys, list) or not keys:
                    log.warning("send_keys from %s (%s) has no key list: %.120r", ip, device, keys)
                    await ws.send(json.dumps(command_error("keys contain disallowed values")))
                    continue
                refused = [key for key in keys if not key_is_allowed(key)]
                if refused:
                    # Logged because the refusal is otherwise INVISIBLE: this branch returns above
                    # the `log.info` below, so a client sending a key this relay does not know
                    # left no trace at all -- which is exactly the case that needs diagnosing,
                    # since it is what a client newer than its relay looks like.
                    log.warning("send_keys from %s (%s) refused for pane %s: %.120r",
                                ip, device, pane_id, refused)
                    detail = ", ".join(str(key)[:24] for key in refused[:4])
                    await ws.send(json.dumps(
                        command_error(f"keys contain disallowed values: {detail}")))
                    continue
                remote = pane_remote_map.get(pane_id)
                content = await asyncio.to_thread(read_pane, pane_id, remote=remote)
                if detect_approval_options(content) and any(key.isdigit() for key in keys):
                    if question_prompt_id(pane_id, content) != msg.get("prompt_id", ""):
                        await ws.send(json.dumps(command_error("prompt changed; refresh and try again")))
                        continue
                log.info("Keys from %s (%s): pane=%s keys=%s", ip, device, pane_id, keys)
                audit("send_keys", ip, device, pane_id, f"keys={keys}")
                # Keys herdr's validator refuses (CSI_TILDE_KEYS / CSI_LETTER_KEYS) travel as raw
                # CSI bytes through `pane send-text`. Consecutive keys of one kind go out in a
                # single call, and the runs keep the order the client sent them -- a client that
                # queues [Escape, PageUp, Enter] gets those three in that order, not regrouped.
                runs = []
                for key in keys:
                    sequence = key_escape_sequence(key)
                    kind = "send-text" if sequence else "send-keys"
                    if runs and runs[-1][0] == kind:
                        runs[-1][1].append(sequence or key)
                    else:
                        runs.append((kind, [sequence or key]))
                failure = ""
                for kind, payload in runs:
                    # send-text takes ONE text argument, so a run of CSI keys is concatenated.
                    args = ["".join(payload)] if kind == "send-text" else payload
                    try:
                        result = await asyncio.to_thread(
                            run_herdr_result, "pane", kind, pane_id, *args, remote=remote
                        )
                    except Exception as exc:
                        failure = f"raised {exc}"
                    else:
                        if result.returncode != 0:
                            failure = f"exit {result.returncode}"
                    if failure:
                        log.warning("send_keys %s failed for pane %s: %s", kind, pane_id, failure)
                        break
                if failure:
                    await ws.send(json.dumps(command_error("send_keys command failed")))
                    continue
                response = {"type": "command_result", "command": "send_keys", "ok": True}
                if request_id:
                    response["request_id"] = request_id
                await ws.send(json.dumps(response))
            elif msg_type == "send_text":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                text = msg.get("text", "")
                if not text or len(text) > 1000:
                    await ws.send(json.dumps({"type": "error", "message": "text empty or too long"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Text from %s (%s): pane=%s text=%r", ip, device, pane_id, text)
                audit("send_text", ip, device, pane_id, f"text={text!r}")
                await asyncio.to_thread(run_herdr, "pane", "send-text", pane_id, text, remote=remote)
            elif msg_type == "agent_prompt":
                # Use 'herdr agent prompt' for proper submission (works with Codex, Claude, etc.)
                request_id = msg.get("request_id")
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    response = {"type": "error", "message": "unknown pane_id"}
                    if request_id:
                        response["request_id"] = request_id
                    await ws.send(json.dumps(response))
                    continue
                text = msg.get("text", "")
                if not text or len(text) > 10000:
                    response = {"type": "error", "message": "text empty or too long"}
                    if request_id:
                        response["request_id"] = request_id
                    await ws.send(json.dumps(response))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Agent prompt from %s (%s): pane=%s text=%r", ip, device, pane_id, text[:100])
                audit("agent_prompt", ip, device, pane_id, f"text={text[:100]!r}")
                await asyncio.to_thread(run_herdr, "agent", "prompt", pane_id, text, remote=remote)
                response = {"type": "command_result", "command": "agent_prompt", "ok": True}
                if request_id:
                    response["request_id"] = request_id
                await ws.send(json.dumps(response))
            elif msg_type == "focus":
                # Move herdr's own focus. Which id the client sent says what to focus, so there is
                # no separate kind field: `agent focus` takes a pane, and herdr walks up to the
                # tab and workspace holding it. (A non-agent pane has no such command -- `pane
                # focus` only steps to a neighbour by direction -- so focusing a shell pane will
                # mean tab focus plus a walk when those panes are listed at all.)
                target_kind, ident = "", ""
                for kind, field in (("pane", "pane_id"), ("tab", "tab_id"), ("workspace", "workspace_id")):
                    if msg.get(field):
                        target_kind, ident = kind, msg[field]
                        break
                if not target_kind:
                    await ws.send(json.dumps({"type": "error", "message": "pane_id, tab_id or workspace_id required"}))
                    continue
                if target_kind == "pane":
                    if ident not in known_panes:
                        await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                        continue
                    remote = pane_remote_map.get(ident)
                    shell = shell_pane_map.get(ident)
                    if shell is not None:
                        log.info("Focus from %s (%s): shell pane=%s", ip, device, ident)
                        audit("focus", ip, device, ident, f"shell pane={ident}")
                        moved = await asyncio.to_thread(
                            focus_shell_pane, ident, shell.get("tab_id", ""), remote=remote
                        )
                        mark_spaces_dirty()
                        await ws.send(json.dumps(
                            {"type": "command_result", "command": "focus", "ok": moved}))
                        continue
                    args = ("agent", "focus", ident)
                else:
                    ok, remote, error = resolve_space(target_kind, ident, msg.get("host", ""))
                    if not ok:
                        await ws.send(json.dumps({"type": "error", "message": error}))
                        continue
                    args = (target_kind, "focus", ident)
                log.info("Focus from %s (%s): %s=%s", ip, device, target_kind, ident)
                audit("focus", ip, device, ident if target_kind == "pane" else "", f"{target_kind}={ident}")
                moved = await asyncio.to_thread(
                    _mutate_herdr, *args, remote=remote
                )
                # Focus is the one mutation whose whole effect is in the hierarchy, so the next
                # broadcast has to carry it rather than wait out the slow cadence.
                mark_spaces_dirty()
                await ws.send(json.dumps({"type": "command_result", "command": "focus", "ok": moved}))
            elif msg_type == "create_tab":
                workspace_id = msg.get("workspace_id", "")
                ok, remote, error = resolve_space("workspace", workspace_id, msg.get("host", ""))
                if not ok:
                    await ws.send(json.dumps({"type": "error", "message": error}))
                    continue
                label = clean_label(msg.get("label", ""))
                log.info("Create tab from %s (%s): workspace=%s", ip, device, workspace_id)
                audit("create_tab", ip, device, "", f"workspace={workspace_id}")
                args = ["tab", "create", "--workspace", workspace_id, "--focus"]
                if label:
                    args += ["--label", label]
                created = await asyncio.to_thread(
                    _mutate_herdr, *args, remote=remote
                )
                mark_spaces_dirty()
                await ws.send(json.dumps({"type": "tab_created", "ok": created}))
            elif msg_type == "rename_tab":
                tab_id = msg.get("tab_id", "")
                ok, remote, error = resolve_space("tab", tab_id, msg.get("host", ""))
                if not ok:
                    await ws.send(json.dumps({"type": "error", "message": error}))
                    continue
                label = clean_label(msg.get("label", ""))
                if not label:
                    await ws.send(json.dumps({"type": "error", "message": f"label empty, leading dash, or over {MAX_LABEL_LEN} chars"}))
                    continue
                log.info("Rename tab from %s (%s): tab=%s label=%r", ip, device, tab_id, label)
                audit("rename_tab", ip, device, "", f"tab={tab_id} label={label!r}")
                renamed = await asyncio.to_thread(
                    _mutate_herdr, "tab", "rename", tab_id, label, remote=remote
                )
                mark_spaces_dirty()
                await ws.send(json.dumps({"type": "command_result", "command": "rename_tab", "ok": renamed}))
            elif msg_type == "close_tab":
                # Destructive, and the relay is the wrong place to second-guess it: closing a tab
                # takes its panes with it. Clients confirm; this logs who asked.
                tab_id = msg.get("tab_id", "")
                ok, remote, error = resolve_space("tab", tab_id, msg.get("host", ""))
                if not ok:
                    await ws.send(json.dumps({"type": "error", "message": error}))
                    continue
                log.info("Close tab from %s (%s): tab=%s", ip, device, tab_id)
                audit("close_tab", ip, device, "", f"tab={tab_id}")
                closed = await asyncio.to_thread(
                    _mutate_herdr, "tab", "close", tab_id, remote=remote
                )
                mark_spaces_dirty()
                await ws.send(json.dumps({"type": "command_result", "command": "close_tab", "ok": closed}))
            elif msg_type == "rename_agent":
                # herdr's own label for the pane, which is what `agents` reports as `label` and
                # what every client shows as the card title. Nothing is typed into the agent.
                pane_id = msg.get("pane_id", "")
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                label = clean_label(msg.get("label", ""))
                clear = bool(msg.get("clear"))
                if not label and not clear:
                    await ws.send(json.dumps({"type": "error", "message": f"label empty, leading dash, or over {MAX_LABEL_LEN} chars"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Rename agent from %s (%s): pane=%s label=%r", ip, device, pane_id, label)
                audit("rename_agent", ip, device, pane_id, f"label={label!r}" if label else "clear")
                args = ("agent", "rename", pane_id, "--clear") if clear else ("agent", "rename", pane_id, label)
                renamed = await asyncio.to_thread(
                    _mutate_herdr, *args, remote=remote
                )
                await ws.send(json.dumps({"type": "command_result", "command": "rename_agent", "ok": renamed}))
            elif msg_type == "push_subscribe":
                sub = msg.get("subscription")
                if sub and sub not in push_subscriptions:
                    push_subscriptions.append(sub)
                    _save_push_subs()
                    log.info("Push subscription added from %s (%s)", ip, device)
                await ws.send(json.dumps({"type": "push_subscribed", "ok": True}))
            elif msg_type == "push_unsubscribe":
                sub = msg.get("subscription")
                if sub and sub in push_subscriptions:
                    push_subscriptions.remove(sub)
                    _save_push_subs()
                await ws.send(json.dumps({"type": "push_unsubscribed", "ok": True}))
    except (ConnectionClosedError, ConnectionClosedOK):
        pass
    finally:
        duration = int(time.monotonic() - connected_at)
        log.info("Client disconnected: ip=%s device=%s duration=%ds", ip, device, duration)
        clients.discard(ws)


class UDPPlugin(asyncio.DatagramProtocol):
    def datagram_received(self, data, addr):
        try:
            event_queue.put_nowait(json.loads(data.decode()))
        except Exception:
            pass


def start_mdns():
    try:
        from zeroconf import Zeroconf, ServiceInfo
        import socket as sock_mod
        ip = sock_mod.gethostbyname(sock_mod.gethostname())
        info = ServiceInfo(
            "_herdr-remote._tcp.local.", "herdr-remote._herdr-remote._tcp.local.",
            addresses=[sock_mod.inet_aton(ip)], port=WS_PORT,
        )
        zc = Zeroconf()
        threading.Thread(target=zc.register_service, args=(info,), daemon=True).start()
        log.info("mDNS registering at %s", ip)
        return zc, info
    except Exception as e:
        log.warning("mDNS skipped: %s", e)
        return None, None


async def main():
    loop = asyncio.get_running_loop()
    zc = info = udp_transport = server = None
    tasks = []
    loop_signal_handlers = []
    fallback_signal_handlers = {}
    stop = loop.create_future()

    def resolve_stop():
        if not stop.done():
            stop.set_result(None)

    def request_stop(*_):
        loop.call_soon_threadsafe(resolve_stop)

    try:
        zc, info = start_mdns()
        try:
            udp_transport, _ = await loop.create_datagram_endpoint(
                UDPPlugin, local_addr=("127.0.0.1", 8376)
            )
        except OSError:
            log.warning("UDP 8376 in use, plugin push disabled")
        tasks = [asyncio.create_task(poll_loop()), asyncio.create_task(event_push())]
        server = await serve(handle_client, RELAY_HOST, WS_PORT, process_request=process_request)
        hosts = ["local"] + REMOTES
        log.info("herdr-remote relay on %s:%d (WebSocket + HTTP POST)", RELAY_HOST, WS_PORT)
        log.info("Polling: %s", ", ".join(hosts))
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, request_stop)
                loop_signal_handlers.append(sig)
            except NotImplementedError:
                fallback_signal_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, request_stop)
        await stop
    finally:
        for sig in loop_signal_handlers:
            loop.remove_signal_handler(sig)
        for sig, handler in fallback_signal_handlers.items():
            signal.signal(sig, handler)
        if server is not None:
            server.close()
            await server.wait_closed()
        if udp_transport is not None:
            udp_transport.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await flush_activity()
        if zc is not None:
            try:
                if info is not None:
                    # unregister_service submits a coroutine to zeroconf's own loop and waits on
                    # .result(). Called from this loop it deadlocks against itself until zeroconf
                    # gives up at _LOADED_SYSTEM_TIMEOUT -- measured 10.4s of a shutdown that
                    # should be instant, on every restart. register_service was already on its own
                    # thread; the teardown beside it never was.
                    await asyncio.to_thread(zc.unregister_service, info)
            finally:
                zc.close()


if __name__ == "__main__":
    asyncio.run(main())
