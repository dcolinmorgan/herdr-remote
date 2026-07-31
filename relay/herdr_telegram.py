#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-telegram-bot>=21.0", "websockets>=14.0"]
# ///
"""herdr-remote Telegram bot — monitor and approve agents from Telegram."""
import asyncio, hashlib, json, os, logging, secrets
from collections import Counter, OrderedDict

from telegram import ForceReply, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

from agent_state import apply_agent_message

logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO; that URL contains the bot token. Silence it.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("herdr-tg")

TOKEN = os.environ.get("HERDR_TG_TOKEN", "")
CHAT_ID = os.environ.get("HERDR_TG_CHAT_ID", "")
RELAY_WS = os.environ.get("HERDR_RELAY", "ws://127.0.0.1:8375")
RELAY_WS_SAFE = RELAY_WS.split("?", 1)[0]  # token-free variant for display and logging; never leak the token
_RELAY_TOKEN = RELAY_WS.split("token=", 1)[1] if "token=" in RELAY_WS else ""


def scrub(value) -> str:
    """Strip secrets from any string before it is logged or sent to Telegram.

    WebSocket exceptions (e.g. InvalidURI) embed the full relay URL incl. the
    ?token= query, so raw exception text must never be surfaced unredacted.
    """
    s = str(value)
    for secret in (_RELAY_TOKEN, TOKEN):
        if secret:
            s = s.replace(secret, "<redacted>")
    return s

if not TOKEN:
    print("Set HERDR_TG_TOKEN (from @BotFather)")
    exit(1)

# State
pending: OrderedDict[tuple[int, int], str] = OrderedDict()  # (chat_id, message_id) -> pane_id
approval_tokens: dict[str, str] = {}  # pane_id -> current blocked-notification generation
agents: list[dict] = []       # current agent list from relay
prev_statuses: dict[str, str] = {}  # pane_id -> last known status
relay_connected = False
daily_stats: dict[str, dict] = {}  # pane_id -> {agent, project, blocked_count, working_mins, last_change}

AGENT_PAGE_SIZE = 20
PENDING_LIMIT = 500
STATUS_ORDER = {"blocked": 0, "working": 1, "done": 2, "idle": 3, "unknown": 3}
STATUS_LABELS = {"blocked": "BLOCKED", "working": "WORKING", "done": "DONE", "idle": "IDLE", "unknown": "IDLE"}
ACTION_CODES = {
    "read": "r",
    "interrupt": "i",
    "select_send": "s",
    "select_reply": "q",
    "trust": "t",
    "approval": "k",
}
CODE_ACTIONS = {code: action for action, code in ACTION_CODES.items()}


# --- Relay communication ---

async def send_to_relay(pane_id: str, text: str):
    """Send a response to the relay via WebSocket."""
    import websockets
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "respond", "pane_id": pane_id, "text": text}))
    except Exception as e:
        log.warning(f"Failed to send to relay: {scrub(e)}")


async def send_keys_to_relay(pane_id: str, keys: list[str]):
    """Send raw key presses to the relay via WebSocket (e.g. ["1"] to pick a prompt option)."""
    import websockets
    async with websockets.connect(RELAY_WS) as ws:
        await ws.send(json.dumps({"type": "send_keys", "pane_id": pane_id, "keys": keys}))
        for _ in range(5):
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            response = json.loads(raw)
            if response.get("type") == "error":
                raise RuntimeError(response.get("message", "relay rejected keys"))
            if response.get("type") == "command_result" and response.get("command") == "send_keys":
                if not response.get("ok"):
                    raise RuntimeError(response.get("message", "relay rejected keys"))
                return
        raise RuntimeError("relay did not acknowledge keys")


async def read_pane(pane_id: str, lines: int = 15) -> str:
    """Read pane content from relay."""
    import websockets
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "read_pane", "pane_id": pane_id, "lines": lines}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            # Might get an agents broadcast first, skip to pane_content
            for _ in range(5):
                if msg.get("type") == "pane_content":
                    return msg.get("content", "(empty)")
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                msg = json.loads(raw)
    except Exception as e:
        return f"(error reading pane: {scrub(e)})"
    return "(no response)"


async def send_text_to_relay(pane_id: str, text: str):
    """Send text followed by Enter to a pane."""
    if not text or len(text) > 1000:
        raise ValueError("text must contain 1-1000 characters")
    import websockets
    async with websockets.connect(RELAY_WS) as ws:
        await ws.send(json.dumps({"type": "send_text", "pane_id": pane_id, "text": text}))
        await ws.send(json.dumps({"type": "send_keys", "pane_id": pane_id, "keys": ["Enter"]}))


# --- Auth guard ---

def authorized(update: Update) -> bool:
    """Reject messages from unauthorized users."""
    if not CHAT_ID:
        return True  # No restriction if CHAT_ID not set (first-run discovery mode)
    return str(update.effective_chat.id) == CHAT_ID


def register_pending(chat_id: int, message_id: int, pane_id: str):
    key = (int(chat_id), int(message_id))
    pending[key] = pane_id
    pending.move_to_end(key)
    while len(pending) > PENDING_LIMIT:
        pending.popitem(last=False)


def pending_pane(chat_id: int, message_id: int) -> str | None:
    return pending.get((int(chat_id), int(message_id)))


def find_agent(pane_id: str) -> dict | None:
    matches = [agent for agent in agents if agent.get("pane_id") == pane_id]
    return matches[0] if len(matches) == 1 else None


def clear_relay_connection_state():
    global agents, relay_connected
    relay_connected = False
    agents = []
    approval_tokens.clear()


async def send_reply_prompt(message, chat, agent: dict, content: str | None = None):
    project = agent.get("project") or agent.get("agent") or "agent"
    parts = [f"{project}:"]
    if content is not None:
        parts.extend(["", content])
    parts.extend(["", "Reply to this message to send your response."])
    kwargs = {}
    if getattr(chat, "type", None) == "private":
        kwargs["reply_markup"] = ForceReply(
            selective=True,
            input_field_placeholder=f"Reply to {project}"[:64],
        )
    sent = await message.reply_text("\n".join(parts), **kwargs)
    register_pending(chat.id, sent.message_id, agent["pane_id"])
    return sent


def agents_for_action(action: str) -> list[dict]:
    if action == "interrupt":
        return [agent for agent in agents if agent.get("status") in ("working", "blocked")]
    if action == "trust":
        return [agent for agent in agents if agent.get("status") == "blocked"]
    return list(agents)


def sorted_agents(agent_list: list[dict]) -> list[dict]:
    return sorted(agent_list, key=lambda agent: (
        STATUS_ORDER.get(agent.get("status", "unknown"), 3),
        agent.get("project", "").lower(),
        agent.get("agent", "").lower(),
        agent.get("host", "local").lower(),
        agent.get("pane_id", ""),
    ))


def compact_identifier(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = hashlib.sha1(value.encode()).hexdigest()[:12]
    return value[:limit - 15] + "..." + digest


def pane_callback_token(pane_id: str) -> str:
    return hashlib.sha256(pane_id.encode()).hexdigest()[:16]


def pane_callback_data(action: str, pane_id: str, **extra) -> str:
    data = {"a": ACTION_CODES[action], "p": pane_callback_token(pane_id), **extra}
    return json.dumps(data, separators=(",", ":"))


def resolve_pane_token(token: str) -> str | None:
    agent_matches = [
        agent.get("pane_id") for agent in agents
        if agent.get("pane_id") and pane_callback_token(agent["pane_id"]) == token
    ]
    if len(agent_matches) == 1:
        return agent_matches[0]
    if len(agent_matches) > 1:
        return None
    pending_matches = {
        pane_id for pane_id in pending.values()
        if pane_id and pane_callback_token(pane_id) == token
    }
    return next(iter(pending_matches)) if len(pending_matches) == 1 else None


def parse_callback_data(raw: str) -> dict:
    data = json.loads(raw)
    if "a" in data:
        data["action"] = CODE_ACTIONS.get(data["a"], "invalid")
    if "p" in data:
        data["pane_id"] = resolve_pane_token(data["p"])
    return data


def agent_button_labels(agent_list: list[dict]) -> list[str]:
    bases = []
    contexts = []
    for agent in agent_list:
        status = STATUS_LABELS.get(agent.get("status", "unknown"), "UNKNOWN")
        project = agent.get("project") or agent.get("cwd") or "unknown"
        name = agent.get("agent") or "agent"
        host = agent.get("host", "local")
        bases.append(f"[{status}] {project} ({name})")
        contexts.append(f" @{compact_identifier(host, 24)}" if host != "local" else "")

    provisional = [base[:max(1, 64 - len(context))] + context for base, context in zip(bases, contexts)]
    counts = Counter(provisional)
    labels = []
    for agent, base, context, candidate in zip(agent_list, bases, contexts, provisional):
        if counts[candidate] == 1:
            labels.append(candidate)
            continue
        pane_id = compact_identifier(agent.get("pane_id", "?"), 18)
        suffix = context + f" [{pane_id}]"
        labels.append(base[:max(1, 64 - len(suffix))] + suffix)
    unique_labels = []
    used = set()
    for label in labels:
        candidate = label
        ordinal = 2
        while candidate in used:
            marker = f" #{ordinal}"
            candidate = label[:64 - len(marker)] + marker
            ordinal += 1
        used.add(candidate)
        unique_labels.append(candidate)
    return unique_labels


def build_agent_keyboard(action: str, page: int = 0, agent_list: list[dict] | None = None) -> InlineKeyboardMarkup:
    ordered = sorted_agents(agents_for_action(action) if agent_list is None else agent_list)
    page_count = max(1, (len(ordered) + AGENT_PAGE_SIZE - 1) // AGENT_PAGE_SIZE)
    page = min(max(page, 0), page_count - 1)
    start = page * AGENT_PAGE_SIZE
    visible = ordered[start:start + AGENT_PAGE_SIZE]
    labels = agent_button_labels(ordered)[start:start + AGENT_PAGE_SIZE]
    keyboard = [[InlineKeyboardButton(
        label,
        callback_data=pane_callback_data(action, agent["pane_id"]),
    )] for agent, label in zip(visible, labels)]

    if page_count > 1:
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton(
                "Previous",
                callback_data=json.dumps(
                    {"action": "page", "menu": action, "page": page - 1}, separators=(",", ":")
                ),
            ))
        if page + 1 < page_count:
            navigation.append(InlineKeyboardButton(
                "Next",
                callback_data=json.dumps(
                    {"action": "page", "menu": action, "page": page + 1}, separators=(",", ":")
                ),
            ))
        keyboard.append(navigation)
    return InlineKeyboardMarkup(keyboard)


def refresh_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Refresh agents",
            callback_data=json.dumps({"action": "page", "menu": action, "page": 0}, separators=(",", ":")),
        )
    ]])


def dashboard_text() -> str:
    if not relay_connected:
        text = "herdr-remote bot\n\nRelay disconnected. Use /status for connection details."
    elif not agents:
        text = "herdr-remote bot\n\nConnected to relay. No agents are running."
    else:
        blocked = sum(agent.get("status") == "blocked" for agent in agents)
        working = sum(agent.get("status") == "working" for agent in agents)
        done = sum(agent.get("status") == "done" for agent in agents)
        idle = len(agents) - blocked - working - done
        text = f"herdr-remote bot\n\nAgents: {len(agents)} ({blocked} blocked, {working} working, {done} done, {idle} idle)\nSelect an agent to read and reply."
    if not CHAT_ID:
        text += "\n\nChat ID: {chat_id}"
    return text


# --- Bot commands ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not authorized(update):
        return
    text = dashboard_text()
    if not CHAT_ID:
        text = text.format(chat_id=update.effective_chat.id)
    if relay_connected and agents:
        await update.message.reply_text(text, reply_markup=build_agent_keyboard("select_reply"))
        return
    await update.message.reply_text(text)


async def cmd_agents(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /agents — list current agents with status."""
    if not agents:
        await update.message.reply_text("No agents connected." if relay_connected else "Not connected to relay.")
        return

    blocked = [a for a in agents if a.get("status") == "blocked"]
    working = [a for a in agents if a.get("status") == "working"]
    done = [a for a in agents if a.get("status") == "done"]
    idle = [a for a in agents if a.get("status") in ("idle", "unknown")]

    lines = []
    if blocked:
        lines.append("BLOCKED:")
        for a in blocked:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")
    if working:
        lines.append("WORKING:")
        for a in working:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")
    if done:
        lines.append("DONE:")
        for a in done:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")
    if idle:
        lines.append("IDLE:")
        for a in idle:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")

    await update.message.reply_text("\n".join(lines))


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /status — show connection info."""
    b = len([a for a in agents if a.get("status") == "blocked"])
    w = len([a for a in agents if a.get("status") == "working"])
    d = len([a for a in agents if a.get("status") == "done"])
    i = len([a for a in agents if a.get("status") in ("idle", "unknown")])

    status = "Connected" if relay_connected else "Disconnected"
    text = (
        f"Relay: {RELAY_WS_SAFE}\n"
        f"Status: {status}\n"
        f"Agents: {len(agents)} ({b} blocked, {w} working, {d} done, {i} idle)"
    )
    await update.message.reply_text(text)


async def cmd_read(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /read [project] — read pane output."""
    args = ctx.args
    if not args:
        # Show agent picker
        if not agents:
            await update.message.reply_text("No agents. Use /agents to check.")
            return
        await update.message.reply_text("Read which agent?", reply_markup=build_agent_keyboard("read"))
        return

    # Find agent by project name
    query = " ".join(args).lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'. Use /agents to see list.")
        return

    content = await read_pane(match["pane_id"])
    if len(content) > 3500:
        content = content[-3500:]
    msg = await update.message.reply_text(f"{match['project']}:\n\n{content}")
    register_pending(update.effective_chat.id, msg.message_id, match["pane_id"])


async def cmd_interrupt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /interrupt [project] — send Ctrl+C."""
    args = ctx.args
    if not args:
        if not agents:
            await update.message.reply_text("No agents.")
            return
        working = [a for a in agents if a.get("status") in ("working", "blocked")]
        if not working:
            await update.message.reply_text("No active agents to interrupt.")
            return
        await update.message.reply_text(
            "Interrupt which agent?",
            reply_markup=build_agent_keyboard("interrupt", agent_list=working),
        )
        return

    query = " ".join(args).lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'.")
        return

    import websockets
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "send_keys", "pane_id": match["pane_id"], "keys": ["Ctrl+c"]}))
        await update.message.reply_text(f"Sent Ctrl+C to {match['project']}")
    except Exception as e:
        await update.message.reply_text(f"Failed: {scrub(e)}")


async def cmd_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /send [project] [text] — send text + Enter to a pane."""
    args = ctx.args
    if not args:
        if not agents:
            await update.message.reply_text("No agents.")
            return
        await update.message.reply_text(
            "Send to which agent?\n(After selecting, reply with your text)",
            reply_markup=build_agent_keyboard("select_send"),
        )
        return

    query = args[0].lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'. Use /agents to see list.")
        return

    text = " ".join(args[1:])
    if not text:
        await send_reply_prompt(update.message, update.effective_chat, match)
        return

    try:
        await send_text_to_relay(match["pane_id"], text)
        await update.message.reply_text(f"Sent to {match['project']}: {text}")
    except Exception as e:
        await update.message.reply_text(f"Failed: {scrub(e)}")


async def cmd_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /reply [project] — show agent output then accept input."""
    args = ctx.args

    if not agents:
        await update.message.reply_text("No agents.")
        return

    if not args:
        await update.message.reply_text("Reply to which agent?", reply_markup=build_agent_keyboard("select_reply"))
        return

    query = " ".join(args).lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'.")
        return

    content = await read_pane(match["pane_id"])
    if len(content) > 3000:
        content = content[-3000:]
    await send_reply_prompt(update.message, update.effective_chat, match, content)


async def cmd_trust(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /trust [project] — send 'trust, always allow' to a blocked agent."""
    args = ctx.args
    blocked = [a for a in agents if a.get("status") == "blocked"]

    if not blocked:
        await update.message.reply_text("No blocked agents.")
        return

    if not args:
        await update.message.reply_text(
            "Trust which agent?",
            reply_markup=build_agent_keyboard("trust", agent_list=blocked),
        )
        return

    query = " ".join(args).lower()
    match = next((a for a in blocked if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No blocked agent matching '{query}'.")
        return

    await send_to_relay(match["pane_id"], "trust, always allow")
    await update.message.reply_text(f"Trusted {match['project']} (always allow)")


async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /digest — show today's agent activity summary."""
    if not daily_stats:
        await update.message.reply_text("No activity recorded yet today.")
        return

    import time
    lines = ["Today's activity:\n"]
    sorted_agents = sorted(daily_stats.values(), key=lambda x: x.get("working_mins", 0), reverse=True)
    for s in sorted_agents:
        blocked = f", blocked {s['blocked_count']}x" if s.get("blocked_count") else ""
        mins = s.get("working_mins", 0)
        time_str = f"{mins}m" if mins < 60 else f"{mins//60}h{mins%60}m"
        lines.append(f"  {s['project']} ({s['agent']}): {time_str} working{blocked}")

    await update.message.reply_text("\n".join(lines))


# --- Callback handler (buttons) ---

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    if CHAT_ID and str(update.effective_chat.id) != CHAT_ID:
        await query.answer("Unauthorized")
        return
    await query.answer()

    if not relay_connected:
        await query.message.reply_text("Relay disconnected. Use /start after it reconnects.")
        return

    data = parse_callback_data(query.data)
    action = data.get("action", "approval")

    if action == "page":
        menu = data.get("menu", "select_reply")
        if not agents_for_action(menu):
            await query.message.reply_text("No eligible agents are available.")
            return
        await query.edit_message_reply_markup(
            reply_markup=build_agent_keyboard(menu, page=data.get("page", 0))
        )
        return

    selected_agent = None
    if action in {"read", "interrupt", "select_send", "select_reply", "trust", "approval"}:
        eligible_action = "trust" if action == "approval" else action
        selected_agent = next(
            (agent for agent in agents_for_action(eligible_action) if agent.get("pane_id") == data.get("pane_id")),
            None,
        )
        if selected_agent is None:
            await query.message.reply_text(
                "That agent is no longer available for this action. Refresh the list and choose another agent.",
                reply_markup=refresh_keyboard(eligible_action),
            )
            return

    if action == "read":
        content = await read_pane(data["pane_id"])
        if len(content) > 3500:
            content = content[-3500:]
        msg = await query.message.reply_text(f"{content}")
        register_pending(update.effective_chat.id, msg.message_id, data["pane_id"])
        return

    if action == "interrupt":
        import websockets
        try:
            async with websockets.connect(RELAY_WS) as ws:
                await ws.send(json.dumps({"type": "send_keys", "pane_id": data["pane_id"], "keys": ["Ctrl+c"]}))
            await query.message.reply_text("Sent Ctrl+C")
        except Exception as e:
            await query.message.reply_text(f"Failed: {scrub(e)}")
        return

    if action == "select_send":
        await send_reply_prompt(query.message, update.effective_chat, selected_agent)
        return

    if action == "select_reply":
        content = await read_pane(data["pane_id"])
        if len(content) > 3000:
            content = content[-3000:]
        await send_reply_prompt(query.message, update.effective_chat, selected_agent, content)
        return

    if action == "trust":
        await send_to_relay(data["pane_id"], "trust, always allow")
        await query.message.reply_text(f"Trusted {selected_agent['project']} (always allow)")
        return

    if action != "approval":
        await query.message.reply_text("That action is no longer valid. Refresh the list and try again.")
        return

    pane_id = data["pane_id"]
    if not data.get("g") or data["g"] != approval_tokens.get(pane_id):
        await query.message.reply_text(
            "That approval belongs to an older prompt. Use the controls on the latest blocked notification."
        )
        return

    # Confirm a blocked agent's prompt by pressing the option number.
    # Sending the option *text* via `respond` does NOT work: the relay pastes it via
    # send-text, and Claude's TUI treats a pasted trailing newline as paste content,
    # not as Enter, so the prompt never gets confirmed. A real key press does.
    key = data.get("k")
    if key is None:
        await query.message.reply_text("That approval action is no longer supported. Use the latest notification.")
        return
    # Recover the pressed button's label for the confirmation (kept out of
    # callback_data to respect Telegram's 64-byte limit).
    label = f"option {key}"
    if query.message and query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                try:
                    if json.loads(btn.callback_data).get("k") == key:
                        label = btn.text
                except (ValueError, TypeError):
                    pass
    try:
        await send_keys_to_relay(pane_id, [key])
    except Exception as e:
        await query.message.reply_text(f"Failed: {scrub(e)}")
        return
    approval_tokens.pop(pane_id, None)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Sent: {label}")


# --- Free text reply ---

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send a Telegram reply to its associated agent pane."""
    if not authorized(update) or not update.message.reply_to_message:
        return
    pane_id = pending_pane(update.effective_chat.id, update.message.reply_to_message.message_id)
    if not pane_id:
        return
    if not relay_connected:
        await update.message.reply_text("Relay disconnected. Use /start after it reconnects.")
        return
    if find_agent(pane_id) is None:
        await update.message.reply_text(
            "That agent is no longer available. Refresh the list and choose another agent.",
            reply_markup=refresh_keyboard("select_reply"),
        )
        return

    try:
        await send_text_to_relay(pane_id, update.message.text)
        await update.message.reply_text("Sent")
    except Exception as e:
        await update.message.reply_text(f"Failed: {scrub(e)}")


# --- Blocked notification ---

TOOL_BUTTONS = [
    ("Yes (once)", "yes, single permission"),
    ("Trust (always)", "trust, always allow"),
    ("No", "no (tab to edit)"),
]

SUBAGENT_BUTTONS = [
    ("Approve all", "approve all pending"),
    ("Configure", "configure individually"),
    ("Cancel", "exit (cancel subagents)"),
]


def make_keyboard(
    pane_id: str,
    options: list[str] | None,
    generation: str | None = None,
) -> InlineKeyboardMarkup:
    if options and "trust" in " ".join(options).lower():
        buttons = TOOL_BUTTONS
    elif options and "approve all" in " ".join(options).lower():
        buttons = SUBAGENT_BUTTONS
    else:
        buttons = [(opt.split(",")[0], opt) for opt in (options or ["yes, single permission", "no (tab to edit)"])]

    # Encode the option's 1-based position as "k"; the callback presses that number
    # key on the agent's prompt. Sending the option *text* does not work (see handle_callback).
    # callback_data must stay under Telegram's 64-byte limit, so keep it to the
    # pane token and the option number; the confirmation label is recovered from the
    # keyboard on press (see handle_callback).
    generation = generation or secrets.token_hex(4)
    keyboard = [
        [InlineKeyboardButton(label, callback_data=pane_callback_data(
            "approval", pane_id, g=generation, k=str(i + 1)))]
        for i, (label, _resp) in enumerate(buttons)
    ]
    keyboard.append([InlineKeyboardButton(
        "Open output & reply",
        callback_data=pane_callback_data("select_reply", pane_id),
    )])
    return InlineKeyboardMarkup(keyboard)


def interaction_keyboard(pane_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Open output & reply",
            callback_data=pane_callback_data("select_reply", pane_id),
        )
    ]])


async def notify_blocked(app: Application, pane_id: str, agent: str, project: str, prompt: str, options: list[str] | None):
    if not CHAT_ID:
        return
    text = (
        f"*{agent}* blocked in `{project}`\n\n```\n{prompt[:400]}\n```\n\n"
        "Use an approval button, open the output, or reply to this notification."
    )
    generation = secrets.token_hex(4)
    keyboard = make_keyboard(pane_id, options, generation=generation)
    msg = await app.bot.send_message(
        chat_id=int(CHAT_ID), text=text, parse_mode="Markdown", reply_markup=keyboard
    )
    approval_tokens[pane_id] = generation
    register_pending(int(CHAT_ID), msg.message_id, pane_id)


async def notify_blocked_safely(app: Application, msg: dict):
    try:
        await notify_blocked(
            app,
            pane_id=msg["pane_id"],
            agent=msg.get("agent", "unknown"),
            project=msg.get("project", ""),
            prompt=msg.get("prompt", ""),
            options=msg.get("options"),
        )
    except Exception as e:
        log.warning("Failed to send blocked notification: %s", scrub(e))


# --- Relay listener ---

async def track_agent_updates(app: Application, updated_agents: list[dict]):
    """Track status transitions for full snapshots and pane-level updates."""
    if not CHAT_ID:
        return

    import time
    now = time.time()
    for agent_data in updated_agents:
        pane_id = agent_data["pane_id"]
        new_status = agent_data.get("status", "unknown")
        old_status = prev_statuses.get(pane_id)

        if pane_id not in daily_stats:
            daily_stats[pane_id] = {
                "agent": agent_data.get("agent", ""),
                "project": agent_data.get("project", ""),
                "blocked_count": 0,
                "working_mins": 0,
                "last_change": now,
            }
        stats = daily_stats[pane_id]
        if old_status == "working" and old_status != new_status:
            stats["working_mins"] += int((now - stats["last_change"]) / 60)
        if new_status == "blocked" and old_status != "blocked":
            stats["blocked_count"] += 1
        if old_status != new_status:
            stats["last_change"] = now
        if agent_data.get("status") and new_status != "blocked":
            approval_tokens.pop(pane_id, None)

        if old_status and old_status != new_status and new_status in ("idle", "done") and old_status in ("working", "blocked"):
            try:
                msg = await app.bot.send_message(
                    chat_id=int(CHAT_ID),
                    text=(
                        f"{agent_data['project']} ({agent_data['agent']}) finished.\n\n"
                        "Open the output below, or reply to this notification to send a follow-up."
                    ),
                    reply_markup=interaction_keyboard(pane_id),
                )
                register_pending(int(CHAT_ID), msg.message_id, pane_id)
            except Exception as e:
                log.warning("Failed to send completion notification: %s", scrub(e))
        prev_statuses[pane_id] = new_status


async def relay_listener(app: Application):
    """Persistent WebSocket connection to relay."""
    import websockets
    global agents, relay_connected, prev_statuses

    while True:
        try:
            async with websockets.connect(RELAY_WS) as ws:
                relay_connected = True
                log.info(f"Connected to relay at {RELAY_WS_SAFE}")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") == "agents":
                        new_agents = msg.get("agents", [])
                        blocked_panes = {
                            agent.get("pane_id") for agent in new_agents
                            if agent.get("status") == "blocked"
                        }
                        for pane_id in list(approval_tokens):
                            if pane_id not in blocked_panes:
                                approval_tokens.pop(pane_id, None)
                        await track_agent_updates(app, new_agents)
                        agents = apply_agent_message(agents, msg)

                    elif msg.get("type") == "agent_update":
                        updated_agent = msg.get("agent") or {}
                        if updated_agent.get("pane_id"):
                            await track_agent_updates(app, [updated_agent])
                            agents = apply_agent_message(agents, msg)

                    elif msg.get("type") == "blocked":
                        await notify_blocked_safely(app, msg)
                clear_relay_connection_state()
        except Exception as e:
            clear_relay_connection_state()
            log.warning(f"Relay connection lost: {e}, reconnecting in 5s...")
            await asyncio.sleep(5)


# --- Main ---

def main():
    app = Application.builder().token(TOKEN).build()
    if CHAT_ID:
        auth_filter = filters.Chat(chat_id=int(CHAT_ID))
    else:
        auth_filter = filters.ALL  # No restriction in discovery mode

    app.add_handler(CommandHandler("start", cmd_start, filters=auth_filter))
    app.add_handler(CommandHandler("agents", cmd_agents, filters=auth_filter))
    app.add_handler(CommandHandler("status", cmd_status, filters=auth_filter))
    app.add_handler(CommandHandler("read", cmd_read, filters=auth_filter))
    app.add_handler(CommandHandler("reply", cmd_reply, filters=auth_filter))
    app.add_handler(CommandHandler("send", cmd_send, filters=auth_filter))
    app.add_handler(CommandHandler("trust", cmd_trust, filters=auth_filter))
    app.add_handler(CommandHandler("digest", cmd_digest, filters=auth_filter))
    app.add_handler(CommandHandler("interrupt", cmd_interrupt, filters=auth_filter))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & auth_filter, handle_text))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run():
        async with app:
            await app.start()
            await app.updater.start_polling()
            await relay_listener(app)

    loop.run_until_complete(run())


if __name__ == "__main__":
    main()
