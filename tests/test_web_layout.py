"""Guard the terminal layout against being re-pinned to a header height.

.terminal-view and .term-history were each positioned with a px offset
measured against .header / .term-header at the time they were written. Both
headers later grew when buttons gained 44px touch targets, and nothing tied
the constants back to them, so the offsets silently went stale. These checks
fail if such an offset comes back.
"""

import re
import unittest
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def css_rule(page, selector):
    """Body of the first `selector { ... }` rule, ignoring compound selectors."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", page)
    if not match:
        raise AssertionError(f"no `{selector} {{ ... }}` rule in index.html")
    return match.group(1)


class TerminalLayoutTests(unittest.TestCase):
    def setUp(self):
        self.page = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_panels_are_not_offset_by_a_hardcoded_header_height(self):
        for selector in (".terminal-view", ".term-history"):
            with self.subTest(selector=selector):
                self.assertNotRegex(css_rule(self.page, selector), r"top:\s*\d")

    def test_terminal_height_is_not_calculated_from_a_hardcoded_header_height(self):
        self.assertNotRegex(self.page, r"calc\(\s*100dvh\s*-\s*\d+px\s*\)")

    def test_focusable_fields_never_drop_below_16px(self):
        """iOS Safari zooms the page whenever a focused control is smaller than
        16px, and the zoom then pans the fixed panes out from under the
        viewport. This is a floor, not a design choice."""
        field_rule = re.compile(
            r"(?m)^[^\n{}]*\b(?:input|select|textarea)\b[^\n{}]*\{([^}]*)\}"
        )
        checked = 0
        for body in field_rule.findall(self.page):
            declared = re.search(r"font-size:\s*([\d.]+)(px|rem)", body)
            if not declared:
                continue
            checked += 1
            size = float(declared.group(1))
            if declared.group(2) == "rem":
                size *= 16
            self.assertGreaterEqual(size, 16, f"field under 16px: {body.strip()[:70]}")
        self.assertGreater(checked, 0, "no field font-size rules matched; selector drifted")

        # The command palette's search box is styled inline, out of reach of
        # any rule above.
        inline = re.search(r'id="cmdSearch"[^>]*font-size:\s*([\d.]+)px', self.page)
        self.assertIsNotNone(inline, "#cmdSearch lost its inline font-size")
        self.assertGreaterEqual(float(inline.group(1)), 16)

    def test_page_itself_never_pans_sideways(self):
        # overflow-x on <body> alone does not hold: the viewport scroller is
        # <html>, and overscroll-behavior stops a swipe inside .chip-strip or
        # .term-content from rubber-banding the document.
        html_rule = css_rule(self.page, "html")
        self.assertIn("overflow-x: hidden", html_rule)
        self.assertIn("overscroll-behavior: none", html_rule)
        self.assertIn("overscroll-behavior: none", css_rule(self.page, "body"))

    def test_output_wraps_on_phones_and_stays_pre_once_80_columns_fit(self):
        self.assertIn("white-space: pre-wrap", css_rule(self.page, ".term-content"))
        wide = self.page.split("@media (min-width: 640px)", 1)[1]
        self.assertIn("white-space: pre;", css_rule(wide, ".term-content"))


if __name__ == "__main__":
    unittest.main()
