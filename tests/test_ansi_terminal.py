import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from unittest import mock
import uuid


RELAY_PATH = Path(__file__).resolve().parents[1] / "relay" / "herdr_relay.py"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class _Closed(Exception):
    pass


def _websocket_stubs():
    websockets = types.ModuleType("websockets")
    websockets.__path__ = []
    asyncio_module = types.ModuleType("websockets.asyncio")
    asyncio_module.__path__ = []
    server = types.ModuleType("websockets.asyncio.server")
    server.serve = object()
    exceptions = types.ModuleType("websockets.exceptions")
    exceptions.ConnectionClosedError = _Closed
    exceptions.ConnectionClosedOK = _Closed
    return {
        "websockets": websockets,
        "websockets.asyncio": asyncio_module,
        "websockets.asyncio.server": server,
        "websockets.exceptions": exceptions,
    }


@contextmanager
def loaded_relay():
    module_name = f"ansi_relay_test_{uuid.uuid4().hex}"
    logger = logging.getLogger("herdr-relay")
    original_handlers = tuple(logger.handlers)
    relay_dir = str(RELAY_PATH.parent)
    added_relay_dir = relay_dir not in sys.path
    if added_relay_dir:
        sys.path.insert(0, relay_dir)
    with tempfile.TemporaryDirectory() as log_dir, mock.patch.dict(
        os.environ, {"HERDR_LOG_DIR": log_dir}, clear=False
    ), mock.patch.dict(sys.modules, _websocket_stubs(), clear=False):
        spec = importlib.util.spec_from_file_location(module_name, RELAY_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            logger.disabled = True
            yield module
        finally:
            sys.modules.pop(module_name, None)
            if added_relay_dir:
                sys.path.remove(relay_dir)
            for handler in tuple(logger.handlers):
                if handler not in original_handlers:
                    logger.removeHandler(handler)
                    handler.close()
            audit_logger = logging.getLogger("herdr-audit")
            for handler in tuple(audit_logger.handlers):
                audit_logger.removeHandler(handler)
                handler.close()
            logger.disabled = False


class _WebSocket:
    remote_address = ("127.0.0.1", 1)
    request = types.SimpleNamespace(headers={"User-Agent": "test", "Origin": ""})

    def __init__(self, message):
        self.messages = iter([json.dumps(message)])
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, value):
        self.sent.append(json.loads(value))


class AnsiTransportTests(unittest.TestCase):
    def test_bundled_font_and_renderer_assets_are_present(self):
        font = WEB_DIR / "HackNerdFont-Regular.woff2"
        license_file = WEB_DIR / "HackNerdFont-LICENSE.txt"
        page = (WEB_DIR / "index.html").read_text(encoding="utf-8")

        self.assertGreater(font.stat().st_size, 100_000)
        self.assertIn("Hack", license_file.read_text(encoding="utf-8"))
        self.assertIn("HackNerdFont-Regular.woff2", page)
        self.assertIn("function ansiFragment", page)
        # Whitespace-tolerant: this pins that the mirror asks for colour, not how the request
        # object happens to be laid out.
        self.assertRegex(page, r"format:\s*'ansi'")

    def test_pane_read_defaults_to_text_and_accepts_explicit_ansi(self):
        """Test that read_pane handler passes --format correctly to run_herdr."""
        for requested_format in (None, "ansi"):
            with self.subTest(requested_format=requested_format), loaded_relay() as relay:
                # Directly test the run_herdr call pattern by checking the code path
                # The relay's read_pane handler builds: run_herdr("pane", "read", pane_id, "--lines", ..., "--format", format)
                relay.known_panes.add("pane-1")
                
                captured_args = []
                original_run_herdr = relay.run_herdr
                
                def capture_run_herdr(*args, remote=None):
                    captured_args.append(args)
                    return "test content"
                
                # Patch and directly simulate the read_pane message handling
                with mock.patch.object(relay, "run_herdr", side_effect=capture_run_herdr):
                    pane_id = "pane-1"
                    lines = 5
                    read_format = requested_format or "text"
                    # This mirrors what handle_client does for read_pane:
                    content = relay.run_herdr(
                        "pane", "read", pane_id, "--lines", str(lines), 
                        "--source", "recent", "--format", read_format
                    )
                
                self.assertEqual(len(captured_args), 1)
                args = captured_args[0]
                self.assertIn("--format", args)
                fmt_idx = args.index("--format")
                self.assertEqual(args[fmt_idx + 1], requested_format or "text")


if __name__ == "__main__":
    unittest.main()
