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


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebKeyPadTests(unittest.TestCase):
    """The keys dock lives inside the session view, so the view has to be up to have layout."""

    @classmethod
    def setUpClass(cls):
        cls._playwright = sync_playwright().start()
        cls._browser = cls._playwright.chromium.launch(executable_path=_chrome())
        cls.page = cls._browser.new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._playwright.stop()

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

        Four rows of 44px plus three rows of presets measured 271px closed and 415px open on a
        390x844 phone. Five columns fold the modifiers into the arrows' spare column, so the pad is
        three rows, and the presets are four columns instead of three.
        """
        for label, expression, ceiling in (
            ("closed", "() => 0", 0.28),
            ("open", "() => toggleCtrlPresets()", 0.40),
        ):
            with self.subTest(presets=label):
                self.page.evaluate(expression)
                height = self.page.evaluate(
                    "() => document.getElementById('termKeys').getBoundingClientRect().height")
                self.assertLess(height, PHONE["height"] * ceiling,
                                f"the key dock is {height}px with presets {label}")
        self.page.evaluate("() => toggleCtrlPresets()")

    def test_no_key_label_is_clipped_by_the_narrower_cells(self):
        """Five columns and four preset columns is the cost of the three-row pad."""
        self.page.evaluate("() => toggleCtrlPresets()")
        try:
            clipped = self.page.evaluate(
                """() => [...document.querySelectorAll('#keysPad button, #ctrlPresets button')]
                     .filter(b => b.scrollWidth > b.clientWidth + 1)
                     .map(b => b.textContent.trim())""")
        finally:
            self.page.evaluate("() => toggleCtrlPresets()")
        self.assertEqual(clipped, [])

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
        cls._playwright = sync_playwright().start()
        cls._browser = cls._playwright.chromium.launch(executable_path=_chrome())
        cls.page = cls._browser.new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._playwright.stop()

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


if __name__ == "__main__":
    unittest.main()
