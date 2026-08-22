"""Tests for the history panel's renderers in web/index.html.

The page is one file with no build step, so its markdown and diff renderers are exercised where
they actually run: a real browser, loading the real file over `file://`. The only page error that
load produces is the WebSocket failing to open without a relay, which is expected here.

Skipped, not failed, when playwright or a chromium build is missing -- the rest of the suite has to
keep running on a machine that has neither.
"""
import json
import os
from pathlib import Path
import unittest


PAGE = (Path(__file__).resolve().parents[1] / "web" / "index.html").as_uri()

# Playwright's own download location. A system chromium works too, hence the env override.
CHROME_CANDIDATES = [
    os.environ.get("HERDR_TEST_CHROME", ""),
    os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"),
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]


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
class WebRendererTests(unittest.TestCase):
    """One browser for the whole class: the page is static, and a launch costs more than the tests."""

    @classmethod
    def setUpClass(cls):
        cls._playwright = sync_playwright().start()
        cls._browser = cls._playwright.chromium.launch(executable_path=_chrome())
        cls.page = cls._browser.new_page()
        cls.page.goto(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._playwright.stop()

    def md(self, source):
        """The DOM mdFragment builds, as nested {tag, text, kids} dicts."""
        return self.page.evaluate(
            """(() => {
              const host = document.createElement('div');
              host.appendChild(mdFragment(SOURCE));
              const walk = node => node.nodeType === 3
                ? {tag: '#text', text: node.data}
                : {tag: node.tagName.toLowerCase(), cls: node.className || '',
                   align: node.style.textAlign || '',
                   kids: [...node.childNodes].map(walk)};
              return [...host.childNodes].map(walk);
            })()""".replace("SOURCE", json.dumps(source)))

    def tags(self, source):
        """Every tag name mdFragment produced, depth-first -- structure without the prose."""
        seen = []

        def walk(nodes):
            for node in nodes:
                seen.append(node["tag"])
                walk(node.get("kids") or [])
        walk(self.md(source))
        return seen

    def text(self, source):
        return self.page.evaluate(
            """(() => {
              const host = document.createElement('div');
              host.appendChild(mdFragment(SOURCE));
              return host.textContent;
            })()""".replace("SOURCE", json.dumps(source)))

    # ------------------------------------------------------------------ inline

    def test_inline_code_bold_and_italic_become_elements(self):
        self.assertIn("code", self.tags("call `history()` first"))
        self.assertIn("strong", self.tags("this is **important**"))
        self.assertIn("em", self.tags("this is *slanted*"))

    def test_a_snake_case_identifier_is_not_emphasis(self):
        """Underscore emphasis is deliberately unsupported: agent output is full of these."""
        self.assertNotIn("em", self.tags("pass include_tools and file_truncated through"))
        self.assertEqual(self.text("pass include_tools and file_truncated through"),
                         "pass include_tools and file_truncated through")

    def test_a_shell_glob_is_not_swallowed_into_one_italic_run(self):
        source = "rename *.ts to *.tsx"
        self.assertNotIn("em", self.tags(source))
        self.assertEqual(self.text(source), source)

    def test_bold_wrapping_code_keeps_both(self):
        """Routine in agent prose, and a flat model rendered the backticks literally."""
        nodes = self.md("fixed in **`c6fe96`** on main")
        strong = next(n for n in nodes[0]["kids"] if n["tag"] == "strong")
        self.assertEqual([k["tag"] for k in strong["kids"]], ["code"])
        self.assertEqual(strong["kids"][0]["kids"][0]["text"], "c6fe96")

    def test_only_http_and_mailto_become_links(self):
        self.assertIn("a", self.tags("see [the docs](https://herdr.dev)"))
        self.assertIn("a", self.tags("write [us](mailto:x@example.com)"))
        bad = "[click](javascript:alert(1))"
        self.assertNotIn("a", self.tags(bad))
        # Not silently dropped either -- the reader still sees what was written.
        self.assertEqual(self.text(bad), bad)

    # ------------------------------------------------------------------ the escaping boundary

    def test_markup_in_a_transcript_stays_text(self):
        """The whole reason this renderer builds nodes instead of an HTML string."""
        for source in ('<img src=x onerror=alert(1)>',
                       '<script>alert(1)</script>',
                       '<div onclick="x">hi</div>',
                       '`<script>alert(1)</script>`',
                       '**<img src=x>**'):
            with self.subTest(source=source):
                tags = self.tags(source)
                for injected in ("img", "script", "div", "iframe"):
                    self.assertNotIn(injected, tags)
                self.assertIn("<", self.text(source))

    def test_a_fenced_block_is_verbatim(self):
        source = "```python\nif x < 1 and **y**:\n    pass\n```"
        nodes = self.md(source)
        self.assertEqual(nodes[0]["tag"], "pre")
        code = nodes[0]["kids"][0]
        self.assertEqual(code["tag"], "code")
        self.assertNotIn("strong", self.tags(source))
        self.assertEqual(code["kids"][0]["text"], "if x < 1 and **y**:\n    pass")

    def test_a_backtick_fence_is_not_closed_by_a_tilde_fence(self):
        source = "```\none\n~~~\ntwo\n```"
        self.assertEqual(self.text(source), "one\n~~~\ntwo")

    def test_an_unclosed_fence_still_renders_its_body(self):
        self.assertEqual(self.text("```\nstill here"), "still here")

    # ------------------------------------------------------------------ blocks

    def test_headings_stay_inside_the_panel_hierarchy(self):
        """An agent's `#` is a section of one message, so it must not outrank the panel title."""
        nodes = self.md("# Top\n\n### Third")
        self.assertEqual([n["tag"] for n in nodes], ["div", "div"])
        self.assertEqual([n["cls"] for n in nodes], ["md-h md-h1", "md-h md-h3"])

    def test_lists_rules_and_quotes(self):
        self.assertEqual([n["tag"] for n in self.md("- one\n- two")], ["ul"])
        self.assertEqual(len(self.md("- one\n- two")[0]["kids"]), 2)
        self.assertEqual([n["tag"] for n in self.md("1. one\n2. two")], ["ol"])
        self.assertEqual([n["tag"] for n in self.md("---")], ["hr"])
        self.assertEqual([n["tag"] for n in self.md("> quoted\n> lines")], ["blockquote"])
        self.assertEqual(self.text("> quoted\n> lines"), "quoted\nlines")

    def test_a_gfm_table_becomes_a_table_with_its_alignment(self):
        source = ("| tool | count |\n"
                  "|:-----|------:|\n"
                  "| Bash | 6109 |\n"
                  "| Edit | 1840 |")
        nodes = self.md(source)
        self.assertEqual(nodes[0]["cls"], "md-table")  # its own scroller, so the panel never pans
        table = nodes[0]["kids"][0]
        self.assertEqual(table["tag"], "table")
        head, body = table["kids"]
        self.assertEqual([c["align"] for c in head["kids"][0]["kids"]], ["left", "right"])
        self.assertEqual(len(body["kids"]), 2)
        self.assertEqual(body["kids"][1]["kids"][1]["kids"][0]["text"], "1840")

    def test_pipes_without_a_delimiter_row_are_prose(self):
        self.assertEqual([n["tag"] for n in self.md("a | b | c")], ["p"])

    def test_a_paragraph_keeps_its_newlines(self):
        """Agents wrap prose by hand, and re-flowing it loses the shape of the sentence."""
        self.assertEqual(self.text("first line\nsecond line"), "first line\nsecond line")

    # ------------------------------------------------------------------ diffs

    def diff(self, body):
        return self.page.evaluate(
            """(() => {
              const block = diffFragment(BODY);
              return [...block.children].map(row => ({cls: row.className,
                                                      mark: row.dataset.mark || '',
                                                      text: row.textContent}));
            })()""".replace("BODY", json.dumps(body)))

    def test_diff_lines_are_classed_and_the_marker_moves_to_a_gutter(self):
        rows = self.diff(" def a():\n-    return 1\n+    return 2")
        self.assertEqual([r["cls"] for r in rows],
                         ["diff-line ctx", "diff-line del", "diff-line add"])
        self.assertEqual([r["mark"] for r in rows], ["", "-", "+"])
        # The code keeps its own indentation instead of being shifted a column by the marker.
        self.assertEqual([r["text"] for r in rows],
                         ["def a():", "    return 1", "    return 2"])

    def test_a_removed_line_of_dashes_is_a_removal_not_a_header(self):
        rows = self.diff("---flag=1\n keep")
        self.assertEqual(rows[0]["cls"], "diff-line del")
        self.assertEqual(rows[0]["text"], "--flag=1")

    def test_a_hunk_gap_renders_as_a_gap(self):
        rows = self.diff("-one\n...\n+two")
        self.assertEqual(rows[1]["cls"], "diff-line gap")
        self.assertEqual(rows[1]["text"], "⋯")

    # ------------------------------------------------------------------ turns

    def turn(self, payload):
        return self.page.evaluate(
            """(() => {
              const node = historyTurnNode(TURN);
              return {tag: node.tagName.toLowerCase(), cls: node.className,
                      open: !!node.open, text: node.textContent,
                      head: node.querySelector('.tool-head, .msg-role')?.textContent || '',
                      tags: [...node.querySelectorAll('*')].map(e => e.tagName.toLowerCase()),
                      classes: [...node.querySelectorAll('*')].map(e => e.className)};
            })()""".replace("TURN", json.dumps(payload)))

    def test_a_person_and_the_agent_are_told_apart(self):
        user = self.turn({"uuid": "u1", "role": "user", "text": "does this work?",
                          "ts": "2026-08-22T09:41:00.000Z"})
        self.assertEqual(user["cls"], "msg user")
        self.assertIn("you", user["head"])
        self.assertIn("09:41", user["head"])
        agent = self.turn({"uuid": "a1", "role": "assistant", "text": "yes, **it does**"})
        self.assertEqual(agent["cls"], "msg assistant")
        self.assertIn("agent", agent["head"])
        self.assertIn("strong", agent["tags"])

    def test_a_plain_tool_call_is_one_row_and_does_not_open(self):
        node = self.turn({"uuid": "t1", "role": "tool", "text": "Bash(ls -la) → ok",
                          "tool": "Bash", "target": "ls -la"})
        self.assertEqual(node["tag"], "div")
        self.assertNotIn("details", node["tags"])
        self.assertIn("ls -la", node["text"])

    def test_a_file_edit_opens_into_its_diff_with_the_whole_count(self):
        node = self.turn({"uuid": "t2", "role": "tool", "text": "Edit(/repo/x.py)",
                          "tool": "Edit", "target": "/repo/relay/herdr_relay.py",
                          "diff": "-a\n+b", "added": 11, "removed": 19})
        self.assertEqual(node["tag"], "details")
        self.assertIn("+11", node["text"])
        self.assertIn("−19", node["text"])
        self.assertIn("diff-line add", node["classes"])

    def test_a_long_path_keeps_the_end_that_identifies_the_file(self):
        node = self.turn({"uuid": "t3", "role": "tool", "tool": "Read", "text": "Read(...)",
                          "target": "/home/odin/workspace/app-tools/herdr-remote-dev/relay/transcript.py"})
        self.assertIn("…/herdr-remote-dev/relay/transcript.py", node["text"])
        # A command is left alone -- there is no tail to prefer.
        plain = self.turn({"uuid": "t4", "role": "tool", "tool": "Bash", "text": "Bash(...)",
                           "target": "grep -rn foo /a/b/c"})
        self.assertIn("grep -rn foo /a/b/c", plain["text"])

    def test_a_clipped_diff_says_it_is_only_the_head(self):
        node = self.turn({"uuid": "t5", "role": "tool", "tool": "Write", "text": "Write(x)",
                          "target": "/repo/new.py", "diff": "+a\n+b", "added": 200, "removed": 0,
                          "diff_clipped": True})
        self.assertIn("+200", node["text"])
        self.assertIn("showing the first 2 lines", node["text"])

    def test_a_failed_call_looks_failed_and_says_why(self):
        node = self.turn({"uuid": "t6", "role": "tool", "tool": "Bash", "text": "Bash(false) ! x",
                          "target": "false", "error": True, "result": "exit status 1"})
        self.assertIn("failed", node["cls"])
        self.assertIn("exit status 1", node["text"])

    def test_an_open_diff_survives_a_re_render(self):
        """The filter re-renders on every keystroke; a diff snapping shut under you is worse than
        the state it saves."""
        opened = self.page.evaluate(
            """(async () => {
              const turn = {uuid: 'keep-me', role: 'tool', tool: 'Edit', text: 'Edit(x)',
                            target: '/repo/x', diff: '-a\\n+b', added: 1, removed: 1};
              historyOpen.clear();
              const first = historyTurnNode(turn);
              document.body.appendChild(first);
              first.open = true;                       // as a tap would
              await new Promise(done => setTimeout(done, 0));   // `toggle` is a queued task
              const again = historyTurnNode(turn);     // as the next render would
              first.remove();
              return {remembered: historyOpen.has('keep-me'), reopened: again.open};
            })()""")
        self.assertTrue(opened["remembered"])
        self.assertTrue(opened["reopened"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
