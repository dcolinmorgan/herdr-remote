"""Tests for the two orderings the web app runs on, and the navigation between them.

The herd list is `triage` -- what needs you, then what is newly ready, then what is running, then
everything else -- with the space and the tab riding on the row rather than becoming headings above
it. Picking a space groups its panes by tab, agents and terminals together, because that is the one
view where "what is in this tab" is the question. The session view then carries herdr's own two
levels below the space: the tabs of this space, then the panes of this tab, so an agent, a tab or a
terminal is reachable by name without backing out to the list -- both in one row, because the
output underneath them is what the screen is for.

All of it is a claim about what a thumb finds on the screen, so it is asserted against the rendered
DOM and, where it is geometry, against measured boxes rather than the CSS.

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
#
# `order` is each pane's index in the host's own `pane list`, which the relay passes through because
# it is the order the panes sit in on the operator's screen and nothing else in the record implies it.
# Numbered here as one walk of the hierarchy, the way herdr answers: wA's two tabs, then wB, wC, wD,
# wE. The terminal is to the left of the agent in the two tabs that hold both.
SNAPSHOT = {
    "type": "agents",
    "agents": [
        # No operator label: its herd title is the SPACE label, not this per-pane `project`.
        _agent("wA:pH", "wA", "wA:t1", status="working", active=300, seen=300, order=1),
        _agent("wB:pH", "wB", "wB:t1", status="blocked", project="billing",
               active=500, seen=0, options=["Yes", "No"], order=4),
        _agent("wD:pH", "wD", "wD:t1", project="orphan", active=200, seen=900, order=7),
        _agent("wE:pR", "wE", "wE:t1", status="done", project="extras", active=400, seen=100,
               order=8),
        _agent("wE:pD", "wE", "wE:t1", status="done", project="extras", active=100, seen=800,
               order=9),
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
        _shell("wA:p2", "wA", "wA:t1", order=0),
        _shell("wA:p3", "wA", "wA:t2", order=2),
        _shell("wB:p2", "wB", "wB:t1", project="billing", order=3),
        _shell("wC:p1", "wC", "wC:t1", project="logs", order=5),
        _shell("wC:p2", "wC", "wC:t1", project="logs", order=6),
    ],
}


# What the order test clones its panes from: one agent record and one shell record with every field
# the renderers read, so a test can lay out a tab of its own without restating the shapes.
SEEDS = {"agent": _agent("seed", "wA", "wA:t1", status="working"),
         "shell": _shell("seed", "wA", "wA:t1")}


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
        """And in herdr's order inside each tab, which is the same order the session strip draws --
        both go through panesInSpace. Merging the two arrays here, which is what this did, put every
        agent ahead of every terminal, so wA:p2 was drawn last in a view whose whole claim is that it
        shows what is in a tab. It is to the LEFT of wA:pH on the operator's screen."""
        self.page.evaluate("selectWorkspace('local|wA')")
        self.assertEqual(self.groups(), [
            ("Tab 1", ["wA:p2", "wA:pH"]),
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
class WebSessionNavTests(unittest.TestCase):
    """The two rows under the session header: the space's tabs, then the tab's panes.

    What they replaced was one flat row of the OTHER panes in the workspace, tagged `Tab` and
    `Space`, each chip named `label || pane_id`. On the measured host 28 of the 30 panes carry no
    operator label, so that row read `w6:pH  w6:pQ  w6:pR`: three chips whose names differ by one
    character, no mark for the pane you were standing in, and no way to reach a tab by name at all.
    """

    @classmethod
    def setUpClass(cls):
        cls.page = _shared["browser"].new_page(viewport=PHONE)
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()

    def setUp(self):
        # The panels outlive a snapshot -- openTerminal only closes them on a real pane switch, so a
        # test that reopens the same pane would inherit whatever the last one left open.
        self.page.evaluate("""s => {
          activeWorkspace = null; activeTab = null;
          hideHistory();
          hideSearch();
          handleMessage(s);
          window.__sent = [];
          paneProcess = {};
          ws = {readyState: 1, send: p => window.__sent.push(JSON.parse(p))};
        }""", SNAPSHOT)

    def strip(self, kind=""):
        """The row in order: ('chip', id, name, tag, is-where-you-are).

        One row and one list, so which chips you mean is a test on the chip rather than on a
        container of its own: `[data-sib-tab]` is a tab's name, `[data-sib-id]` is a pane.
        """
        return self.page.eval_on_selector_all(f"#termSiblings .term-sib{kind}", """els => els.map(e =>
          ['chip', e.dataset.sibId || e.dataset.sibTab, e.children[1].textContent,
           (e.querySelector('.sib-tag') || {textContent: ''}).textContent,
           e.getAttribute('aria-current') === 'true'])""")

    def tabs(self):
        return self.strip("[data-sib-tab]")

    def panes(self):
        return self.strip("[data-sib-id]")

    def groups(self):
        """The row as the reader reads it: each tab's name, then the panes behind it."""
        return self.page.eval_on_selector_all("#termSiblings .sib-group", """els => els.map(g => [
          g.querySelector('.sib-tab').children[1].textContent,
          [...g.querySelectorAll('[data-sib-id]')].map(e => e.dataset.sibId)])""")

    def visible(self, sel):
        return self.page.eval_on_selector(sel, "e => e.offsetHeight") > 0

    def names(self, kind="[data-sib-id]"):
        return [c[2] for c in self.strip(kind) if c[0] == "chip"]

    # -------------------------------------------------------------- the levels

    def test_the_row_is_every_pane_in_the_space_grouped_by_tab(self):
        """space > tab > pane, of which the space is the one left to the herd list -- a tap on Back.
        The other two are ONE list, read left to right the way a multiplexer's status line is: the
        panes of tab 1, then the panes of tab 2, each group led by its tab's name. wA:pH sits in
        wA:t1 beside a terminal, with a second tab holding one more -- and that one is now a single
        tap rather than a tab chip followed by whatever pane it decided to land you on."""
        self.page.evaluate("openTerminal('wA:pH')")
        self.assertEqual(self.groups(), [
            ["Tab 1", ["wA:p2", "wA:pH"]],
            ["deploy", ["wA:p3"]],
        ])
        # And in that order in the DOM, so a screen reader and a thumb travel the same row.
        self.assertEqual(
            [c[1] for c in self.strip()],
            ["wA:t1", "wA:p2", "wA:pH", "wA:t2", "wA:p3"])
        self.assertEqual(self.panes(), [
            ["chip", "wA:p2", "api", "p2", False],
            ["chip", "wA:pH", "claude", "pH", True],
            ["chip", "wA:p3", "api", "p3", False],
        ])

    def test_the_strip_is_in_herdrs_own_order_not_the_ids(self):
        """The shape this was measured on, live: `w6:t1` holds pH and p15 with a terminal p12 between
        them, and reads pH, p15, p12 at the desk -- `pane list` and `pane layout` agree, top-left
        first. Sorting the ids put it in exactly the wrong order, because the suffix is a creation
        counter in a base wider than ten (pH was opened before p12), and merging the relay's two
        arrays would have drawn the terminal last."""
        self.page.evaluate("""seeds => {
          agents = agents.filter(a => a.pane_id !== 'wA:pH');
          shellPanes = shellPanes.filter(p => p.pane_id !== 'wA:p2');
          agents.push({...seeds.agent, pane_id: 'wA:pH', order: 0},
                      {...seeds.agent, pane_id: 'wA:p15', order: 1});
          shellPanes.push({...seeds.shell, pane_id: 'wA:p12', order: 2});
          activePane = null;
          openTerminal('wA:pH');
        }""", SEEDS)
        # wA:p3 rides along at the end: the row is the whole space, and it is in wA:t2.
        self.assertEqual([c[1] for c in self.panes() if c[0] == "chip"],
                         ["wA:pH", "wA:p15", "wA:p12", "wA:p3"])

    def test_a_relay_that_reports_no_order_keeps_the_order_it_sent(self):
        """One rule, no feature detection: a missing `order` sorts last and the sort is stable, so
        every pane ties and both strips keep the arrays as they arrived. Which is what an older relay
        and the demo worker send, and also what a `blocked` push leaves behind for one snapshot."""
        self.page.evaluate("""() => {
          [...agents, ...shellPanes].forEach(p => { delete p.order; });
          activePane = null;
          openTerminal('wA:pH');
        }""")
        self.assertEqual([c[1] for c in self.panes() if c[0] == "chip"],
                         ["wA:pH", "wA:p2", "wA:p3"])

    def test_the_pane_you_are_in_is_in_the_strip_and_marked(self):
        """The old row listed the OTHER panes, so a row of chips had no `you are here` in it. Blue
        is selection everywhere else on this page, and this is a selection."""
        self.page.evaluate("openTerminal('wA:pH')")
        fills = self.page.evaluate("""() => {
          const bg = sel => getComputedStyle(document.querySelector(sel)).backgroundColor;
          return [bg('#termSiblings [data-sib-id="wA:pH"]'),
                  bg('#termSiblings [data-sib-id="wA:p2"]'),
                  bg('#termSiblings [data-sib-tab="wA:t1"]'),
                  bg('#termSiblings [data-sib-tab="wA:t2"]')];
        }""")
        self.assertEqual(fills[0], fills[2], "the open pane and its tab are marked differently")
        self.assertNotEqual(fills[0], fills[1], "the open pane's chip looks like its neighbour's")
        # A tab's name carries an opaque background of its own -- it is pinned, and the panes scroll
        # under it -- so what has to differ is the marked one from the unmarked one.
        self.assertNotEqual(fills[2], fills[3], "the tab you are in looks like the one you are not")

    def test_one_tab_is_not_a_choice(self):
        """6 of the 10 agent panes measured sit in a single-tab space, and would each have paid a
        chip, and a rule, to be told the name of the only tab there is."""
        self.page.evaluate("openTerminal('wB:pH')")
        self.assertEqual(self.tabs(), [])
        self.assertEqual(self.groups(), [])
        # wB:p2's `project` is `billing` and its cwd is /work/api. The chip says `api`, which is
        # the directory -- the field that belongs to the pane rather than to the whole worktree.
        self.assertEqual(self.names(), ["api", "claude"])

    def test_a_tab_with_no_pane_to_land_on_is_not_a_chip(self):
        """There is no CLI for pointing the web client at an empty tab, and a chip that does nothing
        is worse than no chip. It is also what keeps the row honest with HERDR_SHELL_PANES off: it
        becomes the tabs that hold agents."""
        self.page.evaluate("""() => {
          shellPanes = shellPanes.filter(p => p.pane_id !== 'wA:p3');   // wA:t2 is now empty
          render();
          openTerminal('wA:pH');
        }""")
        self.assertEqual(self.tabs(), [])
        self.assertEqual(self.groups(), [])

    def test_a_pane_with_nothing_beside_it_costs_no_pixels(self):
        """Three of the ten agent panes on the measured host have no pane beside them. The threshold
        is the SPACE rather than the tab: one pane in it is nothing to switch to, and the row would
        cost the output 33px to say so."""
        self.page.evaluate("openTerminal('wD:pH')")
        self.assertEqual(self.panes(), [])
        self.assertFalse(self.visible("#termSibs"))

    def test_with_no_shell_panes_the_session_view_is_exactly_what_it_was(self):
        """HERDR_SHELL_PANES off: the relay ships no `panes` key, and wA:pH's only neighbours were
        terminals."""
        without = {k: v for k, v in SNAPSHOT.items() if k != "panes"}
        self.page.evaluate("s => { shellPanes = []; handleMessage(s); openTerminal('wA:pH'); }",
                           without)
        self.assertEqual(self.panes(), [])
        self.assertEqual(self.tabs(), [])
        self.assertFalse(self.visible("#termSibs"))

    # --------------------------------------------------------------- the names

    def test_a_chip_is_named_by_what_the_pane_can_still_say_for_itself(self):
        """28 of 30 panes carry no label, so `label || pane_id` was the pane id -- which is the one
        thing on the chip that reads as machine output. An agent falls back to its harness, a
        terminal to its directory, and `project` is never in it: the relay sets that to
        basename(cwd), which by construction every pane in one worktree shares."""
        self.page.evaluate("""() => {
          shellPanes.find(p => p.pane_id === 'wA:p2').cwd = '/work/api/build';
          openTerminal('wA:pH');
        }""")
        # `api` at the end is wA:t2's own terminal, whose cwd this test did not move.
        self.assertEqual(self.names(), ["build", "claude", "api"])
        self.assertEqual(self.page.evaluate("agents.find(a => a.pane_id === 'wA:pH').project"),
                         "api")

    def test_a_working_agent_is_named_by_what_it_says_it_is_doing(self):
        """herdr's terminal title with the harness banner stripped -- 2 of the 10 agent panes
        measured were saying something real, and both were the two you would want to find."""
        self.page.evaluate("""() => {
          agents.find(a => a.pane_id === 'wA:pH').title = 'fixing the poll';
          openTerminal('wA:pH');
        }""")
        self.assertEqual(self.names(), ["api", "fixing the poll", "api"])

    def test_an_operators_own_label_still_wins(self):
        self.page.evaluate("""() => {
          shellPanes.find(p => p.pane_id === 'wA:p2').label = 'build';
          agents.find(a => a.pane_id === 'wA:pH').label = 'poller';
          agents.find(a => a.pane_id === 'wA:pH').title = 'fixing the poll';
          openTerminal('wA:pH');
        }""")
        self.assertEqual(self.names(), ["build", "poller", "api"])

    def test_the_pane_ids_own_suffix_is_the_handle_when_the_name_repeats(self):
        """Three shells sitting in `herdr`, three claudes in one tab: the name is routinely shared
        by every chip in the strip, and this is the only part that separates them."""
        self.page.evaluate("""() => {
          shellPanes.push({...shellPanes.find(p => p.pane_id === 'wA:p2'), pane_id: 'wA:p9'});
          render();
          openTerminal('wA:pH');
        }""")
        chips = [c for c in self.panes() if c[0] == "chip"]
        # Three in wA:t1, then wA:t2's own terminal -- four chips, three of them called `api`.
        self.assertEqual([c[2] for c in chips], ["api", "api", "claude", "api"])
        self.assertEqual([c[3] for c in chips], ["p2", "p9", "pH", "p3"])

    def test_a_tab_is_named_by_its_label_and_a_bare_number_says_it_is_positional(self):
        """herdr labels an unlabelled tab by its POSITION, so a chip reading `2` beside one reading
        `deploy` would look like a name someone chose."""
        self.page.evaluate("openTerminal('wA:pH')")
        self.assertEqual([c[2] for c in self.tabs() if c[0] == "chip"], ["Tab 1", "deploy"])

    def test_a_tabs_name_is_told_from_a_pane_without_spending_a_word_on_it(self):
        """A written `Tabs` / `Panes` heading cost 40px of the row including its gap, and 4 of the 5
        rows that scrolled on the real host overflowed by less than that. So the tab's name is
        squarer than a pane chip, the pane carries the id tag the tab has not got, and what the shape
        says is announced through the chip's own aria-label, where it costs nothing."""
        self.page.evaluate("openTerminal('wA:pH')")
        shape = self.page.evaluate("""() => [
          getComputedStyle(document.querySelector('#termSiblings [data-sib-tab]')).borderTopLeftRadius,
          getComputedStyle(document.querySelector('#termSiblings [data-sib-id]')).borderTopLeftRadius,
          document.querySelector('#termSiblings [data-sib-tab]').getAttribute('aria-label'),
          document.getElementById('termSiblings').getAttribute('aria-label')]""")
        self.assertNotEqual(shape[0], shape[1], "a tab's name and a pane chip are the same shape")
        self.assertIn("Tab", shape[2])
        self.assertIn("Panes", shape[3])
        self.assertEqual([c[3] for c in self.tabs()], ["", ""])   # no id tag on a tab

    # -------------------------------------------------------------- the marks

    def test_a_chip_carries_the_same_mark_its_card_does(self):
        """Hollow for a terminal, the bucket's colour otherwise -- so the strip needs no legend of
        its own, and the two places cannot come to disagree about a pane."""
        self.page.evaluate("openTerminal('wA:p2')")
        card, chip, shell = self.page.evaluate("""() => {
          const g = sel => { const c = getComputedStyle(document.querySelector(sel));
                             return [c.backgroundColor, c.borderStyle]; };
          return [g('#agents [data-pane-id="wA:pH"] .dot'),
                  g('#termSiblings [data-sib-id="wA:pH"] .dot'),
                  g('#termSiblings [data-sib-id="wA:p2"] .dot')];
        }""")
        self.assertEqual(chip[0], card[0], "the chip and the card disagree about a working agent")
        self.assertEqual(chip[1], "none")
        self.assertNotEqual(shell[1], "none")
        self.assertIn(shell[0], ("rgba(0, 0, 0, 0)", "transparent"))

    def test_a_tab_chip_says_what_is_going_on_inside_it(self):
        """The same classifier as the card and the section header. A tab holding only terminals gets
        the hollow dot -- worstTriage declining to invent a resting state for a pane that has none."""
        self.page.evaluate("""() => {
          agents.push({...agents.find(a => a.pane_id === 'wA:pH'),
                       pane_id: 'wA:pX', tab_id: 'wA:t2', status: 'blocked'});
          render();
          openTerminal('wA:pH');
        }""")
        blocked, terminals = self.page.evaluate("""() => {
          const g = sel => { const c = getComputedStyle(document.querySelector(sel));
                             return [c.backgroundColor, c.borderStyle]; };
          return [g('#termSiblings [data-sib-tab="wA:t2"] .dot'),
                  g('#termSiblings [data-sib-tab="wA:t1"] .dot')];
        }""")
        red = self.page.eval_on_selector(
            '#agents [data-bucket="needs"] .dot', "e => getComputedStyle(e).backgroundColor")
        self.assertEqual(blocked[0], red)
        self.page.evaluate("""() => {
          agents = agents.filter(a => a.pane_id !== 'wA:pH' && a.pane_id !== 'wA:pX');
          render();
          openTerminal('wA:p2');
        }""")
        hollow = self.page.eval_on_selector('#termSiblings [data-sib-tab="wA:t2"] .dot',
                                            "e => getComputedStyle(e).borderStyle")
        self.assertNotEqual(hollow, "none", "a tab of terminals was given a status it does not have")

    # --------------------------------------------------------------- the taps

    def test_tapping_a_pane_chip_switches_pane(self):
        self.page.evaluate("openTerminal('wA:pH')")
        self.page.eval_on_selector('#termSiblings [data-sib-id="wA:p2"]', "e => e.click()")
        self.assertEqual(self.page.evaluate("activePane"), "wA:p2")
        # And the row is redrawn around the pane that is now open.
        self.assertEqual([c[4] for c in self.panes() if c[0] == "chip"], [True, False, False])

    def test_tapping_a_tab_lands_on_the_pane_in_it_that_needed_you(self):
        """Ranked through `bucketOf` like everything else on this page, so the tap goes to whatever
        would have been highest in the herd list -- if something in there is blocked, that is what
        you meant."""
        self.page.evaluate("""() => {
          agents.push({...agents.find(a => a.pane_id === 'wB:pH'),
                       pane_id: 'wA:pX', workspace_id: 'wA', tab_id: 'wA:t2', status: 'blocked'});
          render();
          openTerminal('wA:pH');
        }""")
        self.page.eval_on_selector('#termSiblings [data-sib-tab="wA:t2"]', "e => e.click()")
        self.assertEqual(self.page.evaluate("activePane"), "wA:pX")
        # The mark follows you across; the row itself is the same row.
        self.assertEqual([c[1] for c in self.tabs() if c[0] == "chip" and c[4]], ["wA:t2"])
        self.assertEqual(self.groups(),
                         [["Tab 1", ["wA:p2", "wA:pH"]], ["deploy", ["wA:p3", "wA:pX"]]])

    def test_a_tab_with_only_terminals_in_it_is_still_reachable(self):
        """The landing rule falls through to the first terminal -- a build log is a place to go. And
        it is reachable twice over now: by its own chip in the row, or by its tab's name."""
        self.page.evaluate("openTerminal('wA:pH')")
        self.page.eval_on_selector('#termSiblings [data-sib-tab="wA:t2"]', "e => e.click()")
        self.assertEqual(self.page.evaluate("activePane"), "wA:p3")
        # wA:p3 is alone in wA:t2, but the row is the whole space -- so standing in a one-pane tab
        # still leaves the other tab's panes a tap away, which the two-level row could not say.
        self.assertTrue(self.visible("#termSibs"))
        self.assertEqual(self.groups(), [["Tab 1", ["wA:p2", "wA:pH"]], ["deploy", ["wA:p3"]]])

    def test_the_tab_you_are_already_in_is_inert(self):
        """Re-entering openTerminal would close the history panel you have open for no reason."""
        self.page.evaluate("openTerminal('wA:pH'); toggleHistory()")
        self.page.eval_on_selector('#termSiblings [data-sib-tab="wA:t1"]', "e => e.click()")
        self.assertEqual(self.page.evaluate("activePane"), "wA:pH")
        self.assertNotEqual(
            self.page.eval_on_selector("#termHistory", "e => e.style.display"), "none")

    def test_from_a_terminal_the_agent_is_one_tap_back(self):
        """Same rule read the other way -- the row is every pane in the space, whichever kind."""
        self.page.evaluate("openTerminal('wA:p2')")
        self.assertEqual([c[1] for c in self.panes() if c[0] == "chip"],
                         ["wA:p2", "wA:pH", "wA:p3"])

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

    # ------------------------------------------------------- what stays honest

    def test_a_terminal_appearing_beside_the_open_pane_shows_up_without_a_reopen(self):
        """Every `agents` snapshot redraws both rows -- panes come and go while a session is open."""
        self.page.evaluate("openTerminal('wB:pH')")
        self.assertEqual(self.names(), ["api", "claude"])
        self.page.evaluate("""s => {
          const grown = JSON.parse(JSON.stringify(s));
          grown.panes.push({...grown.panes[2], pane_id: 'wB:p9'});
          handleMessage(grown);
        }""", SNAPSHOT)
        self.assertEqual([c[1] for c in self.panes() if c[0] == "chip"],
                         ["wB:p2", "wB:p9", "wB:pH"])

    def test_one_chip_per_pane_even_when_a_pane_is_in_both_arrays(self):
        """A `blocked` push adds an agent record for a pane that may still be in shellPanes, and
        paneById's own comment admits the overlap. Two chips for one pane, one hollow and one
        coloured, would read as two panes."""
        self.page.evaluate("""() => {
          agents.push({...shellPanes.find(p => p.pane_id === 'wA:p2'),
                       agent: 'claude', status: 'blocked'});
          openTerminal('wA:pH');
        }""")
        self.assertEqual([c[1] for c in self.panes() if c[0] == "chip"],
                         ["wA:p2", "wA:pH", "wA:p3"])

    def test_no_tab_hierarchy_means_no_names_and_the_space_as_one_flat_row(self):
        """With no tab ids to go by there is no level below the space to draw, and the panes row
        falls back to the set it used to show."""
        self.page.evaluate("""() => {
          [...agents, ...shellPanes].forEach(p => { delete p.tab_id; });
          spaces = {workspaces: spaces.workspaces, tabs: []};
          openTerminal('wA:pH');
        }""")
        self.assertEqual(self.tabs(), [])
        # Still herdr's order, now across the whole space: wA:p2, wA:pH, then wA:p3 in the next tab.
        self.assertEqual([c[1] for c in self.panes() if c[0] == "chip"],
                         ["wA:p2", "wA:pH", "wA:p3"])

    # ------------------------------------------------- one row, and what is above it

    def chrome(self):
        """Every pixel between the top of the screen and the first line of output."""
        return self.page.evaluate("""() => {
          const h = s => { const e = document.querySelector(s); return e ? e.offsetHeight : 0; };
          const top = s => Math.round(document.querySelector(s).getBoundingClientRect().top);
          return {header: h('.header'), termHeader: h('.term-header'), sibs: h('#termSibs'),
                  viewTop: top('.terminal-view'), contentTop: top('#termContent'),
                  screen: window.innerHeight};
        }""")

    def test_the_session_view_starts_exactly_where_the_app_header_ends(self):
        """The header is `--header-h` tall and the fixed session view starts at `--header-h`, which
        is one number written once. It used to be two: a hardcoded top of 49px against a header that
        measured 69px, so the view covered the bottom 20px of the header and clipped both of its
        buttons through the whole of a session."""
        self.page.evaluate("openTerminal('wA:pH')")
        c = self.chrome()
        self.assertEqual(c["viewTop"], c["header"],
                         f"the session view starts at {c['viewTop']}px under a {c['header']}px header")

    def test_the_chrome_above_the_output_is_a_measured_eighth_of_the_phone(self):
        """The output is the point of this screen, and everything above it is rent. Measured at
        390x844 with both levels showing: 69px of app header (of which 20 were behind the session
        view), 55px of session header and 66px of two sibling rows -- 170px, 20.1%, before a single
        line of a pane had rendered. It is 44 + 39 + 33 = 116px now, 13.7%, and the ceiling is set
        just above that."""
        self.page.evaluate("openTerminal('wA:pH')")
        c = self.chrome()
        self.assertLess(c["contentTop"] / c["screen"], 0.15,
                        f"the chrome grew to {c['contentTop']}px of {c['screen']}px: {c}")
        self.assertLess(c["header"], 50)
        self.assertLess(c["termHeader"], 45)

    def test_the_session_header_is_one_row_of_children_pinned_to_one_height(self):
        """A flex row is as tall as its tallest child. This one was 55px because `back` carried a
        1.4rem font-size around a 20px icon -- text metrics for a button with no text in it -- so
        every child is pinned instead, the same rule the history bar runs on."""
        self.page.evaluate("openTerminal('wA:pH')")
        heights = self.page.evaluate("""() => [...document.querySelectorAll('.term-header > *')]
          .filter(e => e.offsetParent).map(e => e.offsetHeight)""")
        self.assertGreater(len(heights), 3, "the header lost its controls")
        self.assertEqual(set(heights), {28}, f"the header's children measure {heights}")

    def test_one_row_holds_both_levels_and_one_scroller_holds_the_row(self):
        """They were two rows of 33px, and 4 of the 10 agent panes on the measured host paid for
        both. Then they were one row of two scrollers, which turned the saving into a width fight --
        so it is one list in one scroller, and the outer row must not scroll or a drag would move the
        levels against each other again."""
        self.page.evaluate("openTerminal('wA:pH')")
        row = self.page.evaluate("""() => {
          const strip = document.getElementById('termSiblings');
          const r = document.getElementById('termSibs').getBoundingClientRect();
          const tab = document.querySelector('#termSiblings [data-sib-tab]').getBoundingClientRect();
          const pane = document.querySelector('#termSiblings [data-sib-id]').getBoundingClientRect();
          return {row: Math.round(r.height), tabTop: Math.round(tab.top),
                  paneTop: Math.round(pane.top), tabLeft: Math.round(tab.left),
                  paneLeft: Math.round(pane.left),
                  scrollers: [getComputedStyle(document.getElementById('termSibs')).overflowX,
                              getComputedStyle(strip).overflowX],
                  boxes: document.querySelectorAll('#termSiblings, #termTabs').length};
        }""")
        self.assertLess(row["row"], 40, f"the row is {row['row']}px")
        self.assertEqual(row["tabTop"], row["paneTop"], "the two levels are still stacked")
        self.assertLess(row["tabLeft"], row["paneLeft"], "a tab's name is not ahead of its panes")
        self.assertEqual(row["scrollers"], ["hidden", "auto"])
        self.assertEqual(row["boxes"], 1, "the levels are back in boxes of their own")

    def test_a_pane_chip_is_not_rationed_by_the_tab_names(self):
        """What the two-scroller row cost: 3 tabs measured 253px of a 370px row, 68%, so the pane
        level was capped to 45% and a 178px chip -- a 32vw activity title plus its dot, id tag and
        padding -- could not be shown whole. In one list the widest chip has the whole row."""
        self.page.evaluate("""() => {
          for (let i = 0; i < 6; i++) {
            spaces.tabs.push({tab_id: 'wA:tL' + i, workspace_id: 'wA', label: 'a-long-tab-name-' + i,
                              number: 20 + i, focused: false, pane_count: 1, host: 'local'});
            shellPanes.push({...shellPanes[0], pane_id: 'wA:pL' + i, tab_id: 'wA:tL' + i,
                             order: 30 + i});
          }
          activePane = null;
          openTerminal('wA:pH');
        }""")
        room = self.page.evaluate("""() => {
          const strip = document.getElementById('termSiblings');
          return {view: strip.clientWidth, row: document.getElementById('termSibs').clientWidth,
                  widest: Math.max(...[...strip.querySelectorAll('.term-sib')]
                    .map(e => e.getBoundingClientRect().width))};
        }""")
        self.assertGreater(room["view"], room["row"] * 0.9,
                           f"the scroller was rationed to {room['view']}px of {room['row']}px")
        self.assertGreater(room["view"], room["widest"])

    def test_the_rule_stands_between_groups_and_nowhere_else(self):
        """1px is what says where one tab's panes end, and a rule on the first group would be a mark
        against nothing. wB has a single tab, so it draws no groups at all."""
        self.page.evaluate("openTerminal('wA:pH')")
        borders = self.page.eval_on_selector_all(
            "#termSiblings .sib-group", "els => els.map(e => getComputedStyle(e).borderLeftWidth)")
        self.assertEqual(borders, ["0px", "1px"])
        self.page.evaluate("openTerminal('wB:pH')")
        self.assertEqual(self.page.eval_on_selector_all("#termSiblings .sib-group", "e => e.length"), 0)
        self.assertTrue(self.visible("#termSibs"), "wB has two panes; the row still has a job")

    def test_the_row_itself_goes_when_there_is_nothing_to_switch_to(self):
        """Border and all: wD holds one pane in one tab, so the row would otherwise cost the output
        33px to say that the pane you are in is the only one there is."""
        self.page.evaluate("openTerminal('wD:pH')")
        self.assertFalse(self.visible("#termSibs"))
        self.assertEqual(self.page.evaluate("() => document.getElementById('termSibs').offsetHeight"), 0)

    def crowd_wa_t1(self):
        """Four more terminals in wA:t1, all of them AFTER the open pane in herdr's order, so the row
        overflows a phone with the open chip off the right edge of it."""
        self.page.evaluate("""() => {
          shellPanes.push(...['p4', 'p5', 'p6', 'p7'].map((id, i) => ({
            pane_id: 'wA:' + id, label: 'a-longer-shell-name-' + id, cwd: '/x', project: 'api',
            host: 'local', workspace_id: 'wA', tab_id: 'wA:t1', order: 20 + i})));
          activePane = null;
          openTerminal('wA:pH');
        }""")

    def test_the_open_pane_is_scrolled_onto_the_screen_in_a_row_that_overflows(self):
        """The chip that must not be off screen is the one saying where you are. It sits after every
        earlier pane, which is exactly where a row scrolled to 0 cannot show it."""
        self.crowd_wa_t1()
        seen = self.page.evaluate("""() => {
          const box = document.getElementById('termSiblings');
          const chip = box.querySelector('[data-sib-id][aria-current="true"]');
          const c = chip.getBoundingClientRect(), b = box.getBoundingClientRect();
          return {overflows: box.scrollWidth > box.clientWidth + 1,
                  inside: c.left >= b.left - 1 && c.right <= b.right + 1};
        }""")
        self.assertTrue(seen["overflows"], "the row did not overflow, so nothing was proved")
        self.assertTrue(seen["inside"], "the chip you are standing in is off screen")

    def test_the_open_pane_is_never_placed_under_the_pinned_name(self):
        """A name pinned at the left edge is painted OVER the chips scrolling beneath it, so a reveal
        that put the open pane hard against that edge would hide it behind its own tab's name. Either
        the name fits in the flow beside it, or the pane is placed to the right of the name's width --
        both are `elementFromPoint` on the chip's own left edge coming back as the chip."""
        self.crowd_wa_t1()
        for pane in ("wA:pH", "wA:p7", "wA:p2"):
            self.page.evaluate("p => { activePane = null; openTerminal(p); }", pane)
            own = self.page.evaluate("""() => {
              const chip = document.querySelector('#termSiblings [data-sib-id][aria-current="true"]');
              const r = chip.getBoundingClientRect();
              const hit = document.elementFromPoint(r.left + 3, r.top + r.height / 2);
              return !!(hit && hit.closest('.term-sib') === chip);
            }""")
            self.assertTrue(own, f"{pane}'s chip is under something else")

    def test_the_pinned_name_is_the_group_at_the_left_edge_all_the_way_along(self):
        """The whole reason one scroller is safe. In a shared scroller the scroll that reveals the
        open pane drove the tab level off the left edge -- measured at 390px in a 3-tab space, what
        was left standing there was a SIBLING tab, reading as the breadcrumb of the tab you were in.
        A name that sticks inside its own group cannot be the wrong one: it belongs to the panes
        beside it. Swept across the whole scroll range, 25px at a time."""
        self.crowd_wa_t1()
        sweep = self.page.evaluate("""() => {
          const strip = document.getElementById('termSiblings');
          const b = strip.getBoundingClientRect();
          const out = [];
          for (let x = 0; x <= strip.scrollWidth - strip.clientWidth; x += 25) {
            strip.scrollLeft = x;
            const hit = document.elementFromPoint(b.left + 3, b.top + b.height / 2);
            const chip = hit && hit.closest && hit.closest('.term-sib[data-sib-tab]');
            // Which group actually owns that edge: the last one whose box starts left of it.
            const owner = [...strip.querySelectorAll('.sib-group')]
              .filter(g => g.getBoundingClientRect().left <= b.left + 4).pop();
            if (chip) out.push([chip.closest('.sib-group') === owner, Math.round(x)]);
          }
          return out;
        }""")
        self.assertGreater(len(sweep), 4, "the row did not overflow far enough to prove anything")
        self.assertEqual([s for s in sweep if not s[0]], [],
                         "a name was pinned over another group's panes")

    def test_a_pinned_name_is_opaque(self):
        """`.term-sib` sets `background: none` at the same specificity and later in the file, so
        `.sib-tab` alone lost -- and a transparent pinned chip lets the panes scroll through it,
        which renders as two names on top of each other."""
        self.page.evaluate("openTerminal('wA:pH')")
        fills = self.page.evaluate("""() => [
          getComputedStyle(document.querySelector('#termSiblings [data-sib-tab="wA:t2"]')).backgroundColor,
          getComputedStyle(document.querySelector('#termSiblings [data-sib-tab="wA:t1"]')).backgroundColor,
          getComputedStyle(document.querySelector('#termSiblings [data-sib-tab="wA:t2"]')).position]""")
        self.assertNotIn(fills[0], ("rgba(0, 0, 0, 0)", "transparent"),
                         "an unpinned tab name is see-through")
        self.assertNotEqual(fills[1], fills[0], "aria-current's fill lost to the sticky background")
        self.assertEqual(fills[2], "sticky")

    def test_the_row_contains_its_overscroll(self):
        """The same rule .term-content carries: at either end of a horizontal drag the chain reaches
        the document, and the browser reads it as the gesture that unloads the app. It travels with
        the scroll, so it is on the box that does the scrolling."""
        self.page.evaluate("openTerminal('wA:pH')")
        self.assertEqual(
            self.page.evaluate("() => getComputedStyle(document.getElementById('termSiblings'))"
                               ".overscrollBehaviorX"),
            "contain")

    def test_the_history_panel_still_covers_everything_under_the_header(self):
        """Both rows are in normal flow, so a panel opened over the output covers them too -- the
        same geometry the panel already had, which is why positionHistoryPanel is untouched."""
        self.page.evaluate("openTerminal('wA:pH'); toggleHistory()")
        for sel in ("#termSiblings", "#termSibs"):
            covered = self.page.evaluate("""sel => {
              const s = document.querySelector(sel).getBoundingClientRect();
              const el = document.elementFromPoint(s.left + s.width / 2, s.top + s.height / 2);
              return document.getElementById('termHistory').contains(el);
            }""", sel)
            self.assertTrue(covered, f"{sel} was reachable through the history panel")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebStripScrollTests(unittest.TestCase):
    """A sideways scroll is the reader's, and it survives a rebuild they did not ask for.

    Three rows on this page scroll sideways and are rebuilt from every `agents` snapshot -- the
    Spaces strip, the Tabs strip and the session's sibling row. `innerHTML` / `replaceChildren`
    throw away the elements holding the offset (or collapse the scrollWidth under them, which the
    browser answers by clamping scrollLeft to 0), so all three snapped back to the beginning every
    two seconds: the space you had scrolled across the strip to reach walked back off the screen
    under your thumb, and the sibling row then pulled the OPEN pane back into view on top of that.

    Reached through `handleMessage`, not `render()`, because a snapshot is how it happens in the
    field and the two are only the same until something between them changes.
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
          activeWorkspace = null; activeTab = null; activePane = null;
          hideTerminal();
          handleMessage(s);
          window.__snap = s;
        }""", SNAPSHOT)

    # A strip has to OVERFLOW before it can prove anything, so every test says so first.
    def scroll_to_end(self, sel):
        moved = self.page.eval_on_selector(sel, """e => {
          if (e.scrollWidth <= e.clientWidth + 1) return null;
          e.scrollLeft = e.scrollWidth;          // the browser clamps it to this row's own end
          return Math.round(e.scrollLeft);
        }""")
        self.assertTrue(moved, f"{sel} did not overflow, so nothing would be proved")
        return moved

    def scroll_left(self, sel):
        return self.page.eval_on_selector(sel, "e => Math.round(e.scrollLeft)")

    def snapshot(self):
        """The 2s tick, as it arrives in the field."""
        self.page.evaluate("() => handleMessage(window.__snap)")

    def widen_spaces(self):
        """Four more spaces, each with an agent in it: the strip is only up when the panes span more
        than one space, and four chips fit across a phone."""
        self.page.evaluate("""s => {
          const snap = JSON.parse(JSON.stringify(s));
          ['payments-gateway', 'search-indexer', 'notifications', 'infra-terraform']
            .forEach((label, i) => {
              const id = 'wX' + i;
              snap.spaces.workspaces.push({workspace_id: id, label, number: 90 + i, focused: false,
                                           tab_count: 1, pane_count: 1, host: 'local'});
              snap.spaces.tabs.push({tab_id: id + ':t1', workspace_id: id, label: '1', number: 1,
                                     focused: false, pane_count: 1, host: 'local'});
              snap.agents.push({...s.agents[0], pane_id: id + ':pH',
                                workspace_id: id, tab_id: id + ':t1'});
            });
          window.__snap = snap;
          handleMessage(snap);
        }""", SNAPSHOT)

    # ------------------------------------------------------------- the herd list

    def test_the_spaces_strip_keeps_its_place_across_a_snapshot(self):
        self.widen_spaces()
        moved = self.scroll_to_end('#agents [data-strip="spaces"]')
        self.snapshot()
        self.assertEqual(self.scroll_left('#agents [data-strip="spaces"]'), moved,
                         "the Spaces strip walked back to the beginning on its own")

    def test_the_spaces_strip_keeps_its_place_when_the_space_view_redraws_it(self):
        """Picking a space is exactly when you have just scrolled the strip, and the space view
        draws its own copy -- so the offset is kept by WHAT the strip is, not by where it sits."""
        self.widen_spaces()
        moved = self.scroll_to_end('#agents [data-strip="spaces"]')
        self.page.evaluate("selectWorkspace('local|wX3')")
        self.assertEqual(self.scroll_left('#agents [data-strip="spaces"]'), moved)

    def test_the_tabs_strip_keeps_its_place_across_a_snapshot(self):
        self.page.evaluate("""s => {
          const snap = JSON.parse(JSON.stringify(s));
          for (let i = 0; i < 8; i++) {
            snap.spaces.tabs.push({tab_id: 'wA:tX' + i, workspace_id: 'wA', label: 'deploy-' + i,
                                   number: 10 + i, focused: false, pane_count: 1, host: 'local'});
          }
          window.__snap = snap;
          handleMessage(snap);
          selectWorkspace('local|wA');
        }""", SNAPSHOT)
        moved = self.scroll_to_end('#agents [data-strip="tabs"]')
        self.snapshot()
        self.assertEqual(self.scroll_left('#agents [data-strip="tabs"]'), moved,
                         "the Tabs strip walked back to the beginning on its own")

    # --------------------------------------------------------- the session view

    def crowd_the_tab(self, pane="wA:pH"):
        """Enough panes beside the open one that the pane level overflows a phone -- and all of them
        AFTER it in herdr's order, since the whole question is what happens to an offset that has the
        open chip off screen."""
        self.page.evaluate("""p => {
          const snap = JSON.parse(JSON.stringify(window.__snap));
          snap.panes.push(...['q4', 'q5', 'q6', 'q7'].map((id, i) => ({
            ...snap.panes[0], pane_id: 'wA:' + id, label: 'a-longer-shell-name-' + id,
            workspace_id: 'wA', tab_id: 'wA:t1', order: 20 + i})));
          window.__snap = snap;
          handleMessage(snap);
          openTerminal(p);
        }""", pane)

    def open_chip_is_on_screen(self):
        # `[data-sib-id]`, because a bare `[aria-current]` finds the TAB's name first -- and that one
        # is pinned to the left edge, so it would report every row as placed.
        return self.page.evaluate("""() => {
          const box = document.getElementById('termSiblings');
          const chip = box.querySelector('[data-sib-id][aria-current="true"]');
          const c = chip.getBoundingClientRect(), b = box.getBoundingClientRect();
          return c.left >= b.left - 1 && c.right <= b.right + 1;
        }""")

    def test_the_sibling_row_keeps_its_place_across_a_snapshot(self):
        """The reported bug: the row is rebuilt from every snapshot, so a reader scrolling it to
        read the name of a pane at the far end was snapped back to their own pane every two
        seconds -- first by the clamp, then by the auto-scroll on top of it."""
        self.crowd_the_tab()
        moved = self.scroll_to_end("#termSiblings")
        self.assertFalse(self.open_chip_is_on_screen(),
                         "the open chip is still in view, so nothing would pull the row back")
        self.snapshot()
        self.assertEqual(self.scroll_left("#termSiblings"), moved,
                         "the sibling row was dragged back to the open pane")

    def test_a_blocked_event_for_the_open_pane_does_not_drag_the_row_back(self):
        """`blocked` re-enters openTerminal for the pane already in front of you. A question
        arriving is the moment you are most likely to be reading the row, not the moment to move
        it."""
        self.crowd_the_tab()
        moved = self.scroll_to_end("#termSiblings")
        self.page.evaluate("""() => handleMessage({type: 'blocked', pane_id: 'wA:pH',
          agent: 'claude', project: 'api', prompt: 'ok?', options: ['Yes', 'No'], update: true})""")
        self.assertEqual(self.scroll_left("#termSiblings"), moved)

    def test_a_real_switch_still_puts_the_pane_you_moved_to_on_the_screen(self):
        """The guard must not become a way of switching the anchor off: entering a session, and
        moving to a pane at the other end of the row, are the two moments it MAY move."""
        self.crowd_the_tab()
        self.scroll_to_end("#termSiblings")
        self.page.evaluate("openTerminal('wA:p2')")   # the first chip, off screen at that end
        self.assertTrue(self.open_chip_is_on_screen(),
                        "the pane you switched to was left off screen")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
