"""The web app's installable identity: manifest, favicons, and the routes that serve them.

No browser needed, on purpose. What breaks here is never rendering -- it is a manifest that
claims a size the file does not have, or an icon nobody remembered to add to the relay's route
allowlist. Both are statements about files and tables, so they are checked as such and this file
runs in the plain unittest pass rather than behind playwright.

The four PNGs are the reason this file exists. A reviewer cannot diff a binary, so the icons
arrive with assertions instead: real PNG headers, the exact pixel dimensions the manifest
advertises, and a size ceiling, all read straight out of the committed bytes.
"""
import json
import pathlib
import re
import struct
import unittest

from test_herdr_relay import loaded_relay
import urllib.parse

from web_source import web_source

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
# The head tags are in index.html; the CSS is in app.css since the app was split, so anything
# asserting on a rule has to read the whole thing or it passes by looking in the wrong file.
APP = web_source()
RELAY = (ROOT / "relay" / "herdr_relay.py").read_text(encoding="utf-8")

# What each committed raster must actually be. Rendered from logo.svg; the sizes are dictated by
# who reads them -- 32 for browsers that ignore an svg favicon, 180 for the iOS home screen
# (apple-touch-icon has never supported svg), 192/512 for the manifest.
EXPECTED_ICONS = {
    "favicon-32.png": (32, 32),
    "apple-touch-icon.png": (180, 180),
    "icon-192.png": (192, 192),
    "icon-512.png": (512, 512),
}
# Generous, but it fails loudly if someone drops a photograph in here. Current total is ~46KB.
MAX_ICON_BYTES = 64 * 1024


def png_size(path):
    """(width, height) from a PNG's IHDR, or raise if it is not a PNG at all."""
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path.name} is not a PNG")
    if raw[12:16] != b"IHDR":
        raise AssertionError(f"{path.name} has no IHDR where one must be")
    return struct.unpack(">II", raw[16:24])


def manifest():
    """The manifest, parsed out of the data: URI the page declares it with."""
    match = re.search(r'<link rel="manifest" href="data:application/json,([^"]+)"', INDEX)
    assert match, "the page declares no manifest"
    # The href is HTML-escaped (&quot;) and percent-encoded (%23 for #); undo both, in that order.
    body = match.group(1).replace("&quot;", '"').replace("&amp;", "&")
    return json.loads(urllib.parse.unquote(body))


def declared_icon_paths():
    """Every local path the page or the service worker points at an icon with."""
    paths = {icon["src"] for icon in manifest()["icons"]}
    paths |= set(re.findall(r'<link rel="(?:icon|apple-touch-icon)"[^>]*href="([^"]+)"', INDEX))
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    paths |= set(re.findall(r"(?:icon|badge):\s*'([^']+)'", sw))
    return {p for p in paths if p.startswith("/")}


class WebManifestTests(unittest.TestCase):
    def test_the_manifest_meets_chrome_s_install_criteria(self):
        """Chrome offers to install only with name, start_url, display and a 192 + a 512 icon."""
        data = manifest()
        self.assertTrue(data.get("name"))
        self.assertTrue(data.get("short_name"))
        self.assertEqual(data.get("display"), "standalone")
        # Without start_url the prompt never appears, which is the whole bug this began as.
        self.assertEqual(data.get("start_url"), "/")
        sizes = {icon.get("sizes") for icon in data["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)

    def test_one_icon_is_maskable_so_android_does_not_letterbox_it(self):
        purposes = " ".join(icon.get("purpose", "") for icon in manifest()["icons"])
        self.assertIn("maskable", purposes)

    def test_the_maskable_icon_is_a_raster(self):
        """Android crops maskables to a circle; the svg bleeds off canvas and shows a cut edge."""
        maskable = [i for i in manifest()["icons"] if "maskable" in i.get("purpose", "")]
        self.assertTrue(maskable)
        for icon in maskable:
            self.assertEqual(icon["type"], "image/png", icon["src"])

    def test_the_page_declares_a_favicon_at_all(self):
        """Absent any rel=icon a browser falls back to /favicon.ico, which does not exist here."""
        rels = re.findall(r'<link rel="(icon|apple-touch-icon)"', INDEX)
        self.assertIn("icon", rels)
        # apple-touch-icon has never supported svg, so iOS needs its own raster or it
        # screenshots the page onto the home screen instead.
        self.assertIn("apple-touch-icon", rels)


class InstalledChromeTests(unittest.TestCase):
    """What the installed window looks like around the page, rather than inside it."""

    def test_the_frame_colour_follows_the_os(self):
        """A manifest theme_color is one fixed value; only a meta can carry a media query."""
        metas = re.findall(
            r'<meta name="theme-color" content="(#[0-9a-fA-F]{3,8})"'
            r' media="\(prefers-color-scheme: (light|dark)\)">', INDEX)
        schemes = {scheme for _, scheme in metas}
        self.assertEqual(schemes, {"light", "dark"},
                         "both schemes need their own theme-color, or one of them borrows the other's")
        light = next(colour for colour, scheme in metas if scheme == "light")
        dark = next(colour for colour, scheme in metas if scheme == "dark")
        self.assertNotEqual(light.lower(), dark.lower(), "two identical values follow nothing")

    def test_the_page_fills_the_display_it_asked_to_own(self):
        """black-translucent without viewport-fit=cover is where the white iOS edges came from."""
        viewport = re.search(r'<meta name="viewport" content="([^"]+)"', INDEX)
        self.assertIsNotNone(viewport)
        self.assertIn("viewport-fit=cover", viewport.group(1))

    def test_the_ua_canvas_is_told_which_scheme_it_is_in(self):
        """Without color-scheme the UA paints scrollbars and controls light under a dark page."""
        self.assertRegex(APP, r":root \{[^}]*color-scheme: light dark;")
        # And html itself is painted: with viewport-fit=cover the canvas behind body shows.
        self.assertRegex(APP, re.compile(r"^html \{[^}]*background: var\(--bg\)", re.M))

    def test_no_safe_area_fallback_doubles_as_a_minimum(self):
        """`env(x, 8px)` is a FALLBACK. Where the inset exists and is 0, the 8px silently goes.

        Every inset here must fall back to 0px; padding that also wants a floor has to add the
        two together (`calc(8px + env(..., 0px))`) rather than hide the floor in the fallback.
        """
        bad = [m for m in re.findall(r"env\(safe-area-inset-[a-z]+\s*,\s*([^)]+)\)", APP)
               if m.strip() not in {"0px", "0"}]
        self.assertEqual(bad, [], f"safe-area fallbacks that swallow a minimum: {bad}")


class WebIconFileTests(unittest.TestCase):
    def test_every_committed_icon_is_the_size_it_claims_to_be(self):
        for name, expected in EXPECTED_ICONS.items():
            with self.subTest(icon=name):
                path = WEB / name
                self.assertTrue(path.is_file(), f"{name} is missing")
                self.assertEqual(png_size(path), expected)
                self.assertLess(path.stat().st_size, MAX_ICON_BYTES)

    def test_the_manifest_never_claims_a_size_the_file_does_not_have(self):
        """The bug this catches shipped once already: 192x192 and 512x512 pointing at logo.svg."""
        for icon in manifest()["icons"]:
            if icon.get("type") != "image/png":
                continue
            with self.subTest(icon=icon["src"]):
                path = WEB / icon["src"].lstrip("/")
                self.assertTrue(path.is_file(), f"{icon['src']} is declared but not committed")
                width, height = png_size(path)
                self.assertEqual(icon["sizes"], f"{width}x{height}")

    def test_nothing_points_at_a_file_that_is_not_there(self):
        for declared in sorted(declared_icon_paths()):
            with self.subTest(path=declared):
                self.assertTrue((WEB / declared.lstrip("/")).is_file(),
                                f"{declared} is referenced but not committed")


class RelayIconRouteTests(unittest.TestCase):
    """The relay serves web/ too, and it serves only what two tables name.

    This is the failure that keeps coming back: add an icon, ship it to Cloudflare Pages where
    every file in web/ is public, and never notice that over the relay it 404s -- so the tab
    favicon and the installed app's icon are missing for exactly the users on a tunnel.
    """

    def test_every_icon_the_page_asks_for_is_routed_and_public(self):
        """Asked of web_asset itself, which replaced the two hand-maintained route tables.

        Same claim as before -- an icon the page declares has to be reachable over the relay, not
        only on Pages -- but now it is a question the resolver answers rather than a grep of a
        list, so adding an icon can no longer break it.
        """
        with loaded_relay() as relay:
            for declared in sorted(declared_icon_paths()):
                with self.subTest(path=declared):
                    self.assertIsNotNone(relay.web_asset(declared),
                                         f"{declared} is not served by the relay")

    def test_the_icons_are_served_as_png(self):
        with loaded_relay() as relay:
            for name in EXPECTED_ICONS:
                with self.subTest(icon=name):
                    hit = relay.web_asset(f"/{name}")
                    self.assertIsNotNone(hit)
                    self.assertEqual(hit[1], "image/png")


if __name__ == "__main__":
    unittest.main()
