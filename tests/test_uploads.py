"""Tests for relay/uploads.py -- naming and placement of files a client uploads to an agent's machine."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


UPLOADS_PATH = Path(__file__).resolve().parents[1] / "relay" / "uploads.py"


def load_uploads():
    spec = importlib.util.spec_from_file_location("herdr_uploads_under_test", UPLOADS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


uploads = load_uploads()


class CleanNameTests(unittest.TestCase):
    def test_keeps_safe_characters(self):
        self.assertEqual(uploads.clean_name("Screenshot_2026-10-20.png"), "Screenshot_2026-10-20.png")

    def test_path_parts_cannot_escape(self):
        self.assertEqual(uploads.clean_name("../../.zshrc"), "_.._.zshrc")

    def test_drops_leading_dots(self):
        self.assertFalse(uploads.clean_name(".env").startswith("."))

    def test_unicode_becomes_underscores(self):
        self.assertEqual(uploads.clean_name("截图.png"), "__.png")

    def test_empty_and_non_string_become_file(self):
        for value in ("", "...", None, 7):
            self.assertEqual(uploads.clean_name(value), "file")

    def test_is_at_most_80_characters(self):
        self.assertEqual(len(uploads.clean_name("a" * 300 + ".png")), 80)


class FolderTests(unittest.TestCase):
    def test_a_ref_gives_an_s_folder(self):
        ref = {"kind": "id", "value": "44b7b1b9-694a-40af-a311-b3a7e7e5a71c", "agent": "claude"}
        self.assertRegex(uploads.folder_for("w6:pJB", ref), r"^s-[0-9a-f]{16}$")

    def test_no_ref_gives_a_p_folder(self):
        self.assertRegex(uploads.folder_for("w6:pJB", None), r"^p-[0-9a-f]{16}$")

    def test_the_agent_field_does_not_change_the_folder(self):
        a = {"kind": "id", "value": "x", "agent": "claude"}
        b = {"kind": "id", "value": "x"}
        self.assertEqual(uploads.folder_for("w1:p1", a), uploads.folder_for("w1:p1", b))

    def test_ref_key_joins_kind_and_value(self):
        self.assertEqual(uploads.ref_key({"kind": "id", "value": "x"}), "id:x")

    def test_ref_key_is_none_for_a_missing_or_malformed_ref(self):
        for ref in (None, {}, {"kind": "id"}, {"value": "x"}, "id:x"):
            self.assertIsNone(uploads.ref_key(ref))

    def test_upload_ids(self):
        self.assertTrue(uploads.valid_upload_id("a1B2-c3_d4"))
        for bad in ("short", "x" * 65, "../etc/pass", None, 12345678, "abcdefgh\n"):
            self.assertFalse(uploads.valid_upload_id(bad))

    def test_stored_name_prefixes_the_token(self):
        self.assertEqual(uploads.stored_name("shot.png", "a3f9c0de"), "a3f9c0de-shot.png")


class PlaceLocalTests(unittest.TestCase):
    def spool(self, directory, content=b"data"):
        path = os.path.join(directory, "spool.bin")
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def test_lands_in_its_folder_with_private_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root")
            folder = "s-0123456789abcdef"
            spool = self.spool(tmp)
            placed = uploads.place_local(spool, root, folder, "a3f9c0de-shot.png")
            self.assertEqual(placed, os.path.join(root, folder, "a3f9c0de-shot.png"))
            self.assertTrue(os.path.isabs(placed))
            self.assertEqual(os.stat(os.path.join(root, folder)).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(placed).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(spool))
            self.assertEqual(os.listdir(os.path.join(root, folder)), ["a3f9c0de-shot.png"])

    def test_two_uploads_with_one_name_get_two_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root")
            folder = "p-0123456789abcdef"
            first = uploads.place_local(self.spool(tmp, b"one"), root, folder,
                                        uploads.stored_name("image.png", "aaaaaaaa"))
            second = uploads.place_local(self.spool(tmp, b"two"), root, folder,
                                         uploads.stored_name("image.png", "bbbbbbbb"))
            self.assertNotEqual(first, second)
            self.assertTrue(os.path.exists(first) and os.path.exists(second))


class RemoteScriptTests(unittest.TestCase):
    def test_write_script_quotes_and_prints_the_path(self):
        script = uploads.remote_write_script("s-0123456789abcdef", "a3f9c0de-shot.png")
        self.assertIn("umask 077", script)
        self.assertIn('"$HOME/.cache/herdr-remote/uploads/s-0123456789abcdef"', script)
        self.assertIn(".part", script)
        self.assertIn("printf '%s\\n' \"$d/a3f9c0de-shot.png\"", script)

    def test_write_script_refuses_an_unsafe_folder_or_name(self):
        with self.assertRaises(ValueError):
            uploads.remote_write_script("../x", "a.png")
        with self.assertRaises(ValueError):
            uploads.remote_write_script("s-0123456789abcdef", "a b;rm.png")

    def test_write_script_refuses_a_trailing_newline(self):
        with self.assertRaises(ValueError):
            uploads.remote_write_script("s-0123456789abcdef\n", "a.png")
        with self.assertRaises(ValueError):
            uploads.remote_write_script("s-0123456789abcdef", "a.png\n")


if __name__ == "__main__":
    unittest.main()
