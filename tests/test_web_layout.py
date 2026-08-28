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

    def test_output_wraps_on_phones_and_stays_pre_on_desktop(self):
        self.assertIn("white-space: pre-wrap", css_rule(self.page, ".term-content"))
        desktop = self.page.split("@media (min-width: 768px)", 1)[1]
        self.assertIn("white-space: pre;", css_rule(desktop, ".term-content"))


if __name__ == "__main__":
    unittest.main()
