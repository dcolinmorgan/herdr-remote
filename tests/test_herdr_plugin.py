import ast
import json
import os
from pathlib import Path
import runpy
import socket
import unittest
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT_DIR / "relay" / "on_event.py"


class PluginManifestTests(unittest.TestCase):
    def test_manifests_support_windows_and_resolve_their_hook(self):
        cases = (
            (
                ROOT_DIR / "herdr-plugin.toml",
                ["uv", "run", "--script", "relay/on_event.py"],
            ),
            (
                ROOT_DIR / "relay" / "herdr-plugin.toml",
                ["uv", "run", "--script", "on_event.py"],
            ),
        )

        for manifest_path, expected_command in cases:
            with self.subTest(manifest=manifest_path.relative_to(ROOT_DIR)):
                manifest_lines = manifest_path.read_text(encoding="utf-8").splitlines()
                values = {
                    key: ast.literal_eval(next(
                        line.split("=", 1)[1].strip()
                        for line in manifest_lines
                        if line.startswith(f"{key} =")
                    ))
                    for key in ("platforms", "on", "command")
                }
                self.assertEqual(values["platforms"], ["macos", "linux", "windows"])
                self.assertEqual(values["on"], "pane.agent_status_changed")
                self.assertEqual(values["command"], expected_command)
                self.assertEqual(
                    (manifest_path.parent / values["command"][3]).resolve(strict=True),
                    HOOK_PATH.resolve(strict=True),
                )


class PluginHookTests(unittest.TestCase):
    def _run_hook(self, event, context_json=None):
        environment = {"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}
        if context_json is not None:
            environment["HERDR_PLUGIN_CONTEXT_JSON"] = context_json

        udp_socket = mock.Mock()
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "socket.gethostname", return_value="test-host.example"
        ), mock.patch("socket.socket", return_value=udp_socket) as socket_factory:
            runpy.run_path(str(HOOK_PATH), run_name="__main__")

        socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.sendto.assert_called_once()
        encoded_payload, destination = udp_socket.sendto.call_args.args
        self.assertEqual(destination, ("127.0.0.1", 8376))
        udp_socket.close.assert_called_once_with()
        return json.loads(encoded_payload)

    def test_hook_uses_focused_pane_cwd_for_real_agent_status_event(self):
        focused_cwd = os.path.join(os.sep, "workspaces", "FocusedProject")
        workspace_cwd = os.path.join(os.sep, "workspaces", "WorkspaceProject")
        event = {
            "data": {
                "pane_id": "pane-7",
                "agent_status": "WAITING",
                "agent": "Claude",
            }
        }
        context = {
            "focused_pane_cwd": focused_cwd,
            "workspace_cwd": workspace_cwd,
        }

        payload = self._run_hook(event, json.dumps(context))

        self.assertEqual(
            payload,
            {
                "type": "agent_event",
                "pane_id": "pane-7",
                "status": "waiting",
                "agent": "claude",
                "project": "FocusedProject",
                "cwd": focused_cwd,
                "host": "test-host",
            },
        )

    def test_hook_cwd_fallbacks_degrade_safely(self):
        workspace_cwd = os.path.join(os.sep, "workspaces", "WorkspaceProject")
        legacy_cwd = os.path.join(os.sep, "workspaces", "LegacyProject")
        base_data = {
            "pane_id": "pane-7",
            "agent_status": "WAITING",
            "agent": "Claude",
        }
        cases = (
            (
                "workspace context",
                json.dumps({"workspace_cwd": workspace_cwd}),
                legacy_cwd,
                workspace_cwd,
            ),
            ("missing context", None, legacy_cwd, legacy_cwd),
            ("malformed context", "{not-json", legacy_cwd, legacy_cwd),
            ("non-object context", "[]", legacy_cwd, legacy_cwd),
            ("no cwd source", None, None, ""),
        )

        for name, context_json, event_cwd, expected_cwd in cases:
            with self.subTest(name=name):
                data = dict(base_data)
                if event_cwd is not None:
                    data["cwd"] = event_cwd

                payload = self._run_hook({"data": data}, context_json)

                self.assertEqual(payload["cwd"], expected_cwd)
                self.assertEqual(payload["project"], os.path.basename(expected_cwd))
                self.assertEqual(payload["status"], "waiting")
                self.assertEqual(payload["agent"], "claude")
                self.assertEqual(payload["host"], "test-host")


if __name__ == "__main__":
    unittest.main()
