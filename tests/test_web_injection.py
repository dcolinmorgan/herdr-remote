"""Agent-supplied text never becomes code in the page that holds the relay token.

The ids and names on these rows come from the agent side: `pane_id`, `workspace_id` and `tab_id`
are copied from `herdr pane list`, and `project` is `basename(cwd)` -- so a cloned repository or an
unpacked archive picks it. The page keeps the relay token in `localStorage`, and a token is the
relay's whole control surface (`send_text`, `send_keys` into every pane), so execution here is
remote agent control, not a defaced list.

Two shapes are pinned, the two that were exploitable:

* A quote that breaks out of a JS string. The row handlers used to be written into the markup as
  `onclick="openTerminal('<id>')"`, and HTML-escaping the quote does not save that: the browser
  decodes the attribute BEFORE it parses the handler, so `&#39;` is a live quote by then. The
  payload therefore has to be a breakout, not a tag -- a test that asserts `<img` is absent passes
  happily against a working exfiltration payload that contains no `<`.
* A tag that needs no click, which is what an unescaped field in ordinary HTML gives.

The handlers moved out of the markup and are wired from the dataset after render, so the activation
those rows owe a thumb and a keyboard is pinned here too: that rewiring is the thing that could
silently stop working.

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

TOKEN = "SECRET-TOKEN"

# Closes the string an id used to land in, then reads the token. No `<` anywhere: this is the
# payload an "is there a tag in the markup" assertion cannot see.
BREAKOUT = "x');window.__ran__=localStorage.getItem('herdr_relay_token');//"

# The other half: ordinary HTML, no click needed, fires as the row is drawn.
TAG = "<img src=x onerror=\"window.__ran__=localStorage.getItem('herdr_relay_token')\">"


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
            "cwd": "/work/api", "project": "api", "host": "local", "remote": None,
            "workspace_id": "wA", "tab_id": "wA:t1", "title": "", "focused": False,
            "scrollback": 0, "viewport_rows": 40, "has_session": True,
            "last_active_at": T0, "last_seen_at": T0, **extra}


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebInjectionTests(unittest.TestCase):
    """Hostile ids and names from the agent side, through the real message path."""

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        # A token to steal, and a socket the page believes in -- `ws.send` is what a row's handler
        # reaches for, and a null socket would make a click fail for the wrong reason. The panels
        # are put away too: an open Timeline or session view hides the very list being tapped.
        self.page.evaluate("""token => {
          localStorage.setItem('herdr_relay_token', token);
          delete window.__ran__;
          ws = {readyState: 1, send: () => {}};
          hidePanel(); hideTerminal();
          activeWorkspace = null; activeTab = null;
          timeline.length = 0;
        }""", TOKEN)

    def feed(self, snapshot):
        self.page.evaluate("s => handleMessage(s)", snapshot)

    def ran(self):
        """What a payload sets if it executed: the token it read out of localStorage."""
        return self.page.evaluate("() => window.__ran__ ?? null")

    def test_an_id_that_closes_a_quote_does_not_run_when_the_row_is_tapped(self):
        self.feed({"type": "agents", "agents": [_agent(BREAKOUT)], "panes": []})
        row = self.page.locator(f'[data-pane-id="{BREAKOUT}"]')
        self.assertEqual(row.count(), 1, "the row should still render, with the id as data")
        # The id carries no handler of its own to run.
        self.assertIsNone(row.get_attribute("onclick"))
        row.click()
        self.assertIsNone(self.ran(), "the payload in pane_id executed on click")

    def test_an_id_that_closes_a_quote_does_not_run_from_the_keyboard(self):
        self.feed({"type": "agents", "agents": [_agent(BREAKOUT)], "panes": []})
        row = self.page.locator(f'[data-pane-id="{BREAKOUT}"]')
        self.assertIsNone(row.get_attribute("onkeydown"))
        row.focus()
        self.page.keyboard.press("Enter")
        self.assertIsNone(self.ran(), "the payload in pane_id executed on Enter")

    def test_a_tag_in_a_project_name_does_not_run_when_the_timeline_draws_it(self):
        """`project` is basename(cwd), so the directory an agent was started in picks it. The
        timeline row needs no tap: it runs as the panel is drawn."""
        self.feed({"type": "agents", "agents": [_agent("w0:p1", project=TAG)], "panes": []})
        # A timeline row is a status TRANSITION, so the status has to move.
        self.feed({"type": "agents",
                   "agents": [_agent("w0:p1", project=TAG, status="blocked")], "panes": []})
        self.page.evaluate("() => toggleTimeline()")
        self.page.wait_for_timeout(300)
        self.assertIsNone(self.ran(), "the payload in project executed as the timeline drew")
        # And it is still readable as what it is, rather than silently dropped.
        self.assertIn(TAG, self.page.inner_text("#timelineList"))

    def test_a_tag_in_a_space_name_does_not_run_when_the_chip_strip_draws_it(self):
        self.feed({
            "type": "agents",
            "agents": [_agent("wA:pH"), _agent("wB:pH", workspace_id="wB", tab_id="wB:t1")],
            "panes": [],
            "spaces": {
                "workspaces": [
                    {"workspace_id": "wA", "label": TAG, "number": 1, "focused": False,
                     "tab_count": 1, "pane_count": 1, "host": "local"},
                    {"workspace_id": "wB", "label": "billing", "number": 2, "focused": False,
                     "tab_count": 1, "pane_count": 1, "host": "local"},
                ],
                "tabs": [
                    {"tab_id": "wA:t1", "workspace_id": "wA", "label": TAG, "number": 1,
                     "focused": False, "pane_count": 1, "host": "local"},
                ],
            },
        })
        self.page.wait_for_timeout(100)
        self.assertIsNone(self.ran(), "the payload in a workspace label executed on render")
        self.assertEqual(self.page.locator("#agents img").count(), 0)

    def test_a_space_key_that_closes_a_quote_does_not_run_when_the_chip_is_tapped(self):
        """The chip's key is `host|workspace_id`, and workspace_id is the agent side's too."""
        self.feed({
            "type": "agents",
            "agents": [_agent("wA:pH", workspace_id=BREAKOUT, tab_id="wA:t1"),
                       _agent("wB:pH", workspace_id="wB", tab_id="wB:t1")],
            "panes": [],
        })
        chip = self.page.locator(f'[data-ws-key="local|{BREAKOUT}"]')
        self.assertEqual(chip.count(), 1)
        self.assertIsNone(chip.get_attribute("onclick"))
        chip.click()
        self.assertIsNone(self.ran(), "the payload in workspace_id executed on click")

    def test_an_ordinary_row_still_opens_its_pane_by_tap_and_by_keyboard(self):
        """The handlers these rows carry moved out of the markup, so this is the behaviour that
        pays for the fix: a thumb and a keyboard both still land on the pane."""
        self.feed({"type": "agents", "agents": [_agent("w0:p1"), _agent("w0:p2")], "panes": []})
        self.page.locator('[data-pane-id="w0:p1"]').click()
        self.assertEqual(self.page.evaluate("() => activePane"), "w0:p1")

        self.page.evaluate("() => closeTerminal()")
        self.assertIsNone(self.page.evaluate("() => activePane"))

        self.page.locator('[data-pane-id="w0:p2"]').focus()
        self.page.keyboard.press("Enter")
        self.assertEqual(self.page.evaluate("() => activePane"), "w0:p2")

    def test_an_ordinary_space_chip_still_switches_the_view(self):
        self.feed({
            "type": "agents",
            "agents": [_agent("wA:pH"), _agent("wB:pH", workspace_id="wB", tab_id="wB:t1")],
            "panes": [],
        })
        self.page.locator('[data-ws-key="local|wB"]').click()
        self.assertEqual(self.page.evaluate("() => activeWorkspace"), "local|wB")
