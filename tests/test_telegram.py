#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-telegram-bot>=21.0", "websockets>=14.0"]
# ///
import asyncio
import ast
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
    def __init__(self, chat_id=42, chat_type="private", message_id=10):
        self.replies = []
        self.message_id = message_id
        self.chat_id = chat_id
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.reply_markup = None
        self.reply_to_message = None
        self.text = ""

    async def reply_text(self, text, **kwargs):
        sent = SimpleNamespace(message_id=self.message_id * 100 + len(self.replies), chat_id=self.chat_id)
        self.replies.append((text, kwargs, sent))
        return sent


class FakeCallback:
    def __init__(self, data, chat_id=42, chat_type="private", message_id=10):
        self.data = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
        self.message = FakeMessage(chat_id, chat_type, message_id)
        self.answers = []
        self.edited_markup = None
        self.edit_calls = 0

    async def answer(self, text=None):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.edit_calls += 1
        self.edited_markup = reply_markup


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        message = SimpleNamespace(message_id=500 + len(self.sent), chat_id=chat_id)
        self.sent.append((chat_id, text, kwargs, message))
        return message


class FakeRelayConnection:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return json.dumps(next(self.responses))

    def __aiter__(self):
        async def empty_messages():
            if False:
                yield None
        return empty_messages()


def make_update(chat_id=42, chat_type="private", callback=None, message=None):
    if callback is not None:
        callback.message.chat_id = chat_id
        callback.message.chat = SimpleNamespace(id=chat_id, type=chat_type)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        message=message or FakeMessage(chat_id, chat_type),
        callback_query=callback,
    )


def make_active_approval_keyboard(pane_id, options):
    markup = tg.make_keyboard(pane_id, options)
    generation = json.loads(markup.inline_keyboard[0][0].callback_data)["g"]
    tg.approval_tokens[pane_id] = generation
    tg.blocked_prompt_ids[pane_id] = "prompt-123"
    return markup


class TelegramDashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_chat_id = tg.CHAT_ID
        tg.CHAT_ID = "42"
        tg.agents = []
        tg.relay_connected = False
        tg.pending.clear()
        tg.approval_tokens.clear()
        tg.blocked_prompt_ids.clear()
        tg.prev_statuses.clear()
        tg.daily_stats.clear()

    def tearDown(self):
        tg.CHAT_ID = self.old_chat_id
        tg.agents = []
        tg.relay_connected = False
        tg.approval_tokens.clear()
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
        self.assertTrue(all(tg.parse_callback_data(button.callback_data)["action"] == "select_reply" for button in agent_buttons))

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
        tg.agents = agent_list

        first = tg.build_agent_keyboard("read", page=0, agent_list=agent_list)
        second = tg.build_agent_keyboard("read", page=1, agent_list=agent_list)
        first_ids = [tg.parse_callback_data(row[0].callback_data)["pane_id"] for row in first.inline_keyboard[:-1]]
        second_ids = [tg.parse_callback_data(row[0].callback_data)["pane_id"] for row in second.inline_keyboard[:-1]]

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

    def test_all_pane_callbacks_fit_telegram_byte_limit(self):
        pane_id = "pane-" + "кирилица" * 100
        tg.agents = make_agents(1)
        tg.agents[0]["pane_id"] = pane_id
        markups = [
            tg.build_agent_keyboard(action)
            for action in ("read", "interrupt", "select_send", "select_reply", "trust")
        ]
        markups.extend([tg.make_keyboard(pane_id, None), tg.interaction_keyboard(pane_id)])

        callbacks = [
            button.callback_data
            for markup in markups
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertTrue(all(len(callback.encode()) <= 64 for callback in callbacks))

    async def test_read_and_filtered_trust_pickers_do_not_truncate(self):
        tg.agents = make_agents(16)
        read_update = make_update()
        await tg.cmd_read(read_update, SimpleNamespace(args=[]))
        self.assertEqual(len(read_update.message.replies[0][1]["reply_markup"].inline_keyboard), 16)

        tg.agents = make_agents(12, status="blocked") + make_agents(3, status="idle")
        trust_update = make_update()
        await tg.cmd_trust(trust_update, SimpleNamespace(args=[]))
        self.assertEqual(len(trust_update.message.replies[0][1]["reply_markup"].inline_keyboard), 12)

    async def test_direct_read_send_and_reply_forms_preserve_behavior(self):
        tg.agents = make_agents(1)
        read_update = make_update()
        send_update = make_update()
        reply_update = make_update()

        with (
            patch.object(tg, "read_pane", AsyncMock(side_effect=["read output", "reply output"])),
            patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text,
        ):
            await tg.cmd_read(read_update, SimpleNamespace(args=["project"]))
            await tg.cmd_send(send_update, SimpleNamespace(args=["project", "hello"]))
            await tg.cmd_reply(reply_update, SimpleNamespace(args=["project"]))

        self.assertIn("read output", read_update.message.replies[0][0])
        send_text.assert_awaited_once_with("w0:p1", "hello")
        self.assertIn("Sent to project", send_update.message.replies[0][0])
        reply_text, reply_kwargs, reply_message = reply_update.message.replies[0]
        self.assertIn("reply output", reply_text)
        self.assertIsInstance(reply_kwargs["reply_markup"], tg.ForceReply)
        self.assertEqual(tg.pending_pane(42, reply_message.message_id), "w0:p1")

    async def test_send_and_reply_pickers_target_the_expected_actions(self):
        tg.agents = make_agents(1)
        send_update = make_update()
        reply_update = make_update()

        await tg.cmd_send(send_update, SimpleNamespace(args=[]))
        await tg.cmd_reply(reply_update, SimpleNamespace(args=[]))

        send_button = send_update.message.replies[0][1]["reply_markup"].inline_keyboard[0][0]
        reply_button = reply_update.message.replies[0][1]["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(tg.parse_callback_data(send_button.callback_data)["action"], "select_send")
        self.assertEqual(tg.parse_callback_data(reply_button.callback_data)["action"], "select_reply")

    async def test_done_agent_is_listed_and_sends_finished_notification(self):
        tg.agents = make_agents(1, status="done", project="completed")
        update = make_update()

        await tg.cmd_agents(update, SimpleNamespace(args=[]))

        self.assertIn("DONE:", update.message.replies[0][0])
        bot = FakeBot()
        app = SimpleNamespace(bot=bot)
        tg.prev_statuses["w0:p1"] = "working"
        await tg.track_agent_updates(app, tg.agents)
        chat_id, text, kwargs, sent = bot.sent[0]
        self.assertEqual(chat_id, 42)
        self.assertIn("completed (opencode) finished", text)
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.text, "Open output & reply")
        self.assertEqual(tg.parse_callback_data(button.callback_data)["pane_id"], "w0:p1")
        self.assertEqual(tg.pending_pane(42, sent.message_id), "w0:p1")

    async def test_page_callback_rebuilds_from_latest_cache(self):
        tg.relay_connected = True
        tg.agents = make_agents(tg.AGENT_PAGE_SIZE + 5)
        callback = FakeCallback({"action": "page", "menu": "read", "page": 1})

        await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        self.assertEqual(len(callback.edited_markup.inline_keyboard), 6)

    async def test_dashboard_selection_creates_private_reply_prompt(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        button = tg.build_agent_keyboard("select_reply").inline_keyboard[0][0]
        callback = FakeCallback(button.callback_data)

        with patch.object(tg, "read_pane", AsyncMock(return_value="recent output")):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        text, kwargs, sent = callback.message.replies[0]
        self.assertIn("recent output", text)
        self.assertIsInstance(kwargs["reply_markup"], tg.ForceReply)
        self.assertEqual(kwargs["reply_markup"].input_field_placeholder, "Reply to project")
        self.assertEqual(tg.pending_pane(42, sent.message_id), "w0:p1")

    async def test_stale_selection_offers_refresh(self):
        tg.relay_connected = True
        callback = FakeCallback({"action": "select_reply", "pane_id": "gone:pane"})

        await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

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

    async def test_stale_approval_cannot_type_into_unblocked_agent(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        markup = make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback(markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = markup
        tg.agents[0]["status"] = "working"

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_not_awaited()
        self.assertIn("no longer available", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)

    async def test_approval_transport_failure_preserves_controls(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        markup = make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback(markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = markup

        with patch.object(tg, "send_keys_to_relay", AsyncMock(side_effect=OSError("offline"))):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        self.assertIn("failed", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)
        self.assertIn("w0:p1", tg.approval_tokens)

    async def test_approval_sends_key_and_removes_controls_on_success(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        markup = make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback(markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = markup

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_awaited_once_with("w0:p1", ["1"], prompt_id="prompt-123")
        self.assertEqual(callback.edit_calls, 1)
        self.assertIn("Sent: yes", callback.message.replies[0][0])
        self.assertNotIn("w0:p1", tg.approval_tokens)

    async def test_approval_from_previous_blocked_prompt_is_rejected(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        old_markup = make_active_approval_keyboard("w0:p1", ["old yes", "old no"])
        make_active_approval_keyboard("w0:p1", ["new yes", "new no"])
        callback = FakeCallback(old_markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = old_markup

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_not_awaited()
        self.assertIn("older prompt", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)

    async def test_legacy_approval_is_rejected_without_discarding_controls(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback({"pane_id": "w0:p1", "k": "1"})

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_not_awaited()
        self.assertIn("older prompt", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)

    async def test_send_keys_requires_positive_relay_acknowledgement(self):
        accepted = FakeRelayConnection([
            {"type": "agents", "agents": []},
            {"type": "command_result", "command": "send_keys", "ok": True, "request_id": "request-123"},
        ])
        with patch("websockets.connect", return_value=accepted), patch.object(
            tg.secrets, "token_hex", return_value="request-123"
        ):
            await tg.send_keys_to_relay("w0:p1", ["1"])
        self.assertEqual(accepted.sent, [{
            "type": "send_keys",
            "pane_id": "w0:p1",
            "keys": ["1"],
            "request_id": "request-123",
        }])

        rejected = FakeRelayConnection([{"type": "error", "message": "keys contain disallowed values"}])
        with patch("websockets.connect", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "disallowed"):
                await tg.send_keys_to_relay("w0:p1", ["1"])

    def test_relay_allows_numeric_approval_keys_and_acknowledges_them(self):
        relay_path = ROOT / "relay" / "herdr_relay.py"
        source = relay_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        safe_keys = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "SAFE_KEYS" for target in node.targets)
        )
        values = eval(compile(ast.Expression(safe_keys), str(relay_path), "eval"), {"range": range})
        self.assertTrue({"1", "2", "3"}.issubset(values))
        self.assertIn('"command": "send_keys"', source)
        self.assertIn('"ok": True', source)
        self.assertIn("if result.returncode != 0:", source)

    async def test_omp_choice_uses_question_response_with_label_and_prompt_id(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.agents[0]["agent"] = "omp"
        tg.blocked_prompt_ids["w0:p1"] = "prompt-123"
        markup = tg.make_keyboard(
            "w0:p1",
            ["Red", "Blue"],
            generation="generation",
            interaction="omp_question",
        )
        tg.approval_tokens["w0:p1"] = "generation"
        callback = FakeCallback(markup.inline_keyboard[1][0].callback_data)
        callback.message.reply_markup = markup

        with (
            patch.object(tg, "send_to_relay", AsyncMock()) as send_to_relay,
            patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys,
        ):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_to_relay.assert_awaited_once_with("w0:p1", "Blue", prompt_id="prompt-123")
        send_keys.assert_not_awaited()

    def test_relay_disconnect_clears_approval_generations(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.approval_tokens["w0:p1"] = "generation"
        tg.blocked_prompt_ids["w0:p1"] = "prompt"

        tg.clear_relay_connection_state()

        self.assertFalse(tg.relay_connected)
        self.assertEqual(tg.agents, [])
        self.assertEqual(tg.approval_tokens, {})
        self.assertEqual(tg.blocked_prompt_ids, {})

    async def test_failed_blocked_notification_preserves_previous_generation(self):
        tg.approval_tokens["w0:p1"] = "previous"
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=OSError("offline"))))

        with self.assertRaisesRegex(OSError, "offline"):
            await tg.notify_blocked(app, "w0:p1", "opencode", "project", "prompt", ["yes", "no"])

        self.assertEqual(tg.approval_tokens["w0:p1"], "previous")

    async def test_blocked_notification_failure_does_not_disconnect_relay(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.approval_tokens["w0:p1"] = "previous"
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=OSError("offline"))))

        await tg.notify_blocked_safely(app, {
            "pane_id": "w0:p1",
            "agent": "opencode",
            "project": "project",
            "prompt": "prompt",
            "options": ["yes", "no"],
        })

        self.assertTrue(tg.relay_connected)
        self.assertEqual(tg.agents[0]["pane_id"], "w0:p1")
        self.assertEqual(tg.approval_tokens["w0:p1"], "previous")

    async def test_graceful_relay_close_clears_connection_state(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.approval_tokens["w0:p1"] = "generation"
        calls = 0

        def connect(_url):
            nonlocal calls
            calls += 1
            if calls == 1:
                return FakeRelayConnection([])
            self.assertFalse(tg.relay_connected)
            self.assertEqual(tg.agents, [])
            self.assertEqual(tg.approval_tokens, {})
            raise KeyboardInterrupt

        with patch("websockets.connect", side_effect=connect):
            with self.assertRaises(KeyboardInterrupt):
                await tg.relay_listener(SimpleNamespace())

    async def test_callback_rejects_unauthorized_chat(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        callback = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"})

        await tg.handle_callback(make_update(chat_id=7, callback=callback), SimpleNamespace())

        self.assertEqual(callback.answers, ["Unauthorized"])
        self.assertEqual(callback.message.replies, [])

    def test_pending_registry_is_chat_scoped_and_bounded(self):
        tg.register_pending(42, 10, "local:pane")
        tg.register_pending(43, 10, "other:pane")
        self.assertEqual(tg.pending_pane(42, 10), "local:pane")
        self.assertEqual(tg.pending_pane(43, 10), "other:pane")

        for message_id in range(tg.PENDING_LIMIT + 2):
            tg.register_pending(42, 1000 + message_id, f"pane:{message_id}")
        self.assertEqual(len(tg.pending), tg.PENDING_LIMIT)
        self.assertIsNone(tg.pending_pane(42, 1000))

    async def test_blocked_notification_keeps_approvals_and_adds_interaction(self):
        bot = FakeBot()
        await tg.notify_blocked(
            SimpleNamespace(bot=bot),
            pane_id="w0:p1",
            agent="opencode",
            project="blocked-project",
            prompt="Allow tool?",
            options=["yes, single permission", "trust, always allow", "no (tab to edit)"],
        )

        chat_id, text, kwargs, sent = bot.sent[0]
        rows = kwargs["reply_markup"].inline_keyboard
        self.assertEqual([json.loads(row[0].callback_data)["k"] for row in rows[:3]], ["1", "2", "3"])
        self.assertEqual(rows[-1][0].text, "Open output & reply")
        self.assertIn("reply to this notification", text)
        self.assertEqual(tg.pending_pane(chat_id, sent.message_id), "w0:p1")

    async def test_group_prompts_keep_independent_pane_mappings(self):
        tg.relay_connected = True
        tg.agents = make_agents(2)
        first = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"}, chat_type="group", message_id=10)
        second = FakeCallback({"action": "select_reply", "pane_id": "w1:p1"}, chat_type="group", message_id=11)

        with patch.object(tg, "read_pane", AsyncMock(return_value="output")):
            await tg.handle_callback(make_update(chat_type="group", callback=first), SimpleNamespace())
            await tg.handle_callback(make_update(chat_type="group", callback=second), SimpleNamespace())

        first_text, first_kwargs, first_sent = first.message.replies[0]
        second_text, second_kwargs, second_sent = second.message.replies[0]
        self.assertNotIn("reply_markup", first_kwargs)
        self.assertNotIn("reply_markup", second_kwargs)
        self.assertIn("Reply to this message", first_text)
        self.assertEqual(tg.pending_pane(42, first_sent.message_id), "w0:p1")
        self.assertEqual(tg.pending_pane(42, second_sent.message_id), "w1:p1")

        first_reply = FakeMessage(chat_type="group")
        first_reply.reply_to_message = SimpleNamespace(message_id=first_sent.message_id)
        first_reply.text = "first response"
        second_reply = FakeMessage(chat_type="group")
        second_reply.reply_to_message = SimpleNamespace(message_id=second_sent.message_id)
        second_reply.text = "second response"
        with patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(chat_type="group", message=first_reply), SimpleNamespace())
            await tg.handle_text(make_update(chat_type="group", message=second_reply), SimpleNamespace())
        self.assertEqual(
            send_text.await_args_list,
            [unittest.mock.call("w0:p1", "first response"), unittest.mock.call("w1:p1", "second response")],
        )

    async def test_native_notification_reply_routes_to_associated_pane(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "follow up"

        with patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(message=message), SimpleNamespace())

        send_text.assert_awaited_once_with("w0:p1", "follow up")
        self.assertEqual(message.replies[0][0], "Sent")

    async def test_blocked_notification_reply_uses_prompt_response(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.blocked_prompt_ids["w0:p1"] = "prompt-123"
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "custom answer"

        with (
            patch.object(tg, "send_to_relay", AsyncMock()) as send_response,
            patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text,
        ):
            await tg.handle_text(make_update(message=message), SimpleNamespace())

        send_response.assert_awaited_once_with(
            "w0:p1", "custom answer", prompt_id="prompt-123"
        )
        send_text.assert_not_awaited()

    async def test_mapped_reply_rejects_disconnected_and_stale_panes(self):
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "follow up"

        with patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(message=message), SimpleNamespace())
            send_text.assert_not_awaited()
        self.assertIn("disconnected", message.replies[0][0].lower())

        tg.relay_connected = True
        message.replies.clear()
        with patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(message=message), SimpleNamespace())
            send_text.assert_not_awaited()
        self.assertIn("no longer available", message.replies[0][0].lower())

    async def test_unauthorized_native_reply_is_ignored(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        tg.register_pending(7, 77, "w0:p1")
        message = FakeMessage(chat_id=7)
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "not allowed"

        with patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(chat_id=7, message=message), SimpleNamespace())

        send_text.assert_not_awaited()
        self.assertEqual(message.replies, [])

    async def test_duplicate_cross_host_pane_identity_fails_closed(self):
        tg.relay_connected = True
        tg.agents = make_agents(2)
        tg.agents[1]["pane_id"] = tg.agents[0]["pane_id"]
        tg.agents[1]["host"] = "remote.example"
        button = tg.build_agent_keyboard("select_reply").inline_keyboard[0][0]
        callback = FakeCallback(button.callback_data)

        with patch.object(tg, "read_pane", AsyncMock()) as read_pane:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        read_pane.assert_not_awaited()
        self.assertIn("no longer available", callback.message.replies[0][0].lower())

    async def test_oversized_native_reply_is_rejected_before_relay_send(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "x" * 1001

        await tg.handle_text(make_update(message=message), SimpleNamespace())

        self.assertIn("1-1000", message.replies[0][0])


if __name__ == "__main__":
    unittest.main()
