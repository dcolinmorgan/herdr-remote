#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets==15.0.1"]
# ///
"""Optional, owner-only Iris Chat client for the local herdr-remote relay."""
import argparse
import asyncio
import contextlib
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets

from agent_state import apply_agent_message
from iris_transport import Transport, owner_hex, setup

log = logging.getLogger("herdr-iris")
HELP = """Herdr through Iris Chat
/agents — list agents and their pane IDs
/read PANE — read recent output
/send PANE message — submit a prompt
/interrupt PANE — send Ctrl+C
/help — show these commands
Use the exact pane ID from /agents. Approvals stay in your terminal or web app."""
REQUEST_TIMEOUT = 25
MAX_COMMAND_AGE = 300


class RequestError(Exception):
    """A fixed, safe-to-display client error; never wrap raw relay exceptions."""


def relay_url(url, token):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"ws", "wss"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("Iris requires a loopback HERDR_RELAY URL on the same machine.")
    query = dict(parse_qsl(parsed.query))
    if token:
        query["token"] = token
    return urlunsplit(parsed._replace(query=urlencode(query)))


def find_agent(agents, pane):
    matches = [a for a in agents if a.get("pane_id") == pane and a.get("agent")]
    if len(matches) != 1:
        raise RequestError("Agent unavailable or ambiguous. Refresh /agents.")
    return matches[0]


def agent_binding(agent):
    return tuple(agent.get(k) for k in ("pane_id", "agent", "host", "cwd"))


def clean_text(value, limit=5000):
    # Drop terminal controls; the relay already strips ANSI from pane reads.
    return "".join(c for c in str(value) if c in "\n\t" or c.isprintable())[:limit]


class Relay:
    def __init__(self, url, notify):
        self.url = url
        self.notify = notify
        self.agents = []
        self.ready = False
        self.blocked = set()

    def needs_attention(self, agent):
        pane = agent.get("pane_id")
        if not pane or pane in self.blocked:
            return
        self.blocked.add(pane)
        self.notify(f"{clean_text(agent.get('project') or agent.get('agent'), 120)} "
                    f"({clean_text(pane, 100)}) needs your attention.\n/read {clean_text(pane, 100)}"
                    "\nHandle approvals in your terminal or web app.")

    async def listen(self):
        while True:
            try:
                async with websockets.connect(self.url, open_timeout=10) as ws:
                    async for raw in ws:
                        message = json.loads(raw)
                        if message.get("type") == "blocked":
                            with contextlib.suppress(RequestError):
                                self.needs_attention(find_agent(self.agents, message.get("pane_id")))
                            continue
                        if message.get("type") not in {"agents", "agent_update"}:
                            continue
                        previous = {a["pane_id"]: a for a in self.agents}
                        self.agents = apply_agent_message(self.agents, message)
                        was_ready = self.ready
                        if message["type"] == "agents":
                            self.ready = True
                            self.blocked.intersection_update(a.get("pane_id") for a in self.agents)
                        for agent in self.agents:
                            old = previous.get(agent.get("pane_id"), {})
                            status = agent.get("status")
                            if status == "working" or (old.get("status") == "blocked" and status in {"idle", "done"}):
                                self.blocked.discard(agent.get("pane_id"))
                            blocked = status == "blocked" and old.get("status") != "blocked"
                            finished = (was_ready and old.get("status") in {"working", "blocked"}
                                        and status in {"idle", "done"}
                                        and agent_binding(old) == agent_binding(agent))
                            if blocked:
                                self.needs_attention(agent)
                            if finished:
                                pane = clean_text(agent.get("pane_id"), 100)
                                text = (f"{clean_text(agent.get('project') or agent.get('agent'), 120)} "
                                        f"({pane}) finished.\n/read {pane}")
                                self.notify(text)
            except (OSError, ValueError, websockets.WebSocketException):
                log.warning("Relay disconnected; waiting to reconnect.")
            finally:
                self.ready = False
                self.agents = []
                self.blocked.clear()
            await asyncio.sleep(3)

    async def request(self, pane, command, **fields):
        if not self.ready:
            raise RequestError("Relay disconnected. Try again after it reconnects.")
        selected = find_agent(self.agents, pane)

        async def exchange():
            async with websockets.connect(self.url, open_timeout=10) as ws:
                # Revalidate against this connection's fresh snapshot. Never address a shell
                # pane or silently use a cached agent after a relay/session change.
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("type") == "agents":
                        current = find_agent(message.get("agents", []), pane)
                        if agent_binding(current) != agent_binding(selected):
                            raise RequestError("Agent changed. Refresh /agents before sending.")
                        if command == "agent_prompt" and (current.get("status") == "blocked" or pane in self.blocked):
                            raise RequestError("Agent needs input. Handle the request in your terminal or web app.")
                        break
                else:
                    raise RequestError("Relay disconnected. No command was sent.")
                request_id = secrets.token_hex(16)
                await ws.send(json.dumps({"type": command, "pane_id": pane,
                                          "request_id": request_id, **fields}))
                async for raw in ws:
                    message = json.loads(raw)
                    if command == "read_pane" and message.get("type") == "pane_content":
                        if message.get("pane_id") == pane:
                            output = clean_text(message.get("content", ""), 100000)
                            return (("[Earlier output omitted]\n" + output[-5000:]) if len(output) > 5000
                                    else output or "(empty output)")
                    if message.get("type") == "blocked" and message.get("pane_id") == pane:
                        self.needs_attention(current)
                    if message.get("type") == "error" and message.get("request_id") in (None, request_id):
                        raise RequestError("Relay refused the request. Refresh /agents and /read before retrying.")
                    if message.get("request_id") != request_id:
                        continue
                    if message.get("type") == "command_result" and message.get("command") == command:
                        if message.get("ok") is not True:
                            raise RequestError("Relay could not deliver the request. Check /read before retrying.")
                        return f"Submitted to {pane}. Use /read {pane} to check the result."
                raise RequestError("Delivery is unconfirmed. Check /read before retrying.")

        try:
            return await asyncio.wait_for(exchange(), REQUEST_TIMEOUT)
        except (OSError, ValueError, asyncio.TimeoutError, websockets.WebSocketException):
            raise RequestError("Delivery is unconfirmed. Check /read before retrying.") from None


class IrisBridge:
    def __init__(self, transport, url):
        self.transport = transport
        self.notifications = asyncio.Queue(maxsize=32)
        self.relay = Relay(url, self.notify)

    def notify(self, text):
        try:
            self.notifications.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("Notification queue full; use /agents for current status.")

    async def send_notifications(self):
        while True:
            text = await self.notifications.get()
            try:
                await self.transport.operation("send", {"key": "notice:" + secrets.token_hex(16), "text": text})
            except Exception:
                # Never replay an ambiguous send, or leak a URL, token, key or output to logs.
                log.warning("Iris notification delivery unconfirmed; use /agents for current status.")

    async def command(self, text):
        command, _, rest = text.strip().partition(" ")
        if command in {"/help", "/start"}:
            return HELP
        if not self.relay.ready:
            raise RequestError("Relay disconnected. No command was sent.")
        if command == "/agents" and not rest.strip():
            lines = [f"{clean_text(a.get('pane_id'), 100)} — {clean_text(a.get('project') or a.get('agent'), 100)} "
                     f"({clean_text(a.get('agent'), 60)}): {clean_text(a.get('status'), 30)}"
                     for a in self.relay.agents if a.get("agent")]
            return clean_text("\n".join(lines)) or "No agents are running."
        pane, _, body = rest.strip().partition(" ")
        if command == "/read" and pane and not body:
            return await self.relay.request(pane, "read_pane", lines=80)
        if command == "/interrupt" and pane and not body:
            return await self.relay.request(pane, "send_keys", keys=["ctrl+c"])
        if command == "/send" and pane and body.strip() and len(body) <= 10000:
            return await self.relay.request(pane, "agent_prompt", text=body)
        return HELP

    async def inbox(self):
        while True:
            job = await self.transport.operation("receive", {"active": []})
            if not job:
                await asyncio.sleep(1)
                continue
            key = "reply:" + hashlib.sha256(job["id"].encode()).hexdigest()
            try:
                if job["id"].startswith("operator:"):
                    reply = HELP
                elif job["interrupted"]:
                    reply = "Previous request interrupted; it was not replayed. Check /read before sending it again."
                elif time.time() - job["created"] > MAX_COMMAND_AGE:
                    reply = "Request expired after five minutes. Check /agents and send a new command."
                else:
                    with contextlib.suppress(Exception):
                        await self.transport.operation("seen", {"id": job["id"]})
                        await self.transport.operation("typing", {"active": True})
                    try:
                        reply = await self.command(job["text"])
                    except RequestError as exc:
                        reply = str(exc)
                await self.transport.operation("send", {"key": key, "text": reply})
            except Exception:
                log.warning("Iris request failed or delivery is unconfirmed; request will not be replayed.")
            finally:
                # A crash before finish leaves a claimed job, handled above without execution.
                await self.transport.operation("finish", {"id": job["id"]})
                with contextlib.suppress(Exception):
                    await self.transport.operation("typing", {"active": False})

    async def run(self):
        await self.transport.open()
        tasks = []
        try:
            self.transport.policy()  # Fail closed before connecting to Herdr.
            tasks = [asyncio.create_task(fn()) for fn in
                     (self.relay.listen, self.inbox, self.send_notifications)]
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.transport.operation("typing", {"active": False})
            await self.transport.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=["run", "setup"], default="run")
    parser.add_argument("--json", action="store_true", help="Read setup owner/invite as JSON from stdin")
    args = parser.parse_args()
    state = Path(os.environ.get("HERDR_IRIS_STATE_DIR", "~/.local/share/herdr-remote/iris")).expanduser()
    binary = os.environ.get("HERDR_IRIS_BINARY", "iris")
    if args.mode == "setup":
        if (state / "pairing.json").exists():
            Transport(state, binary).policy()
            print("Existing Iris identity and approved contact preserved.")
            return
        if args.json:
            request = json.load(sys.stdin)
        else:
            print("Create a separate Iris identity for Herdr. No signup or personal secret key needed.")
            request = {"owner": input("Your Iris public user ID (npub): "),
                       "invite": getpass.getpass("Your private Iris invite link: ")}
        owner = owner_hex(request["owner"])
        if not isinstance(request["invite"], str) or not request["invite"].strip():
            raise ValueError("An invite is required.")
        await setup(state, binary, "Herdr", owner, request["invite"].strip())
        print("Iris paired. Run without 'setup' to connect to Herdr.")
        return
    url = relay_url(os.environ.get("HERDR_RELAY", "ws://127.0.0.1:8375"),
                    os.environ.get("HERDR_RELAY_TOKEN", ""))
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    await IrisBridge(Transport(state, binary), url).run()


if __name__ == "__main__":
    os.umask(0o077)
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception:
        print("Iris could not start. Check CLI installation, pairing and the local relay configuration.", file=sys.stderr)
        sys.exit(1)
