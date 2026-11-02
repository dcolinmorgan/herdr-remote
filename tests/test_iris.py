import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock

if sys.platform == "win32":
    raise unittest.SkipTest("The optional Iris CLI transport supports macOS and Linux.")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "relay"))
import herdr_iris as iris
from websockets.asyncio.server import serve

AGENT = {"pane_id": "w1:p1", "agent": "pi", "host": "local", "cwd": "/project", "project": "demo", "status": "idle"}


class FakeTransport:
    def __init__(self, job=None):
        self.job = job
        self.calls = []
        self.finished = asyncio.Event()

    async def operation(self, op, data):
        self.calls.append((op, data))
        if op == "receive":
            job, self.job = self.job, None
            return job
        if op == "finish":
            self.finished.set()


class PolicyTests(unittest.TestCase):
    def test_relay_stays_local_and_uses_existing_token(self):
        self.assertEqual(iris.relay_url("ws://127.0.0.1:8375", "secret & value"),
                         "ws://127.0.0.1:8375?token=secret+%26+value")
        for url in ("ws://example.com", "ws://localhost.example.com", "http://localhost", "ws://user:pass@localhost"):
            with self.assertRaises(ValueError):
                iris.relay_url(url, "secret")

    def test_setup_requires_valid_owner_identity(self):
        for value in ("", "someone", "npub1invalid", "f" * 63):
            with self.assertRaises(ValueError):
                iris.owner_hex(value)

    def test_unpaired_or_wrong_chat_is_rejected(self):
        with tempfile.TemporaryDirectory() as state:
            transport = iris.Transport(state)
            with self.assertRaises(FileNotFoundError):
                transport.policy()
            for chat in ({"kind": "group", "chat_id": "a" * 64},
                         {"kind": "direct", "chat_id": "b" * 64}):
                with self.assertRaises(ValueError):
                    transport.validate_chat(chat, "a" * 64)

    def test_shell_and_ambiguous_panes_rejected(self):
        for agents in ([], [{"pane_id": "w1:p1"}], [AGENT, AGENT]):
            with self.assertRaises(iris.RequestError):
                iris.find_agent(agents, "w1:p1")


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []
        self.snapshot = [dict(AGENT)]
        self.fail = False
        self.output = "tool result"

        async def handler(ws):
            await ws.send(json.dumps({"type": "agents", "agents": self.snapshot}))
            async for raw in ws:
                message = json.loads(raw)
                self.sent.append(message)
                if self.fail:
                    await ws.send(json.dumps({"type": "error", "request_id": message["request_id"],
                                              "message": "secret-token must not reach Iris"}))
                elif message["type"] == "read_pane":
                    await ws.send(json.dumps({"type": "pane_content", "pane_id": "wrong", "content": "wrong pane"}))
                    await ws.send(json.dumps({"type": "pane_content", "pane_id": "w1:p1", "content": self.output}))
                else:
                    await ws.send(json.dumps({"type": "command_result", "command": message["type"],
                                              "request_id": "unrelated", "ok": False}))
                    await ws.send(json.dumps({"type": "command_result", "command": message["type"],
                                              "request_id": message["request_id"], "ok": True}))
        self.server = await serve(handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.bridge = iris.IrisBridge(FakeTransport(), f"ws://127.0.0.1:{port}")
        self.bridge.relay.ready = True
        self.bridge.relay.agents = [dict(AGENT)]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_commands_and_correlated_ack(self):
        self.assertIn("w1:p1", await self.bridge.command("/agents"))
        self.assertEqual(await self.bridge.command("/read w1:p1"), "tool result")
        self.assertIn("Submitted", await self.bridge.command("/send w1:p1 check the tests"))
        await self.bridge.command("/interrupt w1:p1")
        self.assertEqual([m["type"] for m in self.sent], ["read_pane", "agent_prompt", "send_keys"])
        self.assertEqual(self.sent[1]["text"], "check the tests")
        self.assertEqual(self.sent[2]["keys"], ["ctrl+c"])

    async def test_new_snapshot_rejects_disappeared_changed_or_blocked_agent(self):
        for snapshot in ([], [{**AGENT, "agent": "opencode"}], [{**AGENT, "status": "blocked"}]):
            self.snapshot = snapshot
            with self.assertRaises(iris.RequestError):
                await self.bridge.command("/send w1:p1 yes")
        self.assertEqual(self.sent, [])

    async def test_no_implicit_routing_or_approval_command(self):
        for command in ("please fix it", "/approve w1:p1", "/send", "/interrupt w1:p1 extra"):
            self.assertEqual(await self.bridge.command(command), iris.HELP)
        self.assertEqual(self.sent, [])

    async def test_long_output_keeps_the_most_recent_text(self):
        self.output = "old output " * 1000 + "LATEST RESULT"
        result = await self.bridge.command("/read w1:p1")
        self.assertTrue(result.endswith("LATEST RESULT"))
        self.assertLess(len(result), 5100)

    async def test_relay_detected_question_is_not_a_prompt_target(self):
        self.bridge.relay.needs_attention(AGENT)
        self.bridge.relay.needs_attention(AGENT)
        self.assertEqual(self.bridge.notifications.qsize(), 1)
        with self.assertRaises(iris.RequestError):
            await self.bridge.command("/send w1:p1 yes")
        self.assertEqual(self.sent, [])

    async def test_disconnected_client_fails_closed(self):
        self.bridge.relay.ready = False
        with self.assertRaises(iris.RequestError):
            await self.bridge.command("/send w1:p1 do something")
        self.assertEqual(self.sent, [])

    async def test_server_errors_are_not_disclosed_or_retried(self):
        self.fail = True
        with self.assertRaises(iris.RequestError) as error:
            await self.bridge.command("/send w1:p1 hello")
        self.assertNotIn("secret-token", str(error.exception))
        self.assertEqual(len(self.sent), 1)

    async def test_status_transitions_notify_once(self):
        events = [
            {"type": "agents", "agents": [AGENT]},
            {"type": "agent_update", "agent": {**AGENT, "status": "working"}},
            {"type": "agent_update", "agent": {**AGENT, "status": "done"}},
            {"type": "agent_update", "agent": {**AGENT, "status": "done"}},
            {"type": "agent_update", "agent": {**AGENT, "status": "blocked"}},
            {"type": "blocked", "pane_id": "w1:p1", "prompt_id": "test-question"},
            {"type": "agent_update", "agent": {**AGENT, "status": "blocked"}},
        ]
        finished = asyncio.Event()
        async def handler(ws):
            for event in events:
                await ws.send(json.dumps(event))
            await finished.wait()
        async with serve(handler, "127.0.0.1", 0) as server:
            notices = []
            client = iris.Relay(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", notices.append)
            task = asyncio.create_task(client.listen())
            try:
                for _ in range(100):
                    if len(notices) == 2:
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(len(notices), 2)
                self.assertIn("finished", notices[0])
                self.assertIn("needs your attention", notices[1])
            finally:
                finished.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class InboxTests(unittest.IsolatedAsyncioTestCase):
    async def run_job(self, **overrides):
        job = {"id": "test-message", "text": "/send w1:p1 test", "created": time.time(), "interrupted": False, **overrides}
        transport = FakeTransport(job)
        bridge = iris.IrisBridge(transport, "ws://127.0.0.1:8375")
        bridge.command = AsyncMock(return_value="submitted")
        task = asyncio.create_task(bridge.inbox())
        try:
            await asyncio.wait_for(transport.finished.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return bridge, transport

    async def test_interrupted_request_never_reexecutes(self):
        bridge, transport = await self.run_job(interrupted=True)
        bridge.command.assert_not_called()
        reply = next(data["text"] for op, data in transport.calls if op == "send")
        self.assertIn("not replayed", reply)

    async def test_old_request_expires(self):
        bridge, _ = await self.run_job(created=time.time() - 301)
        bridge.command.assert_not_called()

    async def test_receipt_typing_and_finish(self):
        bridge, transport = await self.run_job()
        bridge.command.assert_awaited_once()
        self.assertIn(("seen", {"id": "test-message"}), transport.calls)
        self.assertIn(("typing", {"active": True}), transport.calls)
        self.assertIn(("typing", {"active": False}), transport.calls)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_owner_inbound_messages_are_claimed_once(self):
        with tempfile.TemporaryDirectory() as state:
            owner = "a" * 64
            Path(state, "pairing.json").write_text(json.dumps({"owner_hex": owner, "chat_id": owner}))
            transport = iris.Transport(state)
            message = {"id": "request", "chat_id": owner, "is_outgoing": False,
                       "body": "/agents", "created_at_secs": time.time()}
            transport.history = AsyncMock(return_value=[
                {**message, "id": "stranger", "chat_id": "b" * 64},
                {**message, "id": "outgoing", "is_outgoing": True},
                {**message, "id": "deleted", "deleted_for_everyone": True}, message, message])
            first = await transport.receive([])
            self.assertEqual(first["id"], "request")
            self.assertFalse(first["interrupted"])
            self.assertTrue((await transport.receive([]))["interrupted"])
            await transport.operation("finish", {"id": "request"})
            self.assertIsNone(await transport.receive([]))
            with transport.database() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
