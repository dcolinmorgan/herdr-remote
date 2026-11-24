#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["cryptography>=43", "telethon>=1.36"]
# ///
"""Drive a herdr session on another computer through Telegram, without SSH.

The herdr machine runs the existing bot (`herdr_telegram.py`) with
`HERDR_TG_LINK_KEY` and `HERDR_TG_CHAT_ID` set. This process runs on the other
computer, logged in as a Telegram user. It never sees the bot token.

    uv run relay/herdr_tg.py keygen
    uv run relay/herdr_tg.py login
    uv run relay/herdr_tg.py read my-project
    uv run relay/herdr_tg.py send my-project "run the tests"
    uv run relay/herdr_tg.py interrupt my-project
    uv run relay/herdr_tg.py shell my-project

`send` submits an agent prompt, the same path as the bot's `/send`. There is
no PTY: a full-screen program and a live stream of logs are out of scope.
"""
import asyncio
import os
from pathlib import Path
import sys
import time

import tg_link

HELP = """usage: herdr-tg <command>

  keygen                     print a new HERDR_TG_LINK_KEY
  login                      one-time Telegram user login on this computer
  read <pane>                print recent output
  send <pane> <text>         submit an agent prompt
  interrupt <pane>           send Ctrl+C
  shell <pane>               prompt, send, print, until exit

Environment, on this computer: HERDR_TG_LINK_KEY, HERDR_TG_CHAT_ID,
HERDR_TG_API_ID, HERDR_TG_API_HASH, and HERDR_TG_BOT (the bot's username).
The bot token stays on the herdr machine.
"""

SESSION_NAME = "tg-link"
REPLY_TIMEOUT = 20


def config_dir():
    override = os.environ.get("HERDR_TG_SESSION_DIR", "").strip()
    path = Path(override) if override else Path.home() / ".config" / "herdr-remote"
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return path


def session_base():
    return str(config_dir() / SESSION_NAME)


def _required(names):
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit("Set " + ", ".join(missing) + " before using the link.")


def telegram_app():
    _required(("HERDR_TG_API_ID", "HERDR_TG_API_HASH"))
    try:
        api_id = int(os.environ["HERDR_TG_API_ID"])
    except ValueError as exc:
        raise SystemExit("HERDR_TG_API_ID must be the numeric id from my.telegram.org.") from exc
    return api_id, os.environ["HERDR_TG_API_HASH"].strip()


def link_settings():
    _required(("HERDR_TG_LINK_KEY", "HERDR_TG_CHAT_ID"))
    api_id, api_hash = telegram_app()
    bot = os.environ.get("HERDR_TG_BOT", "").strip().lstrip("@")
    chat_id = os.environ["HERDR_TG_CHAT_ID"].strip()
    if not bot and not chat_id.startswith("-"):
        raise SystemExit("Set HERDR_TG_BOT to the bot username, or use a group chat id.")
    return api_id, api_hash, tg_link.parse_key(os.environ["HERDR_TG_LINK_KEY"]), chat_id, bot


class Session:
    """One sealed request and the matching sealed reply."""

    def __init__(self, key, chat_id, exchange):
        self.key = key
        self.chat_id = str(chat_id)
        self.exchange = exchange

    async def request(self, op, pane, text=""):
        payload = tg_link.new_request(op, pane, text)
        wire = tg_link.seal(payload, key=self.key, chat_id=self.chat_id)
        reply_wire = await self.exchange(wire)
        opened = tg_link.open_sealed(reply_wire, key=self.key, chat_id=self.chat_id)
        if not opened or opened.get("id") != payload["id"]:
            raise RuntimeError("the reply was not the encrypted answer to this command")
        if not opened.get("ok"):
            raise RuntimeError(opened.get("error") or "request failed")
        return opened.get("text") or ""


class TelegramUserExchange:
    """Send as the logged-in user and wait for the bot's sealed reply.

    A program holding the bot token cannot do this. Telegram does not deliver
    a bot's own ``sendMessage`` back to that bot, and a second ``getUpdates``
    call would take the approval bot's messages away from it.
    """

    def __init__(self, api_id, api_hash, chat_id, bot, timeout=REPLY_TIMEOUT):
        self.api_id = api_id
        self.api_hash = api_hash
        self.chat_id = str(chat_id)
        self.bot = bot
        self.timeout = timeout

    async def exchange(self, payload):
        from telethon import TelegramClient

        client = TelegramClient(session_base(), self.api_id, self.api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise SystemExit("This computer is not logged in. Run `herdr-tg login` once.")
            target = await self._target(client)
            sent = await client.send_message(target, payload)
            reply = await self._wait(client, target, sent.id)
        finally:
            await client.disconnect()
        if reply is None:
            raise RuntimeError("no encrypted reply from the herdr machine")
        return reply

    async def _target(self, client):
        if self.chat_id.startswith("-"):
            return await client.get_entity(int(self.chat_id))
        return await client.get_entity(self.bot)

    async def _wait(self, client, target, sent_id):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            async for message in client.iter_messages(target, min_id=sent_id, limit=20):
                if message.out or not tg_link.is_envelope(message.text or ""):
                    continue
                reply_to = getattr(message, "reply_to", None)
                replied = getattr(reply_to, "reply_to_msg_id", None)
                if replied != sent_id:
                    continue
                return message.text
            await asyncio.sleep(0.5)
        return None


async def login():
    api_id, api_hash = telegram_app()
    from telethon import TelegramClient

    client = TelegramClient(session_base(), api_id, api_hash)
    await client.start()
    await client.disconnect()
    session_file = Path(session_base() + ".session")
    if session_file.exists():
        session_file.chmod(0o600)
    print(f"Logged in. Session file: {session_file}")


def build_session():
    api_id, api_hash, key, chat_id, bot = link_settings()
    exchange = TelegramUserExchange(api_id, api_hash, chat_id, bot)
    return Session(key, chat_id, exchange.exchange)


async def run_shell(session, pane, lines, write):
    write(f"Connected to {pane}. Commands are encrypted. `exit` quits.\n")
    for line in lines:
        if line.strip() in {"exit", "quit"}:
            return 0
        if not line.strip():
            continue
        try:
            result = await session.request("prompt", pane, line)
        except Exception as exc:
            write(f"error: {exc}\n")
            continue
        write((result or "") + "\n")
    return 0


def _stdin_lines(pane):
    while True:
        try:
            yield input(f"{pane}> ")
        except EOFError:
            return


async def _command(argv):
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(HELP, end="")
        return 0 if argv else 2
    command, *rest = argv
    if command == "keygen":
        print(tg_link.generate_key())
        return 0
    if command == "login":
        await login()
        return 0
    if command == "read" and len(rest) == 1:
        print(await build_session().request("read", rest[0]))
        return 0
    if command == "interrupt" and len(rest) == 1:
        print(await build_session().request("interrupt", rest[0]))
        return 0
    if command == "send" and len(rest) >= 2:
        print(await build_session().request("prompt", rest[0], " ".join(rest[1:])))
        return 0
    if command == "shell" and len(rest) == 1:
        return await run_shell(build_session(), rest[0], _stdin_lines(rest[0]), sys.stdout.write)
    print(HELP, end="", file=sys.stderr)
    return 2


def main(argv=None):
    try:
        return asyncio.run(_command(list(sys.argv[1:] if argv is None else argv)))
    except (ValueError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
