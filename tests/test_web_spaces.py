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


# Fixed timestamps in ms, because `ready` IS a comparison of two of them and a test that raced the
# real clock would be a test of the clock.
T0 = 1_700_000_000_000


def _agent(pane_id, workspace, tab, status="idle", active=0, seen=0, **extra):
    return {"pane_id": pane_id, "agent": "claude", "label": "", "status": status,
            "cwd": "/work/api", "project": "api", "host": "local", "remote": None,
            "workspace_id": workspace, "tab_id": tab, "title": "", "focused": False,
            "scrollback": 0, "viewport_rows": 40, "has_session": True,
            "last_active_at": T0 + active, "last_seen_at": T0 + seen, **extra}


def _shell(pane_id, workspace, tab, **extra):
    return {"pane_id": pane_id, "label": "", "cwd": "/work/api", "project": "api",
            "host": "local", "remote": None, "workspace_id": workspace, "tab_id": tab,
            "focused": False, "scrollback": 693, "viewport_rows": 68,
            "last_active_at": T0, "last_seen_at": T0, **extra}


# One of every shape that matters: a space whose agent is asking, a space herdr is pointed at with a
# tabmate terminal AND one a tab away, a space holding nothing but terminals, an agent in a space
# `workspace list` never reported, and a space carrying the two halves of `done` -- one finished while
# you were away and one you have already looked at.
SNAPSHOT = {
    "type": "agents",
    "agents": [
        # No operator label: its herd title is the SPACE label, not this per-pane `project`.
        _agent("wA:pH", "wA", "wA:t1", status="working", active=300, seen=300),
        _agent("wB:pH", "wB", "wB:t1", status="blocked", project="billing",
               active=500, seen=0, options=["Yes", "No"]),
        _agent("wD:pH", "wD", "wD:t1", project="orphan", active=200, seen=900),
        _agent("wE:pR", "wE", "wE:t1", status="done", project="extras", active=400, seen=100),
        _agent("wE:pD", "wE", "wE:t1", status="done", project="extras", active=100, seen=800),
    ],
    "spaces": {
        "workspaces": [
            {"workspace_id": "wA", "label": "api", "number": 1, "focused": True,
             "tab_count": 2, "pane_count": 3, "host": "local"},
            {"workspace_id": "wB", "label": "billing", "number": 2, "focused": False,
             "tab_count": 1, "pane_count": 2, "host": "local"},
            {"workspace_id": "wC", "label": "logs", "number": 3, "focused": False,
             "tab_count": 1, "pane_count": 2, "host": "local"},
            {"workspace_id": "wE", "label": "extras", "number": 4, "focused": False,
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
            {"tab_id": "wE:t1", "workspace_id": "wE", "label": "1", "number": 1,
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


class _Page:
    """The DOM readers both suites use."""

    def sequence(self):
        return self.page.eval_on_selector_all("#agents > *", """els => els.map(e =>
          e.classList.contains('section-header')
            ? {kind: 'section', label: e.querySelector('.sec-label').textContent,
               count: (e.innerText.match(/\\((\\d+)\\)/) || [])[1],
               dot: getComputedStyle(e.querySelector('.dot')).backgroundColor,
               controls: e.querySelectorAll('.sec-btn').length}
            : e.classList.contains('tab-heading')
              ? {kind: 'tab', label: e.innerText.split('\\n')[0].replace(/\\s*\\(\\d+\\).*$/, '')}
              : e.classList.contains('agent')
                ? {kind: e.dataset.shell === '1' ? 'shell' : 'agent', id: e.dataset.paneId,
                   bucket: e.dataset.bucket || null,
                   title: [...e.querySelectorAll('.project > span')].map(x => x.textContent),
                   meta: e.querySelector('.meta').textContent,
                   named: e.dataset.agentName}
                : {kind: e.classList.contains('chip-strip') ? 'chips'
                     : e.classList.contains('empty-tab') ? 'empty-tab' : 'other',
                   label: e.innerText})""")

    def sections(self):
        out = []
        for node in self.sequence():
            if node["kind"] == "section":
                out.append((node["label"], []))
            elif node["kind"] == "agent":
                out[-1][1].append(node["id"])
        return out


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebTriageTests(unittest.TestCase, _Page):
    """The herd list: Needs you -> Ready · unseen -> Working -> Recent, and nothing else."""

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
          recentDir = 'newest'; recentOpen = true;
          handleMessage(s);
        }""", SNAPSHOT)

    def test_the_herd_is_in_the_one_order_the_app_agrees_on(self):
        self.assertEqual(self.sections(), [
            ("Needs you", ["wB:pH"]),
            ("Ready · unseen", ["wE:pR"]),
            ("Working", ["wA:pH"]),
            ("Recent", ["wD:pH", "wE:pD"]),
        ])

    def test_ready_is_a_comparison_not_a_flag(self):
        """wE:pR and wE:pD are both `done`. What separates them is whether the relay saw the pane
        move after you last looked at it."""
        self.page.evaluate("""() => {
          agents.find(a => a.pane_id === 'wE:pR').last_seen_at = 9e12;   // you just opened it
          render();
        }""")
        buckets = {n["id"]: n["bucket"] for n in self.sequence() if n["kind"] == "agent"}
        self.assertEqual(buckets["wE:pR"], "recent")
        self.assertNotIn("Ready · unseen", [label for label, _ in self.sections()])

    def test_opening_a_pane_is_all_it_takes_to_clear_it(self):
        """No bookkeeping on either side: the relay's next snapshot carries a bumped last_seen_at
        and the row falls into Recent on its own."""
        moved = {**SNAPSHOT}
        self.page.evaluate("""s => {
          const snap = JSON.parse(JSON.stringify(s));
          const p = snap.agents.find(a => a.pane_id === 'wE:pR');
          p.last_seen_at = p.last_active_at + 1;
          handleMessage(snap);
        }""", moved)
        self.assertEqual([label for label, _ in self.sections()],
                         ["Needs you", "Working", "Recent"])

    def test_a_relay_with_no_timestamps_costs_nothing(self):
        """Every comparator returns 0 and sort is stable, so the sections keep the order the relay
        sent. No feature detection, no branch -- and Ready is simply empty."""
        self.page.evaluate("""s => {
          const snap = JSON.parse(JSON.stringify(s));
          snap.agents.forEach(a => { delete a.last_active_at; delete a.last_seen_at; });
          activeWorkspace = null; handleMessage(snap);
        }""", SNAPSHOT)
        self.assertEqual(self.sections(), [
            ("Needs you", ["wB:pH"]),
            ("Working", ["wA:pH"]),
            # wE:pR is `done` and can no longer be told from wE:pD, so both are Recent -- and in the
            # order the relay sent them, because every comparator returned 0 and sort is stable.
            ("Recent", ["wD:pH", "wE:pR", "wE:pD"]),
        ])

    def test_only_recent_folds_and_only_recent_inverts(self):
        """Collapsing an alert defeats the alert, and an attention section is ordered by urgency,
        which does not invert. The absence of controls on the first three is what marks the fourth."""
        controls = {n["label"]: n["controls"] for n in self.sequence() if n["kind"] == "section"}
        self.assertEqual(controls,
                         {"Needs you": 0, "Ready · unseen": 0, "Working": 0, "Recent": 2})

    def test_recent_folds_away_and_the_others_cannot(self):
        self.page.evaluate("toggleRecentOpen()")
        self.assertEqual(dict(self.sections())["Recent"], [])
        # Every other section still has its rows.
        self.assertEqual(dict(self.sections())["Needs you"], ["wB:pH"])
        self.page.evaluate("toggleRecentOpen()")
        self.assertEqual(dict(self.sections())["Recent"], ["wD:pH", "wE:pD"])

    def test_the_direction_toggle_reaches_recent_and_nothing_else(self):
        before = dict(self.sections())
        self.page.evaluate("flipRecentDir()")
        after = dict(self.sections())
        self.assertEqual(after["Recent"], list(reversed(before["Recent"])))
        for pinned in ("Needs you", "Ready · unseen", "Working"):
            self.assertEqual(after[pinned], before[pinned])

    def test_a_dot_says_which_bucket_not_which_status(self):
        """`done` means two different things depending on whether you have looked at it, and only the
        bucket knows which -- so wE:pR and wE:pD, both `done`, must not share a colour."""
        dots = self.page.evaluate("""() => {
          const g = id => getComputedStyle(
            document.querySelector(`[data-pane-id="${id}"] .dot`)).backgroundColor;
          return {ready: g('wE:pR'), recent: g('wE:pD'), needs: g('wB:pH'), working: g('wA:pH')};
        }""")
        self.assertNotEqual(dots["ready"], dots["recent"])
        self.assertEqual(len({dots["ready"], dots["recent"], dots["needs"], dots["working"]}), 4)

    def test_a_section_dot_matches_the_rows_it_collects(self):
        """One map, so a heading cannot drift from what is under it."""
        headings = {n["label"]: n["dot"] for n in self.sequence() if n["kind"] == "section"}
        row = self.page.eval_on_selector(
            '[data-pane-id="wE:pR"] .dot', "e => getComputedStyle(e).backgroundColor")
        self.assertEqual(headings["Ready · unseen"], row)

    def test_the_herd_is_agents_only(self):
        """Two thirds of the panes on a real host are bare shells with no status at all. Triaging
        them would bury ten agents under twenty rows that can never be anything but Recent."""
        kinds = [n["kind"] for n in self.sequence() if n["kind"] in ("agent", "shell")]
        self.assertNotIn("shell", kinds)
        self.assertEqual(len(kinds), len(SNAPSHOT["agents"]))

    def test_with_no_agents_the_herd_points_at_the_terminals_rather_than_lying(self):
        self.page.evaluate("""s => {
          const snap = JSON.parse(JSON.stringify(s));
          snap.agents = [];
          activeWorkspace = null; handleMessage(snap);
        }""", SNAPSHOT)
        text = self.page.eval_on_selector("#agents .empty", "e => e.innerText")
        self.assertIn("5 terminals", text)

    def test_a_chip_carries_one_dot_from_the_one_classifier(self):
        """So a space chip and the row it stands for cannot disagree about what a colour means."""
        chips = self.page.eval_on_selector_all(
            "#agents .chip-strip:first-of-type .chip", """els => els.map(e => {
              const d = e.querySelector('.chip-dot');
              return [e.textContent.trim(), d ? getComputedStyle(d).backgroundColor : null];
            })""")
        by_name = {name.split(" (")[0]: dot for name, dot in chips}
        row = self.page.eval_on_selector(
            '[data-pane-id="wB:pH"] .dot', "e => getComputedStyle(e).backgroundColor")
        self.assertEqual(by_name["billing"], row)
        # A space holding only terminals has nothing to report, and a resting dot would claim
        # otherwise -- worstTriage returns null and no dot is drawn.
        self.assertIsNone(by_name["logs"])


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebSpaceViewTests(unittest.TestCase, _Page):
    """One space's panes, grouped by tab -- agents and bare shells together."""

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        self.page.evaluate("""s => {
          activeWorkspace = null; activeTab = null; handleMessage(s);
        }""", SNAPSHOT)

    def groups(self):
        out = []
        for node in self.sequence():
            if node["kind"] == "tab":
                out.append((node["label"], []))
            elif node["kind"] in ("agent", "shell"):
                out[-1][1].append(node["id"])
            elif node["kind"] == "empty-tab":
                out[-1][1].append("(empty)")
        return out

    def test_a_space_is_grouped_by_tab_with_both_kinds_together(self):
        self.page.evaluate("selectWorkspace('local|wA')")
        self.assertEqual(self.groups(), [
            ("Tab 1", ["wA:pH", "wA:p2"]),
            ("deploy", ["wA:p3"]),
        ])

    def test_an_empty_tab_is_a_thing_to_see_not_an_absence_to_hide(self):
        """A freshly created tab holds one shell the relay may not have listed yet; hiding the tab
        would leave nowhere to go and launch an agent in it."""
        self.page.evaluate("""() => {
          spaces.tabs.push({tab_id: 'wA:t9', workspace_id: 'wA', label: 'fresh', number: 9,
                            focused: false, pane_count: 1, host: 'local'});
          selectWorkspace('local|wA');
        }""")
        self.assertEqual(self.groups()[-1], ("fresh", ["(empty)"]))

    def test_a_pane_whose_tab_is_not_listed_yet_is_never_lost(self):
        """The poll race right after a create: `pane list` has the pane, `tab list` has not caught up."""
        self.page.evaluate("""() => {
          shellPanes.push({...shellPanes[0], pane_id: 'wA:p9', tab_id: 'wA:t7'});
          selectWorkspace('local|wA');
        }""")
        self.assertEqual(self.groups()[-1], ("…", ["wA:p9"]))

    def test_a_card_in_a_tab_leads_with_the_panes_own_name(self):
        """The heading above it already said the space and the tab. Repeating them says nothing --
        and two panes in one tab would become indistinguishable, since their own name is the only
        thing telling them apart."""
        self.page.evaluate("selectWorkspace('local|wA')")
        card = next(n for n in self.sequence() if n.get("id") == "wA:p2")
        self.assertEqual(card["title"], ["wA:p2"])
        self.assertNotIn("api", card["title"])
        # And the id does not then appear twice -- it has become the title, so the meta line drops it.
        self.assertEqual(card["meta"], "work/api")

    def test_the_tab_filter_still_narrows_to_one_group(self):
        self.page.evaluate("selectWorkspace('local|wA'); selectTab('local|wA:t2')")
        self.assertEqual([n["id"] for n in self.sequence() if n["kind"] in ("agent", "shell")],
                         ["wA:p3"])
        # With one tab shown its own heading would only repeat the chip that selected it.
        self.assertEqual([n for n in self.sequence() if n["kind"] == "tab"], [])


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebPaneNamingTests(unittest.TestCase, _Page):
    """What a row is called, which is two questions and therefore two functions."""

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        self.page.evaluate("""s => {
          activeWorkspace = null; activeTab = null; handleMessage(s);
        }""", SNAPSHOT)

    def card(self, pane_id):
        return next(n for n in self.sequence() if n.get("id") == pane_id)

    def test_the_herd_title_is_the_space_label_not_the_cwd_basename(self):
        """The relay sets `project` to basename(cwd), which is a per-pane fact. What locates a piece
        of work is the space's own label."""
        self.assertEqual(self.card("wA:pH")["title"][0], "api")
        self.assertEqual(self.card("wD:pH")["title"][0], "orphan")   # unlisted space, best guess

    def test_the_tab_rides_the_title_as_its_own_span(self):
        """Not a joined string: at 390px tail-truncating `space · tab` eats the tab, and the
        characters that survive are the ones every row in that space shares."""
        self.page.evaluate("""() => {
          agents.find(a => a.pane_id === 'wA:pH').tab_id = 'wA:t2';
          render();
        }""")
        self.assertEqual(self.card("wA:pH")["title"], ["api", " · ", "deploy"])
        # And the separator's own spaces survive: they sit inside the span, and a flex container
        # collapses them unless told not to -- the title rendered `api·deploy`.
        self.assertIn(
            " · ",
            self.page.eval_on_selector('[data-pane-id="wA:pH"] .project', "e => e.innerText"))

    def test_a_positional_tab_label_is_dropped_when_there_is_only_one_tab(self):
        """herdr labels an unlabelled tab positionally, so `billing · 1` reads as a bug. With two or
        more tabs the number stays -- weak, but the only thing telling two panes in one space apart."""
        self.assertEqual(self.card("wB:pH")["title"], ["billing"])
        self.page.evaluate("""() => {
          spaces.tabs.push({tab_id: 'wB:t2', workspace_id: 'wB', label: '2', number: 2,
                            focused: false, pane_count: 1, host: 'local'});
          render();
        }""")
        self.assertEqual(self.card("wB:pH")["title"], ["billing", " · ", "1"])

    def test_the_cwd_is_dropped_when_it_repeats_the_space(self):
        """A space is almost always named after its directory, so this line spent itself repeating
        line one -- `api` above `work/api`, row after row. What is left when everything drops out is
        the pane id, because something has to separate two rows: measured on a real host, three
        agents share one tab of one space whose directory IS the space's name, and all three read
        `tuyaos-ai-qemu` with an empty second line."""
        self.assertEqual(self.card("wA:pH")["meta"], "claude · wA:pH")
        self.page.evaluate("""() => {
          agents.find(a => a.pane_id === 'wA:pH').cwd = '/work/api/worktrees/hotfix';
          render();
        }""")
        self.assertEqual(self.card("wA:pH")["meta"], "claude · worktrees/hotfix")

    def test_a_hand_set_name_beats_the_cwd_and_the_title(self):
        self.page.evaluate("""() => {
          Object.assign(agents.find(a => a.pane_id === 'wA:pH'),
                        {label: 'ingest rework', title: 'running tests'});
          render();
        }""")
        self.assertEqual(self.card("wA:pH")["meta"], "claude · ingest rework")

    def test_what_a_pane_is_called_never_depends_on_scope(self):
        """`data-agent-name` prefills the rename dialog, so it carries the real thing in both views
        even where the card shows something shorter."""
        herd = self.card("wA:pH")["named"]
        self.page.evaluate("selectWorkspace('local|wA')")
        self.assertEqual(self.card("wA:pH")["named"], herd)
        # Never `project`: the relay sets that to basename(cwd), which a space's panes nearly all
        # share -- on a real host every card in `tmp-workspace` was called `tmp-workspace`, and so
        # was the heading above them.
        self.assertEqual(herd, "wA:pH")
        self.page.evaluate("""() => {
          agents.find(a => a.pane_id === 'wA:pH').label = 'ingest'; render();
        }""")
        self.assertEqual(self.card("wA:pH")["named"], "ingest")

    def test_the_project_gives_up_width_before_the_tab_does(self):
        """The whole reason line one is spans. Measured, not read off the CSS."""
        # Through the snapshot, not through spaceNameByKey: render() rebuilds that map every time,
        # which is the point of it existing.
        self.page.evaluate("""() => {
          agents.find(x => x.pane_id === 'wA:pH').tab_id = 'wA:t2';
          spaces.workspaces.find(w => w.workspace_id === 'wA').label =
            'a-very-long-workspace-name-that-cannot-possibly-fit-on-a-phone';
          render();
        }""")
        boxes = self.page.evaluate("""() => {
          const card = document.querySelector('[data-pane-id="wA:pH"]');
          const w = sel => card.querySelector(sel).getBoundingClientRect().width;
          return {project: w('.pane-project'), tab: w('.pane-tab'),
                  tabText: card.querySelector('.pane-tab').textContent,
                  clipped: card.querySelector('.pane-project').scrollWidth
                           > card.querySelector('.pane-project').clientWidth + 1};
        }""")
        self.assertTrue(boxes["clipped"], "the project was not the part that gave up width")
        self.assertEqual(boxes["tabText"], "deploy")
        self.assertGreater(boxes["tab"], 20, "the tab was squeezed to nothing")


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

    def test_one_chip_per_pane_even_when_a_pane_is_in_both_arrays(self):
        """A `blocked` push adds an agent record for a pane that may still be in shellPanes, and
        paneById's own comment admits the overlap. Two chips for one pane, one hollow and one
        coloured, would read as two panes."""
        self.page.evaluate("""() => {
          agents.push({...shellPanes.find(p => p.pane_id === 'wA:p2'),
                       agent: 'claude', status: 'blocked'});
          openTerminal('wA:pH');
        }""")
        self.assertEqual([c[1] for c in self.strip() if c[0] == "chip"], ["wA:p2", "wA:p3"])

    def test_no_tab_hierarchy_means_no_tab_claim(self):
        """`Tab` says these panes are on the operator's screen beside this one. With no tab ids to
        go by there is nothing to base that on, so everything is simply elsewhere in the space."""
        self.page.evaluate("""() => {
          [...agents, ...shellPanes].forEach(p => { delete p.tab_id; });
          spaces = {workspaces: spaces.workspaces, tabs: []};
          openTerminal('wA:pH');
        }""")
        self.assertEqual(self.strip(), [
            ["label", "Space"], ["chip", "wA:p2", True], ["chip", "wA:p3", True]])

    def test_switching_pane_from_the_strip_does_not_carry_the_search_over(self):
        """`originalContent` is one global holding the open pane's output. Switching with the search
        open left pane A's HTML in it, and the next keystroke restored A's output into B's session
        -- reachable only since a chip made switching a one-tap move from inside the session."""
        self.page.evaluate("""() => {
          openTerminal('wA:pH');
          document.getElementById('termContent').innerHTML = 'AAA-pane-A-output';
          toggleSearch();
          document.getElementById('searchInput').value = 'AAA';
          doSearch();
        }""")
        self.assertNotEqual(self.page.evaluate("originalContent"), "")
        self.page.eval_on_selector('#termSiblings [data-sib-id="wA:p2"]', "e => e.click()")
        self.assertEqual(self.page.evaluate("originalContent"), "")
        self.assertEqual(self.page.eval_on_selector("#searchInput", "e => e.value"), "")
        self.page.evaluate("""() => {
          document.getElementById('termContent').innerHTML = 'BBB-pane-B-output';
          doSearch();
        }""")
        self.assertIn("BBB-pane-B-output",
                      self.page.eval_on_selector("#termContent", "e => e.innerHTML"))

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
