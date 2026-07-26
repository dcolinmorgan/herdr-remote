#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-telegram-bot>=21.0", "websockets>=14.0"]
# ///
import asyncio
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "relay"))
os.environ.setdefault("HERDR_TG_TOKEN", "test-token")

tg = importlib.import_module("herdr_telegram")


def make_agents(count, *, status="idle", project="project"):
    return [
        {
            "pane_id": f"w{i}:p1",
            "agent": "opencode",
            "status": status,
            "project": project,
            "cwd": f"/work/{project}/{i}",
            "host": "local",
        }
        for i in range(count)
    ]


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.message_id = 10
        self.reply_markup = None
        self.reply_to_message = None
        self.text = ""

    async def reply_text(self, text, **kwargs):
        sent = SimpleNamespace(message_id=100 + len(self.replies))
        self.replies.append((text, kwargs, sent))
        return sent


class FakeCallback:
    def __init__(self, data):
        self.data = json.dumps(data, separators=(",", ":"))
        self.message = FakeMessage()
        self.answers = []
        self.edited_markup = None

    async def answer(self, text=None):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.edited_markup = reply_markup


def make_update(chat_id=42, callback=None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=FakeMessage(),
        callback_query=callback,
    )


class TelegramDashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_chat_id = tg.CHAT_ID
        tg.CHAT_ID = "42"
        tg.agents = []
        tg.relay_connected = False
        tg.send_target = ""
        tg.pending.clear()
        tg.prev_statuses.clear()
        tg.daily_stats.clear()

    def tearDown(self):
        tg.CHAT_ID = self.old_chat_id
        tg.agents = []
        tg.relay_connected = False
        tg.send_target = ""
        tg.prev_statuses.clear()
        tg.daily_stats.clear()

    async def test_start_rejects_unauthorized_chat(self):
        update = make_update(chat_id=7)

        await tg.cmd_start(update, SimpleNamespace(args=[]))

        self.assertEqual(update.message.replies, [])

    async def test_start_preserves_chat_discovery_mode(self):
        tg.CHAT_ID = ""
        update = make_update(chat_id=-123)

        await tg.cmd_start(update, SimpleNamespace(args=[]))

        self.assertIn("Chat ID: -123", update.message.replies[0][0])

    async def test_start_reports_disconnected_and_empty_states(self):
        disconnected = make_update()
        await tg.cmd_start(disconnected, SimpleNamespace(args=[]))
        self.assertIn("disconnected", disconnected.message.replies[0][0].lower())
        self.assertNotIn("reply_markup", disconnected.message.replies[0][1])

        tg.relay_connected = True
        empty = make_update()
        await tg.cmd_start(empty, SimpleNamespace(args=[]))
        self.assertIn("no agents", empty.message.replies[0][0].lower())

    async def test_start_lists_current_sixteen_agent_herd(self):
        tg.relay_connected = True
        tg.agents = make_agents(16)
        update = make_update()

        await tg.cmd_start(update, SimpleNamespace(args=[]))

        markup = update.message.replies[0][1]["reply_markup"]
        agent_buttons = [row[0] for row in markup.inline_keyboard if row[0].text not in ("Previous", "Next")]
        self.assertEqual(len(agent_buttons), 16)
        self.assertTrue(all(json.loads(button.callback_data)["action"] == "select_reply" for button in agent_buttons))

    def test_labels_sort_status_and_disambiguate_duplicate_agents(self):
        agent_list = [
            *make_agents(2, status="idle", project="same"),
            *make_agents(1, status="working", project="work"),
            *make_agents(1, status="blocked", project="blocked"),
        ]
        agent_list[-1]["host"] = "remote.example"

        markup = tg.build_agent_keyboard("read", agent_list=agent_list)
        labels = [row[0].text for row in markup.inline_keyboard]

        self.assertTrue(labels[0].startswith("[BLOCKED]"))
        self.assertTrue(labels[1].startswith("[WORKING]"))
        self.assertIn("remote.example", labels[0])
        duplicate_labels = [label for label in labels if "same" in label]
        self.assertEqual(len(set(duplicate_labels)), 2)
        self.assertTrue(all("w" in label and ":p1" in label for label in duplicate_labels))

    def test_large_keyboard_paginates_without_omitting_agents(self):
        agent_list = make_agents(tg.AGENT_PAGE_SIZE + 5)

        first = tg.build_agent_keyboard("read", page=0, agent_list=agent_list)
        second = tg.build_agent_keyboard("read", page=1, agent_list=agent_list)
        first_ids = [json.loads(row[0].callback_data).get("pane_id") for row in first.inline_keyboard[:-1]]
        second_ids = [json.loads(row[0].callback_data).get("pane_id") for row in second.inline_keyboard[:-1]]

        self.assertEqual(len(first_ids), tg.AGENT_PAGE_SIZE)
        self.assertEqual(len(second_ids), 5)
        self.assertEqual(len(set(first_ids + second_ids)), tg.AGENT_PAGE_SIZE + 5)

    def test_long_labels_remain_unique_and_preserve_remote_host(self):
        long_prefix = "project-" + "x" * 70
        agent_list = make_agents(3)
        agent_list[0]["project"] = long_prefix + "-one"
        agent_list[1]["project"] = long_prefix + "-two"
        agent_list[2]["project"] = long_prefix + "-three"
        agent_list[2]["host"] = "remote-" + "host" * 30 + ".example"

        markup = tg.build_agent_keyboard("read", agent_list=agent_list)
        labels = [row[0].text for row in markup.inline_keyboard]

        self.assertEqual(len(set(labels)), 3)
        self.assertTrue(any("@remote-" in label for label in labels))
        self.assertTrue(all(len(label) <= 64 for label in labels))

    def test_compacted_pane_hash_collision_still_has_unique_labels(self):
        agent_list = make_agents(2, project="same")
        agent_list[0]["pane_id"] = "sameprefx-very-long-pane-id-2606"
        agent_list[1]["pane_id"] = "sameprefx-very-long-pane-id-3604"

        markup = tg.build_agent_keyboard("read", agent_list=agent_list)
        labels = [row[0].text for row in markup.inline_keyboard]

        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all(len(label) <= 64 for label in labels))

    async def test_read_and_filtered_trust_pickers_do_not_truncate(self):
        tg.agents = make_agents(16)
        read_update = make_update()
        await tg.cmd_read(read_update, SimpleNamespace(args=[]))
        self.assertEqual(len(read_update.message.replies[0][1]["reply_markup"].inline_keyboard), 16)

        tg.agents = make_agents(12, status="blocked") + make_agents(3, status="idle")
        trust_update = make_update()
        await tg.cmd_trust(trust_update, SimpleNamespace(args=[]))
        self.assertEqual(len(trust_update.message.replies[0][1]["reply_markup"].inline_keyboard), 12)

    async def test_done_agent_is_listed_and_sends_finished_notification(self):
        tg.agents = make_agents(1, status="done", project="completed")
        update = make_update()

        await tg.cmd_agents(update, SimpleNamespace(args=[]))

        self.assertIn("DONE:", update.message.replies[0][0])
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        tg.prev_statuses["w0:p1"] = "working"
        await tg.track_agent_updates(app, tg.agents)
        app.bot.send_message.assert_awaited_once_with(
            chat_id=42,
            text="completed (opencode) finished.",
        )

    async def test_page_callback_rebuilds_from_latest_cache(self):
        tg.relay_connected = True
        tg.agents = make_agents(tg.AGENT_PAGE_SIZE + 5)
        callback = FakeCallback({"action": "page", "menu": "read", "page": 1})

        await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        self.assertEqual(len(callback.edited_markup.inline_keyboard), 6)

    async def test_dashboard_selection_reads_and_arms_one_shot_reply(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        callback = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"})

        with patch.object(tg, "read_pane", AsyncMock(return_value="recent output")):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        self.assertEqual(tg.send_target, "w0:p1")
        self.assertIn("recent output", callback.message.replies[0][0])

    async def test_stale_selection_clears_target_and_offers_refresh(self):
        tg.relay_connected = True
        tg.send_target = "old:pane"
        callback = FakeCallback({"action": "select_reply", "pane_id": "gone:pane"})

        await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        self.assertEqual(tg.send_target, "")
        text, kwargs, _ = callback.message.replies[0]
        self.assertIn("no longer available", text.lower())
        self.assertIn("reply_markup", kwargs)

    async def test_disconnected_callback_cannot_use_stale_agent_cache(self):
        tg.agents = make_agents(1)
        callback = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"})

        with patch.object(tg, "read_pane", AsyncMock()) as read_pane:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        read_pane.assert_not_awaited()
        self.assertIn("disconnected", callback.message.replies[0][0].lower())

    async def test_callback_rechecks_command_eligibility(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="idle")
        callback = FakeCallback({"action": "trust", "pane_id": "w0:p1"})

        with patch.object(tg, "send_to_relay", AsyncMock()) as send_to_relay:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_to_relay.assert_not_awaited()
        self.assertIn("no longer available", callback.message.replies[0][0].lower())

    async def test_callback_rejects_unauthorized_chat(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        callback = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"})

        await tg.handle_callback(make_update(chat_id=7, callback=callback), SimpleNamespace())

        self.assertEqual(callback.answers, ["Unauthorized"])
        self.assertEqual(callback.message.replies, [])


if __name__ == "__main__":
    unittest.main()
