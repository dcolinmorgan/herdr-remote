"""Tests for the terminal key pad in web/index.html.

The layout is geometry, not markup, so it is measured in a real browser rather than asserted
against CSS text: an inverted-T arrow cluster is a claim about where the buttons LAND.

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

if __name__ == "__main__":
    unittest.main()
