"""Optional Web palettes: actual UI/history colors, strict input, persistence and recovery.

The warm light palette is a user-supplied example, not a new built-in theme. Nothing here reads
real sessions or contacts a relay. Browser prerequisites follow test_web_theme.py.
"""
import copy
import json
import unittest

from test_web_theme import CONTRAST, PAGE, PHONE, _chrome, sync_playwright


LIGHT_PALETTE = {
    "colorScheme": "light",
    "colors": {
        "bg": "#F3EAD3", "surface": "#EBDFC4", "border": "#A9997D",
        "text": "#3F382E", "muted": "#645949", "green": "#3F6040",
        "red": "#943D35", "blue": "#345F7A", "orange": "#7C5328",
        "on-accent": "#F3EAD3",
    },
}
_shared = {}


def setUpModule():  # noqa: N802
    if sync_playwright is None or _chrome() is None:
        return
    _shared["playwright"] = sync_playwright().start()
    _shared["browser"] = _shared["playwright"].chromium.launch(executable_path=_chrome())


def tearDownModule():  # noqa: N802
    if "browser" in _shared:
        _shared["browser"].close()
        _shared["playwright"].stop()
    _shared.clear()


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(_chrome() is None, "no chromium build available")
class WebCustomThemeTests(unittest.TestCase):
    def setUp(self):
        self.page = _shared["browser"].new_page(viewport=PHONE, color_scheme="dark")
        # No live reads, writes, or reconnects while exercising the settings and history UI.
        self.page.add_init_script("""window.WebSocket = class {
          constructor() { this.readyState = 0; }
          close() {} send() {}
        };""")
        self.page.goto(PAGE)
        self.page.evaluate("toggleSettings()")
        self.page.click(".theme-editor summary")

    def tearDown(self):
        self.page.close()

    def submit(self, palette=LIGHT_PALETTE):
        text = palette if isinstance(palette, str) else json.dumps(palette)
        # Set directly so an overlong input also exercises validation beyond the DOM maxlength.
        self.page.eval_on_selector("#themePalette", "(e, text) => e.value = text", text)
        self.page.click('button[onclick="applyCustomTheme()"]')

    def appearance(self):
        return self.page.evaluate("""() => {
          const root = document.documentElement, cs = getComputedStyle(document.body);
          return {bg: cs.backgroundColor, text: cs.color, scheme: getComputedStyle(root).colorScheme,
                  pref: root.dataset.theme ?? null,
                  metas: [...document.querySelectorAll('meta[name="theme-color"]')].map(m => m.content)};
        }""")

    def stored(self):
        return self.page.evaluate("""() => ({pref: localStorage.getItem('herdr_theme'),
          palette: localStorage.getItem('herdr_custom_theme')})""")

    def test_custom_is_opt_in_and_the_default_template_is_usable(self):
        self.assertTrue(self.page.is_disabled('[data-theme-choice="custom"]'))
        self.assertEqual(self.appearance()["bg"], "rgb(10, 10, 10)")
        self.assertEqual(self.stored(), {"pref": None, "palette": None})
        template = json.loads(self.page.input_value("#themePalette"))
        self.assertEqual(template["colorScheme"], "dark")
        self.assertEqual(template["colors"]["bg"], "#0a0a0a")
        self.submit(template)
        self.assertEqual(self.appearance()["pref"], "custom")
        self.assertFalse(self.page.is_disabled('[data-theme-choice="custom"]'))

    def test_the_light_default_template_is_also_usable(self):
        self.page.evaluate("setTheme('light'); resetCustomTheme()")
        self.submit(self.page.input_value("#themePalette"))
        self.assertEqual(self.appearance()["bg"], "rgb(245, 245, 245)")
        self.assertEqual(self.appearance()["scheme"], "light")

    def test_the_ui_and_browser_frame_use_the_custom_palette(self):
        self.submit()
        self.assertEqual(self.appearance(), {
            "bg": "rgb(243, 234, 211)", "text": "rgb(63, 56, 46)", "scheme": "light",
            "pref": "custom", "metas": ["#f3ead3", "#f3ead3"],
        })
        self.assertEqual(self.page.eval_on_selector(".setting-group", "e => getComputedStyle(e).backgroundColor"),
                         "rgb(235, 223, 196)")
        # The translucent app header must not retain the old neutral black/grey background.
        self.assertEqual(self.page.evaluate("""() => {
          const e = document.createElement('div');
          e.style.background = 'color-mix(in srgb, #f3ead3 72%, transparent)';
          document.body.appendChild(e);
          const same = getComputedStyle(e).backgroundColor ===
            getComputedStyle(document.querySelector('.header')).backgroundColor;
          e.remove(); return same;
        }"""), True)
        self.assertIn("saved", self.page.inner_text("#themeStatus"))

    def test_custom_survives_reload_and_is_applied_before_the_body_is_parsed(self):
        self.submit()
        self.page.add_init_script("""new MutationObserver((mutations, observer) => {
          if (!document.body) return;
          window.themeAtBody = {pref: document.documentElement.dataset.theme,
            bg: document.documentElement.style.getPropertyValue('--bg')};
          observer.disconnect();
        }).observe(document, {childList: true, subtree: true});""")
        self.page.reload()
        self.assertEqual(self.page.evaluate("window.themeAtBody"), {"pref": "custom", "bg": "#f3ead3"})
        self.assertEqual(self.appearance()["bg"], "rgb(243, 234, 211)")
        saved = json.loads(self.stored()["palette"])
        self.assertEqual(saved["colors"]["text"], "#3f382e")
        self.assertEqual(self.page.get_attribute('[data-theme-choice="custom"]', "aria-checked"), "true")

    def test_custom_does_not_follow_a_system_scheme_change(self):
        self.submit()
        before = self.appearance()
        self.page.emulate_media(color_scheme="light")
        self.assertEqual(self.appearance(), before)
        self.page.emulate_media(color_scheme="dark")
        self.assertEqual(self.appearance(), before)

    def test_every_builtin_mode_removes_overrides_but_keeps_the_saved_palette(self):
        self.submit()
        saved = self.stored()["palette"]
        for pref, expected in (("light", "rgb(245, 245, 245)"), ("dark", "rgb(10, 10, 10)"),
                               ("auto", "rgb(10, 10, 10)")):
            with self.subTest(pref=pref):
                self.page.evaluate("p => setTheme(p)", pref)
                self.assertEqual(self.appearance()["bg"], expected)
                self.assertEqual(self.page.evaluate("document.documentElement.getAttribute('style')"), "")
                self.assertEqual(self.stored()["palette"], saved)
                self.page.evaluate("setTheme('custom')")
                self.assertEqual(self.appearance()["bg"], "rgb(243, 234, 211)")
        self.page.evaluate("setTheme('auto')")
        self.page.emulate_media(color_scheme="light")
        self.assertEqual(self.appearance()["bg"], "rgb(245, 245, 245)")
        self.assertEqual(self.appearance()["metas"], ["#f5f5f5", "#0a0a0a"])

    def test_reset_clears_only_theme_settings(self):
        self.submit()
        self.page.evaluate("""() => {
          localStorage.setItem('herdr_relay_token', 'test-only-token');
          localStorage.setItem('herdr_sessions', '[]');
        }""")
        self.page.click('button[onclick="resetCustomTheme()"]')
        self.assertEqual(self.stored(), {"pref": None, "palette": None})
        self.assertIsNone(self.appearance()["pref"])
        self.assertEqual(self.appearance()["bg"], "rgb(10, 10, 10)")
        self.assertTrue(self.page.is_disabled('[data-theme-choice="custom"]'))
        self.assertEqual(self.page.evaluate("localStorage.getItem('herdr_relay_token')"), "test-only-token")
        self.assertEqual(self.page.evaluate("localStorage.getItem('herdr_sessions')"), "[]")
        self.page.reload()
        self.assertEqual(self.stored(), {"pref": None, "palette": None})

    def test_reset_does_not_unpin_an_active_builtin_theme(self):
        self.submit()
        self.page.evaluate("setTheme('light'); resetCustomTheme()")
        self.assertEqual(self.appearance()["pref"], "light")
        self.assertEqual(self.stored(), {"pref": "light", "palette": None})

    def test_invalid_json_and_unsafe_colors_leave_the_active_palette_and_storage_untouched(self):
        self.submit()
        baseline, stored = self.appearance(), self.stored()
        bad = ["{", "[]", "null", "\"text\"", " " * 4097]
        for key, value in (("colorScheme", "auto"), ("colors", {}), ("extra", "ignored?")):
            palette = copy.deepcopy(LIGHT_PALETTE)
            palette[key] = value
            bad.append(palette)
        for value in ("red", "#fff", "#f3ead380", "var(--text)", "url(https://invalid.test/x)",
                      "#F3EAD3; background: url(https://invalid.test/x)", 123, None):
            palette = copy.deepcopy(LIGHT_PALETTE)
            palette["colors"]["bg"] = value
            bad.append(palette)
        for token in ("--bg", "term-bg", "__proto__"):
            palette = copy.deepcopy(LIGHT_PALETTE)
            palette["colors"][token] = "#123456"
            bad.append(palette)
        for payload in bad:
            with self.subTest(payload=payload):
                self.submit(payload)
                self.assertEqual(self.appearance(), baseline)
                self.assertEqual(self.stored(), stored)
                self.assertEqual(self.page.get_attribute("#themePalette", "aria-invalid"), "true")
                self.assertTrue(self.page.inner_text("#themeStatus"))

    def test_bad_contrast_is_rejected_for_prose_muted_status_and_button_text(self):
        for token, color in (("text", "#F3EAD3"), ("muted", "#F3EAD3"),
                             ("green", "#F3EAD3"), ("on-accent", "#345F7A")):
            with self.subTest(token=token):
                palette = copy.deepcopy(LIGHT_PALETTE)
                palette["colors"][token] = color
                self.submit(palette)
                self.assertIsNone(self.appearance()["pref"])
                self.assertEqual(self.stored(), {"pref": None, "palette": None})
                self.assertIn("4.5:1", self.page.inner_text("#themeStatus"))

    def test_a_corrupt_saved_custom_palette_falls_back_without_breaking_the_app(self):
        for raw in ("{", "[]", " " * 4097, json.dumps({"colorScheme": "light", "colors": {}})):
            with self.subTest(raw=raw[:60]):
                self.page.evaluate("""raw => {
                  localStorage.setItem('herdr_theme', 'custom');
                  localStorage.setItem('herdr_custom_theme', raw);
                }""", raw)
                self.page.reload()
                self.assertIsNone(self.appearance()["pref"])
                self.assertEqual(self.appearance()["bg"], "rgb(10, 10, 10)")
                self.assertTrue(self.page.is_disabled('[data-theme-choice="custom"]'))
                self.assertEqual(self.page.evaluate("typeof toggleSettings"), "function")
        self.page.evaluate("localStorage.removeItem('herdr_custom_theme')")
        self.page.reload()
        self.assertIsNone(self.appearance()["pref"])

    def test_a_bad_unused_palette_does_not_discard_a_legacy_light_or_dark_pin(self):
        for pref in ("light", "dark"):
            self.page.evaluate("""pref => {
              localStorage.setItem('herdr_theme', pref);
              localStorage.setItem('herdr_custom_theme', '{');
            }""", pref)
            self.page.reload()
            self.assertEqual(self.appearance()["pref"], pref)

    def test_blocked_storage_still_allows_changes_and_reset_for_this_tab(self):
        self.page.evaluate("""() => {
          Storage.prototype.setItem = Storage.prototype.removeItem = () => {
            throw new DOMException('Storage blocked', 'QuotaExceededError');
          };
        }""")
        self.submit()
        self.assertEqual(self.appearance()["pref"], "custom")
        self.assertEqual(self.appearance()["bg"], "rgb(243, 234, 211)")
        self.assertIn("this tab only", self.page.inner_text("#themeStatus"))
        self.assertEqual(self.stored(), {"pref": None, "palette": None})
        self.page.evaluate("setTheme('light')")
        self.assertEqual(self.appearance()["bg"], "rgb(245, 245, 245)")
        self.page.evaluate("setTheme('custom'); resetCustomTheme()")
        self.assertIsNone(self.appearance()["pref"])
        self.assertEqual(self.appearance()["bg"], "rgb(10, 10, 10)")

    def test_palette_validation_also_survives_blocked_storage_reads_at_startup(self):
        self.page.add_init_script("""(() => {
          const get = Storage.prototype.getItem;
          Storage.prototype.getItem = function(key) {
            if (key === 'herdr_theme' || key === 'herdr_custom_theme')
              throw new DOMException('Storage blocked', 'SecurityError');
            return get.call(this, key);
          };
        })();""")
        self.page.reload()
        self.assertIsNone(self.appearance()["pref"])
        self.page.evaluate("setTheme('light')")
        self.assertEqual(self.appearance()["pref"], "light")

    def test_radio_keyboard_navigation_skips_disabled_custom_and_selects_one_choice(self):
        self.page.focus('[data-theme-choice="auto"]')
        self.page.keyboard.press("End")
        self.assertEqual(self.appearance()["pref"], "dark")
        self.submit()
        self.page.focus('[data-theme-choice="auto"]')
        self.page.keyboard.press("End")
        self.assertEqual(self.appearance()["pref"], "custom")
        self.page.keyboard.press("ArrowRight")
        self.assertIsNone(self.appearance()["pref"])
        self.assertEqual(self.page.evaluate("""() => [...document.querySelectorAll('[data-theme-choice]')]
          .filter(e => e.tabIndex === 0 && e.getAttribute('aria-checked') === 'true')
          .map(e => e.dataset.themeChoice)"""), ["auto"])

    def test_the_editor_is_labelled_and_does_not_overflow_phone_or_desktop_widths(self):
        self.assertEqual(self.page.get_attribute('label[for="themePalette"]', "for"), "themePalette")
        self.assertEqual(self.page.get_attribute("#themeStatus", "role"), "status")
        self.assertEqual(self.page.get_attribute("#themePalette", "aria-describedby"), "customThemeHint themeStatus")
        self.submit()
        for width in (320, 390, 844):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 844})
                self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                for button in self.page.locator(".theme-seg button, .theme-actions button").all():
                    self.assertTrue(button.evaluate("e => e.scrollWidth <= e.clientWidth + 1"))

    def test_custom_colors_apply_to_history_prose_tools_and_diffs(self):
        self.submit()
        self.page.evaluate("""() => {
          closePanel();
          activePane = 'fixture:p1';
          ws = {readyState: 1, send: () => {}};
          document.getElementById('terminalView').classList.add('active');
          toggleHistory();
          receiveHistory({title: 'Theme fixture', total: 3, messages: [
            {uuid: 'u', role: 'user', text: 'Does this work?'},
            {uuid: 'a', role: 'assistant', text: 'Yes, **it does**. See [docs](https://example.test).'},
            {uuid: 't', role: 'tool', tool: 'Edit', target: '/fixture/sample.py',
             text: 'Edit(sample.py)', diff: '-old\\n+new', added: 1, removed: 1}
          ]});
        }""")
        for selector, property_name, expected in (
            ("#termHistory", "backgroundColor", "rgb(235, 223, 196)"),
            (".msg.assistant .msg-text", "color", "rgb(63, 56, 46)"),
            (".msg-assistant", "color", "rgb(63, 96, 64)"),
            (".msg-text a", "color", "rgb(52, 95, 122)"),
            (".tool-name", "color", "rgb(63, 56, 46)"),
            (".stat-add", "color", "rgb(63, 96, 64)"),
            (".stat-del", "color", "rgb(148, 61, 53)"),
        ):
            with self.subTest(selector=selector):
                actual = self.page.eval_on_selector(selector, "(e, prop) => getComputedStyle(e)[prop]", property_name)
                self.assertEqual(actual, expected)
        self.assertEqual(self.page.locator(".diff-line.add, .diff-line.del").count(), 2)
        self.assertGreater(self.page.evaluate(CONTRAST, ".msg.user .msg-text"), 4.5)
        self.assertGreater(self.page.evaluate(CONTRAST, "#historyToolsBtn"), 4.5)

    def test_live_terminal_ansi_truecolor_and_background_are_unchanged(self):
        snapshot = """() => {
          const el = document.getElementById('termContent'), cs = getComputedStyle(el);
          const host = document.createElement('div');
          host.appendChild(ansiFragment('plain \\x1b[31mred\\x1b[0m \\x1b[38;2;12;34;56mrgb\\x1b[0m'));
          return {bg: cs.backgroundColor, text: cs.color, scheme: cs.colorScheme,
                  ansi: [...ANSI_COLORS], markup: host.innerHTML};
        }"""
        before = self.page.evaluate(snapshot)
        self.submit()
        self.assertEqual(self.page.evaluate(snapshot), before)
        self.assertEqual(before["bg"], "rgb(10, 10, 10)")
        self.assertIn("rgb(12, 34, 56)", before["markup"])

    def test_applying_or_resetting_a_palette_never_sends_to_the_relay(self):
        self.page.evaluate("""() => {
          window.themeMessages = [];
          ws = {readyState: 1, send: text => themeMessages.push(text)};
        }""")
        self.submit()
        self.page.evaluate("setTheme('dark'); setTheme('custom'); resetCustomTheme()")
        self.assertEqual(self.page.evaluate("window.themeMessages"), [])


if __name__ == "__main__":
    unittest.main()
