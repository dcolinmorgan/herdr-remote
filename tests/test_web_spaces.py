"""Tests for the two things that make the workspace the unit of the web app rather than a filter.

The unfiltered list groups by workspace, agents and terminals together, because that is what herdr
itself groups by -- `workspace list` reports a `worktree` block for a git one -- and because status
grouping put a real host's wT agent and wT build terminal twenty rows apart. The session view then
carries a strip of the open pane's neighbours, split into the ones sharing its tab and the rest of
its workspace: measured on that host, at most three tabmates and at most five panes in a space, so
a strip is the whole control.

Both are claims about what a thumb finds on the screen, so all of it is asserted against the
rendered DOM and, where it is geometry, against measured boxes rather than the CSS.

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


def _agent(pane_id, workspace, tab, status="idle", **extra):
    return {"pane_id": pane_id, "agent": "claude", "label": "", "status": status,
            "cwd": "/work/api", "project": "api", "host": "local", "remote": None,
            "workspace_id": workspace, "tab_id": tab, "title": "", "focused": False,
            "scrollback": 0, "viewport_rows": 40, "has_session": True, **extra}


def _shell(pane_id, workspace, tab, **extra):
    return {"pane_id": pane_id, "label": "", "cwd": "/work/api", "project": "api",
            "host": "local", "remote": None, "workspace_id": workspace, "tab_id": tab,
            "focused": False, "scrollback": 693, "viewport_rows": 68, **extra}


# Four spaces' worth of shapes, one of each that matters: a space whose agent is asking (sorts
# first), a space herdr is pointed at with a tabmate terminal AND one a tab away, a space holding
# nothing but terminals, and an agent in a space `workspace list` never reported.
SNAPSHOT = {
    "type": "agents",
    "agents": [
        # No operator label: its chip has to fall back to the pane id, because `project` is the
        # same string for every pane in a workspace and names none of them.
        _agent("wA:pH", "wA", "wA:t1", status="working"),
        _agent("wB:pH", "wB", "wB:t1", status="blocked", label="billing", project="billing",
               options=["Yes", "No"]),
        _agent("wD:pH", "wD", "wD:t1", label="orphan", project="orphan"),
    ],
    "spaces": {
        "workspaces": [
            {"workspace_id": "wA", "label": "api", "number": 1, "focused": True,
             "tab_count": 2, "pane_count": 3, "host": "local"},
            {"workspace_id": "wB", "label": "billing", "number": 2, "focused": False,
             "tab_count": 1, "pane_count": 2, "host": "local"},
            {"workspace_id": "wC", "label": "logs", "number": 3, "focused": False,
             "tab_count": 1, "pane_count": 2, "host": "local"},
        ],
        "tabs": [
            {"tab_id": "wA:t1", "workspace_id": "wA", "label": "1", "number": 1,
             "focused": True, "pane_count": 2, "host": "local"},
            {"tab_id": "wA:t2", "workspace_id": "wA", "label": "deploy", "number": 2,
             "focused": False, "pane_count": 1, "host": "local"},
            {"tab_id": "wB:t1", "workspace_id": "wB", "label": "1", "number": 1,
             "focused": False, "pane_count": 2, "host": "local"},
            {"tab_id": "wC:t1", "workspace_id": "wC", "label": "1", "number": 1,
             "focused": False, "pane_count": 2, "host": "local"},
        ],
    },
    "panes": [
        _shell("wA:p2", "wA", "wA:t1"),
        _shell("wA:p3", "wA", "wA:t2"),
        _shell("wB:p2", "wB", "wB:t1", project="billing"),
        _shell("wC:p1", "wC", "wC:t1", project="logs"),
        _shell("wC:p2", "wC", "wC:t1", project="logs"),
    ],
}

ALL_PANES = ([a["pane_id"] for a in SNAPSHOT["agents"]]
             + [p["pane_id"] for p in SNAPSHOT["panes"]])


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebSpaceGroupTests(unittest.TestCase):
    """The unfiltered list: one group per workspace, the asking one first."""

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

    def sequence(self):
        return self.page.eval_on_selector_all("#agents > *", """els => els.map(e =>
          e.classList.contains('space-header')
            ? {kind: 'space', name: e.querySelector('.space-name').textContent,
               count: e.querySelector('.space-count').textContent,
               alert: e.classList.contains('alert'), focused: e.classList.contains('focused')}
            : e.classList.contains('section-header')
              ? {kind: 'head', name: e.innerText.split('\\n')[0]}
              : e.classList.contains('agent')
                ? {kind: e.dataset.shell === '1' ? 'shell' : 'agent', id: e.dataset.paneId}
                : {kind: e.classList.contains('chip-strip') ? 'chips' : 'other'})""")

    def groups(self):
        """[(space name, [pane ids in order]), ...]"""
        out = []
        for node in self.sequence():
            if node["kind"] == "space":
                out.append((node["name"], []))
            elif node["kind"] in ("agent", "shell"):
                out[-1][1].append(node["id"])
        return out

    def test_the_list_is_one_group_per_workspace(self):
        self.assertEqual(self.groups(), [
            ("billing", ["wB:pH", "wB:p2"]),
            ("api", ["wA:pH", "wA:p2", "wA:p3"]),
            ("logs", ["wC:p1", "wC:p2"]),
            ("orphan", ["wD:pH"]),
        ])

    def test_the_space_that_is_asking_comes_first(self):
        """And is the reason this view needs no `Needs you` hoist: a hoist plus a group would
        render the blocked card twice, and the first group cannot bury it."""
        first = next(n for n in self.sequence() if n["kind"] == "space")
        self.assertEqual(first["name"], "billing")
        self.assertTrue(first["alert"])
        self.assertNotIn("NEEDS YOU", [n.get("name", "") for n in self.sequence()])

    def test_every_pane_is_rendered_exactly_once(self):
        ids = [n["id"] for n in self.sequence() if n["kind"] in ("agent", "shell")]
        self.assertEqual(sorted(ids), sorted(ALL_PANES))

    def test_a_space_the_hierarchy_never_reported_still_gets_a_group(self):
        """`workspace list` knows nothing about wD. Every other view shows the pane anyway, so the
        one view that groups does not get to lose it."""
        self.assertIn("orphan", [name for name, _ in self.groups()])

    def test_a_space_holding_only_terminals_is_no_longer_just_a_chip(self):
        """Three of the ten workspaces on the host this was measured on hold no agent at all."""
        logs = dict(self.groups())["logs"]
        self.assertEqual(logs, ["wC:p1", "wC:p2"])

    def test_the_header_counts_what_is_under_it(self):
        counts = {n["name"]: n["count"] for n in self.sequence() if n["kind"] == "space"}
        self.assertEqual(counts["api"], "1 agent · 2 terminals")
        self.assertEqual(counts["logs"], "2 terminals")
        self.assertEqual(counts["orphan"], "1 agent")

    def test_the_header_marks_where_herdr_is_standing(self):
        marked = [n["name"] for n in self.sequence() if n["kind"] == "space" and n["focused"]]
        self.assertEqual(marked, ["api"])

    def test_tabmates_are_adjacent_and_the_agent_leads_its_tab(self):
        """wA:p3 is a tab away; it sorts after the t1 pair rather than between them."""
        self.assertEqual(dict(self.groups())["api"], ["wA:pH", "wA:p2", "wA:p3"])

    def test_status_headings_are_gone_from_the_unfiltered_list(self):
        """Two axes, and the useful one is whichever the chips have not established."""
        self.assertEqual([n["name"] for n in self.sequence() if n["kind"] == "head"], [])

    def test_drilling_in_restores_the_status_axis(self):
        self.page.evaluate("selectWorkspace('local|wA')")
        heads = [n["name"] for n in self.sequence() if n["kind"] == "head"]
        self.assertEqual(heads, ["WORKING", "TERMINALS"])
        self.assertEqual([n["name"] for n in self.sequence() if n["kind"] == "space"], [])

    def test_the_hoist_inside_a_space_is_about_that_space(self):
        """It used to be unfiltered, so drilling into `api` put billing's blocked agent on top."""
        self.page.evaluate("selectWorkspace('local|wA')")
        self.assertEqual([n["name"] for n in self.sequence() if n["kind"] == "head"],
                         ["WORKING", "TERMINALS"])
        self.page.evaluate("selectWorkspace('local|wB')")
        seq = self.sequence()
        self.assertEqual(seq[0]["name"], "NEEDS YOU")
        self.assertEqual([n["id"] for n in seq if n["kind"] == "agent"], ["wB:pH"])

    def test_the_header_drills_in_the_way_its_chip_does(self):
        self.page.evaluate("""() => document.querySelectorAll('.space-header')[1].click()""")
        self.assertEqual(self.page.evaluate("activeWorkspace"), "local|wA")

    def test_the_header_is_the_chips_twin_for_long_press(self):
        """Same data attributes, so `Focus in herdr` reaches a space from either one."""
        pairs = self.page.eval_on_selector_all(
            ".space-header", "els => els.map(e => [e.dataset.wsKey, e.dataset.wsName])")
        self.assertIn(["local|wA", "api"], pairs)

    def test_a_relay_with_no_hierarchy_and_one_space_still_gets_the_flat_list(self):
        flat = {"type": "agents", "agents": [_agent("wA:pH", "wA", "wA:t1")], "panes": []}
        self.page.evaluate("s => { activeWorkspace = null; shellPanes = []; handleMessage(s); }",
                           flat)
        self.assertEqual([n["name"] for n in self.sequence() if n["kind"] == "space"], [])
        self.assertEqual([n["name"] for n in self.sequence() if n["kind"] == "head"], ["IDLE"])


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebSiblingStripTests(unittest.TestCase):
    """The session view's strip of neighbours."""

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

    def strip(self):
        """The strip's contents in order: ('label', text) or ('chip', id, is_shell)."""
        return self.page.eval_on_selector_all("#termSiblings > *", """els => els.map(e =>
          e.classList.contains('sib-label')
            ? ['label', e.textContent]
            : ['chip', e.dataset.sibId, e.dataset.sibShell === '1'])""")

    def visible(self):
        return self.page.eval_on_selector("#termSiblings", "e => e.offsetHeight") > 0

    def test_an_agents_neighbours_are_split_by_tab(self):
        """The distinction herdr itself draws: a tabmate is on the operator's screen beside this
        pane, anything else in the space is a tab away."""
        self.page.evaluate("openTerminal('wA:pH')")
        self.assertEqual(self.strip(), [
            ["label", "Tab"], ["chip", "wA:p2", True],
            ["label", "Space"], ["chip", "wA:p3", True],
        ])

    def test_the_second_label_is_absent_when_every_neighbour_is_a_tabmate(self):
        self.page.evaluate("openTerminal('wB:pH')")
        self.assertEqual(self.strip(), [["label", "Tab"], ["chip", "wB:p2", True]])

    def test_a_pane_with_no_neighbours_costs_no_pixels(self):
        """Three of the ten agent panes on the measured host have no terminal beside them."""
        self.page.evaluate("openTerminal('wD:pH')")
        self.assertEqual(self.strip(), [])
        self.assertFalse(self.visible())

    def test_from_a_terminal_the_agent_is_one_tap_back(self):
        """Same rule read the other way -- the strip is every other pane in the workspace."""
        self.page.evaluate("openTerminal('wA:p2')")
        self.assertEqual(self.strip(), [
            ["label", "Tab"], ["chip", "wA:pH", False],
            ["label", "Space"], ["chip", "wA:p3", True],
        ])

    def test_a_chip_carries_the_same_mark_its_card_does(self):
        """Hollow for a terminal, the agent's own status colour otherwise -- so the strip needs no
        legend of its own, and the two places cannot come to disagree about a pane."""
        self.page.evaluate("openTerminal('wA:p2')")
        card, chip, shell = self.page.evaluate("""() => {
          const g = sel => { const c = getComputedStyle(document.querySelector(sel));
                             return [c.backgroundColor, c.borderStyle]; };
          return [g('#agents [data-pane-id="wA:pH"] .dot'),
                  g('#termSiblings [data-sib-id="wA:pH"] .dot'),
                  g('#termSiblings [data-sib-id="wA:p3"] .dot')];
        }""")
        self.assertEqual(chip[0], card[0], "the chip and the card disagree about a working agent")
        self.assertEqual(chip[1], "none")
        self.assertNotEqual(shell[1], "none")
        self.assertIn(shell[0], ("rgba(0, 0, 0, 0)", "transparent"))

    def test_a_chip_never_names_a_pane_after_something_its_siblings_share(self):
        """`project` is one string per workspace, so it identifies nothing inside one -- on the real
        host it produced a chip reading `tmp-workspace` under a header reading `tmp-workspace`, and
        w6's three agents would have been three identical chips. Directories are no better: those 20
        shell panes collapse to 12 distinct cwd basenames within their own spaces."""
        self.page.evaluate("openTerminal('wA:p2')")
        names = self.page.eval_on_selector_all(
            "#termSiblings .term-sib", "els => els.map(e => e.innerText.trim())")
        self.assertEqual(names, ["wA:pH", "wA:p3"])        # both unlabelled -> both by id
        self.page.evaluate("openTerminal('wA:pH')")
        self.assertNotIn("api", self.page.eval_on_selector("#termSiblings", "e => e.innerText"))

    def test_an_operators_own_label_still_wins(self):
        self.page.evaluate("""() => {
          shellPanes.find(p => p.pane_id === 'wA:p3').label = 'build';
          openTerminal('wA:pH');
        }""")
        self.assertEqual(
            self.page.eval_on_selector_all("#termSiblings .term-sib",
                                           "els => els.map(e => e.innerText.trim())"),
            ["wA:p2", "build"])

    def test_tapping_a_chip_switches_pane(self):
        self.page.evaluate("openTerminal('wA:pH')")
        self.page.eval_on_selector('#termSiblings [data-sib-id="wA:p3"]', "e => e.click()")
        self.assertEqual(self.page.evaluate("activePane"), "wA:p3")
        # And the strip is redrawn around the pane that is now open: wA:p3 sits alone in
        # wA:t2, so its two neighbours are a tab away rather than beside it.
        self.assertEqual(self.strip(), [
            ["label", "Space"], ["chip", "wA:pH", False], ["chip", "wA:p2", True],
        ])

    def test_a_terminal_appearing_beside_the_open_pane_shows_up_without_a_reopen(self):
        """Every `agents` snapshot redraws it -- panes come and go while a session is open."""
        self.page.evaluate("openTerminal('wB:pH')")
        self.assertEqual(len([c for c in self.strip() if c[0] == "chip"]), 1)
        self.page.evaluate("""s => {
          const grown = JSON.parse(JSON.stringify(s));
          grown.panes.push({...grown.panes[2], pane_id: 'wB:p9'});
          handleMessage(grown);
        }""", SNAPSHOT)
        self.assertEqual([c[1] for c in self.strip() if c[0] == "chip"], ["wB:p2", "wB:p9"])

    def test_with_no_shell_panes_the_session_view_is_exactly_what_it_was(self):
        """HERDR_SHELL_PANES off: the relay ships no `panes` key, and wA:pH's only neighbours were
        terminals."""
        without = {k: v for k, v in SNAPSHOT.items() if k != "panes"}
        self.page.evaluate("s => { shellPanes = []; handleMessage(s); openTerminal('wA:pH'); }",
                           without)
        self.assertEqual(self.strip(), [])
        self.assertFalse(self.visible())

    def test_the_strip_costs_a_bounded_slice_of_a_phone_screen(self):
        """It sits above the output, and the output is the point. Measured at 390x844."""
        self.page.evaluate("openTerminal('wA:pH')")
        height, screen = self.page.evaluate(
            "() => [document.getElementById('termSiblings').offsetHeight, window.innerHeight]")
        self.assertGreater(height, 0)
        self.assertLess(height / screen, 0.045, f"the strip grew to {height}px of {screen}px")

    def test_the_history_panel_still_covers_everything_under_the_header(self):
        """The strip is in normal flow, so a panel opened over the output covers it too -- the same
        geometry the panel already had, which is why positionHistoryPanel is untouched."""
        self.page.evaluate("openTerminal('wA:pH'); toggleHistory()")
        covered = self.page.evaluate("""() => {
          const s = document.getElementById('termSiblings').getBoundingClientRect();
          const el = document.elementFromPoint(s.left + s.width / 2, s.top + s.height / 2);
          return document.getElementById('termHistory').contains(el);
        }""")
        self.assertTrue(covered, "the sibling strip was reachable through the history panel")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
