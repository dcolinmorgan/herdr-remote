"""Sealed commands between two computers sharing a Telegram chat.

Telegram's bot API is transport encryption only: the service can read every
message, a bot never receives a message it sent itself, and a second
``getUpdates`` poller steals the bot's updates. Callers therefore seal the
command with AES-256-GCM under a key that never goes in the chat, and the
computer that runs herdr is the only one polling the bot. The other computer
sends as a Telegram user.

A ciphertext is bound to one chat id. Replays of a request id, and requests
more than two minutes off the relay's clock, are dropped.
"""
import base64
import json
import logging
import os
import re
import secrets
import time
from collections import OrderedDict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("herdr-tg-link")

PREFIX = "hr1."
MAX_ENVELOPE = 4096
MAX_PLAIN = 2400
TEXT_BUDGET = 2000
SKEW_SECONDS = 120
REPLAY_LIMIT = 256
OPS = frozenset({"read", "prompt", "interrupt"})
ID_RE = re.compile(r"^[0-9a-f]{16}$")
_AAD_PREFIX = b"herdr-tg-link-v1\n"


def generate_key():
    """A url-safe 32-byte key. The trailing padding is stripped."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def parse_key(value):
    """Return 32 key bytes, or raise ValueError. Never log ``value``."""
    if not isinstance(value, str):
        raise ValueError("link key must be the url-safe key from `herdr-tg keygen`")
    try:
        raw = _b64decode(value.strip())
    except Exception as exc:
        raise ValueError("link key must be the url-safe key from `herdr-tg keygen`") from exc
    if len(raw) != 32:
        raise ValueError("link key must be the url-safe key from `herdr-tg keygen`")
    return raw


def load_key():
    """The process key, or None when the link is off or the value is unusable."""
    raw = os.environ.get("HERDR_TG_LINK_KEY", "").strip()
    if not raw:
        return None
    try:
        return parse_key(raw)
    except ValueError:
        log.warning("HERDR_TG_LINK_KEY is set but unusable; ignoring link envelopes")
        return None


def is_envelope(text):
    return isinstance(text, str) and text.startswith(PREFIX) and len(text) <= MAX_ENVELOPE


def _aad(chat_id):
    return _AAD_PREFIX + str(chat_id).encode("utf-8")


def _b64decode(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def seal(payload, *, key, chat_id):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_PLAIN:
        raise ValueError("payload too large")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, raw, _aad(chat_id))
    body = base64.urlsafe_b64encode(nonce + ciphertext).decode().rstrip("=")
    text = PREFIX + body
    if len(text) > MAX_ENVELOPE:
        raise ValueError("payload too large")
    return text


def open_sealed(text, *, key, chat_id):
    """The payload dict, or None when the text is not a genuine envelope for this chat."""
    if not is_envelope(text) or key is None:
        return None
    try:
        blob = _b64decode(text[len(PREFIX):])
        if len(blob) < 12 + 16:
            return None
        raw = AESGCM(key).decrypt(blob[:12], blob[12:], _aad(chat_id))
        if len(raw) > MAX_PLAIN:
            return None
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


class ReplayWindow:
    """Request ids already accepted, plus a clock window. Bounded so a busy link cannot grow it."""

    def __init__(self, limit=REPLAY_LIMIT, skew=SKEW_SECONDS):
        self.limit = limit
        self.skew = skew
        self.seen = OrderedDict()

    def accept(self, request_id, ts, now):
        if abs(int(now) - int(ts)) > self.skew:
            return False
        if request_id in self.seen:
            return False
        self.seen[request_id] = int(now)
        while len(self.seen) > self.limit:
            self.seen.popitem(last=False)
        return True


REPLAY = ReplayWindow()


def new_request(op, pane, text="", now=None):
    if op not in OPS:
        raise ValueError("unsupported")
    if not isinstance(pane, str) or not pane.strip() or len(pane) > 128:
        raise ValueError("pane must be a pane id or a project name")
    if not isinstance(text, str) or len(text.encode("utf-8")) > TEXT_BUDGET:
        raise ValueError("text too long")
    return {
        "v": 1,
        "id": secrets.token_hex(8),
        "op": op,
        "pane": pane.strip(),
        "text": text,
        "ts": int(time.time() if now is None else now),
    }


def validate_request(obj, *, now, replay):
    if not isinstance(obj, dict) or obj.get("v") != 1:
        return None
    request_id = obj.get("id")
    op = obj.get("op")
    pane = obj.get("pane")
    text = obj.get("text", "")
    ts = obj.get("ts")
    if not isinstance(request_id, str) or ID_RE.fullmatch(request_id) is None:
        return None
    if op not in OPS:
        return None
    if not isinstance(pane, str) or not pane.strip() or len(pane) > 128:
        return None
    if not isinstance(text, str) or len(text.encode("utf-8")) > TEXT_BUDGET:
        return None
    # `bool` is an int. A JSON `true` must not pass as a timestamp.
    if isinstance(ts, bool) or not isinstance(ts, int):
        return None
    if not replay.accept(request_id, ts, now):
        return None
    return {"id": request_id, "op": op, "pane": pane.strip(), "text": text, "ts": ts}


def resolve_pane(agents, query):
    """One agent dict, or an error string. Exact pane id wins, then an exact name, then one substring."""
    query = (query or "").strip()
    rows = list(agents or [])
    exact = [row for row in rows if row.get("pane_id") == query]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, "ambiguous agent"
    low = query.lower()
    named = []
    for row in rows:
        fields = [row.get("project") or "", row.get("label") or ""]
        if any(field and field.lower() == low for field in fields):
            named.append(row)
    if len(named) == 1:
        return named[0], None
    if len(named) > 1:
        return None, "ambiguous agent"
    partial = [row for row in rows if low and low in (row.get("project") or "").lower()]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, "ambiguous agent"
    return None, "no matching agent"


def clip_text(text, budget):
    """Keep the tail. A terminal read is newest-at-the-bottom, and that is the part a remote shell shows."""
    raw = (text or "").encode("utf-8")
    if len(raw) <= budget:
        return text or ""
    return raw[-budget:].decode("utf-8", "ignore")


def _ok(request_id, text):
    return {"v": 1, "id": request_id, "ok": True, "text": clip_text(text, TEXT_BUDGET)}


def _fail(request_id, error):
    return {"v": 1, "id": request_id, "ok": False, "error": error}


async def dispatch(text, *, chat_id, key, agents, relay_up, read, prompt, interrupt,
                   now=None, replay=None):
    """A sealed reply, or None when this text is not a request we will answer.

    None is deliberate for garbage and replays: the chat must not gain a new
    message that tells a prober the bytes were inspected.
    """
    if key is None:
        return None
    opened = open_sealed(text, key=key, chat_id=chat_id)
    if opened is None:
        return None
    current = time.time() if now is None else now
    window = REPLAY if replay is None else replay
    request = validate_request(opened, now=current, replay=window)
    if request is None:
        return None
    if not relay_up:
        return seal(_fail(request["id"], "relay disconnected"), key=key, chat_id=chat_id)
    agent, error = resolve_pane(agents, request["pane"])
    if error:
        return seal(_fail(request["id"], error), key=key, chat_id=chat_id)
    pane_id = agent.get("pane_id") or ""
    log.info("link op=%s pane=%s", request["op"], pane_id)
    try:
        if request["op"] == "read":
            body = _ok(request["id"], await read(pane_id))
        elif request["op"] == "prompt":
            if not request["text"].strip():
                body = _fail(request["id"], "missing text")
            else:
                await prompt(pane_id, request["text"])
                body = _ok(request["id"], "sent")
        else:
            await interrupt(pane_id)
            body = _ok(request["id"], "sent")
    except Exception:
        log.warning("link op=%s pane=%s rejected by the relay", request["op"], pane_id)
        body = _fail(request["id"], "relay rejected the command")
    return seal(body, key=key, chat_id=chat_id)
