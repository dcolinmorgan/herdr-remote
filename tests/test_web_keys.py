"""Tests for the terminal key pad and the panel/session layering in web/index.html.

Both are geometry, not markup, so both are measured in a real browser rather than asserted against
CSS text: an inverted-T arrow cluster is a claim about where the buttons LAND, and "the settings
panel is covered by the session view" is a claim about which element paints at a given point.
`elementFromPoint` answers the second one exactly the way a thumb does.

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

# A phone, because `.terminal-view` is only `position: fixed` below the 768px breakpoint -- which
# is the width where it covers a panel outright instead of merely pushing it down the page.
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


# One browser for the file. Each class used to launch its own chromium, and `unittest discover`
# runs this alongside test_web_history.py in a single process -- four concurrent browsers was
# enough to make `page.goto` time out intermittently, and it failed a full `tests/run.sh` once
# before passing on the retry. Pages are still per-class, so a test that resizes the viewport or
# leaves a panel open cannot reach another class.
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


def _open_page(viewport=None):
    page = _shared["browser"].new_page(viewport=viewport or PHONE)
    page.goto(PAGE)
    return page


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebKeyPadTests(unittest.TestCase):
    """The keys dock lives inside the session view, so the view has to be up to have layout."""

    @classmethod
    def setUpClass(cls):
        cls.page = _open_page()

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        # Show the pad and capture what the page would put on the wire, without a relay.
        self.page.evaluate("""() => {
          document.getElementById('terminalView').classList.add('active');
          document.getElementById('termKeys').style.display = '';
          window.__sent = [];
          activePane = 'w0:p1';
          ws = {readyState: 1, send: payload => window.__sent.push(JSON.parse(payload))};
          keyQueue = []; armedMod = null; renderMods();
        }""")

    def sent(self):
        return self.page.evaluate("() => window.__sent")

    def box(self, label):
        """The centre of the pad button whose text is `label`."""
        return self.page.evaluate(
            """(() => {
              const btn = [...document.querySelectorAll('#keysPad button')]
                .find(b => b.textContent.trim() === LABEL);
              if (!btn) return null;
              const r = btn.getBoundingClientRect();
              return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                      w: r.width, h: r.height, top: r.top, bottom: r.bottom};
            })()""".replace("LABEL", json.dumps(label)))

    # --- layout ---

    def test_the_arrows_form_an_inverted_t(self):
        """The old pad split them across two rows as Tab/left/down/up then right/Shift/Ctrl."""
        up, down, left, right = (self.box(a) for a in ("↑", "↓", "←", "→"))
        for name, found in zip("↑↓←→", (up, down, left, right)):
            self.assertIsNotNone(found, f"{name} is missing from the pad")
        # Up sits directly above Down, in the same column.
        self.assertAlmostEqual(up["x"], down["x"], delta=1)
        self.assertLess(up["bottom"], down["top"] + 1)
        # Left and Right flank Down on its own row.
        self.assertAlmostEqual(left["y"], down["y"], delta=1)
        self.assertAlmostEqual(right["y"], down["y"], delta=1)
        self.assertLess(left["x"], down["x"])
        self.assertLess(down["x"], right["x"])
        # And nothing occupies the keyboard's empty cell to the left of Up.
        gap = self.page.evaluate(
            """(() => {
              const up = [...document.querySelectorAll('#keysPad button')]
                .find(b => b.textContent.trim() === '\\u2191');
              const r = up.getBoundingClientRect();
              const hit = document.elementFromPoint(r.x - r.width / 2, r.y + r.height / 2);
              return hit ? hit.id || hit.className : null;
            })()""")
        self.assertNotIn("nav-key", gap or "")

    def test_the_pad_leaves_the_terminal_most_of_the_phone(self):
        """The complaint that started this: the dock ate a third of the screen, then half.

        Measured on a 390x844 phone across four revisions: four rows of 44px was 271px closed and
        415px with presets open; five columns and three rows was 205 / 301; seven columns and two
        rows, with the pad switch and the presets disclosure sharing one line, was 121 / 201; and
        trimming every key's own height and padding brought it to 111 / 183. The ceilings are set
        just above the last of those, so the pad cannot grow back into the terminal without a test
        saying so.
        """
        for label, expression, ceiling in (
            ("closed", "() => 0", 0.14),
            ("open", "() => toggleCtrlPresets()", 0.225),
        ):
            with self.subTest(presets=label):
                self.page.evaluate(expression)
                height = self.page.evaluate(
                    "() => document.getElementById('termKeys').getBoundingClientRect().height")
                self.assertLess(height, PHONE["height"] * ceiling,
                                f"the key dock is {height}px with presets {label}")
        self.page.evaluate("() => toggleCtrlPresets()")

    def test_no_key_label_is_clipped_by_the_narrower_cells(self):
        """Seven columns is ~49px a cell at 390px wide, which is what "PgUp" has to survive."""
        self.page.evaluate("() => toggleCtrlPresets()")
        try:
            clipped = self.page.evaluate(
                """() => [...document.querySelectorAll(
                       '#keysPad button, #ctrlPresets button, .keys-bar button, #quickDock button')]
                     .filter(b => b.scrollWidth > b.clientWidth + 1)
                     .map(b => b.textContent.trim())""")
        finally:
            self.page.evaluate("() => toggleCtrlPresets()")
        self.assertEqual(clipped, [])

    def test_the_pad_is_two_rows(self):
        """Not a proxy for the height check: rows are the thing that was traded for width."""
        rows = self.page.evaluate(
            """() => new Set([...document.querySelectorAll('#keysPad .keys-grid button')]
                 .map(b => Math.round(b.getBoundingClientRect().top))).size""")
        self.assertEqual(rows, 2)

    def test_the_switch_and_the_presets_disclosure_share_a_line(self):
        centres = self.page.evaluate(
            """() => ['tabKeys', 'tabDigits', 'presetsBtn'].map(id => {
                 const r = document.getElementById(id).getBoundingClientRect();
                 return Math.round(r.top + r.height / 2); })""")
        self.assertLessEqual(max(centres) - min(centres), 1,
                             f"the control row broke into more than one line: {centres}")

    def test_the_digit_pad_is_one_row(self):
        """3x3 of 52px keys was 164px, taller than the whole keys pad it sits under."""
        self.page.evaluate("() => switchKeyTab('digits')")
        try:
            rows, height = self.page.evaluate(
                """() => [new Set([...document.querySelectorAll('#digitsPad button')]
                            .map(b => Math.round(b.getBoundingClientRect().top))).size,
                          document.getElementById('termKeys').getBoundingClientRect().height]""")
        finally:
            self.page.evaluate("() => switchKeyTab('keys')")
        self.assertEqual(rows, 1)
        self.assertLess(height, PHONE["height"] * 0.10)

    def test_the_input_row_stays_short(self):
        """It was 60px for one line of text and four icons, and stayed 60px after the stylesheet
        rule was cut -- the two tallest children carried their padding INLINE, where no rule can
        reach it, and the row is a flex box so every child stretches to the tallest. 43px now.
        """
        height = self.page.evaluate(
            "() => document.querySelector('.term-input').getBoundingClientRect().height")
        self.assertLess(height, 47, f"the input row is back up to {height}px")

    def test_no_button_in_the_input_row_sets_its_own_padding(self):
        """The trap above, closed: an inline padding outranks every stylesheet rule, so shrinking
        the row silently does nothing. Style them by class or the height cannot be governed."""
        offenders = self.page.evaluate(
            """() => [...document.querySelectorAll('.term-input > *')]
                 .filter(el => /padding/.test(el.getAttribute('style') || ''))
                 .map(el => el.className || el.tagName)""")
        self.assertEqual(offenders, [])

    def test_the_letter_keys_carry_the_dock_theme(self):
        """y / a / n had no CSS at all: three 11x19px native buttons in Chrome's own grey, unthemed
        inside a dark dock. Injected here rather than driven through a blocked pane, because what
        regressed is the stylesheet, not the rendering path."""
        try:
            got = self.page.evaluate("""() => {
              const ak = document.getElementById('actionKeys');
              ak.replaceChildren();
              for (const cls of ['key-green', 'key-blue', 'key-red']) {
                const b = document.createElement('button');
                b.className = cls; b.textContent = cls[4];
                ak.appendChild(b);
              }
              const surface = getComputedStyle(document.getElementById('termKeys')).backgroundColor;
              return [...ak.children].map(b => {
                const r = b.getBoundingClientRect(), cs = getComputedStyle(b);
                return {w: Math.round(r.width), h: Math.round(r.height),
                        themed: cs.backgroundColor !== 'rgb(239, 239, 239)',
                        distinct: cs.borderTopColor !== surface};
              });
            }""")
        finally:
            self.page.evaluate("() => document.getElementById('actionKeys').replaceChildren()")
        self.assertEqual(len(got), 3)
        for key in got:
            self.assertTrue(key["themed"], "a letter key still has the browser's default fill")
            self.assertTrue(key["distinct"], "a letter key has no visible edge")
            self.assertGreater(key["w"], 60, f"a letter key is only {key['w']}px wide")
            self.assertGreaterEqual(key["h"], 28)

    def test_the_presets_disclosure_leaves_with_the_pad_it_opens(self):
        """It shares its line with the switch now, so it no longer hides along with the pad."""
        self.page.evaluate("() => switchKeyTab('digits')")
        try:
            self.assertFalse(self.page.evaluate(
                "() => !!document.getElementById('presetsBtn').offsetParent"))
        finally:
            self.page.evaluate("() => switchKeyTab('keys')")
        self.assertTrue(self.page.evaluate(
            "() => !!document.getElementById('presetsBtn').offsetParent"))

    def test_every_pad_button_stays_inside_the_viewport(self):
        widest = self.page.evaluate(
            """(() => {
              const pad = document.getElementById('keysPad').getBoundingClientRect();
              let over = 0;
              for (const b of document.querySelectorAll('#keysPad button')) {
                const r = b.getBoundingClientRect();
                over = Math.max(over, r.right - pad.right, pad.left - r.left);
              }
              return over;
            })()""")
        self.assertLessEqual(widest, 1, "a pad button overflows its container")

    # --- the page keys ---

    def test_pgup_and_pgdn_send_the_key_names_the_relay_translates(self):
        for label, key in (("PgUp", "PageUp"), ("PgDn", "PageDown")):
            with self.subTest(label=label):
                self.page.evaluate("() => { window.__sent = []; }")
                self.page.evaluate(
                    """(() => [...document.querySelectorAll('#keysPad button')]
                         .find(b => b.textContent.trim() === LABEL).click())()"""
                    .replace("LABEL", json.dumps(label)))
                self.assertEqual(
                    self.sent(),
                    [{"type": "send_keys", "pane_id": "w0:p1", "keys": [key]}])

    def test_ctrl_home_and_ctrl_end_are_offered_as_presets(self):
        labels = self.page.evaluate("() => CTRL_PRESETS.map(p => p.label)")
        self.assertIn("Ctrl Home", labels)
        self.assertIn("Ctrl End", labels)
        keys = self.page.evaluate(
            "() => CTRL_PRESETS.filter(p => /Home|End/.test(p.label)).map(p => p.keys)")
        self.assertEqual(keys, [["ctrl+Home"], ["ctrl+End"]])

    def test_arming_a_modifier_composes_a_chord_the_relay_accepts(self):
        """`ctrl+PageUp` has a CSI encoding too, so the pad cannot compose an invalid chord.

        An armed modifier queues rather than sends -- that is the pad's own design -- so the chord
        is checked where it is built and again on the wire after the queue is flushed.
        """
        self.page.evaluate("() => { window.__sent = []; armMod('ctrl'); fireKey('PageUp'); }")
        self.assertEqual(self.page.evaluate("() => keyQueue"), ["ctrl+PageUp"])
        self.page.evaluate("() => sendQueuedKeys()")
        self.assertEqual(
            self.sent(),
            [{"type": "send_keys", "pane_id": "w0:p1", "keys": ["ctrl+PageUp"]}])


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebPanelLayeringTests(unittest.TestCase):
    """Settings and Timeline opened from inside a session used to render under it, unreachable."""

    @classmethod
    def setUpClass(cls):
        cls.page = _open_page()

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        self.page.evaluate("""() => {
          hidePanel();
          document.getElementById('terminalView').classList.remove('active');
          document.getElementById('agentListView').style.display = '';
        }""")

    def enter_session(self):
        self.page.evaluate(
            "() => document.getElementById('terminalView').classList.add('active')")

    def topmost_over(self, panel_id):
        """Which element actually paints at the centre of the panel -- the panel, or its cover."""
        return self.page.evaluate(
            """(() => {
              const panel = document.getElementById(PANEL);
              const r = panel.getBoundingClientRect();
              if (!r.height) return 'panel has no box';
              const hit = document.elementFromPoint(r.x + r.width / 2, r.y + 20);
              if (!hit) return 'nothing';
              return panel.contains(hit) ? 'panel' : (hit.closest('[id]') || hit).id || hit.tagName;
            })()""".replace("PANEL", json.dumps(panel_id)))

    def visible(self):
        return self.page.evaluate("""() => ({
          settings: document.getElementById('settingsView').style.display,
          timeline: document.getElementById('timelineView').style.display,
          list: document.getElementById('agentListView').style.display,
          session: document.getElementById('terminalView').classList.contains('active'),
        })""")

    def test_settings_opens_on_top_when_reached_from_a_session(self):
        self.enter_session()
        self.page.evaluate("() => toggleSettings()")
        self.assertEqual(self.topmost_over("settingsView"), "panel")
        self.assertFalse(self.visible()["session"], "the session view must step aside")

    def test_timeline_opens_on_top_when_reached_from_a_session(self):
        self.enter_session()
        self.page.evaluate("() => toggleTimeline()")
        self.assertEqual(self.topmost_over("timelineView"), "panel")

    def test_settings_still_opens_on_top_from_the_agent_list(self):
        self.page.evaluate("() => toggleSettings()")
        self.assertEqual(self.topmost_over("settingsView"), "panel")
        self.assertEqual(self.visible()["list"], "none")

    def test_closing_returns_to_the_session_it_was_opened_from(self):
        self.enter_session()
        self.page.evaluate("() => toggleSettings()")
        self.page.evaluate("() => closePanel()")
        state = self.visible()
        self.assertTrue(state["session"], "the session must come back")
        # It used to reappear UNDER the still-active session view.
        self.assertEqual(state["list"], "none", "the agent list must stay hidden")

    def test_closing_returns_to_the_agent_list_when_opened_from_there(self):
        self.page.evaluate("() => toggleSettings()")
        self.page.evaluate("() => closePanel()")
        state = self.visible()
        self.assertFalse(state["session"])
        self.assertEqual(state["list"], "")

    def test_swapping_panels_inside_a_session_still_remembers_the_session(self):
        """The second open must not re-read the session flag: it is already deactivated by then."""
        self.enter_session()
        self.page.evaluate("() => toggleSettings()")
        self.page.evaluate("() => toggleTimeline()")
        self.assertEqual(self.topmost_over("timelineView"), "panel")
        self.page.evaluate("() => closePanel()")
        state = self.visible()
        self.assertTrue(state["session"], "swapping panels lost the session")
        self.assertEqual(state["list"], "none")

    def test_a_wide_viewport_also_frees_the_panel(self):
        """Above 768px the view is `position: relative`, and used to push the panel off-screen."""
        self.page.set_viewport_size({"width": 1100, "height": 800})
        try:
            self.enter_session()
            self.page.evaluate("() => toggleSettings()")
            self.assertEqual(self.topmost_over("settingsView"), "panel")
            # The session view claimed a full viewport-height of layout below the panel.
            self.assertFalse(self.page.evaluate(
                "() => document.documentElement.scrollHeight > innerHeight + 2 "
                "&& document.getElementById('terminalView').offsetHeight > 0"))
        finally:
            self.page.set_viewport_size(PHONE)


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebToggleStateTests(unittest.TestCase):
    """Every toggle in the session view has to look different open than closed.

    Search, History and the two docks all used to render pixel-identical either way -- the only
    clue that a dock was open was the dock itself, and for Search and History not even that once
    the panel was scrolled past. So each one is checked twice: `aria-pressed` for the attribute the
    CSS and the screen reader both read, and the computed background for the pixels a thumb sees.
    """

    @classmethod
    def setUpClass(cls):
        cls.page = _open_page()

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        self.page.evaluate("""() => {
          document.getElementById('terminalView').classList.add('active');
          activePane = 'w0:p1';
          ws = {readyState: 1, send: () => {}};
          hideSearch(); hideHistory(); showDock(null);
        }""")

    def look(self, button_id):
        return self.page.evaluate(
            """(() => {
              const el = document.getElementById(ID);
              const cs = getComputedStyle(el);
              return {pressed: el.getAttribute('aria-pressed'),
                      bg: cs.backgroundColor, fg: cs.color, border: cs.borderTopColor};
            })()""".replace("ID", json.dumps(button_id)))

    def assert_lights(self, button_id, open_js, close_js):
        off = self.look(button_id)
        self.assertEqual(off["pressed"], "false")
        self.page.evaluate(open_js)
        on = self.look(button_id)
        self.assertEqual(on["pressed"], "true")
        self.assertNotEqual(on["bg"], off["bg"], f"{button_id} looks the same open as closed")
        self.assertNotEqual(on["fg"], off["fg"], f"{button_id} keeps its closed foreground")
        self.page.evaluate(close_js)
        self.assertEqual(self.look(button_id)["pressed"], "false")

    def test_the_search_button_lights_while_the_bar_is_open(self):
        self.assert_lights("searchBtn", "() => toggleSearch()", "() => toggleSearch()")

    def test_the_history_button_lights_while_the_panel_is_open(self):
        self.assert_lights("historyBtn", "() => toggleHistory()", "() => toggleHistory()")

    def test_the_keys_dock_button_lights_while_the_dock_is_up(self):
        self.assert_lights("keysDockBtn", "() => toggleKeysDock()", "() => toggleKeysDock()")

    def test_the_quick_dock_button_lights_while_the_dock_is_up(self):
        self.assert_lights("quickDockBtn", "() => toggleQuickDock()", "() => toggleQuickDock()")

    def test_opening_one_dock_unlights_the_other(self):
        """They are mutually exclusive, so the closed one has to be un-lit, not merely hidden."""
        self.page.evaluate("() => toggleKeysDock()")
        self.page.evaluate("() => toggleQuickDock()")
        self.assertEqual(self.look("keysDockBtn")["pressed"], "false")
        self.assertEqual(self.look("quickDockBtn")["pressed"], "true")

    def test_search_and_history_unlight_each_other(self):
        """They share the space under the header, so opening one closes -- and dims -- the other."""
        self.page.evaluate("() => toggleHistory()")
        self.page.evaluate("() => toggleSearch()")
        self.assertEqual(self.look("historyBtn")["pressed"], "false")
        self.assertEqual(self.look("searchBtn")["pressed"], "true")
        self.page.evaluate("() => toggleHistory()")
        self.assertEqual(self.look("searchBtn")["pressed"], "false")
        self.assertEqual(self.look("historyBtn")["pressed"], "true")

    def test_search_closing_history_also_drops_its_history_entry(self):
        """It used to hide the panel directly, leaving a nav layer with nothing behind it."""
        self.page.evaluate("() => toggleHistory()")
        self.page.evaluate("() => toggleSearch()")
        self.assertNotIn("history", self.page.evaluate("() => navStack.map(l => l.key)"))

    def test_the_pad_switch_shows_which_pad_is_up(self):
        self.page.evaluate("() => switchKeyTab('digits')")
        try:
            self.assertEqual(self.look("tabDigits")["pressed"], "true")
            self.assertEqual(self.look("tabKeys")["pressed"], "false")
        finally:
            self.page.evaluate("() => switchKeyTab('keys')")
        self.assertEqual(self.look("tabKeys")["pressed"], "true")

    def test_the_presets_disclosure_says_whether_it_is_open(self):
        """A text disclosure, not a chip, so it says it with its colour and its chevron -- the
        chevron alone was the old signal and it is 7px of glyph on a phone."""
        off = self.look("presetsBtn")
        self.assertEqual(off["pressed"], "false")
        self.page.evaluate("() => toggleCtrlPresets()")
        try:
            on = self.look("presetsBtn")
            self.assertEqual(on["pressed"], "true")
            self.assertNotEqual(on["fg"], off["fg"], "the disclosure keeps its closed colour")
            self.assertEqual(
                self.page.evaluate("() => document.getElementById('ctrlChevron').textContent"),
                "\u25be")
        finally:
            self.page.evaluate("() => toggleCtrlPresets()")
        self.assertEqual(self.look("presetsBtn")["pressed"], "false")

    def test_the_refresh_button_is_not_dressed_as_a_toggle(self):
        """It fires and returns; a pressed state on it would be a lie, and the contrast with the
        two chips beside it is what says those two are toggles."""
        self.assertIsNone(self.page.evaluate(
            "() => document.querySelector('.refresh-btn').getAttribute('aria-pressed')"))

    def test_the_history_panel_starts_with_tool_calls_shown(self):
        """A tool call is most of what a turn consists of, so hiding them by default read as a
        conversation with holes in it. The chip is the way back to prose-only."""
        self.assertTrue(self.page.evaluate("() => history_.tools"))
        self.assertEqual(
            self.page.evaluate(
                "() => document.getElementById('historyToolsBtn').getAttribute('aria-pressed')"),
            "true")

    def test_the_history_request_carries_the_tool_flag_it_shows(self):
        sent = self.page.evaluate("""() => {
          const out = [];
          history_.loading = false;
          const real = ws;
          ws = {readyState: 1, send: p => out.push(JSON.parse(p))};
          loadHistory();
          ws = real;
          return out;
        }""")
        self.assertEqual([m["include_tools"] for m in sent], [True])


if __name__ == "__main__":
    unittest.main()
