import ast
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest import mock
import uuid


RELAY_PATH = Path(__file__).resolve().parents[1] / "relay" / "herdr_relay.py"


class _ConnectionClosed(Exception):
    pass


def _websockets_stubs():
    websockets = types.ModuleType("websockets")
    websockets.__path__ = []
    websockets_asyncio = types.ModuleType("websockets.asyncio")
    websockets_asyncio.__path__ = []
    websockets_server = types.ModuleType("websockets.asyncio.server")
    websockets_server.serve = object()
    exceptions = types.ModuleType("websockets.exceptions")
    exceptions.ConnectionClosedError = _ConnectionClosed
    exceptions.ConnectionClosedOK = _ConnectionClosed
    return {
        "websockets": websockets,
        "websockets.asyncio": websockets_asyncio,
        "websockets.asyncio.server": websockets_server,
        "websockets.exceptions": exceptions,
    }


@contextmanager
def loaded_relay(*, herdr_bin=None, relay_host=None, relay_token=None, trusted_origins=None):
    module_name = f"herdr_relay_test_{uuid.uuid4().hex}"
    logger = logging.getLogger("herdr-relay")
    original_handlers = tuple(logger.handlers)
    original_level = logger.level
    audit_logger = logging.getLogger("herdr-audit")
    original_audit_handlers = tuple(audit_logger.handlers)
    original_audit_level = audit_logger.level
    original_audit_disabled = audit_logger.disabled
    original_disabled = logger.disabled
    websockets_logger = logging.getLogger("websockets")
    original_websockets_level = websockets_logger.level

    with tempfile.TemporaryDirectory() as log_dir:
        environment = {"HERDR_LOG_DIR": log_dir}
        if herdr_bin is not None:
            environment["HERDR_BIN"] = herdr_bin
        if relay_host is not None:
            environment["HERDR_RELAY_HOST"] = relay_host
        if relay_token is not None:
            environment["HERDR_RELAY_TOKEN"] = relay_token
        if trusted_origins is not None:
            environment["HERDR_TRUSTED_ORIGINS"] = trusted_origins

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.dict(
            sys.modules, _websockets_stubs(), clear=False
        ):
            for name, value in (
                ("HERDR_BIN", herdr_bin),
                ("HERDR_RELAY_HOST", relay_host),
                ("HERDR_RELAY_TOKEN", relay_token),
                ("HERDR_TRUSTED_ORIGINS", trusted_origins),
            ):
                if value is None:
                    os.environ.pop(name, None)

            spec = importlib.util.spec_from_file_location(module_name, RELAY_PATH)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                logger.disabled = True
                yield module
            finally:
                sys.modules.pop(module_name, None)
                for handler in tuple(logger.handlers):
                    if handler not in original_handlers:
                        logger.removeHandler(handler)
                        handler.close()
                logger.setLevel(original_level)
                for handler in tuple(audit_logger.handlers):
                    if handler not in original_audit_handlers:
                        audit_logger.removeHandler(handler)
                        handler.close()
                audit_logger.setLevel(original_audit_level)
                audit_logger.disabled = original_audit_disabled
                logger.disabled = original_disabled
                websockets_logger.setLevel(original_websockets_level)


class _FakeWebSocket:
    def __init__(self, messages, headers=None):
        self.remote_address = ("127.0.0.1", 12345)
        self.request = types.SimpleNamespace(
            headers={
                "User-Agent": "Python unittest",
                "Origin": "",
                **(headers or {}),
            }
        )
        self._messages = iter(messages)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(message)


class RelayConfigurationTests(unittest.TestCase):
    def test_relay_defaults_to_loopback(self):
        with loaded_relay() as relay:
            self.assertEqual(relay.RELAY_HOST, "127.0.0.1")

    def test_herdr_defaults_to_path_lookup(self):
        with loaded_relay() as relay:
            expected = relay.shutil.which("herdr")
            if expected is None:
                expected = "herdr" if relay.sys.platform == "win32" else "/opt/homebrew/bin/herdr"
            self.assertEqual(relay.HERDR, expected)

    def test_herdr_bin_override_is_honored(self):
        configured_path = os.path.join("custom", "bin", "herdr")
        with loaded_relay(herdr_bin=configured_path) as relay:
            self.assertEqual(relay.HERDR, configured_path)

    def test_herdr_output_uses_utf8_decoding(self):
        with loaded_relay() as relay:
            completed = subprocess.CompletedProcess([], 0, stdout="ready\n", stderr="")
            with mock.patch.object(relay.subprocess, "run", return_value=completed) as run:
                for remote in (None, "agent-host"):
                    with self.subTest(remote=remote):
                        self.assertEqual(relay.run_herdr("pane", "list", remote=remote), "ready")
                        kwargs = run.call_args.kwargs
                        self.assertEqual(kwargs["encoding"], "utf-8")
                        self.assertEqual(kwargs["errors"], "replace")

    def test_herdr_output_falls_back_to_empty_string_on_invocation_error(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay.subprocess, "run", side_effect=OSError("unavailable")):
                self.assertEqual(relay.run_herdr("pane", "list"), "")


class RelayPaneStateTests(unittest.TestCase):
    def test_stale_panes_are_removed_from_agent_cache(self):
        with loaded_relay() as relay:
            relay.known_panes.update({"active-pane", "stale-pane"})
            relay.agent_cache.update({
                "active-pane": {"pane_id": "active-pane", "project": "current"},
                "stale-pane": {"pane_id": "stale-pane", "project": "old"},
            })

            relay.update_pane_maps([
                {"pane_id": "active-pane", "remote": None},
            ])

            # Stale panes are dropped; surviving entries are refreshed from the
            # poll rather than keeping whatever was cached previously.
            self.assertNotIn("stale-pane", relay.agent_cache)
            self.assertNotIn("stale-pane", relay.known_panes)
            self.assertEqual(
                relay.agent_cache,
                {"active-pane": {"pane_id": "active-pane", "remote": None}},
            )

    @staticmethod
    def _pane_list(label=None):
        pane = {
            "pane_id": "w1:p1",
            "agent": "claude",
            "agent_status": "idle",
            "cwd": "/home/user/personal",
            "workspace_id": "w1",
            "tab_id": "w1:t1",
        }
        if label is not None:
            pane["label"] = label
        return json.dumps({"result": {"panes": [pane]}})

    @staticmethod
    def _workspace_list(label="central AC"):
        return json.dumps({
            "result": {"workspaces": [{"workspace_id": "w1", "label": label}]}
        })

    def test_workspace_name_is_exposed_to_clients(self):
        with loaded_relay() as relay:
            with mock.patch.object(
                relay,
                "run_herdr",
                side_effect=[self._pane_list(), self._workspace_list()],
            ):
                agents = relay.get_agents_from_host()

            # Without this, clients fall back to the cwd basename ("personal").
            self.assertEqual(agents[0]["workspace_label"], "central AC")

    def test_workspace_name_does_not_overwrite_the_pane_label(self):
        with loaded_relay() as relay:
            with mock.patch.object(
                relay,
                "run_herdr",
                side_effect=[self._pane_list(label="pane name"), self._workspace_list()],
            ):
                agents = relay.get_agents_from_host()

            # Tab chips key off label, so it must stay pane-scoped.
            self.assertEqual(agents[0]["label"], "pane name")
            self.assertEqual(agents[0]["workspace_label"], "central AC")

    def test_pane_label_stays_empty_when_herdr_gives_none(self):
        with loaded_relay() as relay:
            with mock.patch.object(
                relay,
                "run_herdr",
                side_effect=[self._pane_list(), self._workspace_list()],
            ):
                agents = relay.get_agents_from_host()

            self.assertEqual(agents[0]["label"], "")

    def test_unusable_workspace_list_leaves_workspace_label_empty(self):
        for raw in ("", "not json", json.dumps({"result": {}})):
            with self.subTest(raw=raw):
                with loaded_relay() as relay:
                    with mock.patch.object(
                        relay,
                        "run_herdr",
                        side_effect=[self._pane_list(), raw],
                    ):
                        agents = relay.get_agents_from_host()

                    self.assertEqual(agents[0]["workspace_label"], "")
                    self.assertEqual(agents[0]["project"], "personal")

    def test_unusable_pane_list_skips_the_workspace_lookup(self):
        with loaded_relay() as relay:
            calls = []

            def record(*args, **kwargs):
                calls.append(args)
                return "not json"

            with mock.patch.object(relay, "run_herdr", side_effect=record):
                self.assertEqual(relay.get_agents_from_host(), [])

            # An unreachable SSH remote should not cost a second timeout.
            self.assertEqual(calls, [("pane", "list")])


class RelaySessionSwitchTests(unittest.TestCase):
    SESSION_LIST = (
        "name                 status   directory                socket\n"
        "default              stopped  /home/u/.config/herdr    /home/u/.config/herdr/herdr.sock\n"
        "personal             running  /home/u/.config/herdr/s  /home/u/.config/herdr/s/herdr.sock\n"
    )

    def test_get_sessions_parses_name_and_running_state(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay, "run_herdr", return_value=self.SESSION_LIST):
                sessions = relay.get_sessions()

            self.assertEqual(
                sessions,
                [{"name": "default", "running": False},
                 {"name": "personal", "running": True}],
            )

    def test_get_sessions_forwards_remote_to_run_herdr(self):
        # The one unpinned hop: a regression that dropped `remote=` here
        # would validate a *remote* switch against *local* session names --
        # a validation bypass. Not caught by shape alone; pin the call arg.
        with loaded_relay() as relay:
            with mock.patch.object(relay, "run_herdr", return_value=self.SESSION_LIST) as run_herdr:
                relay.get_sessions(remote="user@host")

            run_herdr.assert_called_once_with("session", "list", remote="user@host")

    def test_get_sessions_caches_within_ttl(self):
        # sessions_message() calls this once per source on every client
        # connect, each a blocking subprocess; a second call within the TTL
        # must not pay for another one.
        with loaded_relay() as relay:
            with mock.patch.object(relay, "run_herdr", return_value=self.SESSION_LIST) as run_herdr:
                first = relay.get_sessions(remote="user@host")
                second = relay.get_sessions(remote="user@host")

            self.assertEqual(first, second)
            run_herdr.assert_called_once_with("session", "list", remote="user@host")

    def test_reset_pane_state_clears_session_list_cache(self):
        # A real switch must always validate and display against a freshly
        # read session list, never a pre-switch one still inside its TTL.
        with loaded_relay() as relay:
            with mock.patch.object(relay, "run_herdr", return_value=self.SESSION_LIST) as run_herdr:
                relay.get_sessions(remote="user@host")
                relay.reset_pane_state()
                relay.get_sessions(remote="user@host")

            self.assertEqual(run_herdr.call_count, 2)

    def test_get_sessions_returns_empty_on_unusable_output(self):
        for raw in ("", "   ", "name status\n", "garbage"):
            with self.subTest(raw=raw):
                with loaded_relay() as relay:
                    with mock.patch.object(relay, "run_herdr", return_value=raw):
                        self.assertEqual(relay.get_sessions(), [])

    def test_herdr_env_sets_session_when_active(self):
        with loaded_relay() as relay:
            env = relay._herdr_env("personal")
            self.assertEqual(env["HERDR_SESSION"], "personal")

    def test_herdr_env_strips_inherited_session_for_default(self):
        # The relay's own env pins a session via config.env. Following herdr's
        # default session means removing it, not inheriting it.
        with loaded_relay() as relay:
            with mock.patch.dict(
                relay.os.environ,
                {"HERDR_SESSION": "personal", "HERDR_SOCKET_PATH": "/tmp/x.sock"},
            ):
                env = relay._herdr_env(None)

            self.assertNotIn("HERDR_SESSION", env)
            self.assertNotIn("HERDR_SOCKET_PATH", env)

    def test_active_session_falls_back_to_env_default_for_local(self):
        with loaded_relay() as relay:
            relay.ACTIVE_SESSIONS.clear()
            relay.DEFAULT_LOCAL_SESSION = "personal"

            self.assertEqual(relay.active_session_for(None), "personal")
            # A remote has no env default; it follows its own default session.
            self.assertIsNone(relay.active_session_for("user@host"))

    def test_active_session_prefers_explicit_selection(self):
        with loaded_relay() as relay:
            relay.DEFAULT_LOCAL_SESSION = "personal"
            relay.ACTIVE_SESSIONS[None] = "default"

            self.assertEqual(relay.active_session_for(None), "default")

    def test_explicit_none_overrides_env_default(self):
        # Selecting "herdr's default session" must not fall back to the env.
        with loaded_relay() as relay:
            relay.DEFAULT_LOCAL_SESSION = "personal"
            relay.ACTIVE_SESSIONS[None] = None

            self.assertIsNone(relay.active_session_for(None))

    def test_remote_invocation_prefixes_session_assignment(self):
        with loaded_relay() as relay:
            relay.ACTIVE_SESSIONS["user@host"] = "personal"
            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                return types.SimpleNamespace(stdout="", returncode=0)

            with mock.patch.object(relay.subprocess, "run", side_effect=fake_run):
                relay._invoke_herdr("pane", "list", remote="user@host")

            self.assertIn("HERDR_SESSION=personal", captured["cmd"])
            # The assignment must precede the binary in the remote argv.
            self.assertLess(
                captured["cmd"].index("HERDR_SESSION=personal"),
                captured["cmd"].index(relay.REMOTE_HERDR),
            )
            # An env= kwarg would not survive ssh; the remote session
            # assignment must travel via argv only, never the child env.
            self.assertNotIn("env", captured["kwargs"])

    def test_active_sessions_round_trip_through_state_file(self):
        with loaded_relay() as relay:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "active_sessions.json")
                with mock.patch.object(relay, "ACTIVE_SESSIONS_FILE", path):
                    relay.ACTIVE_SESSIONS.clear()
                    relay.ACTIVE_SESSIONS[None] = "default"
                    relay.ACTIVE_SESSIONS["user@host"] = "personal"
                    relay._save_active_sessions()

                    # JSON has no null keys, so local is stored as "local".
                    with open(path) as f:
                        self.assertEqual(
                            json.load(f), {"local": "default", "user@host": "personal"}
                        )

                    relay.ACTIVE_SESSIONS.clear()
                    relay._load_active_sessions()
                    self.assertEqual(
                        relay.ACTIVE_SESSIONS,
                        {None: "default", "user@host": "personal"},
                    )

    def test_load_active_sessions_ignores_non_string_values(self):
        # {"local": 5} parses and is a dict, but 5 would flow into
        # _herdr_env and make subprocess.run(env=...) raise TypeError,
        # which run_herdr swallows -- silently zeroing the agent list
        # forever, surviving every restart. The valid sibling entry must
        # still load: this isn't a whole-file reject, just a per-value one.
        with loaded_relay() as relay:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "active_sessions.json")
                with open(path, "w") as f:
                    json.dump({"local": 5, "user@host": "personal"}, f)
                with mock.patch.object(relay, "ACTIVE_SESSIONS_FILE", path):
                    relay.ACTIVE_SESSIONS.clear()
                    relay._load_active_sessions()
                    self.assertEqual(relay.ACTIVE_SESSIONS, {"user@host": "personal"})

    def test_load_active_sessions_tolerates_missing_and_corrupt_file(self):
        for content in (None, "not json", "[1,2,3]"):
            with self.subTest(content=content):
                with loaded_relay() as relay:
                    with tempfile.TemporaryDirectory() as tmp:
                        path = os.path.join(tmp, "active_sessions.json")
                        if content is not None:
                            with open(path, "w") as f:
                                f.write(content)
                        with mock.patch.object(relay, "ACTIVE_SESSIONS_FILE", path):
                            relay.ACTIVE_SESSIONS.clear()
                            relay._load_active_sessions()
                            self.assertEqual(relay.ACTIVE_SESSIONS, {})

    def test_reset_pane_state_clears_only_pane_keyed_state(self):
        with loaded_relay() as relay:
            relay.known_panes.add("w1:p1")
            relay.agent_cache["w1:p1"] = {"pane_id": "w1:p1"}
            relay.pane_remote_map["w1:p1"] = None
            relay.last_statuses["w1:p1"] = "working"
            relay.last_blocked_prompts["w1:p1"] = ("pid", (), "prompt?")
            relay.clients.add("sentinel-client")
            relay.push_subscriptions.append({"endpoint": "x"})
            before = relay.POLL_GENERATION

            relay.reset_pane_state()

            self.assertEqual(relay.known_panes, set())
            self.assertEqual(relay.agent_cache, {})
            self.assertEqual(relay.pane_remote_map, {})
            self.assertEqual(relay.last_statuses, {})
            self.assertEqual(relay.last_blocked_prompts, {})
            # Not pane-keyed; must survive a switch.
            self.assertIn("sentinel-client", relay.clients)
            self.assertEqual(len(relay.push_subscriptions), 1)
            self.assertEqual(relay.POLL_GENERATION, before + 1)

    def test_switch_does_not_leak_blocked_prompt_across_sessions(self):
        # w1:p1 exists in every session. update_pane_maps only prunes panes
        # absent from the new list, so without an explicit reset the old
        # fingerprint survives and suppresses a real notification.
        with loaded_relay() as relay:
            fingerprint = ("prompt-1", (), "Deploy to prod?")
            relay.known_panes.add("w1:p1")
            relay.last_blocked_prompts["w1:p1"] = fingerprint

            relay.update_pane_maps([{"pane_id": "w1:p1", "remote": None}])
            self.assertEqual(relay.last_blocked_prompts.get("w1:p1"), fingerprint)

            relay.reset_pane_state()
            self.assertIsNone(relay.last_blocked_prompts.get("w1:p1"))

    def test_stale_poll_bails_without_mutating_state(self):
        with loaded_relay() as relay:
            agents = [{"pane_id": "w1:p1", "agent": "claude", "status": "idle",
                       "cwd": "/tmp/x", "project": "x", "host": "local",
                       "remote": None, "label": "", "workspace_label": "",
                       "workspace_id": "w1", "tab_id": "w1:t1"}]

            async def switch_mid_broadcast(_message):
                relay.reset_pane_state()          # simulates a switch landing

            with mock.patch.object(relay, "get_all_agents", return_value=agents), \
                 mock.patch.object(relay, "broadcast", side_effect=switch_mid_broadcast):
                asyncio.run(relay._poll_once())

            # The poll must not repopulate state cleared by the switch.
            self.assertEqual(relay.last_statuses, {})

    def test_stale_poll_bails_after_blocked_broadcast_without_leaking_second_pane(self):
        # Two blocked panes: the first pane's broadcast is where the switch
        # lands. Without the post-broadcast/post-push generation check, the
        # loop would carry on to the second pane and re-seed its fingerprint.
        with loaded_relay() as relay:
            agents = [
                {"pane_id": "w1:p1", "agent": "claude", "status": "blocked",
                 "cwd": "/tmp/x", "project": "x", "host": "local", "remote": None},
                {"pane_id": "w1:p2", "agent": "claude", "status": "blocked",
                 "cwd": "/tmp/y", "project": "y", "host": "local", "remote": None},
            ]
            calls = {"blocked_broadcasts": 0}

            async def switch_on_first_blocked_broadcast(message):
                if message.get("type") == "agents":
                    return
                calls["blocked_broadcasts"] += 1
                if calls["blocked_broadcasts"] == 1:
                    relay.reset_pane_state()          # simulates a switch landing

            with mock.patch.object(relay, "get_all_agents", return_value=agents), \
                 mock.patch.object(relay, "read_pane", return_value="Deploy to prod?"), \
                 mock.patch.object(relay, "broadcast", side_effect=switch_on_first_blocked_broadcast), \
                 mock.patch.object(relay, "send_web_push", new=mock.AsyncMock()):
                asyncio.run(relay._poll_once())

            # The switch must stop the poll before it re-seeds a second pane.
            self.assertEqual(relay.last_blocked_prompts, {})

    def test_stale_poll_bails_after_clear_push_without_restoring_status(self):
        # last_statuses[pid] == "blocked" pre-set so the poll takes the
        # clear-push branch; the switch lands during send_web_push. Without
        # the post-push generation check, the trailing `last_statuses[pid] =
        # status` line would restore an entry the reset just cleared.
        with loaded_relay() as relay:
            relay.last_statuses["w1:p1"] = "blocked"
            agents = [{"pane_id": "w1:p1", "agent": "claude", "status": "idle",
                       "cwd": "/tmp/x", "project": "x", "host": "local", "remote": None}]

            async def switch_mid_clear_push(*args, **kwargs):
                relay.reset_pane_state()          # simulates a switch landing

            with mock.patch.object(relay, "get_all_agents", return_value=agents), \
                 mock.patch.object(relay, "broadcast", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "send_web_push", side_effect=switch_mid_clear_push):
                asyncio.run(relay._poll_once())

            # The switch must stop the poll from restoring last_statuses.
            self.assertEqual(relay.last_statuses, {})

    def test_stale_event_does_not_reseed_blocked_prompt_after_switch(self):
        # A queued agent_event that is already in hand (past reset's queue
        # drain) but whose processing straddles a reset_pane_state() must not
        # re-seed last_blocked_prompts with a stale fingerprint.
        with loaded_relay() as relay:
            event = {
                "type": "agent_event",
                "pane_id": "w1:p1",
                "agent": "claude",
                "status": "blocked",
                "cwd": "/tmp/x",
                "project": "x",
                "host": "local",
            }

            async def run_one_event():
                switched = asyncio.Event()

                async def switch_mid_broadcast(message):
                    if message.get("type") == "agents":
                        relay.reset_pane_state()          # simulates a switch landing
                        switched.set()

                with mock.patch.object(relay, "get_all_agents", return_value=[]), \
                     mock.patch.object(relay, "read_pane", return_value="Deploy to prod?"), \
                     mock.patch.object(relay, "broadcast", side_effect=switch_mid_broadcast):
                    task = asyncio.create_task(relay.event_push())
                    try:
                        await relay.event_queue.put(event)
                        await asyncio.wait_for(switched.wait(), timeout=1)
                    finally:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

            asyncio.run(run_one_event())

            self.assertEqual(relay.last_blocked_prompts, {})

    def test_reset_pane_state_drains_queued_events(self):
        # An event queued before a switch (never dequeued by event_push)
        # must not survive the switch to be processed afterward.
        with loaded_relay() as relay:
            event = {
                "type": "agent_event",
                "pane_id": "w1:p1",
                "agent": "claude",
                "status": "blocked",
                "cwd": "/tmp/x",
                "project": "x",
                "host": "local",
            }
            relay.event_queue.put_nowait(event)

            relay.reset_pane_state()

            self.assertTrue(relay.event_queue.empty())
            self.assertEqual(relay.agent_cache, {})
            self.assertEqual(relay.last_blocked_prompts, {})

    def test_apply_session_switch_updates_persists_and_resets(self):
        with loaded_relay() as relay:
            saved = {}
            relay.known_panes.add("w1:p1")

            with mock.patch.object(relay, "_save_active_sessions",
                                   side_effect=lambda: saved.update(relay.ACTIVE_SESSIONS)), \
                 mock.patch.object(relay, "audit"):
                ok, err, changed = relay.apply_session_switch(
                    "local", "default", names={"default", "personal"})

            self.assertTrue(ok)
            self.assertEqual(err, "")
            self.assertTrue(changed)
            self.assertEqual(relay.ACTIVE_SESSIONS[None], "default")
            self.assertEqual(saved[None], "default")
            self.assertEqual(relay.known_panes, set())   # reset happened

    def test_apply_session_switch_accepts_null_for_default_session(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay, "_save_active_sessions"), \
                 mock.patch.object(relay, "audit"):
                # None never touches the allowlist -- it means "follow herdr's own default".
                ok, err, changed = relay.apply_session_switch("local", None, names={"personal"})

            self.assertTrue(ok)
            self.assertTrue(changed)
            self.assertIsNone(relay.ACTIVE_SESSIONS[None])

    def test_apply_session_switch_rejects_unknown_session(self):
        # The value reaches a subprocess environment; it must be validated
        # against discovered names, never passed through. Names that merely
        # differ in case or extend a valid name must also be rejected -- a
        # denylist (e.g. a traversal/metacharacter filter) would let those
        # through, so this pins the exact-match allowlist specifically.
        with loaded_relay() as relay:
            relay.known_panes.add("w1:p1")
            before = dict(relay.ACTIVE_SESSIONS)
            with mock.patch.object(relay, "audit"):
                names = {"personal"}
                ok, err, changed = relay.apply_session_switch(
                    "local", "../../etc/passwd", names=names)
                self.assertFalse(ok)
                self.assertFalse(changed)
                self.assertIn("unknown session", err)

                ok, err, changed = relay.apply_session_switch("local", "personal2", names=names)
                self.assertFalse(ok)
                self.assertFalse(changed)
                self.assertIn("unknown session", err)

                ok, err, changed = relay.apply_session_switch("local", "Personal", names=names)
                self.assertFalse(ok)
                self.assertFalse(changed)
                self.assertIn("unknown session", err)

            self.assertEqual(relay.ACTIVE_SESSIONS, before)
            self.assertIn("w1:p1", relay.known_panes)

    def test_apply_session_switch_rejects_non_string_session(self):
        # session ends up in `x not in names` against a set; a list or dict
        # is unhashable there and must be rejected, not raise.
        with loaded_relay() as relay:
            before = dict(relay.ACTIVE_SESSIONS)
            with mock.patch.object(relay, "audit"):
                names = {"personal"}
                ok, err, changed = relay.apply_session_switch("local", ["personal"], names=names)
                self.assertFalse(ok)
                self.assertFalse(changed)
                self.assertIn("unknown session", err)

                ok, err, changed = relay.apply_session_switch(
                    "local", {"name": "personal"}, names=names)
                self.assertFalse(ok)
                self.assertFalse(changed)
                self.assertIn("unknown session", err)

            self.assertEqual(relay.ACTIVE_SESSIONS, before)

    def test_apply_session_switch_rejects_unknown_host(self):
        with loaded_relay() as relay:
            relay.REMOTES.clear()
            relay.known_panes.add("w1:p1")
            before = dict(relay.ACTIVE_SESSIONS)
            with mock.patch.object(relay, "audit"):
                ok, err, changed = relay.apply_session_switch(
                    "user@nope", "personal", names=None)

            self.assertFalse(ok)
            self.assertFalse(changed)
            self.assertIn("unknown host", err)
            self.assertEqual(relay.ACTIVE_SESSIONS, before)
            self.assertIn("w1:p1", relay.known_panes)

    def test_apply_session_switch_allows_stopped_session(self):
        with loaded_relay() as relay:
            # A stopped session is still selectable, and that property now lives in
            # session_switch_names -- it takes every entry's name, running or not.
            with mock.patch.object(relay, "get_sessions", return_value=[
                        {"name": "default", "running": False}]):
                names = relay.session_switch_names("local")
            self.assertEqual(names, {"default"})

            with mock.patch.object(relay, "_save_active_sessions"), \
                 mock.patch.object(relay, "audit"):
                ok, err, changed = relay.apply_session_switch("local", "default", names=names)

            self.assertTrue(ok)
            self.assertTrue(changed)

    def test_apply_session_switch_attributes_audit_to_caller(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay, "_save_active_sessions"), \
                 mock.patch.object(relay, "audit") as audit_mock:
                ok, err, changed = relay.apply_session_switch(
                    "local", "default", ip="10.0.0.5", device="phone", names={"default"})

            self.assertTrue(ok)
            self.assertTrue(changed)
            audit_mock.assert_called_once_with(
                "session_switch", "10.0.0.5", "phone", "",
                "host=local session=default")

    def test_apply_session_switch_completes_when_persistence_fails(self):
        # A save failure must not leave the switch half-applied: pane state
        # still has to reset and the action still has to audit, or a stale
        # cache under the new session is exactly the cross-session leak the
        # reset exists to prevent.
        with loaded_relay() as relay:
            relay.known_panes.add("w1:p1")
            with mock.patch.object(relay, "_save_active_sessions",
                                   side_effect=OSError("disk full")), \
                 mock.patch.object(relay, "audit") as audit_mock:
                ok, err, changed = relay.apply_session_switch(
                    "local", "default", names={"default"})

            self.assertTrue(ok)
            self.assertEqual(err, "")
            self.assertTrue(changed)
            self.assertEqual(relay.ACTIVE_SESSIONS[None], "default")
            self.assertEqual(relay.known_panes, set())   # reset still happened
            audit_mock.assert_called_once()

    def test_apply_session_switch_is_noop_when_already_active(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay, "_save_active_sessions"), \
                 mock.patch.object(relay, "audit"):
                ok, err, changed = relay.apply_session_switch(
                    "local", "default", names={"default"})
                self.assertTrue(ok)
                self.assertTrue(changed)   # the real switch

                relay.known_panes.add("w1:p1")
                ok, err, changed = relay.apply_session_switch(
                    "local", "default", names={"default"})

            self.assertTrue(ok)
            self.assertEqual(err, "")
            self.assertFalse(changed)   # the no-op re-selection
            self.assertIn("w1:p1", relay.known_panes)

    def test_session_switch_names_reports_an_unknown_host_as_none(self):
        """None, not an empty set: the handler passes it straight through, and an empty set
        would be indistinguishable from a host with no sessions on it."""
        with loaded_relay() as relay:
            relay.REMOTES.clear()
            with mock.patch.object(relay, "get_sessions") as sessions:
                self.assertIsNone(relay.session_switch_names("user@nope"))
            sessions.assert_not_called()

    def test_apply_session_switch_will_not_run_without_an_allowlist(self):
        """`names` is keyword-only with no default so this function cannot reach a blocking
        call. A caller that forgets it must fail loudly, not fall back to reading the list."""
        with loaded_relay() as relay:
            with self.assertRaises(TypeError):
                relay.apply_session_switch("local", "default")

    def test_an_empty_allowlist_rejects_every_named_session(self):
        """Fail closed: a source whose session list could not be read must not become a source
        where any name is accepted -- the value ends up interpolated into an ssh argv."""
        with loaded_relay() as relay:
            before = dict(relay.ACTIVE_SESSIONS)
            with mock.patch.object(relay, "audit"):
                for names in (set(), None):
                    with self.subTest(names=names):
                        ok, err, changed = relay.apply_session_switch(
                            "local", "default", names=names)
                        self.assertFalse(ok)
                        self.assertFalse(changed)
                        self.assertIn("unknown session", err)
            self.assertEqual(relay.ACTIVE_SESSIONS, before)

            # ...but following herdr's own default still works, since that names nothing.
            with mock.patch.object(relay, "_save_active_sessions"), \
                 mock.patch.object(relay, "audit"):
                ok, err, changed = relay.apply_session_switch("local", None, names=None)
            self.assertTrue(ok)
            self.assertTrue(changed)

    def test_sessions_message_shape(self):
        with loaded_relay() as relay:
            relay.REMOTES.clear()
            relay.ACTIVE_SESSIONS.clear()
            relay.ACTIVE_SESSIONS[None] = "personal"

            with mock.patch.object(relay, "get_sessions", return_value=[
                        {"name": "default", "running": False},
                        {"name": "personal", "running": True}]):
                message = relay.sessions_message()

            self.assertEqual(message["type"], "sessions")
            self.assertEqual(len(message["sources"]), 1)
            source = message["sources"][0]
            self.assertEqual(source["host"], "local")
            self.assertEqual(source["active"], "personal")
            self.assertEqual(
                source["sessions"],
                [{"name": "default", "running": False},
                 {"name": "personal", "running": True}],
            )

    def test_sessions_message_includes_each_remote(self):
        with loaded_relay() as relay:
            relay.REMOTES[:] = ["user@host"]
            relay.ACTIVE_SESSIONS.clear()

            with mock.patch.object(relay, "get_sessions", return_value=[]) as get_sessions:
                message = relay.sessions_message()

            self.assertEqual([s["host"] for s in message["sources"]],
                             ["local", "user@host"])
            # Pins per-source correctness: a call to get_sessions() with no
            # `remote` (always listing LOCAL) would produce the same host
            # list above but silently report local sessions under every
            # remote's host.
            get_sessions.assert_has_calls(
                [mock.call(remote=None), mock.call(remote="user@host")]
            )

    def test_broadcast_sessions_skips_send_when_generation_changes_mid_build(self):
        # get_sessions() is called synchronously while building the message;
        # if a switch lands (bumping POLL_GENERATION) during that build, the
        # message reflects a mix of pre/post-switch state and must not go out.
        with loaded_relay() as relay:
            def stale_get_sessions(remote=None):
                relay.reset_pane_state()  # simulates a switch landing mid-build
                return []

            with mock.patch.object(relay, "get_sessions", side_effect=stale_get_sessions), \
                 mock.patch.object(relay, "broadcast", new=mock.AsyncMock()) as broadcast_mock:
                asyncio.run(relay.broadcast_sessions())

            broadcast_mock.assert_not_awaited()

    def test_session_switch_applies_with_caller_identity_then_broadcasts_acks_and_repolls(self):
        with loaded_relay() as relay:
            request_id = "switch-1"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "session_switch",
                    "host": "user@host",
                    "session": "personal",
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )
            order = []

            def fake_apply(host, session, ip, device, *, names):
                order.append(("apply", host, session, ip, device, names))
                return True, "", True

            async def fake_broadcast_sessions():
                order.append(("broadcast_sessions",))

            async def fake_poll_once():
                order.append(("poll_once",))

            async def record_send(message):
                order.append(("send", message))

            ws.send = record_send

            with mock.patch.object(relay, "session_switch_names", return_value={"personal"}), \
                 mock.patch.object(relay, "apply_session_switch", side_effect=fake_apply) as apply_mock, \
                 mock.patch.object(relay, "broadcast_sessions", side_effect=fake_broadcast_sessions), \
                 mock.patch.object(relay, "_poll_once", side_effect=fake_poll_once):
                asyncio.run(relay.handle_client(ws))

            apply_mock.assert_called_once_with(
                "user@host", "personal", "127.0.0.1", "script", names={"personal"})
            self.assertEqual(
                [step[0] for step in order],
                ["apply", "broadcast_sessions", "send", "poll_once"],
            )
            self.assertEqual(
                json.loads(order[2][1]),
                {
                    "type": "command_result",
                    "command": "session_switch",
                    "request_id": request_id,
                    "ok": True,
                },
            )

    def test_session_switch_rejected_sends_error_with_request_id_and_skips_broadcast(self):
        with loaded_relay() as relay:
            request_id = "switch-2"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "session_switch",
                    "host": "bogus",
                    "session": "default",
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )

            with mock.patch.object(relay, "session_switch_names", return_value=None), \
                 mock.patch.object(
                        relay, "apply_session_switch",
                        return_value=(False, "unknown host: bogus", False)) as apply_mock, \
                 mock.patch.object(relay, "broadcast_sessions", new=mock.AsyncMock()) as broadcast_mock, \
                 mock.patch.object(relay, "_poll_once", new=mock.AsyncMock()) as poll_mock:
                asyncio.run(relay.handle_client(ws))

            apply_mock.assert_called_once_with(
                "bogus", "default", "127.0.0.1", "script", names=None)
            self.assertEqual(
                json.loads(ws.sent[-1]),
                {
                    "type": "error",
                    "message": "unknown host: bogus",
                    "request_id": request_id,
                },
            )
            broadcast_mock.assert_not_awaited()
            poll_mock.assert_not_awaited()

    def test_session_switch_noop_acks_without_broadcast_or_repoll(self):
        # apply_session_switch's own no-op short-circuit (skipping the
        # blocking `herdr session list` call and the pane-state reset) is
        # defeated if the handler runs the broadcast + re-poll anyway --
        # those are exactly the expensive part `changed` exists to let the
        # handler skip.
        with loaded_relay() as relay:
            request_id = "switch-3"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "session_switch",
                    "host": "local",
                    "session": "default",
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )

            with mock.patch.object(relay, "session_switch_names", return_value={"default"}), \
                 mock.patch.object(
                        relay, "apply_session_switch",
                        return_value=(True, "", False)) as apply_mock, \
                 mock.patch.object(relay, "broadcast_sessions", new=mock.AsyncMock()) as broadcast_mock, \
                 mock.patch.object(relay, "_poll_once", new=mock.AsyncMock()) as poll_mock:
                asyncio.run(relay.handle_client(ws))

            apply_mock.assert_called_once_with(
                "local", "default", "127.0.0.1", "script", names={"default"})
            broadcast_mock.assert_not_awaited()
            poll_mock.assert_not_awaited()
            self.assertEqual(
                json.loads(ws.sent[-1]),
                {
                    "type": "command_result",
                    "command": "session_switch",
                    "request_id": request_id,
                    "ok": True,
                },
            )


class RelayResponseTests(unittest.TestCase):
    def test_respond_sends_correlated_acknowledgement(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            relay.known_panes.add(pane_id)
            content = "yes, single permission"
            request_id = "request-123"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "yes",
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "pane_is_omp", return_value=False), \
                 mock.patch.object(relay.subprocess, "run", return_value=completed):
                asyncio.run(relay.handle_client(ws))

            self.assertEqual(
                json.loads(ws.sent[-1]),
                {
                    "type": "command_result",
                    "command": "respond",
                    "ok": True,
                    "request_id": request_id,
                },
            )

    def test_respond_strips_crlf_and_sends_canonical_text_before_enter(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            remote = "agent-host"
            relay.known_panes.add(pane_id)
            relay.pane_remote_map[pane_id] = remote
            content = "yes, single permission"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "  yes\r\n",
                })]
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "pane_is_omp", return_value=False), \
                 mock.patch.object(relay.subprocess, "run", return_value=completed) as run:
                asyncio.run(relay.handle_client(ws))

            command_prefix = [
                "ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", remote, relay.REMOTE_HERDR
            ]
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    [*command_prefix, "pane", "send-text", pane_id, "yes"],
                    [*command_prefix, "pane", "send-keys", pane_id, "Enter"],
                ],
            )
            for call in run.call_args_list:
                self.assertFalse(any("\r" in arg or "\n" in arg for arg in call.args[0]))

    def test_respond_does_not_send_enter_when_send_text_fails(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            relay.known_panes.add(pane_id)
            content = "yes, single permission"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "yes",
                })]
            )
            failed = subprocess.CompletedProcess([], 1, stdout="", stderr="failed")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "pane_is_omp", return_value=False), \
                 mock.patch.object(relay.subprocess, "run", return_value=failed) as run:
                asyncio.run(relay.handle_client(ws))

            run.assert_called_once()
            self.assertEqual(
                run.call_args.args[0],
                [relay.HERDR, "pane", "send-text", pane_id, "yes"],
            )



class RelayQuestionTests(unittest.TestCase):
    ASK_SCREEN = """
╭─ Ask ─╮
│ Which color? │
│   Red │
│    Blue │
│    Green │
│    Other (type your own) │
│ Enter select · ↑/↓ move · Esc cancel │
╰───────╯
"""
    MULTI_SCREEN = """
╭─ Ask ─╮
│ Which capabilities? │
│   Color output │
│    Nerd Font │
│    Mobile layout │
│    Other (type your own) │
╰───────╯
"""

    MULTI_SELECTED_SCREEN = """
╭─ Ask ─╮
│ capabilities    Submit │
│ Which capabilities? │
│    Color output │
│   Nerd Font │
│    Mobile layout │
│    Other (type your own) │
╰───────╯
"""


    def test_detects_live_omp_question_options_and_cursor(self):
        with loaded_relay() as relay:
            question = relay.detect_question(self.ASK_SCREEN)

            self.assertIsNotNone(question)
            self.assertEqual(
                [option["label"] for option in question["options"]],
                ["Red", "Blue", "Green", relay.QUESTION_OTHER],
            )
            self.assertEqual(question["selected_index"], 0)
            self.assertEqual(relay.detect_options(self.ASK_SCREEN), ["Red", "Blue", "Green"])

    def test_prompt_identity_includes_question_text(self):
        first_prompt = self.ASK_SCREEN.replace("Which color?", "Which environment?")
        second_prompt = self.ASK_SCREEN.replace("Which color?", "Delete all data?")

        with loaded_relay() as relay:
            self.assertNotEqual(
                relay.question_prompt_id("pane-1", first_prompt),
                relay.question_prompt_id("pane-1", second_prompt),
            )

    def test_long_questions_with_identical_options_have_distinct_identity(self):
        first_prompt = "Which deployment target should receive this very long request?\n" + "\n".join(
            f"detail line {index}" for index in range(35)
        ) + "\n  staging\n   production\n   Other (type your own)"
        second_prompt = first_prompt.replace(
            "Which deployment target should receive this very long request?",
            "Which database should receive this very long request?",
        )

        with loaded_relay() as relay:
            self.assertNotEqual(
                relay.question_prompt_id("pane-1", first_prompt),
                relay.question_prompt_id("pane-1", second_prompt),
            )

    def test_read_pane_preserves_long_question_for_prompt_identity(self):
        def pane_output(question):
            return "\n".join([
                question,
                *(f"detail line {index}" for index in range(35)),
                "  staging",
                "   production",
                "   Other (type your own)",
            ])

        with loaded_relay() as relay:
            with mock.patch.object(
                relay,
                "run_herdr",
                side_effect=[
                    pane_output("Which deployment target should receive this request?"),
                    pane_output("Which database should receive this request?"),
                ],
            ):
                first = relay.read_pane("pane-1")
                second = relay.read_pane("pane-1")

            self.assertNotEqual(
                relay.question_prompt_id("pane-1", first),
                relay.question_prompt_id("pane-1", second),
            )

    def test_prompt_identity_ignores_multi_selection_state(self):
        with loaded_relay() as relay:
            self.assertEqual(
                relay.question_prompt_id("pane-1", self.MULTI_SCREEN),
                relay.question_prompt_id("pane-1", self.MULTI_SELECTED_SCREEN),
            )

    def test_unknown_blocked_prompt_has_no_approval_fallback(self):
        with loaded_relay() as relay:
            message = relay.blocked_message("pane-1", "omp", "project", "local", "What name?")

            self.assertEqual(message["options"], [])
            self.assertEqual(message["interaction"], "prompt")

    def test_question_choice_moves_from_live_cursor_before_enter(self):
        with loaded_relay() as relay:
            question = relay.detect_question(self.ASK_SCREEN)
            with mock.patch.object(relay, "_mutate_herdr", return_value=True) as mutate:
                delivered = relay.respond_to_question(
                    "pane-1", "Blue", question, remote="agent-host"
                )

            self.assertTrue(delivered)
            mutate.assert_called_once_with(
                "pane", "send-keys", "pane-1", "Down", "Enter", remote="agent-host"
            )

    def test_custom_question_answer_waits_for_editor_then_submits(self):
        with loaded_relay() as relay:
            question = relay.detect_question(self.ASK_SCREEN)
            with mock.patch.object(
                relay,
                "read_pane",
                return_value="Custom answer: Which color?\n>\nenter or ctrl+q submit",
            ), mock.patch.object(relay, "_mutate_herdr", return_value=True) as mutate:
                delivered = relay.respond_to_question(
                    "pane-1", "Purple", question, remote=None
                )

            self.assertTrue(delivered)
            self.assertEqual(
                mutate.call_args_list,
                [
                    mock.call("pane", "send-keys", "pane-1", "Down", "Down", "Down", "Enter", remote=None),
                    mock.call("pane", "send-text", "pane-1", "Purple", remote=None),
                    mock.call("pane", "send-keys", "pane-1", "Enter", remote=None),
                ],
            )



    def test_multi_question_toggle_and_done_submission_use_live_cursor(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay, "pane_is_omp", return_value=True), \
                 mock.patch.object(
                     relay,
                     "read_pane",
                     side_effect=[self.MULTI_SCREEN, self.MULTI_SELECTED_SCREEN],
                 ), mock.patch.object(relay, "_mutate_herdr", return_value=True) as mutate:
                toggled = relay.toggle_question_option("pane-1", "Nerd Font")
                submitted = relay.submit_multi_question("pane-1")

            self.assertTrue(toggled)
            self.assertTrue(submitted)
            self.assertEqual(
                mutate.call_args_list,
                [
                    mock.call("pane", "send-keys", "pane-1", "Down", remote=None),
                    mock.call("pane", "send-keys", "pane-1", "Enter", remote=None),
                    mock.call("pane", "send-keys", "pane-1", "Tab", "Enter", remote=None),
                ],
            )

    def test_non_omp_checkbox_prompt_never_uses_question_navigation(self):
        with loaded_relay() as relay:
            message = relay.blocked_message("pane-1", "claude", "project", "local", self.MULTI_SCREEN)

            self.assertEqual(message["interaction"], "prompt")
            self.assertEqual(message["options"], [])

    def test_arbitrary_response_is_rejected_for_non_question_prompt(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            relay.known_panes.add(pane_id)
            content = "Approve this tool?"
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "run arbitrary command",
                })
            ])
            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "_mutate_herdr") as mutate:
                asyncio.run(relay.handle_client(ws))

            mutate.assert_not_called()
            self.assertIn("detected question", json.loads(ws.sent[-1])["message"])

    def test_stale_standard_approval_is_rejected_before_delivery(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            old_content = "Run read-only status command?\nyes, single permission"
            current_content = "Delete production data?\nyes, single permission"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, old_content),
                    "text": "yes, single permission",
                })
            ])

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=current_content), \
                 mock.patch.object(relay, "_mutate_herdr") as mutate:
                asyncio.run(relay.handle_client(ws))

            mutate.assert_not_called()
            self.assertIn("prompt changed", json.loads(ws.sent[-1])["message"])

    def test_stale_custom_editor_response_is_rejected_before_delivery(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            old_content = "Enter your response:\nWhich environment?"
            current_content = "Enter your response:\nType the production deletion token"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, old_content),
                    "text": "staging",
                })
            ])

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=current_content), \
                 mock.patch.object(relay, "_mutate_herdr") as mutate:
                asyncio.run(relay.handle_client(ws))

            mutate.assert_not_called()
            self.assertIn("prompt changed", json.loads(ws.sent[-1])["message"])

    def test_stale_standard_approval_key_is_rejected_before_delivery(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            old_content = "Run read-only status command?\nyes, single permission"
            current_content = "Delete production data?\nyes, single permission"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "send_keys",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, old_content),
                    "keys": ["1"],
                })
            ])

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=current_content), \
                 mock.patch.object(relay, "run_herdr_result") as run:
                asyncio.run(relay.handle_client(ws))

            run.assert_not_called()
            self.assertIn("prompt changed", json.loads(ws.sent[-1])["message"])


class RelayKeyGrammarTests(unittest.TestCase):
    """The relay forwards keys in herdr's grammar, not tmux's.

    Live-verified against herdr 0.8.0 (protocol 19) on a throwaway session: `C-c` is the only
    tmux-style spelling herdr still aliases, `BSpace` was never valid, and the `+`-joined chords
    the web app composes at runtime are.
    """

    # herdr matches special names case-insensitively, so a client spelling `esc` or `shift+tab`
    # in lower case is not wrong.
    ACCEPTED = ("ctrl+c", "shift+tab", "alt+Up", "ctrl+shift+p", "C-c", "Enter", "Escape",
                "esc", "tab", "Space", "Backspace", "F5", "f12", "Up", "1", "y")
    # PageUp/PageDown/Home/End are accepted by the RELAY but refused by herdr in every spelling
    # (re-probed on 0.8.2); they leave as CSI bytes through `pane send-text`. Insert and Delete are
    # equally refused by herdr and deliberately not covered, which is why they stay rejected here.
    ACCEPTED = ACCEPTED + ("PageUp", "pagedown", "Home", "End", "ctrl+Home", "shift+PageUp")
    REJECTED = ("BSpace", "BTab", "PgUp", "Insert", "Delete",
                "C-u", "M-x", "cmd+q", "ctrl+", "+c", "", "nonsense",
                "ctrl+ctrl+c", "ctrl+ctrl+Home")

    def test_key_grammar_matches_what_herdr_accepts(self):
        with loaded_relay() as relay:
            for key in self.ACCEPTED:
                with self.subTest(key=key):
                    self.assertTrue(relay.key_is_allowed(key), f"{key!r} must be forwarded")
            for key in self.REJECTED:
                with self.subTest(key=key):
                    self.assertFalse(relay.key_is_allowed(key), f"{key!r} must be refused")

    def test_non_string_keys_are_refused(self):
        with loaded_relay() as relay:
            for key in (None, 1, ["ctrl", "c"], {"key": "c"}):
                with self.subTest(key=key):
                    self.assertFalse(relay.key_is_allowed(key))

    def test_send_keys_forwards_a_modifier_chord_the_web_app_composes(self):
        """The Ctrl presets in web/index.html were rejected wholesale by the old allowlist."""
        pane_id = "w0:p1"
        with loaded_relay() as relay:
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([json.dumps({
                "type": "send_keys",
                "pane_id": pane_id,
                "keys": ["ctrl+c"],
                "request_id": "req-1",
            })])
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=""), \
                 mock.patch.object(relay, "run_herdr_result", return_value=completed) as run:
                asyncio.run(relay.handle_client(ws))

            self.assertEqual(
                run.call_args.args,
                ("pane", "send-keys", pane_id, "ctrl+c"),
            )
            self.assertEqual(
                [json.loads(message) for message in ws.sent],
                [{"type": "command_result", "command": "send_keys", "ok": True, "request_id": "req-1"}],
            )

    def test_send_keys_refuses_an_empty_or_non_list_keys_field(self):
        pane_id = "w0:p1"
        for keys in ([], "ctrl+c", None):
            with self.subTest(keys=keys):
                with loaded_relay() as relay:
                    relay.known_panes.add(pane_id)
                    ws = _FakeWebSocket([json.dumps({
                        "type": "send_keys", "pane_id": pane_id, "keys": keys,
                    })])
                    with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                         mock.patch.object(relay, "read_pane", return_value=""), \
                         mock.patch.object(relay, "run_herdr_result") as run:
                        asyncio.run(relay.handle_client(ws))
                    run.assert_not_called()
                    self.assertEqual(json.loads(ws.sent[0])["type"], "error")

    def test_a_refused_key_is_named_and_logged(self):
        """The refusal returns above the log line, so it used to leave no trace at all.

        That is precisely the case worth diagnosing -- a client newer than the relay it talks to --
        so both the message and the log say which key, and only which key.
        """
        pane_id = "w0:p1"
        with loaded_relay() as relay:
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([json.dumps({
                "type": "send_keys", "pane_id": pane_id, "keys": ["Enter", "Insert"],
            })])
            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=""), \
                 mock.patch.object(relay, "run_herdr_result") as run, \
                 mock.patch.object(relay.log, "warning") as warned:
                asyncio.run(relay.handle_client(ws))
            run.assert_not_called()
            message = json.loads(ws.sent[0])["message"]
            self.assertIn("Insert", message)
            self.assertNotIn("Enter", message, "only the refused key belongs in the message")
            self.assertTrue(
                any("Insert" in str(call) for call in warned.call_args_list),
                "the refused key must reach the log")


class RelayCsiKeyTests(unittest.TestCase):
    """Keys herdr refuses outright, delivered as the CSI bytes a terminal would send.

    Live-probed on herdr 0.8.2: `pane send-keys` answers `unsupported key PageUp` to every
    spelling of PageUp/PageDown/Home/End, while `pane send-text` passes ESC through byte for byte
    (`cat -v` in a throwaway pane showed `^[[5~`). `less` on a 500-line file then paged from row 1
    to row 70 on ESC[6~ and back on ESC[5~, so a real TUI reads the bytes AS the key.
    """

    # xterm's encoding: 1 + shift(1) + alt(2) + ctrl(4). The tilde keys carry the modifier as a
    # second parameter; the letter keys grow a leading `1;` they do not have when bare.
    EXPECTED = {
        "PageUp": "\x1b[5~",
        "PageDown": "\x1b[6~",
        "pageup": "\x1b[5~",
        "Home": "\x1b[H",
        "End": "\x1b[F",
        "ctrl+Home": "\x1b[1;5H",
        "ctrl+End": "\x1b[1;5F",
        "shift+PageUp": "\x1b[5;2~",
        "alt+PageDown": "\x1b[6;3~",
        "ctrl+shift+Home": "\x1b[1;6H",
    }

    def test_sequences_match_the_terminal_encoding(self):
        with loaded_relay() as relay:
            for key, sequence in self.EXPECTED.items():
                with self.subTest(key=key):
                    self.assertEqual(relay.key_escape_sequence(key), sequence)

    def test_keys_herdr_can_send_itself_get_no_sequence(self):
        """An empty return is what routes a key to `send-keys`, so it must stay empty."""
        with loaded_relay() as relay:
            for key in ("Enter", "Escape", "ctrl+c", "Up", "C-c", "y", "F5", "Delete", "Insert"):
                with self.subTest(key=key):
                    self.assertEqual(relay.key_escape_sequence(key), "")

    def test_a_malformed_modifier_resolves_to_nothing_rather_than_the_bare_key(self):
        """Silently dropping the modifier would send Home when the client asked for cmd+Home."""
        with loaded_relay() as relay:
            for key in ("cmd+Home", "ctrl+ctrl+Home", "super+End", "ctrl+", "+Home", None, 5):
                with self.subTest(key=key):
                    self.assertEqual(relay.key_escape_sequence(key), "")

    def test_a_page_key_goes_out_as_text_not_as_a_key(self):
        pane_id = "w0:p1"
        with loaded_relay() as relay:
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([json.dumps({
                "type": "send_keys", "pane_id": pane_id, "keys": ["PageUp"], "request_id": "req-1",
            })])
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=""), \
                 mock.patch.object(relay, "run_herdr_result", return_value=completed) as run:
                asyncio.run(relay.handle_client(ws))
            self.assertEqual(run.call_args.args, ("pane", "send-text", pane_id, "\x1b[5~"))
            self.assertEqual(
                [json.loads(message) for message in ws.sent],
                [{"type": "command_result", "command": "send_keys", "ok": True, "request_id": "req-1"}],
            )

    def test_a_mixed_queue_keeps_its_order_across_the_two_channels(self):
        """The key queue composes arbitrary runs, and [Escape, PageUp, Enter] must arrive so."""
        pane_id = "w0:p1"
        with loaded_relay() as relay:
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([json.dumps({
                "type": "send_keys", "pane_id": pane_id,
                "keys": ["Escape", "PageUp", "PageDown", "Enter"],
            })])
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=""), \
                 mock.patch.object(relay, "run_herdr_result", return_value=completed) as run:
                asyncio.run(relay.handle_client(ws))
            self.assertEqual(
                [call.args for call in run.call_args_list],
                [
                    ("pane", "send-keys", pane_id, "Escape"),
                    # One call, because a run of CSI keys concatenates into one text argument.
                    ("pane", "send-text", pane_id, "\x1b[5~\x1b[6~"),
                    ("pane", "send-keys", pane_id, "Enter"),
                ],
            )

    def test_a_failing_text_call_reports_and_stops(self):
        """A half-delivered queue must not answer ok, and must not send the rest."""
        pane_id = "w0:p1"
        with loaded_relay() as relay:
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([json.dumps({
                "type": "send_keys", "pane_id": pane_id, "keys": ["PageUp", "Enter"],
            })])
            failed = subprocess.CompletedProcess([], 1, stdout="", stderr="nope")
            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=""), \
                 mock.patch.object(relay, "run_herdr_result", return_value=failed) as run:
                asyncio.run(relay.handle_client(ws))
            self.assertEqual(len(run.call_args_list), 1)
            self.assertEqual(json.loads(ws.sent[0])["type"], "error")


class RelayCommandTests(unittest.TestCase):
    def test_command_connection_skips_snapshot_and_correlates_ack(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            request_id = "request-123"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "send_keys",
                    "pane_id": pane_id,
                    "keys": ["C-c"],
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()) as snapshot, \
                 mock.patch.object(relay, "read_pane", return_value=""), \
                 mock.patch.object(relay, "run_herdr_result", return_value=completed):
                asyncio.run(relay.handle_client(ws))

            snapshot.assert_not_awaited()
            self.assertEqual(
                json.loads(ws.sent[-1]),
                {
                    "type": "command_result",
                    "command": "send_keys",
                    "ok": True,
                    "request_id": request_id,
                },
            )


class RelayEventPushTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_agent_event_broadcasts_snapshot_before_blocked_prompt(self):
        complete_snapshot = [
            {
                "pane_id": "event-pane",
                "agent": "omp",
                "status": "blocked",
                "cwd": "/projects/current",
                "project": "current",
                "host": "local",
                "remote": None,
            },
            {
                "pane_id": "unrelated-pane",
                "agent": "claude",
                "status": "idle",
                "cwd": "/projects/other",
                "project": "other",
                "host": "agent-host",
                "remote": "agent-host",
            },
        ]
        event = {
            "type": "agent_event",
            "pane_id": "event-pane",
            "agent": "omp",
            "status": "blocked",
            "cwd": "/projects/current",
            "project": "current",
            "host": "local",
        }
        fallback_agent = {
            "pane_id": "event-pane",
            "agent": "omp",
            "status": "blocked",
            "cwd": "/projects/current",
            "project": "current",
            "host": "local",
            "remote": "existing-host",
        }

        cases = (
            ("complete", complete_snapshot, complete_snapshot),
            ("empty", [], [fallback_agent]),
        )
        for case, snapshot, expected_agents in cases:
            with self.subTest(case=case), loaded_relay() as relay:
                relay.known_panes.update({"event-pane", "stale-pane"})
                relay.pane_remote_map["event-pane"] = "existing-host"
                relay.pane_remote_map["stale-pane"] = "old-host"
                relay.last_statuses["stale-pane"] = "idle"
                messages = []
                broadcasts_complete = asyncio.Event()

                async def capture_broadcast(message):
                    messages.append(message)
                    if len(messages) == 2:
                        broadcasts_complete.set()

                with mock.patch.object(
                    relay, "get_all_agents", return_value=snapshot
                ), mock.patch.object(
                    relay, "read_pane", return_value="approve all pending"
                ) as read_pane, mock.patch.object(
                    relay, "broadcast", side_effect=capture_broadcast
                ):
                    task = asyncio.create_task(relay.event_push())
                    try:
                        await relay.event_queue.put(event)
                        await asyncio.wait_for(broadcasts_complete.wait(), timeout=1)
                    finally:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

                expected_prompt_id = relay.question_prompt_id("event-pane", "approve all pending")
                self.assertEqual(
                    messages,
                    [
                        {"type": "agents", "agents": expected_agents},
                        {
                            "type": "blocked",
                            "pane_id": "event-pane",
                            "agent": "omp",
                            "project": "current",
                            "host": "local",
                            "prompt": "approve all pending",
                            "prompt_id": expected_prompt_id,
                            "options": relay.SUBAGENT_OPTIONS,
                            "multi_options": [],
                            "selected_options": [],
                            "interaction": "prompt",
                            "multi": False,
                            "update": False,
                        },
                    ],
                )
                expected_event_remote = expected_agents[0].get("remote")
                read_pane.assert_called_once_with(
                    "event-pane", remote=expected_event_remote
                )
                expected_pane_ids = {agent["pane_id"] for agent in expected_agents}
                expected_remote_map = {
                    agent["pane_id"]: agent.get("remote") for agent in expected_agents
                }
                self.assertEqual(relay.known_panes, expected_pane_ids)
                self.assertEqual(relay.pane_remote_map, expected_remote_map)
                self.assertNotIn("stale-pane", relay.last_statuses)


class RelayTerminalTitleTests(unittest.TestCase):
    """The title is live activity or it is the harness saying its own name -- never a session."""

    def test_a_working_agents_activity_survives_and_the_banner_does_not(self):
        with loaded_relay() as relay:
            # What a working claude puts there.
            self.assertEqual(relay.activity_title("fix P0, draft the P1 plan", "claude"),
                             "fix P0, draft the P1 plan")
            # What an idle or done one leaves behind. Measured on one real host: of nine agent
            # panes, seven reported no title and the two that did reported exactly this.
            self.assertEqual(relay.activity_title("Claude Code", "claude"), "")
            self.assertEqual(relay.activity_title("\u2733 Claude Code", "claude"), "")
            # Prefix matching, so a new harness needs no entry in a list.
            self.assertEqual(relay.activity_title("Codex", "codex"), "")
            self.assertEqual(relay.activity_title("OpenCode", "opencode"), "")
            self.assertEqual(relay.activity_title(None, "claude"), "")
            self.assertEqual(relay.activity_title("   ", "claude"), "")
            # A title that merely mentions the harness is still a title.
            self.assertEqual(relay.activity_title("porting the claude reader", "claude"),
                             "porting the claude reader")
            # No agent name to compare against: nothing is a banner.
            self.assertEqual(relay.activity_title("Claude Code", ""), "Claude Code")

    def test_the_agents_entry_carries_the_activity_and_drops_the_banner(self):
        listing = json.dumps({"result": {"panes": [
            {"pane_id": "w1:p1", "agent": "claude", "agent_status": "working",
             "cwd": "/w/herdr", "workspace_id": "w1", "tab_id": "w1:t1",
             "terminal_title_stripped": "fix the poll"},
            {"pane_id": "w1:p2", "agent": "claude", "agent_status": "done",
             "cwd": "/w/herdr", "workspace_id": "w1", "tab_id": "w1:t1",
             "terminal_title_stripped": "Claude Code"},
        ]}})
        with loaded_relay() as relay:
            with mock.patch.object(relay, "run_herdr", return_value=listing), \
                 mock.patch.object(relay, "get_workspace_labels", return_value={}):
                agents = relay.get_agents_from_host()
            self.assertEqual([a["title"] for a in agents], ["fix the poll", ""])


class RelaySubprocessConcurrencyTests(unittest.TestCase):
    def test_calls_to_the_same_remote_are_serialized(self):
        with loaded_relay() as relay:
            first_entered = threading.Event()
            second_started = threading.Event()
            second_entered = threading.Event()
            release_first = threading.Event()
            invocation_count = 0
            count_lock = threading.Lock()

            def fake_subprocess_run(command, **kwargs):
                nonlocal invocation_count
                with count_lock:
                    invocation_count += 1
                    invocation = invocation_count
                if invocation == 1:
                    first_entered.set()
                    if not release_first.wait(5):
                        raise AssertionError("test did not release the first subprocess")
                else:
                    second_entered.set()
                return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

            def run_second_call():
                second_started.set()
                return relay.run_herdr("pane", "read", "second", remote="same-host")

            with mock.patch.object(relay.subprocess, "run", side_effect=fake_subprocess_run):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(
                        relay.run_herdr, "pane", "read", "first", remote="same-host"
                    )
                    self.assertTrue(first_entered.wait(2), "first subprocess did not start")
                    second = executor.submit(run_second_call)
                    self.assertTrue(second_started.wait(2), "second call did not start")
                    try:
                        self.assertFalse(
                            second_entered.wait(0.5),
                            "second subprocess entered before the first completed",
                        )
                    finally:
                        release_first.set()
                    self.assertEqual(first.result(timeout=2), "ok")
                    self.assertEqual(second.result(timeout=2), "ok")

            self.assertTrue(second_entered.is_set())
            self.assertEqual(invocation_count, 2)

    def test_other_remotes_and_local_calls_do_not_share_a_lock(self):
        with loaded_relay() as relay:
            entered = {
                "blocked-host": threading.Event(),
                "other-host": threading.Event(),
                "local": threading.Event(),
            }
            release_blocked = threading.Event()

            def command_target(command):
                if command[0] != "ssh":
                    return "local"
                batch_mode_index = command.index("BatchMode=yes")
                return command[batch_mode_index + 1]

            def fake_subprocess_run(command, **kwargs):
                target = command_target(command)
                entered[target].set()
                if target == "blocked-host" and not release_blocked.wait(5):
                    raise AssertionError("test did not release blocked-host")
                return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

            with mock.patch.object(relay.subprocess, "run", side_effect=fake_subprocess_run):
                with ThreadPoolExecutor(max_workers=3) as executor:
                    blocked = executor.submit(
                        relay.run_herdr, "pane", "read", "one", remote="blocked-host"
                    )
                    self.assertTrue(
                        entered["blocked-host"].wait(2),
                        "blocked remote subprocess did not start",
                    )
                    other = executor.submit(
                        relay.run_herdr, "pane", "read", "two", remote="other-host"
                    )
                    local = executor.submit(relay.run_herdr, "pane", "read", "three")
                    try:
                        self.assertTrue(
                            entered["other-host"].wait(2),
                            "different remote was blocked by the first remote",
                        )
                        self.assertTrue(
                            entered["local"].wait(2),
                            "local execution was blocked by a remote",
                        )
                    finally:
                        release_blocked.set()
                    self.assertEqual(blocked.result(timeout=2), "ok")
                    self.assertEqual(other.result(timeout=2), "ok")
                    self.assertEqual(local.result(timeout=2), "ok")

    def test_a_slow_herdr_call_does_not_stall_the_event_loop(self):
        """The whole point of the worker threads: one slow call must not freeze the relay.

        Awaited inline, a poll tick holds the loop for as long as its herdr call takes -- locally
        a few ms, but an SSH read can run to the 15s timeout, and for that whole time no other
        client is served and no broadcast goes out.
        """
        with loaded_relay() as relay:
            def slow_subprocess_run(command, **kwargs):
                time.sleep(0.3)
                return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

            async def scenario():
                ticks = 0

                async def ticker():
                    nonlocal ticks
                    while True:
                        await asyncio.sleep(0.005)
                        ticks += 1

                beat = asyncio.create_task(ticker())
                await asyncio.sleep(0)  # let the ticker reach its first await
                await relay._poll_once()
                beat.cancel()
                return ticks

            with mock.patch.object(relay.subprocess, "run", side_effect=slow_subprocess_run):
                ticks = asyncio.run(scenario())

            # 0.3s of subprocess against a 5ms tick. Inline it is exactly 0; off the loop it is
            # tens. Ten is far below the ~60 a healthy loop manages and far above a blocked one.
            self.assertGreater(
                ticks, 10, "the event loop was blocked for the length of the herdr call"
            )

    def test_no_blocking_call_is_awaited_inline_from_async_code(self):
        """Structural guard for the above, because the behavioural test only covers one path.

        Builds the call graph over the relay's own sync functions, seeds it with the primitives
        that actually block, then fails on any of them called straight from an `async def` body
        rather than through asyncio.to_thread. A new handler that forgets the wrapper is a silent
        regression -- everything still works, the relay just stops answering anyone else while it
        runs -- so it has to be caught here rather than in review.
        """
        seeds = {"subprocess.run", "subprocess.check_output", "time.sleep",
                 "zc.unregister_service"}
        tree = ast.parse(RELAY_PATH.read_text(encoding="utf-8"), str(RELAY_PATH))

        def called_names(node, unwrap_to_thread=False):
            """Call names directly in `node`, not descending into nested defs.

            With unwrap_to_thread, an asyncio.to_thread(fn, ...) contributes nothing for `fn`
            itself -- that is the whole point of the wrapper -- but its remaining arguments are
            still walked, so `to_thread(f, read_pane(x))` is not let through.
            """
            found, stack = [], list(ast.iter_child_nodes(node))
            while stack:
                current = stack.pop()
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(current, ast.Call):
                    name = _dotted(current.func)
                    if unwrap_to_thread and name == "asyncio.to_thread":
                        stack.extend(current.args[1:] + current.keywords)
                        continue
                    if name:
                        found.append((name, current.lineno))
                stack.extend(ast.iter_child_nodes(current))
            return found

        sync = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        asynchronous = {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}

        blocking = {name for name, node in sync.items()
                    if any(call in seeds for call, _ in called_names(node))}
        while True:
            grown = {name for name, node in sync.items()
                     if name not in blocking
                     and any(call in blocking for call, _ in called_names(node))}
            if not grown:
                break
            blocking |= grown

        # If this set ever empties the test has stopped testing anything -- a rename upstream
        # would silently make every assertion below vacuous.
        self.assertIn("run_herdr", blocking)
        self.assertIn("read_pane", blocking)
        self.assertIn("get_all_agents", blocking)

        offenders = [
            f"{RELAY_PATH.name}:{line} {call}() inside async def {holder}()"
            for holder, node in asynchronous.items()
            for call, line in called_names(node, unwrap_to_thread=True)
            if call in blocking or call in seeds
        ]
        self.assertEqual(offenders, [], "wrap these in asyncio.to_thread:\n  " + "\n  ".join(offenders))


def _dotted(node):
    """`a.b.c` for an attribute/name chain, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


if __name__ == "__main__":
    unittest.main()
