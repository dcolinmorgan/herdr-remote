"""Tests for the theme in web/index.html: the palette, the Auto/Light/Dark pin, and the font.

Measured in a real browser rather than read off the CSS, because every claim here is about what a
reader actually gets:

- "the background is #0a0a0a" is a computed colour, and the tokens are `light-dark()` pairs that
  only resolve once an element has a `color-scheme` -- reading the declaration back would prove
  nothing about which half won;
- "a pin beats the OS" needs an OS to disagree with, which is `emulate_media`;
- "the UI is monospace" is a claim about advance widths, so the test measures two strings of equal
  length and different glyphs, not the font-family string (which lists names the runner may not
  have installed).

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

# Claude Code's ground, by way of collie: collie's oklch tokens rasterized. These are the numbers
# the CSS, the two theme-color metas and the web manifest all have to agree on.
DARK_BG, DARK_TEXT = "rgb(10, 10, 10)", "rgb(250, 250, 250)"
LIGHT_BG, LIGHT_TEXT = "rgb(245, 245, 245)", "rgb(10, 10, 10)"


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


# Contrast of an element's own text against its own background, WCAG 2.1 relative luminance.
CONTRAST = """(sel => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const cs = getComputedStyle(el);
  // `rgb(10, 10, 10)` OR `color(srgb 0.04 0.04 0.04)` -- chromium serializes anything that went
  // through color-mix() or a wide-gamut space as the latter, on a 0..1 scale. Reading the two the
  // same way is what made a first pass at this measure 1.05:1 for near-black on near-white.
  const parse = v => {
    const n = v.match(/[\\d.]+/g).slice(0, 3).map(Number);
    return v.startsWith('color(') ? n.map(x => x * 255) : n;
  };
  const lum = ([r, g, b]) => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const a = lum(parse(cs.color)), b = lum(parse(cs.backgroundColor));
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
})"""


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebThemeTests(unittest.TestCase):
    """The palette and the three-way pin."""

    @classmethod
    def setUpClass(cls):
        # A dark OS, because Auto on a dark OS is what most readers of this app get.
        cls.page = _shared["browser"].new_page(viewport=PHONE, color_scheme="dark")
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        # Back to Auto on a dark OS. Every test owns its own starting point, and the pin is
        # localStorage -- it would otherwise outlive the test that set it.
        self.page.emulate_media(color_scheme="dark")
        self.page.evaluate("() => setTheme('auto')")

    def body(self):
        return self.page.evaluate("""() => {
          const cs = getComputedStyle(document.body);
          return {bg: cs.backgroundColor, text: cs.color,
                  pin: document.documentElement.dataset.theme ?? null};
        }""")

    # --- the palette ---

    def test_the_dark_ground_is_claude_codes_own(self):
        """#0a0a0a under #fafafa -- the one number the request was written in."""
        got = self.body()
        self.assertEqual((got["bg"], got["text"]), (DARK_BG, DARK_TEXT))

    def test_the_light_ground_is_a_step_off_white(self):
        """collie's --background, not #ffffff: the cards are white and rest ON this."""
        self.page.emulate_media(color_scheme="light")
        got = self.body()
        self.assertEqual((got["bg"], got["text"]), (LIGHT_BG, LIGHT_TEXT))
        self.assertEqual(
            self.page.evaluate("() => getComputedStyle(document.querySelector('.setting-group'))"
                               ".backgroundColor"), "rgb(255, 255, 255)")

    def test_the_mirror_is_dark_under_both_themes(self):
        """ANSI_COLORS is VS Code's Dark+ set and an agent's truecolor cannot be re-themed, so the
        pane keeps its own ground -- and its own `color-scheme`, or a light theme hands the dark
        output a light scrollbar."""
        for scheme in ("dark", "light"):
            self.page.emulate_media(color_scheme=scheme)
            got = self.page.evaluate("""() => {
              const cs = getComputedStyle(document.getElementById('termContent'));
              return {bg: cs.backgroundColor, color: cs.color, scheme: cs.colorScheme};
            }""")
            self.assertEqual(got["bg"], DARK_BG, scheme)
            self.assertEqual(got["color"], DARK_TEXT, scheme)
            self.assertEqual(got["scheme"], "dark", scheme)

    def test_text_on_a_saturated_fill_clears_aa_in_both_themes(self):
        """A saturated token is DARK in the light theme and LIGHT in the dark one, so the literal
        `color: #fff` these controls used to carry could only be right in one of them -- it measured
        2.6:1 on the dark theme's blue. --on-accent is the pair that works on both."""
        fills = (".term-send", ".btn-yes", ".btn-no", ".chip.active")
        self.page.evaluate("""() => {
          ws = {readyState: 1, send: () => {}};
          document.getElementById('agents').innerHTML =
            '<button class="chip active">All</button>'
            + '<button class="btn-yes">Yes</button><button class="btn-no">No</button>';
          document.getElementById('terminalView').classList.add('active');
        }""")
        for scheme in ("dark", "light"):
            self.page.emulate_media(color_scheme=scheme)
            for sel in fills:
                ratio = self.page.evaluate(CONTRAST, sel)
                self.assertIsNotNone(ratio, f"{sel} is missing")
                self.assertGreater(ratio, 4.5, f"{sel} in {scheme} mode measures {ratio:.2f}:1")
        self.page.evaluate("() => { document.getElementById('terminalView').classList.remove('active'); render(); }")

    # --- the pin ---

    def test_a_pin_beats_the_os_in_both_directions(self):
        for os_scheme, pin, expect in (("dark", "light", LIGHT_BG), ("light", "dark", DARK_BG)):
            self.page.emulate_media(color_scheme=os_scheme)
            self.page.evaluate("p => setTheme(p)", pin)
            got = self.body()
            self.assertEqual(got["bg"], expect, f"{pin} pinned on a {os_scheme} system")
            self.assertEqual(got["pin"], pin)

    def test_auto_takes_the_stale_pin_off(self):
        """The bug this exists to stop: Dark -> Auto leaving `data-theme` stamped, so the page stays
        dark on a light system until a full reload."""
        self.page.emulate_media(color_scheme="light")
        self.page.evaluate("() => setTheme('dark')")
        self.assertEqual(self.body()["bg"], DARK_BG)
        self.page.evaluate("() => setTheme('auto')")
        got = self.body()
        self.assertIsNone(got["pin"])
        self.assertEqual(got["bg"], LIGHT_BG)

    def test_a_pin_is_stamped_before_the_first_paint(self):
        """The <head> script, which is the whole reason the pin is an attribute: a reader who chose
        Dark must not be shown the system's Light and then have it swapped out from under them."""
        self.page.emulate_media(color_scheme="light")
        self.page.evaluate("() => setTheme('dark')")
        self.page.reload()
        # The class list is stamped by the time the parser reaches the body -- so a script that runs
        # on the first element of the body already sees it.
        self.assertEqual(self.page.evaluate("() => document.documentElement.dataset.theme"), "dark")
        self.assertEqual(self.body()["bg"], DARK_BG)
        self.page.evaluate("() => setTheme('auto')")
        self.page.reload()
        self.assertIsNone(self.page.evaluate("() => document.documentElement.dataset.theme ?? null"))

    def test_the_control_is_one_exclusive_choice(self):
        """Three buttons in a radiogroup, not three toggles: `aria-pressed` on each would announce
        three independent switches, one of which happens to be on."""
        self.assertEqual(
            self.page.evaluate("() => document.querySelector('.theme-seg').getAttribute('role')"),
            "radiogroup")
        for pref in ("light", "dark", "auto"):
            self.page.evaluate("p => setTheme(p)", pref)
            checked = self.page.evaluate("""() => [...document.querySelectorAll('[data-theme-choice]')]
              .filter(b => b.getAttribute('aria-checked') === 'true')
              .map(b => b.dataset.themeChoice)""")
            self.assertEqual(checked, [pref])

    def test_the_metas_follow_the_pin_and_the_manifest_agrees(self):
        """Both metas carry `media`, so on Auto each keeps its own half and the browser's query
        picks; pinned, both have to say the pinned colour or Android's URL bar renders the other
        theme above the page. The manifest is a data: URL and cannot be re-themed, so it holds the
        dark ground -- what the app looks like out of the box."""
        self.page.evaluate("() => setTheme('auto')")
        self.assertEqual(self.page.evaluate(
            "() => [...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content)"),
            ["#f5f5f5", "#0a0a0a"])
        self.page.evaluate("() => setTheme('light')")
        self.assertEqual(self.page.evaluate(
            "() => [...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content)"),
            ["#f5f5f5", "#f5f5f5"])
        self.page.evaluate("() => setTheme('dark')")
        self.assertEqual(self.page.evaluate(
            "() => [...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content)"),
            ["#0a0a0a", "#0a0a0a"])
        manifest = self.page.evaluate("() => document.querySelector('link[rel=manifest]').href")
        self.assertIn("%230a0a0a", manifest)

    # --- the font ---

    def test_the_ui_is_monospace_by_advance_width(self):
        """Measured, not read: the stack names fonts the runner may not have, and what matters is
        that the glyphs are the same width -- this app is a window onto a terminal."""
        widths = self.page.evaluate("""() => {
          const probe = document.createElement('span');
          probe.style.cssText = 'position:fixed;visibility:hidden;white-space:pre';
          document.body.appendChild(probe);
          const measure = s => { probe.textContent = s; return probe.getBoundingClientRect().width; };
          const got = {narrow: measure('iiiiiiii'), wide: measure('WWWWWWWW'),
                       font: getComputedStyle(probe).fontFamily};
          probe.remove();
          return got;
        }""")
        self.assertAlmostEqual(widths["narrow"], widths["wide"], delta=0.5)
        self.assertGreater(widths["narrow"], 0)

    def test_the_terminal_keeps_the_bundled_nerd_font_and_the_ui_does_not(self):
        """982KB of woff2. Nothing fetches it until a session opens, because a `display: none`
        element loads no font -- putting it in the UI stack would move that download in front of
        every cold load's first paint."""
        stacks = self.page.evaluate("""() => ({
          body: getComputedStyle(document.body).fontFamily,
          term: getComputedStyle(document.getElementById('termContent')).fontFamily,
        })""")
        self.assertNotIn("Hack Nerd Font", stacks["body"])
        self.assertTrue(stacks["term"].startswith('"Hack Nerd Font"'), stacks["term"])
        # And the terminal falls back to the same stack the UI runs on, so one edit moves both.
        self.assertIn("ui-monospace", stacks["term"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
