"""Tests for the pane link that a push notification carries.

The relay sends each blocked push with `url: "/?pane=<pane_id>"`. A tap on the notification gets
to the app in one of two ways (`web/sw.js`): with no window open, the service worker opens that
URL. With a window open, it focuses the window and posts `{type: "navigate", url}`. Either way the
app must open the session for that pane, and only once.

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
RELAY = "ws://relay.test:8375"


def _chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


try:  # pragma: no cover - environment probe
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


# One browser for the file, one playwright for the process -- see test_web_keys.py.
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
    this.sent = [];
    this.onopen = this.onclose = this.onerror = this.onmessage = null;
    window.__sockets.push(this);
  }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; }
}
FakeSocket.CONNECTING = 0; FakeSocket.OPEN = 1; FakeSocket.CLOSING = 2; FakeSocket.CLOSED = 3;
window.WebSocket = FakeSocket;
window.__open = () => { const s = window.__sockets[0]; s.readyState = 1; if (s.onopen) s.onopen({}); };
window.__msg = payload => {
  const s = window.__sockets[0];
  if (s.onmessage) s.onmessage({data: JSON.stringify(payload)});
};
"""

# The service worker posts on navigator.serviceWorker. A file:// page has no worker to post, so
# the test dispatches the same MessageEvent on that container itself.
POST_NAVIGATE = """url => navigator.serviceWorker.dispatchEvent(
  new MessageEvent('message', {data: {type: 'navigate', url}}))"""

SEED = (
    "localStorage.setItem('herdr_relay_url', %s);\n"
    "localStorage.removeItem('herdr_relay_token');\n"
) % json.dumps(RELAY)


def _agent(pane_id, status="blocked"):
    return {
        "pane_id": pane_id,
        "agent": "claude",
        "label": "",
        "workspace_label": "herdr-remote",
        "project": "herdr-remote",
        "status": status,
        "cwd": "/tmp/herdr-remote",
        "host": "local",
        "workspace_id": "w1",
        "tab_id": "w1:t1",
    }


def _snapshot(*agents):
    return {"type": "agents", "agents": list(agents), "panes": []}


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebPushLinkTests(unittest.TestCase):
    """A tap on a blocked push opens the session for the pane that asked."""

    def open_page(self, query=""):
        self.page = _shared["browser"].new_page(viewport=PHONE)
        self.page.add_init_script(SEED)
        self.page.add_init_script(FAKE_SOCKET)
        self.page.goto(PAGE + query)  # nav.js boots the first connect on a 100ms timer
        self.page.wait_for_function("() => window.__sockets.length === 1")
        self.page.evaluate("() => __open()")

    def tearDown(self):
        self.page.close()

    def send(self, payload):
        self.page.evaluate("p => __msg(p)", payload)

    def open_pane(self):
        """The pane whose session is in front of the reader, or None on the list."""
        return self.page.evaluate(
            "() => document.getElementById('terminalView').classList.contains('active')"
            " ? activePane : null"
        )

    # --- no window open: the service worker opens /?pane=<id> ---

    def test_a_pane_link_opens_that_session_on_the_first_snapshot(self):
        self.open_page("?pane=w1%3Ap2")
        self.send(_snapshot(_agent("w1:p1", "working"), _agent("w1:p2")))
        self.assertEqual(self.open_pane(), "w1:p2")

    def test_a_link_to_a_pane_the_relay_does_not_list_stays_on_the_list(self):
        self.open_page("?pane=w1%3Ap9")
        self.send(_snapshot(_agent("w1:p1")))
        self.assertIsNone(self.open_pane())

    def test_the_link_opens_the_session_once(self):
        """Back to the list, then the next snapshot: the reader stays where they went."""
        self.open_page("?pane=w1%3Ap2")
        self.send(_snapshot(_agent("w1:p2")))
        self.page.evaluate("() => hideTerminal()")
        self.send(_snapshot(_agent("w1:p2")))
        self.assertIsNone(self.open_pane())

    def test_a_pane_that_appears_after_the_first_snapshot_does_not_open(self):
        """The first snapshot is the whole herd, so a link it cannot match is spent."""
        self.open_page("?pane=w1%3Ap2")
        self.send(_snapshot(_agent("w1:p1")))
        self.send(_snapshot(_agent("w1:p1"), _agent("w1:p2")))
        self.assertIsNone(self.open_pane())

    # --- a window open: the service worker posts {type: "navigate", url} ---

    def test_a_navigate_message_opens_that_session(self):
        self.open_page()
        self.send(_snapshot(_agent("w1:p1"), _agent("w1:p2")))
        self.page.evaluate(POST_NAVIGATE, "/?pane=w1%3Ap2")
        self.assertEqual(self.open_pane(), "w1:p2")

    def test_a_navigate_message_switches_from_another_open_session(self):
        self.open_page()
        self.send(_snapshot(_agent("w1:p1"), _agent("w1:p2")))
        self.page.evaluate("() => openTerminal('w1:p1')")
        self.page.evaluate(POST_NAVIGATE, "/?pane=w1%3Ap2")
        self.assertEqual(self.open_pane(), "w1:p2")

    def test_a_navigate_message_before_the_first_snapshot_waits_for_it(self):
        self.open_page()
        self.page.evaluate(POST_NAVIGATE, "/?pane=w1%3Ap2")
        self.send(_snapshot(_agent("w1:p2")))
        self.assertEqual(self.open_pane(), "w1:p2")

    def test_a_navigate_message_with_no_pane_leaves_the_session_open(self):
        self.open_page()
        self.send(_snapshot(_agent("w1:p1")))
        self.page.evaluate("() => openTerminal('w1:p1')")
        self.page.evaluate(POST_NAVIGATE, "/")
        self.assertEqual(self.open_pane(), "w1:p1")


if __name__ == "__main__":
    unittest.main()
