"""Tests for how web/ holds its one WebSocket: switching relays, and reconnecting after a drop.

Every claim is about event ORDER, so it is measured in a browser against a stand-in socket whose
`close()` fires `onclose` on a later task -- the ordering that made a relay switch cycle
offline -> connecting -> live every 3s until the page was reloaded.

Skipped, not failed, when playwright or a chromium build is missing.
"""
import json
import os
from pathlib import Path
import unittest


PAGE = (Path(__file__).resolve().parents[1] / "web" / "index.html").as_uri()

CHROME_CANDIDATES = [
    os.environ.get("HERDR_TEST_CHROME", ""),
    os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"),
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]

PHONE = {"width": 390, "height": 844}

RELAY_A = "ws://relay-a.test:8375"
RELAY_B = "ws://relay-b.test:8375"

PAST_RECONNECT = 3600  # past the app's own 3s, so "no timer fired" is a real claim


def _chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


try:  # pragma: no cover - environment probe
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


# One browser for the file, one playwright for the process -- see test_web_keys.py: a second
# playwright instance under `unittest discover` is what makes `page.goto` time out.
_shared = {}


def setUpModule():  # noqa: N802 - unittest's own name
    if sync_playwright is None or _chrome() is None:
        return
    _shared["playwright"] = sync_playwright().start()
    _shared["browser"] = _shared["playwright"].chromium.launch(executable_path=_chrome())


def tearDownModule():  # noqa: N802 - unittest's own name
    if "browser" in _shared:
        _shared["browser"].close()
        _shared["playwright"].stop()
    _shared.clear()


FAKE_SOCKET = """
window.__sockets = [];
class FakeSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.closedByApp = false;
    this.sent = [];
    this.onopen = this.onclose = this.onerror = this.onmessage = null;
    window.__sockets.push(this);
  }
  send(data) { this.sent.push(data); }
  close() {
    if (this.readyState === 3) return;
    this.closedByApp = true;
    this.readyState = 3;
    setTimeout(() => { if (this.onclose) this.onclose({}); }, 0);
  }
}
FakeSocket.CONNECTING = 0; FakeSocket.OPEN = 1; FakeSocket.CLOSING = 2; FakeSocket.CLOSED = 3;
window.WebSocket = FakeSocket;

window.__sock = i => window.__sockets[i];
window.__open = i => { const s = window.__sockets[i]; s.readyState = 1; if (s.onopen) s.onopen({}); };
window.__drop = i => { const s = window.__sockets[i]; s.readyState = 3; if (s.onclose) s.onclose({}); };
window.__msg = (i, payload) => {
  const s = window.__sockets[i];
  if (s.onmessage) s.onmessage({data: JSON.stringify(payload)});
};
window.__conn = () => document.getElementById('connLabel').textContent;
window.__state = () => window.__sockets.map(s => ({url: s.url, readyState: s.readyState, closedByApp: s.closedByApp}));
"""


def _seed(url, sessions):
    # A script BODY, not a function: an arrow expression here is never called, and the app then
    # boots against its own `ws://` fallback instead of the seeded relay.
    return (
        "localStorage.setItem('herdr_relay_url', %s);\n"
        "localStorage.removeItem('herdr_relay_token');\n"
        "localStorage.setItem('herdr_sessions', %s);\n"
    ) % (json.dumps(url), json.dumps(json.dumps(sessions)))


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebConnectionTests(unittest.TestCase):
    """One live socket at a time, and one pending reconnect at a time."""

    def setUp(self):
        # A page per test: a reconnect timer left over from one would look just like the bug the
        # next one is measuring.
        self.page = _shared["browser"].new_page(viewport=PHONE)
        self.page.add_init_script(
            _seed(RELAY_A, [{"name": "A", "url": RELAY_A}, {"name": "B", "url": RELAY_B}])
        )
        self.page.add_init_script(FAKE_SOCKET)
        self.page.goto(PAGE)  # nav.js boots the first connect on a 100ms timer
        self.page.wait_for_function("() => window.__sockets.length === 1")
        self.page.evaluate("() => __open(0)")
        self.assertEqual(self.page.evaluate("() => __conn()"), "live")

    def tearDown(self):
        self.page.close()

    def switch_to_b(self):
        """What the reader does: tap the other saved session."""
        self.page.evaluate("() => switchSession(1)")
        self.page.wait_for_function("() => window.__sockets.length === 2")
        self.page.evaluate("() => __open(1)")

    # --- the reported bug ---

    def test_a_relay_switch_leaves_one_live_socket(self):
        """The old socket's late close must not take the new socket down with it."""
        self.switch_to_b()
        self.assertEqual(self.page.evaluate("() => __conn()"), "live")
        self.page.wait_for_timeout(PAST_RECONNECT)
        state = self.page.evaluate("() => __state()")
        self.assertEqual(len(state), 2, "a third socket means a reconnect fired: %s" % state)
        self.assertEqual(state[1]["url"], RELAY_B)
        self.assertFalse(state[1]["closedByApp"], "the healthy socket was closed again")
        self.assertEqual(self.page.evaluate("() => __conn()"), "live")

    def test_the_old_sockets_close_does_not_report_offline(self):
        """The status after a switch is the NEW socket's, not the departing one's."""
        self.page.evaluate("() => switchSession(1)")
        # Let the old socket's queued close land before the new one opens -- the ordering that
        # used to paint 'offline' over a connecting socket.
        self.page.wait_for_timeout(50)
        self.assertEqual(self.page.evaluate("() => __conn()"), "connecting…")
        self.page.evaluate("() => __open(1)")
        self.assertEqual(self.page.evaluate("() => __conn()"), "live")

    def test_a_message_from_the_relay_left_behind_is_ignored(self):
        """A snapshot in flight from relay A must not merge into relay B's state."""
        self.page.evaluate(
            "() => __msg(0, {type: 'agents', agents: [{pane_id: 'a:p1', agent: 'claude', status: 'idle'}]})"
        )
        self.assertEqual(self.page.evaluate("() => agents.map(a => a.pane_id)"), ["a:p1"])
        self.switch_to_b()
        self.page.evaluate(
            "() => __msg(0, {type: 'agents', agents: [{pane_id: 'stale:p9', agent: 'claude', status: 'idle'}]})"
        )
        self.assertEqual(self.page.evaluate("() => agents.map(a => a.pane_id)"), ["a:p1"])

    # --- and the reconnect it must not have broken ---

    def test_a_dropped_socket_reconnects_once(self):
        """The guard is per socket, not a way of switching reconnection off."""
        self.page.evaluate("() => __drop(0)")
        self.assertEqual(self.page.evaluate("() => __conn()"), "offline")
        self.page.wait_for_function("() => window.__sockets.length === 2", timeout=6000)
        self.page.evaluate("() => __open(1)")
        self.assertEqual(self.page.evaluate("() => __conn()"), "live")
        self.page.wait_for_timeout(PAST_RECONNECT)
        self.assertEqual(len(self.page.evaluate("() => __state()")), 2)

    def test_a_manual_connect_does_not_fork_the_reconnect_chain(self):
        """Two chains would each keep closing the other's socket, which is the same loop."""
        self.page.evaluate("() => __drop(0)")   # schedules a reconnect
        self.page.evaluate("() => connect()")   # the reader, not waiting for it
        self.page.wait_for_function("() => window.__sockets.length === 2")
        self.page.evaluate("() => __open(1)")
        self.page.wait_for_timeout(PAST_RECONNECT)
        state = self.page.evaluate("() => __state()")
        self.assertEqual(len(state), 2, "the scheduled reconnect survived the manual one: %s" % state)
        self.assertFalse(state[1]["closedByApp"])
        self.assertEqual(self.page.evaluate("() => __conn()"), "live")


if __name__ == "__main__":
    unittest.main()
