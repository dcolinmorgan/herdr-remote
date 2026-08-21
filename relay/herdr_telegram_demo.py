#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-telegram-bot>=21.0", "websockets>=14.0"]
# ///
"""herdr-remote DEMO Telegram bot — connects to the public demo relay."""
import asyncio, json, os, logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("herdr-demo-tg")

TOKEN = os.environ.get("HERDR_DEMO_TG_TOKEN", "")
DEMO_RELAY = "wss://herdr-remote-demo.yyrzrh5wfg.workers.dev"
PANE_READ_TIMEOUT = 15  # was 5 messages * 3s; kept as one deadline instead

if not TOKEN:
    print("Set HERDR_DEMO_TG_TOKEN")
    exit(1)

agents: list[dict] = []
relay_connected = False


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "herdr-remote demo bot\n\n"
        "This bot shows a live preview of herdr-remote.\n"
        "Agents are simulated — try the commands:\n\n"
        "/agents — list demo agents\n"
        "/read — read agent output\n"
        "/trust — approve a blocked agent\n\n"
        "Real version: github.com/dcolinmorgan/herdr-remote"
    )


async def cmd_agents(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not agents:
        await update.message.reply_text("Connecting to demo..." if not relay_connected else "No agents.")
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


async def cmd_read(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not agents:
        await update.message.reply_text("No agents.")
        return
    keyboard = [[InlineKeyboardButton(
        f"{a['project']} ({a['agent']})",
        callback_data=json.dumps({"action": "read", "pane_id": a["pane_id"]})
    )] for a in agents[:6]]
    await update.message.reply_text("Read which agent?", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_trust(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    blocked = [a for a in agents if a.get("status") == "blocked"]
    if not blocked:
        await update.message.reply_text("No blocked agents right now. Wait a moment — agents randomly block in the demo.")
        return
    keyboard = [[InlineKeyboardButton(
        f"{a['project']} ({a['agent']})",
        callback_data=json.dumps({"action": "trust", "pane_id": a["pane_id"]})
    )] for a in blocked[:6]]
    await update.message.reply_text("Trust which agent?", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = json.loads(query.data)

    if data.get("action") == "read":
        import websockets
        try:
            async with websockets.connect(DEMO_RELAY) as ws:
                await ws.send(json.dumps({"type": "read_pane", "pane_id": data["pane_id"]}))
                loop = asyncio.get_running_loop()
                deadline = loop.time() + PANE_READ_TIMEOUT
                # The connect preamble (sessions/agents/one-blocked-per-blocked-
                # agent) can put any number of unrelated messages ahead of
                # pane_content. Skip by deadline, not by a fixed message count
                # -- same fix as herdr_telegram.py's read_pane; this relay
                # happens to never emit `sessions` today, but a fixed count
                # is one relay change away from breaking here too.
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    msg = json.loads(raw)
                    if msg.get("type") == "pane_content":
                        await query.message.reply_text(msg.get("content", "(empty)"))
                        return
            await query.message.reply_text("(demo read timeout)")
        except:
            await query.message.reply_text("(demo read timeout)")
        return

    if data.get("action") == "trust":
        import websockets
        try:
            async with websockets.connect(DEMO_RELAY) as ws:
                await ws.send(json.dumps({"type": "respond", "pane_id": data["pane_id"], "text": "trust, always allow"}))
        except:
            pass
        agent_name = next((a['project'] for a in agents if a['pane_id'] == data['pane_id']), '?')
        await query.message.reply_text(f"Trusted {agent_name} (demo)")
        return


async def relay_listener(app: Application):
    import websockets
    global agents, relay_connected
    while True:
        try:
            async with websockets.connect(DEMO_RELAY) as ws:
                relay_connected = True
                log.info(f"Connected to demo relay")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except:
                        continue
                    if msg.get("type") == "agents":
                        agents = msg.get("agents", [])
        except Exception as e:
            relay_connected = False
            await asyncio.sleep(5)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("read", cmd_read))
    app.add_handler(CommandHandler("trust", cmd_trust))
    app.add_handler(CallbackQueryHandler(handle_callback))

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
