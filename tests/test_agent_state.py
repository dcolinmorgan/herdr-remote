#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "relay"))

from agent_state import agent_update_message, apply_agent_message, complete_agent_update_message


class AgentStateTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = [
            {
                "pane_id": "w1:p1",
                "agent": "opencode",
                "status": "working",
                "project": "alpha",
                "cwd": "/work/alpha",
                "host": "local",
                "workspace_id": "w1",
                "tab_id": "w1:t1",
            },
            {
                "pane_id": "w2:p1",
                "agent": "pi",
                "status": "idle",
                "project": "beta",
                "cwd": "/work/beta",
                "host": "local",
            },
        ]

    def test_agent_event_uses_distinct_incremental_shape(self):
        message = agent_update_message({
            "type": "agent_event",
            "pane_id": "w1:p1",
            "agent": "opencode",
            "status": "blocked",
            "project": "alpha",
            "cwd": "/work/alpha",
            "host": "dev-mac",
        }, local_hostname="dev-mac.local")

        self.assertEqual(message["type"], "agent_update")
        self.assertNotIn("agents", message)
        self.assertEqual(message["agent"]["pane_id"], "w1:p1")
        self.assertEqual(message["agent"]["host"], "local")

    def test_sparse_event_is_enriched_without_empty_metadata(self):
        message = complete_agent_update_message({
            "type": "agent_event",
            "pane_id": "w1:p1",
            "agent": "",
            "status": "blocked",
            "cwd": "",
            "project": "",
            "host": None,
        }, current=self.snapshot[0])

        self.assertEqual(message["agent"]["agent"], "opencode")
        self.assertEqual(message["agent"]["project"], "alpha")
        self.assertEqual(message["agent"]["status"], "blocked")
        self.assertEqual(message["agent"]["host"], "local")

    def test_unknown_sparse_event_is_not_emitted(self):
        message = complete_agent_update_message({
            "type": "agent_event",
            "pane_id": "w9:p9",
            "status": "blocked",
        })

        self.assertIsNone(message)

    def test_incremental_update_preserves_other_agents_and_snapshot_fields(self):
        result = apply_agent_message(self.snapshot, {
            "type": "agent_update",
            "agent": {"pane_id": "w1:p1", "status": "blocked"},
        })

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["status"], "blocked")
        self.assertEqual(result[0]["workspace_id"], "w1")
        self.assertEqual(result[1], self.snapshot[1])
        self.assertEqual(self.snapshot[0]["status"], "working")

    def test_incremental_update_adds_unknown_agent(self):
        result = apply_agent_message(self.snapshot, {
            "type": "agent_update",
            "agent": {"pane_id": "w3:p1", "agent": "claude", "status": "working"},
        })

        self.assertEqual([agent["pane_id"] for agent in result], ["w1:p1", "w2:p1", "w3:p1"])

    def test_complete_snapshot_replaces_and_removes_agents(self):
        replacement = [{"pane_id": "w2:p1", "agent": "pi", "status": "working"}]
        result = apply_agent_message(self.snapshot, {"type": "agents", "agents": replacement})

        self.assertEqual(result, replacement)
        self.assertIsNot(result[0], replacement[0])


if __name__ == "__main__":
    unittest.main()
