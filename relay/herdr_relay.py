#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets>=14.0", "zeroconf>=0.80.0", "pywebpush>=2.0.0", "py-vapid>=1.9.0"]
# ///
"""herdr-remote relay — polls herdr, accepts push events (HTTP POST + WebSocket + UDP), broadcasts to clients."""
import asyncio, hashlib, json, logging, os, re, shutil, signal, socket, subprocess, threading, time

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
last_blocked_prompts = {}
event_queue = asyncio.Queue()
pane_remote_map = {}
known_panes = set()
agent_cache = {}
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
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
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
        cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", remote]
        if session:
            # An env= would not survive ssh; the remote shell applies this.
            cmd.append(f"HERDR_SESSION={session}")
        cmd += [REMOTE_HERDR, *args]
        with _remote_locks_guard:
            remote_lock = _remote_locks.get(remote)
            if remote_lock is None:
                remote_lock = threading.Lock()
                _remote_locks[remote] = remote_lock
        with remote_lock:
            return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)

    cmd = [HERDR, *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=15, env=_herdr_env(session),
    )


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


def get_agents_from_host(remote=None):
    raw = run_herdr("pane", "list", remote=remote)
    host_label = remote or "local"
    try:
        data = json.loads(raw)
        panes = data.get("result", {}).get("panes", [])
        workspace_labels = get_workspace_labels(remote=remote) if panes else {}
        return [
            {
                "pane_id": p["pane_id"],
                "agent": p.get("agent", ""),
                "label": p.get("label", ""),
                # Names the space, and stands in for panes that have no label.
                "workspace_label": workspace_labels.get(p.get("workspace_id", ""), ""),
                # A working claude sets its terminal title to what it is doing, so this is live
                # activity rather than a stable session name. Idle and done panes report either
                # nothing or the harness's own banner, which says less than the cwd it would
                # displace on a client, so activity_title drops those.
                "title": activity_title(p.get("terminal_title_stripped"), p.get("agent", "")),
                "status": p.get("agent_status", "unknown"),
                "cwd": p.get("cwd", ""),
                "project": os.path.basename(p.get("cwd", "")),
                "host": host_label,
                "remote": remote,
                "workspace_id": p.get("workspace_id", ""),
                "tab_id": p.get("tab_id", ""),
            }
            for p in panes if p.get("agent")
        ]
    except (json.JSONDecodeError, KeyError):
        return []


def get_all_agents():
    agents = get_agents_from_host(remote=None)
    for remote in REMOTES:
        agents.extend(get_agents_from_host(remote=remote))
    return agents


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


def update_pane_maps(agents):
    current_pane_ids = {agent["pane_id"] for agent in agents}
    for agent in agents:
        pane_id = agent["pane_id"]
        pane_remote_map[pane_id] = agent.get("remote")
        known_panes.add(pane_id)
        agent_cache[pane_id] = agent

    stale = known_panes - current_pane_ids
    if stale:
        known_panes.difference_update(stale)
        for pane_id in stale:
            pane_remote_map.pop(pane_id, None)
            last_statuses.pop(pane_id, None)
            last_blocked_prompts.pop(pane_id, None)
            agent_cache.pop(pane_id, None)


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


def read_pane(pane_id, remote=None):
    raw = run_herdr("pane", "read", pane_id, "--lines", "100", "--source", "recent", remote=remote)
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
    agents = await asyncio.to_thread(get_all_agents)
    update_pane_maps(agents)
    await ws.send(json.dumps({"type": "agents", "agents": agents}))
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
        agents = await asyncio.to_thread(get_all_agents)
        update_pane_maps(agents)
        # Always broadcast (even empty list) so clients stay in sync
        await broadcast({"type": "agents", "agents": agents})
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
            agents = await asyncio.to_thread(get_all_agents)
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
            update_pane_maps(agents)
            await broadcast({"type": "agents", "agents": agents})
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


async def process_request(connection, request):
    """Handle HTTP POST on the same port as WebSocket."""
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    public_paths = {
        "/sw.js", "/logo.svg", "/api/vapid-public-key",
        "/HackNerdFont-Regular.woff2", "/HackNerdFont-LICENSE.txt",
    }
    request_path = (request.path or "/").split("?", 1)[0]

    # Token auth (if configured)
    if AUTH_TOKEN and request_path not in public_paths:
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

    static_files = {
        "/HackNerdFont-Regular.woff2": ("HackNerdFont-Regular.woff2", "font/woff2"),
        "/HackNerdFont-LICENSE.txt": ("HackNerdFont-LICENSE.txt", "text/plain; charset=utf-8"),
    }
    if path in static_files:
        filename, content_type = static_files[path]
        asset_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "web", filename
        )
        if os.path.isfile(asset_path):
            with open(asset_path, "rb") as f:
                body = f.read()
            headers = Headers([
                ("Content-Type", content_type),
                ("Cache-Control", "public, max-age=31536000, immutable"),
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
                lines = msg.get("lines", "30")
                read_format = msg.get("format", "text")
                if read_format not in {"text", "ansi"}:
                    await ws.send(json.dumps({"type": "error", "message": "invalid pane read format"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                content = await asyncio.to_thread(
                    run_herdr,
                    "pane", "read", pane_id, "--lines", str(lines), "--source", "recent",
                    "--format", read_format, remote=remote
                )
                await ws.send(json.dumps({"type": "pane_content", "pane_id": pane_id, "content": content}))
            elif msg_type == "get_history":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                # Try to read conversation history from agent's session log
                history = await asyncio.to_thread(
                    run_herdr, "agent", "history", pane_id, "--format", "json", remote=remote
                )
                messages = []
                try:
                    data = json.loads(history) if history else {}
                    messages = data.get("messages", data.get("history", []))
                except Exception:
                    pass
                await ws.send(json.dumps({"type": "history", "pane_id": pane_id, "messages": messages}))
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
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                text = msg.get("text", "")
                if not text or len(text) > 10000:
                    await ws.send(json.dumps({"type": "error", "message": "text empty or too long"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Agent prompt from %s (%s): pane=%s text=%r", ip, device, pane_id, text[:100])
                audit("agent_prompt", ip, device, pane_id, f"text={text[:100]!r}")
                await asyncio.to_thread(run_herdr, "agent", "prompt", pane_id, text, remote=remote)
                await ws.send(json.dumps({"type": "command_result", "command": "agent_prompt", "ok": True}))
            elif msg_type == "create_tab":
                workspace_id = msg.get("workspace_id", "")
                if workspace_id:
                    log.info("Create tab from %s (%s): workspace=%s", ip, device, workspace_id)
                    audit("create_tab", ip, device, "", f"workspace={workspace_id}")
                    await asyncio.to_thread(
                        run_herdr, "tab", "create", "--workspace", workspace_id, "--focus"
                    )
                    await ws.send(json.dumps({"type": "tab_created", "ok": True}))
                else:
                    await ws.send(json.dumps({"type": "error", "message": "workspace_id required"}))
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
