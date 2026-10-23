"""The relay serves web/ as a directory now, so what it will and will not hand out is a test.

Two separate claims. That every file the page asks for is reachable -- which used to be a
hand-maintained table and is why an asset could work on Cloudflare Pages and 404 over a tunnel.
And that widening it to a directory did not also widen it into the filesystem, or past the token.
"""
import asyncio
import os
import pathlib
import re
import sys
import types
import unittest
from unittest import mock

from test_herdr_relay import _http_stubs, loaded_relay

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")


def referenced_assets():
    """Every same-origin asset index.html loads, as the request path a browser would send.

    The page writes these RELATIVE, so that it also opens from file:// -- which is how the
    browser tests load it. Served at `/`, a relative `js/state.js` is requested as `/js/state.js`,
    and that is the string the relay has to recognise.
    """
    found = set(re.findall(r'<link rel="stylesheet" href="([^"]+)"', INDEX))
    found |= set(re.findall(r'<script src="([^"]+)"></script>', INDEX))
    found |= set(re.findall(r'<link rel="(?:icon|apple-touch-icon|manifest)"[^>]*href="([^"]+)"', INDEX))
    paths = set()
    for ref in found:
        if ref.startswith(("http://", "https://", "data:")):
            continue
        paths.add(ref if ref.startswith("/") else "/" + ref.lstrip("./"))
    return paths


class WebAssetRoutingTests(unittest.TestCase):
    def test_every_asset_the_page_loads_is_served(self):
        """The failure this replaces: add a file to web/, forget the relay, ship it broken."""
        assets = referenced_assets()
        self.assertTrue(assets, "the page loads no external assets -- did the split regress?")
        with loaded_relay() as relay:
            for path in sorted(assets):
                with self.subTest(path=path):
                    self.assertIsNotNone(relay.web_asset(path), f"{path} is not served")

    def test_the_scripts_are_not_cached_forever(self):
        """app.css, js/*.js and the manifest change under a fixed name; immutable would pin them."""
        with loaded_relay() as relay:
            for path in sorted(referenced_assets()):
                if not path.endswith((".css", ".js", ".webmanifest")):
                    continue
                with self.subTest(path=path):
                    _, _, cache = relay.web_asset(path)
                    self.assertNotIn("immutable", cache)
                    self.assertIn("no-cache", cache)

    def test_fonts_and_rasters_still_get_a_year(self):
        with loaded_relay() as relay:
            hit = relay.web_asset("/HackNerdFont-Regular.woff2")
            self.assertIsNotNone(hit)
            self.assertIn("immutable", hit[2])

    def test_the_content_type_follows_the_extension(self):
        with loaded_relay() as relay:
            for path, expected in [("/app.css", "text/css"),
                                   ("/js/state.js", "text/javascript"),
                                   ("/logo.svg", "image/svg+xml"),
                                   ("/manifest.webmanifest", "application/manifest+json")]:
                with self.subTest(path=path):
                    self.assertIn(expected, relay.web_asset(path)[1])


class WebAssetContainmentTests(unittest.TestCase):
    """A directory rule is only as good as its floor."""

    ESCAPES = [
        "/../relay/herdr_relay.py",
        "/../../etc/passwd",
        "/js/../../relay/herdr_relay.py",
        "/js/./../app.css",              # rejected even though the target is legitimate
        "//etc/passwd",                  # empty first segment
        "/./app.css",
        "relay/herdr_relay.py",          # no leading slash at all
        "/js/",                          # trailing empty segment
    ]

    def test_nothing_reaches_outside_web(self):
        with loaded_relay() as relay:
            for path in self.ESCAPES:
                with self.subTest(path=path):
                    self.assertIsNone(relay.web_asset(path))

    def test_a_symlink_out_of_the_tree_is_refused(self):
        """Segment checks cannot see this one; only re-resolving the final path can."""
        with loaded_relay() as relay:
            link = WEB / "escape-probe.css"
            target = ROOT / "relay" / "herdr_relay.py"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable here")
            try:
                self.assertIsNone(relay.web_asset("/escape-probe.css"))
            finally:
                link.unlink()

    def test_an_extension_nobody_asked_for_is_not_served(self):
        with loaded_relay() as relay:
            for path in ["/index.html", "/sw.js.map", "/relay.log", "/notes.md", "/app"]:
                with self.subTest(path=path):
                    self.assertIsNone(relay.web_asset(path))

    def test_index_html_is_not_reachable_as_an_asset(self):
        """It is served further up, behind the token. This rule must not become a way past it."""
        with loaded_relay() as relay:
            self.assertIsNone(relay.web_asset("/index.html"))
            self.assertNotIn(".html", relay.WEB_ASSET_TYPES)

    def test_a_missing_file_is_a_miss_not_a_crash(self):
        with loaded_relay() as relay:
            self.assertIsNone(relay.web_asset("/js/nothing-here.js"))

    def test_a_non_string_path_is_refused(self):
        with loaded_relay() as relay:
            for path in (None, 5, [], {"path": "/app.css"}):
                with self.subTest(path=path):
                    self.assertIsNone(relay.web_asset(path))


class WebAssetAuthTests(unittest.TestCase):
    """A browser fetches the stylesheet and the scripts before it can authenticate.

    Asserted through process_request rather than against the source line that spells the rule:
    what matters is which requests the relay answers without a token, and the exemption has to
    stay narrow enough that index.html and a WebSocket handshake are not among them.
    """

    def answer(self, relay, path, headers=()):
        request = types.SimpleNamespace(
            path=path,
            headers=types.SimpleNamespace(raw_items=lambda: tuple(headers)),
        )
        with mock.patch.dict(sys.modules, _http_stubs(), clear=False):
            return asyncio.run(relay.process_request(None, request))

    def test_assets_are_exempt_from_the_token_but_the_app_is_not(self):
        with loaded_relay(relay_token="secret") as relay:
            for path in ("/app.css", "/js/cards.js", "/logo.svg"):
                with self.subTest(path=path):
                    self.assertEqual(self.answer(relay, path).status_code, 200)
            for path in ("/", "/index.html"):
                with self.subTest(path=path):
                    self.assertEqual(self.answer(relay, path).status_code, 401)

    def test_the_exemption_does_not_reach_a_websocket_handshake(self):
        """A socket is not a file fetch, and it lands on the surface that types into panes."""
        with loaded_relay(relay_token="secret") as relay:
            answer = self.answer(relay, "/app.css",
                                 (("upgrade", "websocket"), ("origin", "http://localhost:8375")))
            self.assertIsNotNone(answer)
            self.assertEqual(answer.status_code, 401)


if __name__ == "__main__":
    unittest.main()


class WebAppOriginTests(unittest.TestCase):
    """The page holds the relay token, so it runs no script that another origin serves."""

    def test_no_script_or_module_loads_from_another_origin(self):
        sources = {"index.html": INDEX}
        sources.update({f"js/{p.name}": p.read_text(encoding="utf-8") for p in (WEB / "js").glob("*.js")})
        external = re.compile(r"""(?:<script[^>]+src=|\bfrom\s+|\bimport\s*\()\s*["']https?://""")
        for name, text in sorted(sources.items()):
            with self.subTest(file=name):
                self.assertIsNone(external.search(text))

    def test_the_policy_allows_scripts_from_this_origin_only(self):
        with loaded_relay() as relay:
            directives = dict(
                part.strip().split(" ", 1) for part in relay.WEB_APP_CSP.split(";") if part.strip()
            )
            self.assertEqual(directives["script-src"], "'self' 'unsafe-inline'")
            self.assertEqual(directives["object-src"], "'none'")
            self.assertIn("https:", directives["connect-src"])
