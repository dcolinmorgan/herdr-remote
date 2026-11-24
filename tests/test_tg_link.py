"""Sealed Telegram link: the wire carries ciphertext, and only an authorized chat can open a command."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "relay"))
os.environ.setdefault("HERDR_TG_TOKEN", "test-token")

import herdr_telegram as tg
import herdr_tg
import tg_link


CHAT = "42"
AGENTS = [
    {"pane_id": "w1:p1", "project": "phoenix-api", "label": ""},
    {"pane_id": "w2:p1", "project": "phoenix-api", "label": "other"},
    {"pane_id": "w9:p4", "project": "billing", "label": "ledger"},
]


def key():
    return tg_link.parse_key(tg_link.generate_key())


class FakeMessage:
    def __init__(self, text, chat_id=42):
        self.text = text
        self.chat_id = chat_id
        self.replies = []
        self.reply_to_message = None

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return text


def update(text, chat_id=42):
    return unittest.mock.Mock(
        effective_chat=unittest.mock.Mock(id=chat_id),
        message=FakeMessage(text, chat_id),
    )


class SealTests(unittest.TestCase):
    def test_key_roundtrip_and_distinct_nonces(self):
        raw = tg_link.generate_key()
        secret = tg_link.parse_key(raw)
        payload = tg_link.new_request("read", "w9:p4", now=1_700_000_000)
        first = tg_link.seal(payload, key=secret, chat_id=CHAT)
        second = tg_link.seal(payload, key=secret, chat_id=CHAT)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("hr1."))
        self.assertEqual(tg_link.open_sealed(first, key=secret, chat_id=CHAT)["pane"], "w9:p4")

    def test_wrong_key_wrong_chat_and_tamper_do_not_open(self):
        secret = key()
        wire = tg_link.seal(tg_link.new_request("read", "w9:p4", now=1_700_000_000), key=secret, chat_id=CHAT)
        self.assertIsNone(tg_link.open_sealed(wire, key=key(), chat_id=CHAT))
        self.assertIsNone(tg_link.open_sealed(wire, key=secret, chat_id="99"))
        flipped = wire[:-1] + ("A" if wire[-1] != "A" else "B")
        self.assertIsNone(tg_link.open_sealed(flipped, key=secret, chat_id=CHAT))
        self.assertIsNone(tg_link.load_key())

    def test_unusable_env_key_turns_the_link_off(self):
        with unittest.mock.patch.dict(os.environ, {"HERDR_TG_LINK_KEY": "not-a-key"}):
            self.assertIsNone(tg_link.load_key())

    def test_clip_keeps_the_tail(self):
        self.assertEqual(tg_link.clip_text("abcdef", 3), "def")


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret = key()
        self.replay = tg_link.ReplayWindow()
        self.read = AsyncMock(return_value="PLAINTEXT-SENTINEL-do-not-leak")
        self.prompt = AsyncMock()
        self.interrupt = AsyncMock()

    async def deliver(self, op, pane, text="", now=1_700_000_000, request_now=None, relay_up=True, agents=None):
        payload = tg_link.new_request(op, pane, text, now=now if request_now is None else request_now)
        wire = tg_link.seal(payload, key=self.secret, chat_id=CHAT)
        reply = await tg_link.dispatch(
            wire, chat_id=CHAT, key=self.secret, agents=AGENTS if agents is None else agents,
            relay_up=relay_up, read=self.read, prompt=self.prompt, interrupt=self.interrupt,
            now=now, replay=self.replay,
        )
        return payload, wire, reply

    def opened(self, reply):
        self.assertIsNotNone(reply)
        return tg_link.open_sealed(reply, key=self.secret, chat_id=CHAT)

    async def test_read_reply_hides_the_output_on_the_wire(self):
        payload, wire, reply = await self.deliver("read", "billing")
        self.assertNotIn("PLAINTEXT-SENTINEL-do-not-leak", wire)
        self.assertNotIn("PLAINTEXT-SENTINEL-do-not-leak", reply)
        opened = self.opened(reply)
        self.assertEqual(opened["id"], payload["id"])
        self.assertEqual(opened["text"], "PLAINTEXT-SENTINEL-do-not-leak")
        self.read.assert_awaited_once_with("w9:p4")

    async def test_prompt_and_interrupt_hit_the_relay_once(self):
        _payload, _wire, reply = await self.deliver("prompt", "w9:p4", "run the tests")
        self.assertTrue(self.opened(reply)["ok"])
        self.prompt.assert_awaited_once_with("w9:p4", "run the tests")
        await self.deliver("interrupt", "ledger")
        self.interrupt.assert_awaited_once_with("w9:p4")

    async def test_replay_and_stale_requests_get_no_reply(self):
        _payload, wire, reply = await self.deliver("read", "w9:p4")
        self.assertIsNotNone(reply)
        again = await tg_link.dispatch(
            wire, chat_id=CHAT, key=self.secret, agents=AGENTS, relay_up=True,
            read=self.read, prompt=self.prompt, interrupt=self.interrupt,
            now=1_700_000_000, replay=self.replay,
        )
        self.assertIsNone(again)
        self.read.assert_awaited_once()
        _payload, _wire, stale = await self.deliver(
            "read", "w9:p4", now=1_700_000_000 + 1000, request_now=1_700_000_000,
        )
        self.assertIsNone(stale)

    async def test_ambiguous_missing_and_down_relay(self):
        _payload, _wire, reply = await self.deliver("prompt", "phoenix-api", "hi")
        self.assertEqual(self.opened(reply)["error"], "ambiguous agent")
        self.prompt.assert_not_awaited()
        _payload, _wire, missing = await self.deliver("read", "nope")
        self.assertEqual(self.opened(missing)["error"], "no matching agent")
        _payload, _wire, down = await self.deliver("read", "w9:p4", relay_up=False)
        self.assertEqual(self.opened(down)["error"], "relay disconnected")
        self.read.assert_not_awaited()

    async def test_empty_prompt_and_relay_failure_stay_sealed(self):
        _payload, _wire, reply = await self.deliver("prompt", "w9:p4", "   ")
        self.assertEqual(self.opened(reply)["error"], "missing text")
        self.prompt.side_effect = RuntimeError("token=should-not-surface")
        _payload, wire, reply = await self.deliver("prompt", "w9:p4", "hello")
        opened = self.opened(reply)
        self.assertEqual(opened["error"], "relay rejected the command")
        self.assertNotIn("should-not-surface", reply)
        self.assertNotIn("hello", wire)


class BotHookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_chat = tg.CHAT_ID
        self.old_agents = list(tg.agents)
        self.old_connected = tg.relay_connected
        tg.CHAT_ID = CHAT
        tg.agents = [{"pane_id": "w9:p4", "project": "billing", "label": ""}]
        tg.relay_connected = True
        tg_link.REPLAY.seen.clear()
        self.env = unittest.mock.patch.dict(os.environ, {"HERDR_TG_LINK_KEY": tg_link.generate_key()})
        self.env.start()
        self.secret = tg_link.parse_key(os.environ["HERDR_TG_LINK_KEY"])

    def tearDown(self):
        self.env.stop()
        tg.CHAT_ID = self.old_chat
        tg.agents = self.old_agents
        tg.relay_connected = self.old_connected
        tg_link.REPLAY.seen.clear()

    def envelope(self, op, pane, text="", now=None):
        payload = tg_link.new_request(op, pane, text, now=now)
        return payload, tg_link.seal(payload, key=self.secret, chat_id=CHAT)

    async def test_envelope_is_not_typed_into_the_pane(self):
        import time
        _payload, wire = self.envelope("prompt", "billing", "run the tests", now=int(time.time()))
        owned = update(wire)
        with unittest.mock.patch.object(tg, "send_text_to_relay", AsyncMock()) as send:
            await tg.handle_text(owned, SimpleNamespace())
        send.assert_awaited_once_with("w9:p4", "run the tests")
        self.assertEqual(len(owned.message.replies), 1)
        self.assertNotIn("run the tests", owned.message.replies[0])
        self.assertEqual(
            tg_link.open_sealed(owned.message.replies[0], key=self.secret, chat_id=CHAT)["text"],
            "sent",
        )
        # The same ciphertext is a replay. The bot stays silent and does not type it.
        again = update(wire)
        with unittest.mock.patch.object(tg, "send_text_to_relay", AsyncMock()) as send:
            await tg.handle_text(again, SimpleNamespace())
        send.assert_not_awaited()
        self.assertEqual(again.message.replies, [])

    async def test_discovery_mode_and_other_chats_drop_the_envelope(self):
        now = int(__import__("time").time())
        _payload, wire = self.envelope("prompt", "billing", "run the tests", now=now)
        tg.CHAT_ID = ""
        owned = update(wire)
        with unittest.mock.patch.object(tg, "send_text_to_relay", AsyncMock()) as send:
            await tg.handle_text(owned, SimpleNamespace())
        send.assert_not_awaited()
        self.assertEqual(owned.message.replies, [])

        tg.CHAT_ID = CHAT
        stranger = update(wire, chat_id=7)
        with unittest.mock.patch.object(tg, "send_text_to_relay", AsyncMock()) as send:
            await tg.handle_text(stranger, SimpleNamespace())
        send.assert_not_awaited()
        self.assertEqual(stranger.message.replies, [])

    async def test_a_normal_reply_is_unchanged(self):
        tg.relay_connected = True
        tg.register_pending(42, 77, "w9:p4")
        message = FakeMessage("follow up")
        message.reply_to_message = SimpleNamespace(message_id=77)
        with unittest.mock.patch.object(tg, "send_text_to_relay", AsyncMock()) as send:
            await tg.handle_text(
                unittest.mock.Mock(effective_chat=unittest.mock.Mock(id=42), message=message),
                SimpleNamespace(),
            )
        send.assert_awaited_once_with("w9:p4", "follow up")


class CliTests(unittest.IsolatedAsyncioTestCase):
    def test_keygen_prints_a_usable_key(self):
        from io import StringIO
        from contextlib import redirect_stdout
        buf = StringIO()
        with redirect_stdout(buf):
            code = herdr_tg.main(["keygen"])
        self.assertEqual(code, 0)
        tg_link.parse_key(buf.getvalue().strip())

    def test_usage_and_missing_env(self):
        from io import StringIO
        from contextlib import redirect_stderr, redirect_stdout
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(herdr_tg.main([]), 2)
        self.assertIn("keygen", out.getvalue())
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERDR_TG_LINK_KEY", None)
            os.environ.pop("HERDR_TG_CHAT_ID", None)
            with self.assertRaises(SystemExit):
                herdr_tg.main(["read", "billing"])

    async def test_shell_quits_locally_and_reports_a_refusal_without_the_command(self):
        class Fake:
            def __init__(self):
                self.calls = []

            async def request(self, op, pane, text=""):
                self.calls.append((op, pane, text))
                if text == "nope":
                    raise RuntimeError("relay rejected the command")
                return "done"

        fake = Fake()
        written = []
        code = await herdr_tg.run_shell(fake, "billing", ["", "run the tests", "nope", "exit"], written.append)
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [("prompt", "billing", "run the tests"), ("prompt", "billing", "nope")])
        self.assertIn("error: relay rejected the command\n", written)
        self.assertTrue(any("Connected to billing" in line for line in written))

    async def test_client_and_bot_agree_on_one_exchange(self):
        secret = key()
        seen = []

        async def exchange(wire):
            seen.append(wire)
            reply = await tg_link.dispatch(
                wire, chat_id=CHAT, key=secret, agents=AGENTS, relay_up=True,
                read=AsyncMock(return_value="PLAINTEXT-SENTINEL-do-not-leak"),
                prompt=AsyncMock(), interrupt=AsyncMock(),
                now=int(__import__("time").time()), replay=tg_link.ReplayWindow(),
            )
            seen.append(reply)
            return reply

        session = herdr_tg.Session(secret, CHAT, exchange)
        text = await session.request("read", "w9:p4")
        self.assertEqual(text, "PLAINTEXT-SENTINEL-do-not-leak")
        self.assertTrue(all("PLAINTEXT-SENTINEL-do-not-leak" not in item for item in seen))


if __name__ == "__main__":
    unittest.main()
