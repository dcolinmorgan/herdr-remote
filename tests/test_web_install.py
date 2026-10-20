"""Chrome's own verdict on whether the web app can be installed, under the relay's real CSP.

test_web_pwa.py checks the manifest as a file. That passed while Android refused the app outright:
the CSP named no `manifest-src`, so the manifest fetch fell back to `default-src 'self'` and Chrome
never saw the manifest at all. Only a browser holding the header can say that, so this one asks
Chrome through CDP, on a page served over http with the header the relay sends.

Skipped, not failed, when playwright or a chromium build is missing.
"""
import json
import os
from pathlib import Path
import unittest

from test_herdr_relay import loaded_relay


WEB = Path(__file__).resolve().parents[1] / "web"
ORIGIN = "http://localhost:8399"

CHROME_CANDIDATES = [
    os.environ.get("HERDR_TEST_CHROME", ""),
    os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"),
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]

# A non-persistent playwright context is an incognito profile, and Chrome never installs from one.
# That error is the test rig's, not the app's.
RIG_ERRORS = {"in-incognito"}


def _chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


try:  # pragma: no cover - environment probe
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


# One browser for the file, one playwright for the process -- see test_web_keys.py.
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


def relay_policy():
    """The CSP and the extension -> (content type, cache) table, as the relay serves them."""
    with loaded_relay() as relay:
        return relay.WEB_APP_CSP, relay.WEB_ASSET_TYPES


def serve_web(route, csp, types):
    """Answer for web/ the way the relay does: the page carries the CSP, the files do not."""
    path = route.request.url[len(ORIGIN):].split("?", 1)[0]
    name = "index.html" if path in ("", "/") else path.lstrip("/")
    file = WEB / name
    if name == "index.html":
        headers = {"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": csp}
    elif file.is_file() and file.suffix in types:
        headers = {"Content-Type": types[file.suffix][0]}
    else:
        route.fulfill(status=404, body="")
        return
    route.fulfill(status=200, headers=headers, body=file.read_bytes())


class WebInstallTests(unittest.TestCase):
    def setUp(self):
        if "browser" not in _shared:
            self.skipTest("playwright or chromium is not available")
        self.page = _shared["browser"].new_page()
        self.addCleanup(self.page.close)
        csp, types = relay_policy()
        self.page.route(f"{ORIGIN}/**", lambda route: serve_web(route, csp, types))
        self.page.goto(f"{ORIGIN}/")
        self.cdp = self.page.context.new_cdp_session(self.page)

    def test_chrome_reads_the_manifest_under_the_relay_csp(self):
        found = self.cdp.send("Page.getAppManifest")
        self.assertEqual(found.get("errors"), [])
        self.assertEqual(json.loads(found["data"])["name"], "herdr-remote")

    def test_chrome_reports_no_installability_error_of_the_app_s_own(self):
        found = self.cdp.send("Page.getInstallabilityErrors")["installabilityErrors"]
        errors = {error["errorId"] for error in found}
        self.assertEqual(errors - RIG_ERRORS, set())


if __name__ == "__main__":
    unittest.main()
