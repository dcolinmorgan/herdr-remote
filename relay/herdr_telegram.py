#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-telegram-bot>=21.0", "websockets>=14.0"]
# ///
"""herdr-remote Telegram bot — monitor and approve agents from Telegram."""
import asyncio, json, os, logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

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
pending: dict[int, str] = {}  # message_id -> pane_id
agents: list[dict] = []       # current agent list from relay
prev_statuses: dict[str, str] = {}  # pane_id -> last known status
relay_connected = False
send_target: str = ""         # pane_id for next free-text message (set by /send picker)
daily_stats: dict[str, dict] = {}  # pane_id -> {agent, project, blocked_count, working_mins, last_change}


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
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "send_keys", "pane_id": pane_id, "keys": keys}))
    except Exception as e:
        log.warning(f"Failed to send keys to relay: {scrub(e)}")


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


# --- Auth guard ---

def authorized(update: Update) -> bool:
    """Reject messages from unauthorized users."""
    if not CHAT_ID:
        return True  # No restriction if CHAT_ID not set (first-run discovery mode)
    return str(update.effective_chat.id) == CHAT_ID


# --- Bot commands ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "herdr-remote bot\n\n"
        "Commands:\n"
        "/agents — list all agents\n"
        "/status — relay connection info\n"
        "/read — read last output from an agent\n"
        "/send — send text to an agent\n"
        "/trust — trust all tools for a blocked agent\n"
        "/interrupt — send Ctrl+C to an agent\n\n"
        "Pick an agent from the menu, then type — no reply needed.\n"
        "You'll get notified when agents block or finish.\n\n"
        f"Chat ID: {update.effective_chat.id}"
    )


async def cmd_agents(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /agents — list current agents with status."""
    if not agents:
        await update.message.reply_text("No agents connected." if relay_connected else "Not connected to relay.")
        return

    blocked = [a for a in agents if a.get("status") == "blocked"]
    working = [a for a in agents if a.get("status") == "working"]
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
    i = len([a for a in agents if a.get("status") in ("idle", "unknown")])

    status = "Connected" if relay_connected else "Disconnected"
    text = (
        f"Relay: {RELAY_WS_SAFE}\n"
        f"Status: {status}\n"
        f"Agents: {len(agents)} ({b} blocked, {w} working, {i} idle)"
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
        keyboard = [[InlineKeyboardButton(
            f"{a['project']} ({a['agent']})",
            callback_data=json.dumps({"action": "read", "pane_id": a["pane_id"]})
        )] for a in agents[:8]]
        await update.message.reply_text("Read which agent?", reply_markup=InlineKeyboardMarkup(keyboard))
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
    # Store pane_id so user can reply to this message to send text
    pending[msg.message_id] = match["pane_id"]


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
        keyboard = [[InlineKeyboardButton(
            f"{a['project']} ({a['agent']})",
            callback_data=json.dumps({"action": "interrupt", "pane_id": a["pane_id"]})
        )] for a in working[:8]]
        await update.message.reply_text("Interrupt which agent?", reply_markup=InlineKeyboardMarkup(keyboard))
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
        keyboard = [[InlineKeyboardButton(
            f"{a['project']} ({a['agent']})",
            callback_data=json.dumps({"action": "select_send", "pane_id": a["pane_id"]})
        )] for a in agents[:8]]
        await update.message.reply_text("Send to which agent?\n(After selecting, reply with your text)", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    query = args[0].lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'. Use /agents to see list.")
        return

    text = " ".join(args[1:])
    if not text:
        msg = await update.message.reply_text(f"Selected {match['project']}. Reply to this message with text to send.")
        pending[msg.message_id] = match["pane_id"]
        return

    import websockets
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "send_text", "pane_id": match["pane_id"], "text": text}))
            await ws.send(json.dumps({"type": "send_keys", "pane_id": match["pane_id"], "keys": ["Enter"]}))
        await update.message.reply_text(f"Sent to {match['project']}: {text}")
    except Exception as e:
        await update.message.reply_text(f"Failed: {scrub(e)}")


async def cmd_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /reply [project] — show agent output then accept input."""
    global send_target
    args = ctx.args

    if not agents:
        await update.message.reply_text("No agents.")
        return

    if not args:
        keyboard = [[InlineKeyboardButton(
            f"{a['project']} ({a['agent']})",
            callback_data=json.dumps({"action": "select_reply", "pane_id": a["pane_id"]})
        )] for a in agents[:8]]
        await update.message.reply_text("Reply to which agent?", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    query = " ".join(args).lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'.")
        return

    # Show output then set send target
    content = await read_pane(match["pane_id"])
    if len(content) > 3000:
        content = content[-3000:]
    send_target = match["pane_id"]
    await update.message.reply_text(f"{match['project']}:\n\n{content}\n\n--- Type your response below ---")


async def cmd_trust(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /trust [project] — send 'trust, always allow' to a blocked agent."""
    args = ctx.args
    blocked = [a for a in agents if a.get("status") == "blocked"]

    if not blocked:
        await update.message.reply_text("No blocked agents.")
        return

    if not args:
        keyboard = [[InlineKeyboardButton(
            f"{a['project']} ({a['agent']})",
            callback_data=json.dumps({"action": "trust", "pane_id": a["pane_id"]})
        )] for a in blocked[:8]]
        await update.message.reply_text("Trust which agent?", reply_markup=InlineKeyboardMarkup(keyboard))
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

    data = json.loads(query.data)
    action = data.get("action", "respond")

    if action == "read":
        content = await read_pane(data["pane_id"])
        if len(content) > 3500:
            content = content[-3500:]
        msg = await query.message.reply_text(f"{content}")
        pending[msg.message_id] = data["pane_id"]
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
        global send_target
        send_target = data["pane_id"]
        agent_name = next((a['project'] for a in agents if a['pane_id'] == data['pane_id']), '?')
        await query.message.reply_text(f"Ready. Type your message — it will be sent to {agent_name}.")
        return

    if action == "select_reply":
        send_target = data["pane_id"]
        agent_name = next((a['project'] for a in agents if a['pane_id'] == data['pane_id']), '?')
        content = await read_pane(data["pane_id"])
        if len(content) > 3000:
            content = content[-3000:]
        await query.message.reply_text(f"{agent_name}:\n\n{content}\n\n--- Type your response below ---")
        return

    if action == "trust":
        await send_to_relay(data["pane_id"], "trust, always allow")
        agent_name = next((a['project'] for a in agents if a['pane_id'] == data['pane_id']), '?')
        await query.message.reply_text(f"Trusted {agent_name} (always allow)")
        return

    # Default: confirm a blocked agent's prompt by pressing the option number.
    # Sending the option *text* via `respond` does NOT work: the relay pastes it via
    # send-text, and Claude's TUI treats a pasted trailing newline as paste content,
    # not as Enter, so the prompt never gets confirmed. A real key press does.
    pane_id = data["pane_id"]
    key = data.get("k")
    if key is None:  # legacy text buttons: fall back to the old path
        await send_to_relay(pane_id, data.get("response", ""))
        await query.edit_message_reply_markup(reply_markup=None)
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
    await send_keys_to_relay(pane_id, [key])
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Sent: {label}")


# --- Free text reply ---

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send text to an agent. Uses send_target if set, or reply-to-message."""
    global send_target
    
    # Check if there's an active send target (from /send picker)
    pane_id = None
    if send_target:
        pane_id = send_target
        send_target = ""  # one-shot
    elif update.message.reply_to_message:
        orig_id = update.message.reply_to_message.message_id
        pane_id = pending.get(orig_id)
    
    if not pane_id:
        return  # Not a reply and no send target — ignore

    import websockets
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "send_text", "pane_id": pane_id, "text": update.message.text}))
            await ws.send(json.dumps({"type": "send_keys", "pane_id": pane_id, "keys": ["Enter"]}))
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


def make_keyboard(pane_id: str, options: list[str] | None) -> InlineKeyboardMarkup:
    if options and "trust" in " ".join(options).lower():
        buttons = TOOL_BUTTONS
    elif options and "approve all" in " ".join(options).lower():
        buttons = SUBAGENT_BUTTONS
    else:
        buttons = [(opt.split(",")[0], opt) for opt in (options or ["yes, single permission", "no (tab to edit)"])]

    # Encode the option's 1-based position as "k"; the callback presses that number
    # key on the agent's prompt. Sending the option *text* does not work (see handle_callback).
    # callback_data must stay under Telegram's 64-byte limit, so keep it to the
    # pane and the option number; the confirmation label is recovered from the
    # keyboard on press (see handle_callback).
    keyboard = [
        [InlineKeyboardButton(label, callback_data=json.dumps(
            {"pane_id": pane_id, "k": str(i + 1)}, separators=(",", ":")))]
        for i, (label, _resp) in enumerate(buttons)
    ]
    return InlineKeyboardMarkup(keyboard)


async def notify_blocked(app: Application, pane_id: str, agent: str, project: str, prompt: str, options: list[str] | None):
    if not CHAT_ID:
        return
    text = f"*{agent}* blocked in `{project}`\n\n```\n{prompt[:400]}\n```"
    keyboard = make_keyboard(pane_id, options)
    msg = await app.bot.send_message(
        chat_id=int(CHAT_ID), text=text, parse_mode="Markdown", reply_markup=keyboard
    )
    pending[msg.message_id] = pane_id


# --- Relay listener ---

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
                        # Detect status transitions
                        if CHAT_ID:
                            import time
                            now = time.time()
                            for a in new_agents:
                                pid = a["pane_id"]
                                new_status = a.get("status", "unknown")
                                old_status = prev_statuses.get(pid)

                                # Update daily stats
                                if pid not in daily_stats:
                                    daily_stats[pid] = {"agent": a.get("agent",""), "project": a.get("project",""), "blocked_count": 0, "working_mins": 0, "last_change": now}
                                ds = daily_stats[pid]
                                if old_status == "working" and old_status != new_status:
                                    elapsed = (now - ds["last_change"]) / 60
                                    ds["working_mins"] += int(elapsed)
                                if new_status == "blocked" and old_status != "blocked":
                                    ds["blocked_count"] += 1
                                if old_status != new_status:
                                    ds["last_change"] = now

                                if old_status and old_status != new_status:
                                    if new_status == "idle" and old_status in ("working", "blocked"):
                                        try:
                                            await app.bot.send_message(
                                                chat_id=int(CHAT_ID),
                                                text=f"{a['project']} ({a['agent']}) finished."
                                            )
                                        except Exception:
                                            pass
                                prev_statuses[pid] = new_status
                        agents = new_agents

                    elif msg.get("type") == "blocked":
                        await notify_blocked(
                            app,
                            pane_id=msg["pane_id"],
                            agent=msg.get("agent", "unknown"),
                            project=msg.get("project", ""),
                            prompt=msg.get("prompt", ""),
                            options=msg.get("options"),
                        )
        except Exception as e:
            relay_connected = False
            log.warning(f"Relay connection lost: {e}, reconnecting in 5s...")
            await asyncio.sleep(5)


# --- Main ---

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    if CHAT_ID:
        auth_filter = filters.Chat(chat_id=int(CHAT_ID))
    else:
        auth_filter = filters.ALL  # No restriction in discovery mode

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
