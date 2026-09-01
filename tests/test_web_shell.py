"""Tests for how web/index.html renders and drives the panes with no agent in them.

Two thirds of the panes on a real herdr host are these, so the questions worth measuring are
whether they are visibly NOT agents, whether the space filters reach them, and whether opening one
offers the things that only make sense for a terminal (scrollback) and hides the ones that don't
(a transcript). All of it is asserted against the rendered DOM rather than the source, because
every one of those is a claim about what a thumb finds on the screen.

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


def _agent(pane_id, workspace, tab, status="idle", **extra):
    return {"pane_id": pane_id, "agent": "claude", "label": "", "status": status,
            "cwd": "/work/api", "project": "api", "host": "local", "remote": None,
            "workspace_id": workspace, "tab_id": tab, "title": "", "focused": False,
            "scrollback": 0, "viewport_rows": 40, "has_session": True, **extra}


def _shell(pane_id, workspace, tab, **extra):
    return {"pane_id": pane_id, "label": "", "cwd": "/work/api", "project": "api",
            "host": "local", "remote": None, "workspace_id": workspace, "tab_id": tab,
            "focused": False, "scrollback": 693, "viewport_rows": 68, **extra}


# One agent and three terminals over two workspaces: enough for the chip strip to come up, for a
# tab that holds only a terminal, and for a remote one.
SNAPSHOT = {
    "type": "agents",
    "agents": [_agent("wE:pH", "wE", "wE:t1", status="working")],
    "spaces": {
        "workspaces": [
            {"workspace_id": "wE", "label": "api", "number": 1, "focused": True,
             "tab_count": 2, "pane_count": 4, "host": "local"},
            {"workspace_id": "w6", "label": "kv", "number": 2, "focused": False,
             "tab_count": 1, "pane_count": 1, "host": "local"},
        ],
        "tabs": [
            {"tab_id": "wE:t1", "workspace_id": "wE", "label": "1", "number": 1,
             "focused": True, "pane_count": 2, "host": "local"},
            {"tab_id": "wE:t2", "workspace_id": "wE", "label": "logs", "number": 2,
             "focused": False, "pane_count": 1, "host": "local"},
            {"tab_id": "w6:t1", "workspace_id": "w6", "label": "1", "number": 1,
             "focused": False, "pane_count": 1, "host": "local"},
        ],
    },
    "panes": [
        _shell("wE:p2", "wE", "wE:t1", focused=True),
        _shell("wE:p5", "wE", "wE:t2", scrollback=0),
        _shell("w6:p3", "w6", "w6:t1", host="gpu-box", remote="gpu-box", project="kv"),
    ],
}


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebShellPaneListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        self.page.evaluate("s => { activeWorkspace = null; activeTab = null; handleMessage(s); }",
                           SNAPSHOT)

    def cards(self):
        return self.page.eval_on_selector_all(
            "#agents .agent",
            "els => els.map(e => ({id: e.dataset.paneId, shell: e.dataset.shell === '1'}))")

    def headers(self):
        return self.page.eval_on_selector_all(
            "#agents .section-header", "els => els.map(e => e.innerText.split('\\n')[0])")

    def sequence(self):
        """The list in document order, headings included."""
        return self.page.eval_on_selector_all("#agents > *", """els => els.map(e =>
          e.classList.contains('section-header')
            ? {kind: 'head', name: e.querySelector('.sec-label').textContent}
            : e.classList.contains('tab-heading')
              ? {kind: 'tab', name: e.innerText.split('\\n')[0].replace(/\\s*\\(\\d+\\)$/, '')}
              : e.classList.contains('agent')
                ? {kind: e.dataset.shell === '1' ? 'shell' : 'agent', id: e.dataset.paneId}
                : {kind: e.classList.contains('chip-strip') ? 'chips' : 'other'})""")

    def in_space(self, key):
        """Every pane the space view draws for one space, in order."""
        self.page.evaluate(f"selectWorkspace('{key}')")
        return [n["id"] for n in self.sequence() if n["kind"] in ("agent", "shell")]

    def test_a_terminal_lives_under_its_tab_in_the_space_view(self):
        """Not in the herd, which is agents only: two thirds of the panes on a real host are these,
        and they have no status to triage. The space view is where "what is in this tab" is the
        question being asked, so it shows both kinds together."""
        self.page.evaluate("selectWorkspace('local|wE')")
        self.assertEqual(
            [(n.get("name") or n.get("id")) for n in self.sequence()
             if n["kind"] in ("tab", "agent", "shell")],
            ["Tab 1", "wE:pH", "wE:p2", "logs", "wE:p5"])

    def test_no_terminal_is_stranded(self):
        """Including w6:p3, whose host disagrees with its space's. The herd cannot show it, so every
        one of them has to be reachable by picking a space -- and w6's chip is what offers it."""
        reachable = set(self.in_space("local|wE")) | set(self.in_space("gpu-box|w6"))
        self.assertLessEqual({p["pane_id"] for p in SNAPSHOT["panes"]}, reachable)

    def test_a_terminal_is_never_drawn_as_an_agent(self):
        """The whole reason the relay ships them in a separate array. Not an ordering claim: a group
        is ordered by tab, so a terminal in an earlier tab legitimately precedes an agent in a later
        one. What must hold is that no `panes` entry is ever rendered by agentCard -- it would show
        up with an empty harness name and a fourth shade of status grey."""
        drawn = {}
        for key in ("local|wE", "gpu-box|w6"):
            self.page.evaluate(f"selectWorkspace('{key}')")
            drawn.update({n["id"]: n["kind"] for n in self.sequence()
                          if n["kind"] in ("agent", "shell")})
        for pane in SNAPSHOT["panes"]:
            self.assertEqual(drawn.get(pane["pane_id"]), "shell", pane["pane_id"])
        for agent in SNAPSHOT["agents"]:
            self.assertEqual(drawn.get(agent["pane_id"]), "agent", agent["pane_id"])

    def test_a_terminal_is_visibly_not_an_agent(self):
        """A status dot it does not have would be a fifth shade competing with the four triage
        buckets; hollow is not a shade."""
        self.page.evaluate("selectWorkspace('local|wE')")
        filled, hollow = self.page.evaluate("""() => {
          const dot = sel => {
            const s = getComputedStyle(document.querySelector(sel + ' .dot'));
            return [s.backgroundColor, s.borderStyle, s.borderWidth];
          };
          return [dot('[data-pane-id="wE:pH"]'), dot('[data-pane-id="wE:p2"]')];
        }""")
        self.assertEqual(filled[1], "none")
        self.assertNotEqual(hollow[1], "none")
        self.assertIn(hollow[0], ("rgba(0, 0, 0, 0)", "transparent"))

    def test_the_space_filters_reach_them(self):
        self.page.evaluate("selectWorkspace('local|wE')")
        self.assertEqual([c["id"] for c in self.cards()], ["wE:pH", "wE:p2", "wE:p5"])
        self.page.evaluate("selectTab('local|wE:t2')")
        self.assertEqual([c["id"] for c in self.cards()], ["wE:p5"])

    def test_a_tab_holding_only_a_terminal_shows_it_rather_than_apologising(self):
        """`emptySpaceNotice` used to be the only thing such a tab could render."""
        self.page.evaluate("selectWorkspace('local|wE'); selectTab('local|wE:t2')")
        self.assertEqual([c["id"] for c in self.cards()], ["wE:p5"])
        self.assertEqual(
            self.page.eval_on_selector_all("#agents .empty", "els => els.map(e => e.innerText)"),
            [])

    def test_the_id_is_on_the_card_because_the_directory_is_not_unique(self):
        """Measured on a real host: 20 shell panes, 12 distinct cwd basenames -- and only 12 within
        their own workspaces either. Which line it lands on depends on whether a heading has
        already said the project (paneLabel); that it is on the card at all does not."""
        for view in ("selectWorkspace('local|wE')", "selectWorkspace('local|wE'); selectTab('local|wE:t1')"):
            with self.subTest(view=view):
                self.page.evaluate(f"() => {{ {view}; render(); }}")
                card = self.page.eval_on_selector('[data-pane-id="wE:p2"]', "e => e.innerText")
                self.assertIn("wE:p2", card)
                self.assertEqual(card.count("wE:p2"), 1, "the id was printed twice")

    def test_a_remote_terminal_says_which_host(self):
        self.page.evaluate("selectWorkspace('gpu-box|w6')")
        text = self.page.eval_on_selector('[data-pane-id="w6:p3"]', "e => e.innerText")
        self.assertIn("@gpu-box", text)

    def test_a_relay_that_sends_no_panes_renders_exactly_as_before(self):
        """HERDR_SHELL_PANES off, or a relay older than it: the key is simply absent."""
        without = {k: v for k, v in SNAPSHOT.items() if k != "panes"}
        self.page.evaluate("s => { shellPanes = []; activeWorkspace = null; activeTab = null;"
                           " handleMessage(s); }", without)
        self.assertEqual([c["id"] for c in self.cards()], ["wE:pH"])
        self.assertNotIn("TERMINALS", self.headers())


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebShellPaneSessionTests(unittest.TestCase):
    """Opening one. A terminal offers scrollback and no transcript; an agent pane is the reverse."""

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
          window.__sent = [];
          paneProcess = {};
          ws = {readyState: 1, send: p => window.__sent.push(JSON.parse(p))};
        }""", SNAPSHOT)

    def sent(self):
        return self.page.evaluate("window.__sent")

    def test_opening_one_hides_the_history_button(self):
        """A terminal has no transcript -- the relay would answer no-session either way."""
        self.page.evaluate("openTerminal('wE:p2')")
        self.assertEqual(
            self.page.eval_on_selector(".history-btn", "e => getComputedStyle(e).display"), "none")
        self.page.evaluate("openTerminal('wE:pH')")
        self.assertNotEqual(
            self.page.eval_on_selector(".history-btn", "e => getComputedStyle(e).display"), "none")

    def test_scrollback_is_offered_where_there_is_a_ring_and_not_where_there_is_none(self):
        """`canLoadMore` asks what a read could return, which is the one thing scrollback says.

        It is NOT a way to tell an agent pane from a terminal -- the fixture's agent reports 0
        because a pane on the alternate screen can, not because agent panes must. Nor is the answer
        stable per pane on herdr 0.8.2: one measurement on this host had 9 of 10 agent panes
        reporting a ring, a later one had all 9 reporting 0."""
        self.page.evaluate("openTerminal('wE:p2')")
        self.assertEqual(self.page.evaluate("paneScrollback()"), 693)
        self.assertTrue(self.page.evaluate("canLoadMore()"))
        self.page.evaluate("openTerminal('wE:pH')")
        self.assertFalse(self.page.evaluate("canLoadMore()"))

    def test_the_follow_read_carries_scrollback_whatever_ring_the_pane_has(self):
        """Which source is a question about THIS read, not about the pane -- and the answer is
        always `recent`, because `visible` is the rendered grid and nothing else.

        Priming one `recent` read on open would not do: mirrorPatch reconciles the whole buffer, so
        the next tick deletes every line the viewport no longer holds. Nor did the per-pane rule
        this replaces pay for itself -- measured on this host (herdr 0.8.2, all 35 live panes,
        ansi), every agent pane reports no ring, so `recent` 200 and `visible` are byte-identical
        there, and where they differ the extra bytes are the scrollback the reader came for."""
        for pane, ring in (("wE:pH", 1662), ("wE:pH", 0), ("wE:p2", 693), ("wE:p5", 0)):
            with self.subTest(pane=pane, ring=ring):
                self.page.evaluate("""([p, r]) => {
                  paneById(p).scrollback = r;
                  openTerminal(p); window.__sent = []; refreshPane();
                }""", [pane, ring])
                read = self.sent()[0]
                self.assertEqual(read["source"], "recent")
                # The window, not the pane's height: one screenful is what hid the history.
                self.assertEqual(read["lines"], self.page.evaluate("PANE_LINES_BASE"))

    def test_paging_back_asks_for_recent_and_stops_the_tick(self):
        """History does not change under you, and it is not re-fetched on a timer either.

        The tick used to reuse whatever paneLines had grown to: 125.7KB per pass at the 1000-line
        ceiling, 42KB/s, for output the reader had already scrolled away from."""
        self.page.evaluate("openTerminal('wE:p2'); window.__sent = []; loadMore()")
        read = self.sent()[0]
        self.assertEqual(read["source"], "recent")
        self.assertEqual(read["lines"], 600)

        self.page.evaluate("window.__sent = []; mirrorTick()")
        self.assertEqual(self.sent(), [], "the tick replaced the page the reader had paged back to")

    def test_refresh_is_the_way_back_to_the_live_screen(self):
        """A bare re-read would fetch the same page of history again -- a button that does nothing.
        It is also the only way back, since the tick is stopped while a page is held."""
        self.page.evaluate("openTerminal('wE:p2'); loadMore(); window.__sent = []; followPane()")
        # Both reads are `recent`; what tells a held page from a live one is the window it asks for.
        self.assertEqual(self.sent()[0]["lines"], 200)
        self.assertEqual(self.page.evaluate("paneLines"), 200)

        self.page.evaluate("window.__sent = []; mirrorTick()")
        self.assertEqual(self.sent()[0]["lines"], 200, "the tick did not resume")

    def test_the_process_is_asked_for_once_per_terminal_and_never_for_an_agent(self):
        """One extra CLI call on the relay, which is one SSH round trip for a remote host."""
        self.page.evaluate("openTerminal('wE:p2')")
        first = [m for m in self.sent() if m["type"] == "read_pane"]
        self.assertTrue(first[0].get("process"), "the opening read did not ask for the process")

        self.page.evaluate("""() => {
          window.__sent = [];
          handleMessage({type: 'pane_content', pane_id: 'wE:p2', content: 'x',
                         process: {name: 'vim', cmdline: 'vim relay/herdr_relay.py'}});
          refreshPane();
        }""")
        self.assertFalse(self.sent()[0].get("process"),
                         "the mirror tick asked for the process again")

        self.page.evaluate("openTerminal('wE:pH'); window.__sent = []; refreshPane()")
        self.assertFalse(self.sent()[0].get("process"),
                         "an agent pane was charged for a process lookup")

    def test_the_process_replaces_the_id_in_the_title_once_it_arrives(self):
        self.page.evaluate("openTerminal('wE:p2')")
        self.assertIn("wE:p2", self.page.eval_on_selector("#termTitle", "e => e.textContent"))
        self.page.evaluate("""() => handleMessage({type: 'pane_content', pane_id: 'wE:p2',
          content: 'x', process: {name: 'vim', cmdline: 'vim x'}})""")
        self.assertIn("vim", self.page.eval_on_selector("#termTitle", "e => e.textContent"))

    def test_typing_into_a_terminal_is_one_respond_not_send_text_plus_enter(self):
        """The relay runs both halves and audits it as respond_shell -- the line that says a
        command was run rather than text typed at an agent."""
        self.page.evaluate("""() => {
          openTerminal('wE:p2');
          window.__sent = [];
          document.getElementById('termInput').value = 'ls -la';
          sendText();
        }""")
        self.assertEqual([m["type"] for m in self.sent() if m["type"] != "read_pane"],
                         ["respond"])
        self.assertEqual(self.sent()[0]["text"], "ls -la")

    def test_typing_into_an_idle_agent_pane_is_unchanged(self):
        """The terminal branch must not have swallowed the agent one."""
        self.page.evaluate("""() => {
          openTerminal('wE:pH');
          window.__sent = [];
          document.getElementById('termInput').value = 'hello';
          sendText();
        }""")
        self.assertEqual([m["type"] for m in self.sent() if m["type"] != "read_pane"],
                         ["send_text", "send_keys"])


if __name__ == "__main__":
    unittest.main()
