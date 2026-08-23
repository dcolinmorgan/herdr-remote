"""Tests for the one rule that lets a reader copy text out of a page that rebuilds itself.

The mirror is replaced every 3s and the herd list on every 2s `agents` snapshot, and both writes
detach the text nodes a selection is anchored to -- so a selection held long enough to reach the
copy button did not survive to be copied. The rule is that a timed rebuild of a container the
reader is selecting inside does not run, and it has three edges worth measuring: a caret (a plain
tap) must NOT freeze anything, a selection somewhere else on the page must not either, and the
skipped update has to land as soon as the selection is released.

Skipped, not failed, when playwright or a chromium build is missing.
"""
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


def _chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


try:  # pragma: no cover - environment probe
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


# One browser for the file, for the reason spelled out in test_web_keys.py: `unittest discover`
# runs every test_web_*.py in one process and concurrent chromiums make `page.goto` time out.
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


T0 = 1_700_000_000_000


def _agent(pane_id, **extra):
    return {"pane_id": pane_id, "agent": "claude", "label": "", "status": "working",
            "cwd": "/work/billing", "project": "billing", "host": "local", "remote": None,
            "workspace_id": "wB", "tab_id": "wB:t1", "title": "", "focused": False,
            "scrollback": 0, "viewport_rows": 40, "has_session": True,
            "last_active_at": T0, "last_seen_at": T0, **extra}


SNAPSHOT = {
    "type": "agents",
    "agents": [_agent("wB:pH"), _agent("wB:pQ")],
    "spaces": {
        "workspaces": [{"workspace_id": "wB", "label": "billing", "number": 1, "focused": True,
                        "tab_count": 1, "pane_count": 2, "host": "local"}],
        "tabs": [{"tab_id": "wB:t1", "workspace_id": "wB", "label": "1", "number": 1,
                  "focused": True, "pane_count": 2, "host": "local"}],
    },
    "panes": [],
}

# The third row is what a snapshot arriving mid-selection would add, so its absence is the proof
# the list held still and its presence the proof the skipped update was not lost.
GREW = {**SNAPSHOT, "agents": [_agent("wB:pH"), _agent("wB:pQ"), _agent("wB:pR")]}


class _Selecting:
    """Selection fixtures. A programmatic Range is what a drag leaves behind: same object, same
    endpoints, and `window.getSelection()` reports it identically."""

    SELECT = """([root, needle]) => {
      const walker = document.createTreeWalker(document.querySelector(root),
                                               NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode, i = node.textContent.indexOf(needle);
        if (i < 0) continue;
        const range = document.createRange();
        range.setStart(node, i);
        range.setEnd(node, i + needle.length);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        return sel.toString();
      }
      return null;
    }"""

    def select(self, root, needle):
        got = self.page.evaluate(self.SELECT, [root, needle])
        self.assertEqual(got, needle, f"the fixture never selected {needle!r} inside {root}")

    def caret(self, root):
        """A tap, not a drag: one collapsed range, which is what every click leaves behind."""
        self.page.evaluate("""root => {
          const walker = document.createTreeWalker(document.querySelector(root),
                                                   NodeFilter.SHOW_TEXT);
          walker.nextNode();
          const range = document.createRange();
          range.setStart(walker.currentNode, 1);
          range.collapse(true);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
        }""", root)
        self.assertTrue(self.page.evaluate("window.getSelection().isCollapsed"))

    def release(self):
        self.page.evaluate("window.getSelection().removeAllRanges()")

    def selected(self):
        return self.page.evaluate("window.getSelection().toString()")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebMirrorSelectionTests(unittest.TestCase, _Selecting):
    """The 3s mirror tick, which is the one the operator meets: select a line of an agent's output
    and it went dark a moment later."""

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        self.page.evaluate("""s => {
          activeWorkspace = null; activeTab = null;
          handleMessage(s);
          ws = {readyState: 1, send: p => window.__sent.push(JSON.parse(p))};
          window.__sent = [];
          openTerminal('wB:pH');
          // The real interval would fire mid-test and answer for the tick under measurement.
          clearInterval(refreshInterval);
          handleMessage({type: 'pane_content', pane_id: 'wB:pH',
                         content: 'error: the first content\\nsecond line'});
          window.__sent = [];
        }""", SNAPSHOT)

    def tearDown(self):
        self.release()

    def mirror(self):
        return self.page.eval_on_selector("#termContent", "e => e.textContent")

    def reads(self):
        return [m for m in self.page.evaluate("window.__sent") if m["type"] == "read_pane"]

    def test_a_read_arriving_mid_selection_does_not_wipe_it(self):
        """The in-flight case: the request left before the drag started, so the tick's own guard
        never saw it and this swap is the one that used to clear the highlight."""
        self.select("#termContent", "the first content")
        self.page.evaluate("""() => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pH', content: 'replaced'})""")
        self.assertEqual(self.selected(), "the first content")
        self.assertIn("the first content", self.mirror())

    def test_the_tick_does_not_even_ask_the_relay_while_a_selection_is_held(self):
        """Fetching content the tick has already decided it may not render costs a herdr call --
        an SSH round trip on a remote host."""
        self.select("#termContent", "second line")
        self.page.evaluate("mirrorTick()")
        self.assertEqual(self.reads(), [])

    def test_releasing_the_selection_lets_the_next_tick_through(self):
        """Nothing is queued, so the skipped update has to arrive on the tick after the release --
        otherwise the mirror stays dark until the operator finds the refresh button."""
        self.select("#termContent", "second line")
        self.page.evaluate("mirrorTick()")
        self.release()
        self.page.evaluate("mirrorTick()")
        self.assertEqual(len(self.reads()), 1)
        self.page.evaluate("""() => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pH', content: 'replaced'})""")
        self.assertEqual(self.mirror(), "replaced")

    def test_a_caret_is_not_a_selection(self):
        """A tap inside the output leaves a collapsed range behind. Freezing on that would stop the
        mirror for good on the first touch, which is worse than the bug being fixed."""
        self.caret("#termContent")
        self.page.evaluate("mirrorTick()")
        self.assertEqual(len(self.reads()), 1)
        self.page.evaluate("""() => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pH', content: 'replaced'})""")
        self.assertEqual(self.mirror(), "replaced")

    def test_a_selection_elsewhere_on_the_page_does_not_freeze_the_mirror(self):
        """The guard is per container: text picked out of the header says nothing about the output."""
        self.select("#termTitle", "billing")
        self.page.evaluate("mirrorTick()")
        self.assertEqual(len(self.reads()), 1)
        self.assertEqual(self.selected(), "billing")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebHerdSelectionTests(unittest.TestCase, _Selecting):
    """The same rule on the list, which is rewritten wholesale every 2s by the relay's own poll."""

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        self.page.evaluate("""s => {
          activeWorkspace = null; activeTab = null;
          hideTerminal();
          handleMessage(s);
        }""", SNAPSHOT)

    def tearDown(self):
        self.release()

    def cards(self):
        return self.page.eval_on_selector_all("#agents .agent", "els => els.length")

    def test_a_snapshot_does_not_wipe_a_selection_out_of_the_list(self):
        self.assertEqual(self.cards(), 2)
        self.select("#agents", "billing")
        self.page.evaluate("s => handleMessage(s)", GREW)
        self.assertEqual(self.selected(), "billing")
        self.assertEqual(self.cards(), 2, "the list was rebuilt under the selection")

    def test_the_list_catches_up_on_the_next_snapshot(self):
        """Skipping is only honest because the next snapshot carries the whole state again."""
        self.select("#agents", "billing")
        self.page.evaluate("s => handleMessage(s)", GREW)
        self.release()
        self.page.evaluate("s => handleMessage(s)", GREW)
        self.assertEqual(self.cards(), 3)


if __name__ == "__main__":
    unittest.main()
