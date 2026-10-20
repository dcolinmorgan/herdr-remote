"""Tests for the attach button in web/: picking or pasting a file uploads it through the relay, and
the path it landed at goes into the reply box.

Every claim is about what goes on the wire and in what order, so it is measured in a browser
against the same stand-in socket `test_web_connection.py` uses. The relay's half is answered by
hand through `__msg`, one message at a time, which is what lets a test say "the next chunk waited
for its ack".

Skipped, not failed, when playwright or a chromium build is missing.
"""
import base64
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
NARROW = {"width": 320, "height": 640}

RELAY = "ws://relay-a.test:8375"
MAX_BYTES = 20 * 1024 * 1024
CHUNK = 256 * 1024
PATH = "/u/.cache/herdr-remote/uploads/s-0123456789abcdef/a3f9c0de-shot.png"


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
  close() {
    if (this.readyState === 3) return;
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
window.__uploads = () => window.__sockets[0].sent.map(m => JSON.parse(m))
  .filter(m => m.type.startsWith('upload_'));
"""

SEED = (
    "localStorage.setItem('herdr_relay_url', %s);\n"
    "localStorage.removeItem('herdr_relay_token');\n"
    "localStorage.setItem('herdr_sessions', %s);\n"
) % (json.dumps(RELAY), json.dumps(json.dumps([{"name": "A", "url": RELAY}])))


def _file(name="shot.png", size=10, mime="image/png"):
    return {"name": name, "mimeType": mime, "buffer": bytes(i % 251 for i in range(size))}


class _UploadPage:
    """Shared setup. A mixin rather than a TestCase, so unittest does not collect it on its own."""

    viewport = PHONE

    def setUp(self):
        self.page = _shared["browser"].new_page(viewport=self.viewport)
        self.page.set_default_timeout(5000)  # every wait here is for a local event, not a network
        self.page.add_init_script(SEED)
        self.page.add_init_script(FAKE_SOCKET)
        self.page.goto(PAGE)
        self.page.wait_for_function("() => window.__sockets.length === 1")
        self.page.evaluate("() => __open(0)")

    def tearDown(self):
        self.page.close()

    def advertise(self, max_bytes=MAX_BYTES, panes=("w1:p1",)):
        snapshot = {"type": "agents",
                    "agents": [{"pane_id": p, "agent": "claude", "status": "idle"} for p in panes]}
        if max_bytes is not None:
            snapshot["upload_max_bytes"] = max_bytes
        self.page.evaluate("m => __msg(0, m)", snapshot)

    def open_pane(self, pane="w1:p1"):
        self.page.evaluate("p => openTerminal(p)", pane)

    def pick(self, *files):
        self.page.set_input_files("#attachInput", list(files))

    def uploads(self):
        return self.page.evaluate("() => __uploads()")

    def wait_uploads(self, n):
        self.page.wait_for_function("n => __uploads().length >= n", arg=n)
        return self.uploads()

    def reply(self, payload):
        self.page.evaluate("m => __msg(0, m)", payload)

    def ack(self, upload_id, received):
        self.reply({"type": "upload_ack", "upload_id": upload_id, "received": received})

    def drive(self, path=PATH):
        """Play the relay for one upload already started: ack every step, then answer with `path`."""
        start = [m for m in self.uploads() if m["type"] == "upload_start"][-1]
        uid, size, received, seen = start["upload_id"], start["size"], 0, len(self.uploads())
        self.ack(uid, 0)
        while True:
            msgs = self.wait_uploads(seen + 1)
            last = msgs[-1]
            seen = len(msgs)
            if last["type"] == "upload_end":
                break
            received += len(base64.b64decode(last["data"]))
            self.ack(uid, received)
        self.assertEqual(received, size)
        self.reply({"type": "upload_result", "upload_id": uid, "ok": True, "path": path})
        return uid

    def box(self):
        return self.page.evaluate("() => document.getElementById('termInput').value")

    def type_into_box(self, text):
        self.page.fill("#termInput", text)

    def notice(self):
        return self.page.evaluate(
            "() => { const e = document.getElementById('sendError'); return e.hidden ? null : e.textContent; }")

    def attach_visible(self):
        return self.page.is_visible("#attachBtn")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebAttachButtonTests(_UploadPage, unittest.TestCase):
    """The button is offered only by a relay that says it takes uploads."""

    def test_the_button_is_hidden_without_the_advert(self):
        self.advertise(max_bytes=None)
        self.open_pane()
        self.assertFalse(self.attach_visible())

    def test_the_button_shows_with_the_advert(self):
        self.advertise()
        self.open_pane()
        self.assertTrue(self.attach_visible())

    def test_the_button_goes_when_the_advert_does(self):
        self.advertise()
        self.advertise(max_bytes=None)
        self.open_pane()
        self.assertFalse(self.attach_visible())


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebUploadTests(_UploadPage, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.advertise()
        self.open_pane()

    def test_a_picked_file_sends_start_chunks_and_end_in_order(self):
        size = 2 * CHUNK + 1000
        self.pick(_file(size=size))
        start = self.wait_uploads(1)[0]
        self.assertEqual(start["type"], "upload_start")
        self.assertEqual((start["pane_id"], start["name"], start["size"]), ("w1:p1", "shot.png", size))
        self.assertRegex(start["upload_id"], r"^[0-9a-f]{16}$")
        uid = start["upload_id"]
        received = 0
        for seq in range(3):
            # Nothing goes out until the previous step is acked.
            self.page.wait_for_timeout(50)
            self.assertEqual(len(self.uploads()), 1 + seq)
            self.ack(uid, received)
            chunk = self.wait_uploads(2 + seq)[-1]
            self.assertEqual((chunk["type"], chunk["upload_id"], chunk["seq"]), ("upload_chunk", uid, seq))
            data = base64.b64decode(chunk["data"])
            self.assertLessEqual(len(data), CHUNK)
            self.assertEqual(data, _file(size=size)["buffer"][received:received + len(data)])
            received += len(data)
        self.assertEqual(received, size)
        self.page.wait_for_timeout(50)
        self.assertEqual(len(self.uploads()), 4)
        self.ack(uid, received)
        self.assertEqual(self.wait_uploads(5)[-1], {"type": "upload_end", "upload_id": uid})

    def test_the_path_lands_at_the_caret(self):
        self.type_into_box("see ")
        self.pick(_file())
        self.wait_uploads(1)
        self.drive()
        self.assertEqual(self.box(), "see " + PATH + " ")

    def test_the_path_is_spaced_from_the_text_around_it(self):
        self.type_into_box("see this")
        self.page.evaluate("() => document.getElementById('termInput').setSelectionRange(3, 3)")
        self.pick(_file())
        self.wait_uploads(1)
        self.drive()
        self.assertEqual(self.box(), "see " + PATH + " this")

    def test_a_path_with_a_space_is_quoted(self):
        self.pick(_file(name="my shot.png"))
        self.wait_uploads(1)
        self.drive(path="/u/uploads/s-0123456789abcdef/a3f9c0de-my shot.png")
        self.assertEqual(self.box(), '"/u/uploads/s-0123456789abcdef/a3f9c0de-my shot.png" ')

    def test_the_button_is_busy_while_a_file_uploads(self):
        self.pick(_file(), _file(name="two.png"))
        self.wait_uploads(1)
        self.assertEqual(self.page.get_attribute("#attachBtn", "aria-busy"), "true")
        self.assertEqual(self.page.get_attribute("#termInput", "placeholder"), "Uploading 1 of 2…")
        self.drive()
        self.wait_uploads(4)
        self.assertEqual(self.page.get_attribute("#termInput", "placeholder"), "Uploading 2 of 2…")
        self.drive(path="/u/two.png")
        self.assertIsNone(self.page.get_attribute("#attachBtn", "aria-busy"))
        self.assertEqual(self.page.get_attribute("#termInput", "placeholder"), "Type…")
        self.assertEqual(self.box(), PATH + " /u/two.png ")

    def test_a_refusal_shows_and_keeps_the_typed_text(self):
        self.type_into_box("keep me")
        self.pick(_file())
        uid = self.wait_uploads(1)[0]["upload_id"]
        self.reply({"type": "upload_result", "upload_id": uid, "ok": False, "error": "file too large"})
        self.assertIn("file too large", self.notice())
        self.assertEqual(self.box(), "keep me")
        self.assertIsNone(self.page.get_attribute("#attachBtn", "aria-busy"))

    def test_a_file_over_the_limit_is_refused_in_the_app(self):
        self.advertise(max_bytes=100)
        self.pick(_file(name="big.bin", size=101))
        self.page.wait_for_timeout(50)
        self.assertEqual(self.uploads(), [])
        self.assertIn("big.bin", self.notice())

    def test_a_reply_for_another_upload_is_ignored(self):
        self.pick(_file())
        self.wait_uploads(1)
        self.reply({"type": "upload_result", "upload_id": "0000000000000000", "ok": True, "path": "/stale"})
        self.reply({"type": "upload_result", "upload_id": "0000000000000000", "ok": False, "error": "nope"})
        self.assertEqual(self.box(), "")
        self.assertIsNone(self.notice())

    def test_a_pasted_image_uploads(self):
        self.page.evaluate("""() => {
          const dt = new DataTransfer();
          dt.items.add(new File([new Uint8Array([1, 2, 3])], 'paste.png', {type: 'image/png'}));
          const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
          window.__pasteDefault = document.getElementById('termInput').dispatchEvent(ev);
        }""")
        start = self.wait_uploads(1)[0]
        self.assertEqual((start["type"], start["name"]), ("upload_start", "paste.png"))
        self.assertFalse(self.page.evaluate("() => window.__pasteDefault"))

    def test_a_pasted_text_does_not_upload(self):
        self.page.evaluate("""() => {
          const dt = new DataTransfer();
          dt.setData('text/plain', 'hello');
          const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
          window.__pasteDefault = document.getElementById('termInput').dispatchEvent(ev);
        }""")
        self.page.wait_for_timeout(50)
        self.assertEqual(self.uploads(), [])
        self.assertTrue(self.page.evaluate("() => window.__pasteDefault"))

    def test_six_files_send_only_five(self):
        self.pick(*[_file(name="f%d.png" % i) for i in range(6)])
        names = []
        for i in range(5):
            self.page.wait_for_function(
                "n => __uploads().filter(m => m.type === 'upload_start').length >= n", arg=i + 1)
            names.append([m for m in self.uploads() if m["type"] == "upload_start"][-1]["name"])
            self.drive(path="/u/" + names[-1])
        self.page.wait_for_timeout(50)
        self.assertEqual(names, ["f0.png", "f1.png", "f2.png", "f3.png", "f4.png"])
        self.assertEqual(len([m for m in self.uploads() if m["type"] == "upload_start"]), 5)

    def test_a_real_pane_switch_cancels_and_inserts_nothing(self):
        self.advertise(panes=("w1:p1", "w1:p2"))
        self.pick(_file())
        uid = self.wait_uploads(1)[0]["upload_id"]
        self.open_pane("w1:p2")
        self.assertEqual(self.uploads()[-1], {"type": "upload_cancel", "upload_id": uid})
        self.reply({"type": "upload_result", "upload_id": uid, "ok": True, "path": PATH})
        self.assertEqual(self.box(), "")
        self.assertIsNone(self.page.get_attribute("#attachBtn", "aria-busy"))

    def test_a_reentry_for_the_same_pane_cancels_nothing(self):
        self.pick(_file())
        self.wait_uploads(1)
        self.open_pane("w1:p1")
        self.assertNotIn("upload_cancel", [m["type"] for m in self.uploads()])
        self.drive()
        self.assertEqual(self.box(), PATH + " ")

    def test_a_dropped_socket_stops_the_upload_and_keeps_the_text(self):
        self.type_into_box("still here")
        self.pick(_file())
        self.wait_uploads(1)
        self.page.evaluate("() => __drop(0)")
        self.assertIn("Connection lost", self.notice())
        self.assertEqual(self.box(), "still here")
        self.assertIsNone(self.page.get_attribute("#attachBtn", "aria-busy"))

    def test_a_relay_switch_stops_the_upload(self):
        """connect() detaches the old socket before closing it, so its onclose never runs."""
        self.pick(_file())
        uid = self.wait_uploads(1)[0]["upload_id"]
        self.page.evaluate("() => connect()")
        self.assertEqual(self.uploads()[-1], {"type": "upload_cancel", "upload_id": uid})
        self.assertIsNone(self.page.get_attribute("#attachBtn", "aria-busy"))

    def test_a_closed_socket_uploads_nothing_and_says_so(self):
        self.page.evaluate("() => { __sock(0).readyState = 3; }")  # the page slept; no onclose yet
        self.pick(_file())
        self.page.wait_for_timeout(50)
        self.assertEqual(self.uploads(), [])
        self.assertIn("Not connected", self.notice())


class _RowMeasure(_UploadPage):
    def setUp(self):
        super().setUp()
        self.advertise()
        self.open_pane()
        self.assertTrue(self.attach_visible())

    def row_height(self):
        return self.page.evaluate(
            "() => document.querySelector('.term-input').getBoundingClientRect().height")

    def clipped(self):
        return self.page.evaluate(
            """() => [...document.querySelectorAll('.term-input > button')]
                 .filter(b => b.offsetParent && b.scrollWidth > b.clientWidth + 1)
                 .map(b => b.getAttribute('aria-label') || b.textContent.trim())""")

    def test_the_input_row_stays_short_with_the_button(self):
        self.assertLess(self.row_height(), 47)

    def test_no_button_in_the_row_is_clipped(self):
        self.assertEqual(self.clipped(), [])

    def test_no_button_in_the_row_sets_its_own_padding(self):
        offenders = self.page.evaluate(
            """() => [...document.querySelectorAll('.term-input > *')]
                 .filter(el => /padding/.test(el.getAttribute('style') || ''))
                 .map(el => el.className || el.tagName)""")
        self.assertEqual(offenders, [])


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebInputRowPhoneTests(_RowMeasure, unittest.TestCase):
    viewport = PHONE


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebInputRowNarrowTests(_RowMeasure, unittest.TestCase):
    viewport = NARROW


if __name__ == "__main__":
    unittest.main()
