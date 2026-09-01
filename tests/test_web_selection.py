"""Tests for what it takes to copy text out of a page that rebuilds itself on a timer.

`replaceChildren` does not merely lose a selection anchored inside it: measured in chromium, it
COLLAPSES the selection to (container, 0), so the next extend -- a drag continuing, a phone's
handle being moved -- runs from the top of the output and the reader watches the first line
highlight itself. That is asserted here directly, because it is the whole reason for the rest.

Three claims follow, and each has its own class:

- A timed rebuild of a container the reader is selecting inside does not run. Its edges: a caret (a
  plain tap) must NOT freeze anything, a selection elsewhere on the page must not either, and the
  skipped update has to land as soon as the selection is released.
- The mirror does not replace what it does not have to -- identical content touches no DOM, and
  output appended to a run is appended to the text node already on screen. This is what closes the
  window the freeze cannot: a touch drag dismisses the old selection before it makes the new one,
  and a tick landing in between is the one that moves the anchor.
- `scroll` says nothing about which axis moved. A horizontal drag -- exactly what a long line asks
  for -- must not be read as "I want more lines".

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
class WebPaneSwitchTests(unittest.TestCase, _Selecting):
    """What is on screen while the read for the pane you just picked is still on the wire.

    The mirror is the output of the pane you LEFT, and a tap on a sibling chip moves the title, the
    filled chip and nothing else -- so for a local host that is milliseconds of stale content and for
    a remote one an SSH round trip of it, with nothing on the screen saying so. The reported lag is
    exactly that: the labels switched and the content did not.
    """

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
          activePane = null;
          handleMessage(s);
          window.__sent = [];
          ws = {readyState: 1, send: p => window.__sent.push(JSON.parse(p))};
          openTerminal('wB:pH');
          clearInterval(refreshInterval);
          handleMessage({type: 'pane_content', pane_id: 'wB:pH',
                         content: 'the output of the pane you left\\nsecond line'});
          window.__sent = [];
        }""", SNAPSHOT)

    def tearDown(self):
        self.release()

    def mirror(self):
        return self.page.eval_on_selector("#termContent", "e => e.textContent")

    def reads(self):
        return [m for m in self.page.evaluate("window.__sent") if m["type"] == "read_pane"]

    def test_a_switch_drops_the_other_panes_output_instead_of_leaving_it_up(self):
        """The whole of the fix. Until the read lands there is nothing true to show, so it says so
        rather than showing something false."""
        self.assertIn("the pane you left", self.mirror())
        self.page.evaluate("openTerminal('wB:pQ')")
        self.assertNotIn("the pane you left", self.mirror())
        self.assertEqual(self.mirror(), "Loading\u2026")
        self.assertEqual([r["pane_id"] for r in self.reads()], ["wB:pQ"])

    def test_the_new_panes_first_read_fills_it(self):
        """And the emptying is not a state anything has to be dug out of: the read openTerminal
        already sends is what ends it."""
        self.page.evaluate("openTerminal('wB:pQ')")
        self.page.evaluate("""() => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pQ', content: 'the new pane'})""")
        self.assertEqual(self.mirror(), "the new pane")

    def test_the_other_panes_read_still_in_flight_never_lands(self):
        """It was requested for a pane that is no longer open, and it would arrive AFTER the switch
        -- under the new pane's title, which is the same lie in the other direction."""
        self.page.evaluate("openTerminal('wB:pQ')")
        self.page.evaluate("""() => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pH', content: 'late answer for the old pane'})""")
        self.assertNotIn("late answer", self.mirror())

    def test_a_selection_left_in_the_old_output_does_not_block_the_new_pane(self):
        """selectionInside guards the mirror and cannot tell a stale range from a live one, so a
        drag in the pane you left would have refused the new pane's first read -- and the emptied
        mirror would have stayed empty until the reader thought to tap somewhere."""
        self.select("#termContent", "the output of the pane you left")
        self.page.evaluate("openTerminal('wB:pQ')")
        self.assertEqual(self.selected(), "")
        self.page.evaluate("""() => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pQ', content: 'the new pane'})""")
        self.assertEqual(self.mirror(), "the new pane")

    def test_a_selection_anywhere_else_on_the_page_survives_the_switch(self):
        """Only the output being thrown away is this function's business. Not the session title,
        which openTerminal rewrites on its own and which therefore cannot hold a range across a
        switch either way -- the app header can, and is the one the reader would have copied from."""
        self.select(".header h1", "herdr")
        self.page.evaluate("openTerminal('wB:pQ')")
        self.assertEqual(self.selected(), "herdr")

    def test_reopening_the_pane_already_open_does_not_blank_it(self):
        """openTerminal is re-entered on every `blocked` event for the pane in front of you. Clearing
        there would blink the output away every time an agent asked a question."""
        self.page.evaluate("openTerminal('wB:pH')")
        self.assertIn("the pane you left", self.mirror())

    def test_the_switch_does_not_leave_the_mirror_reconciling_against_another_pane(self):
        """mirrorPatch's memory of what is on screen is a property of the element, and the element
        outlives the pane. Left in place, a new pane whose first read happens to match the old
        content would be answered with "nothing changed" against a buffer that says Loading."""
        self.page.evaluate("openTerminal('wB:pQ')")
        self.assertIsNone(self.page.evaluate(
            "() => document.getElementById('termContent').__mirror ?? null"))
        self.page.evaluate("""() => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pQ',
           content: 'the output of the pane you left\\nsecond line'})""")
        self.assertIn("the pane you left", self.mirror())


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


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebSelectionCollapseTests(unittest.TestCase):
    """What chromium actually does to a selection whose nodes are replaced. Everything else in this
    file is built on this measurement, so it is measured rather than cited."""

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)
        # The box has to be RENDERED: Selection.toString() of an undisplayed subtree is empty, so a
        # measurement taken with the session view closed would prove nothing either way.
        cls.page.evaluate("""s => {
          handleMessage(s);
          ws = {readyState: 1, send: () => {}};
          openTerminal('wB:pH');
          clearInterval(refreshInterval);
        }""", SNAPSHOT)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def test_a_replaced_child_collapses_the_selection_to_the_top_of_the_box(self):
        got = self.page.evaluate("""() => {
          const box = document.getElementById('termContent');
          box.replaceChildren(ansiFragment('line1 AAA\\nline2 BBB\\nline3 NEEDLE CCC'));
          const node = box.firstChild.firstChild;
          const at = node.data.indexOf('NEEDLE');
          const range = document.createRange();
          range.setStart(node, at); range.setEnd(node, at + 6);
          const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
          const before = sel.toString();

          box.replaceChildren(ansiFragment('zine1 AAA\\nzine2 BBB\\nzine3 NEEDLE CCC'));
          const after = {ranges: getSelection().rangeCount,
                         anchorIsTheBox: getSelection().anchorNode === box,
                         offset: getSelection().anchorOffset};
          getSelection().extend(box.firstChild.firstChild, 15);
          return {before, after, extended: getSelection().toString()};
        }""")
        self.assertEqual(got["before"], "NEEDLE")
        # Not "the selection is gone" -- it is alive, anchored at the very start of the output.
        self.assertEqual(got["after"], {"ranges": 1, "anchorIsTheBox": True, "offset": 0})
        # ... so the reader's next drag highlights the first line, which is the bug as reported.
        self.assertEqual(got["extended"], "zine1 AAA\nzine2")
        self.page.evaluate("getSelection().removeAllRanges()")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebMirrorPatchTests(unittest.TestCase, _Selecting):
    """The reconciler. Every claim here is about BLAST RADIUS: not "can a node be rewritten without
    moving what is anchored in it" -- measured, none can -- but "how little has to be rewritten"."""

    BODY = "\n".join([f"line {i} NEEDLE " + "C" * 60 for i in range(50)])
    GREW = BODY + "\nline 50 tail"
    ONE_LINE_CHANGED = "\n".join(
        [f"line {i} NEEDLE " + "C" * 60 if i != 40 else "line 40 MOVED" for i in range(50)])
    SCROLLED = "\n".join([f"line {i} NEEDLE " + "C" * 60 for i in range(5, 55)])
    REPAINT = "\n".join([f"XX {i} " + "C" * 60 for i in range(50)])

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)
        cls.page.evaluate("""s => {
          handleMessage(s);
          ws = {readyState: 1, send: () => {}};
          openTerminal('wB:pH');
          clearInterval(refreshInterval);
        }""", SNAPSHOT)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        """Built from scratch, then every line node stamped -- so "did this survive" is a fact."""
        self.page.evaluate("""body => {
          const el = document.getElementById('termContent');
          el.__mirror = null;
          mirrorPatch(el, body);
          [...el.children].forEach((n, i) => n.__gen = i);
        }""", self.BODY)

    def tearDown(self):
        self.release()

    def patch(self, content):
        return self.page.evaluate(
            "c => mirrorPatch(document.getElementById('termContent'), c)", content)

    def kept(self):
        """Which of the stamped line nodes are still in the box, in document order."""
        return self.page.eval_on_selector_all(
            "#termContent > *", "els => els.map(e => e.__gen === undefined ? null : e.__gen)")

    def caret_on(self, line, column):
        self.page.evaluate("""([line, column]) => {
          const el = document.getElementById('termContent');
          const walker = document.createTreeWalker(el.children[line], NodeFilter.SHOW_TEXT);
          walker.nextNode();
          const range = document.createRange();
          range.setStart(walker.currentNode, column);
          range.collapse(true);
          const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
        }""", [line, column])

    def caret(self):
        """Where the caret is in the mirror's own terms: which line node holds it, or -1."""
        return self.page.evaluate("""() => {
          const el = document.getElementById('termContent');
          const sel = getSelection();
          if (!sel.rangeCount) return {line: -1, offset: -1, isTheBox: false, dropped: true};
          return {line: [...el.children].findIndex(n => n === sel.anchorNode
                                                     || n.contains(sel.anchorNode)),
                  offset: sel.anchorOffset, isTheBox: sel.anchorNode === el, dropped: false};
        }""")

    def test_the_line_structure_changes_neither_a_character_nor_a_pixel(self):
        """The newline lives BETWEEN the line spans, not inside one, so textContent is byte-identical
        -- doSearch counts offsets in it. The boxes are measured because "one span per line" is a
        claim about layout that reading the CSS cannot settle."""
        got = self.page.evaluate("""body => {
          const el = document.getElementById('termContent');
          el.__mirror = null;
          el.replaceChildren(ansiFragment(body));          // the old flat render
          const flat = {h: el.scrollHeight, w: el.scrollWidth, text: el.textContent};
          el.__mirror = null;
          mirrorPatch(el, body);                           // one span per line
          return {flat, lines: {h: el.scrollHeight, w: el.scrollWidth, text: el.textContent},
                  count: el.children.length, body};
        }""", self.BODY)
        self.assertEqual(got["lines"]["text"], got["body"])
        self.assertEqual(got["lines"]["text"], got["flat"]["text"])
        self.assertEqual((got["lines"]["h"], got["lines"]["w"]),
                         (got["flat"]["h"], got["flat"]["w"]))
        self.assertEqual(got["count"], 50)

    def test_an_unchanged_tick_touches_no_dom_at_all(self):
        """Most ticks. An idle pane returns the same bytes every 3s, and the old code rebuilt the
        whole buffer 20 times a minute -- every rebuild a chance to catch a gesture mid-flight."""
        self.assertFalse(self.patch(self.BODY))
        self.assertEqual(self.kept(), list(range(50)))

    def test_a_change_on_one_line_leaves_every_other_line_alone(self):
        self.assertTrue(self.patch(self.ONE_LINE_CHANGED))
        self.assertEqual(self.kept(), list(range(50)))   # the line node itself is reused
        self.assertEqual(
            self.page.eval_on_selector("#termContent", "e => e.children[40].textContent"),
            "line 40 MOVED")

    def test_a_caret_three_lines_up_does_not_move_when_line_forty_changes(self):
        """The reported bug, stated exactly. A touch drag leaves a caret behind; the freeze ignores
        carets on purpose (freezing on every tap would stop the mirror for good); so the caret is
        what an update has to not move."""
        self.caret_on(3, 7)
        self.patch(self.ONE_LINE_CHANGED)
        self.assertEqual(self.caret(), {"line": 3, "offset": 7, "isTheBox": False,
                                        "dropped": False})

    def test_a_full_repaint_leaves_the_caret_on_its_own_line(self):
        """Every line differs -- a working agent's TUI. The caret cannot keep its column (assigning
        text collapses ranges to offset 0 of the node) but it must not leave its line, because
        (box, 0) is what makes the reader's next drag highlight the first line."""
        self.caret_on(3, 7)
        self.patch(self.REPAINT)
        self.assertEqual(self.caret(), {"line": 3, "offset": 0, "isTheBox": False,
                                        "dropped": False})

    def test_a_scrolled_buffer_keeps_the_nodes_that_only_moved_up(self):
        """Five lines printed: the other 45 are the same text one row up, so they keep their NODES
        and a selection on them survives a pane that is actually working."""
        self.select("#termContent", "line 20 NEEDLE")
        self.assertTrue(self.patch(self.SCROLLED))
        self.assertEqual(self.kept()[:45], list(range(5, 50)))
        self.assertEqual(self.kept()[45:], [None] * 5)
        self.assertEqual(self.selected(), "line 20 NEEDLE")

    def test_a_caret_in_a_line_that_scrolls_off_is_dropped_not_teleported(self):
        """Its text is gone, so there is nowhere honest to put it -- and leaving it to fall back to
        (box, 0) is the bug itself. Dropped means the reader's next drag starts where their finger
        is."""
        self.caret_on(3, 7)
        self.patch(self.SCROLLED)
        self.assertTrue(self.caret()["dropped"])

    def test_output_appended_to_a_line_keeps_the_node_and_the_selection(self):
        """appendData is the one mutation the DOM spec leaves ranges alone through, which is why
        this may land mid-drag."""
        self.select("#termContent", "line 49 NEEDLE")
        self.assertTrue(self.patch(self.GREW))
        self.assertEqual(self.kept()[:50], list(range(50)))
        self.assertEqual(self.selected(), "line 49 NEEDLE")
        self.assertTrue(self.page.eval_on_selector(
            "#termContent", "e => e.textContent.endsWith('line 50 tail')"))

    def test_an_unchanged_tick_does_not_yank_the_scroll_either(self):
        """The fix-up below the patch pins an unscrolled mirror to the bottom. Running it on a tick
        that changed nothing is how a reader who nudged the view got pulled back every 3s."""
        self.page.evaluate("document.getElementById('termContent').scrollTop = 20")
        self.page.evaluate("""body => handleMessage(
          {type: 'pane_content', pane_id: 'wB:pH', content: body})""", self.BODY)
        self.assertEqual(self.page.eval_on_selector("#termContent", "e => e.scrollTop"), 20)


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebMirrorScrollAxisTests(unittest.TestCase, _Selecting):
    """`if (scrollTop === 0) loadMore()` fired on horizontal scrolling too, and scrollTop is 0 for
    the whole of a sideways drag -- which is the gesture a long line asks for."""

    # Wide enough to scroll sideways, tall enough to scroll down.
    BODY = "\n".join([f"line {i} NEEDLE " + "C" * 300 for i in range(80)])

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        # Left at the TOP, with the handler's own memory of the position agreeing. That is the state
        # the bug lives in: `scrollTop === 0` is then true for the whole of a sideways drag.
        self.page.evaluate("""([s, body]) => {
          activeWorkspace = null; activeTab = null;
          handleMessage(s);
          window.__sent = [];
          ws = {readyState: 1, send: p => window.__sent.push(JSON.parse(p))};
          openTerminal('wB:p2');           // a shell pane: the only kind with a ring to load
          clearInterval(refreshInterval);
          handleMessage({type: 'pane_content', pane_id: 'wB:p2', content: body});
          document.getElementById('termContent').scrollTop = 0;
          mirrorScrollTop = 0;
          paneLines = 200;
          window.__sent = [];
        }""", [SNAPSHOT, self.BODY])

    def tearDown(self):
        self.release()

    def scroll(self, **pos):
        self.page.evaluate("""pos => {
          const el = document.getElementById('termContent');
          Object.assign(el, pos);
          el.dispatchEvent(new Event('scroll'));
        }""", pos)

    def reads(self):
        return [m["lines"] for m in self.page.evaluate("window.__sent")
                if m["type"] == "read_pane"]

    def test_scrolling_sideways_does_not_ask_for_more_lines(self):
        """Measured before the fix, at the top of a long line: one wheel right took the read from
        200 lines to 600 and the next to 1000, each answer a different content -- hundreds of lines
        arriving in FRONT of the reader's own -- replacing the mirror under their hands."""
        for left in (100, 260, 700):
            self.scroll(scrollLeft=left)
        self.assertEqual(self.reads(), [])
        self.assertEqual(self.page.evaluate("paneLines"), 200)

    def test_a_mirror_shorter_than_its_box_never_loads_more(self):
        """The other half of it: with nothing to scroll down to, scrollTop is 0 forever, so EVERY
        scroll event was a request for more lines."""
        self.page.evaluate("""() => {
          handleMessage({type: 'pane_content', pane_id: 'wB:p2', content: 'one\\ntwo'});
          document.getElementById('termContent').scrollTop = 0;
          mirrorScrollTop = 0;
          window.__sent = [];
        }""")
        for left in (40, 120, 200):
            self.scroll(scrollLeft=left)
        self.assertEqual(self.reads(), [])

    def test_arriving_at_the_top_still_loads_more(self):
        """The feature the axis check must not cost: paging back through a shell pane's ring."""
        self.scroll(scrollTop=400)
        self.scroll(scrollTop=0)
        self.assertEqual(self.reads(), [600])

    def test_a_selection_defers_the_bigger_read(self):
        """Hundreds of lines arrive in FRONT of what is on screen, so this is the one update the
        patch cannot make non-destructively."""
        self.scroll(scrollTop=400)
        self.select("#termContent", "NEEDLE")
        self.scroll(scrollTop=0)
        self.assertEqual(self.reads(), [])


if __name__ == "__main__":
    unittest.main()
