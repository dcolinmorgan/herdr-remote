"""Tests for relay/uploads.py -- naming and placement of files a client uploads to an agent's machine."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
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

    def test_a_ref_key_gives_the_same_folder_as_its_ref(self):
        ref = {"kind": "id", "value": "x", "agent": "claude"}
        self.assertEqual(uploads.folder_for_key("w1:p1", uploads.ref_key(ref)),
                         uploads.folder_for("w1:p1", ref))
        self.assertEqual(uploads.folder_for_key("w1:p1", None), uploads.folder_for("w1:p1", None))

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


def entry(**overrides):
    base = {"host": "local", "remote": None, "session": None, "pane_id": "w1:p1",
            "folder": "s-0123456789abcdef", "ref": "id:abc", "path": "/x", "at": 0.0}
    return {**base, **overrides}


class EndedEntriesTests(unittest.TestCase):
    def test_a_live_ref_survives(self):
        self.assertEqual(uploads.ended_entries([entry()], "local", None, {"w1:p1": "id:abc"}), [])

    def test_a_closed_pane_ends(self):
        self.assertEqual(uploads.ended_entries([entry()], "local", None, {}), [entry()])

    def test_a_changed_ref_ends(self):
        self.assertEqual(uploads.ended_entries([entry()], "local", None, {"w1:p1": "id:new"}), [entry()])

    def test_a_lost_ref_ends(self):
        self.assertEqual(uploads.ended_entries([entry()], "local", None, {"w1:p1": None}), [entry()])

    def test_an_entry_with_no_ref_survives_the_pane_gaining_one(self):
        e = entry(ref=None, folder="p-0123456789abcdef")
        self.assertEqual(uploads.ended_entries([e], "local", None, {"w1:p1": "id:abc"}), [])

    def test_an_entry_from_another_herdr_session_survives(self):
        e = entry(session="work")
        self.assertEqual(uploads.ended_entries([e], "local", "personal", {}), [])

    def test_an_entry_from_another_host_survives(self):
        e = entry(host="user@box")
        self.assertEqual(uploads.ended_entries([e], "local", None, {}), [])


class LedgerTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "uploads.json")
            entries = [entry(), entry(remote="user@box", session="work", ref=None,
                                      folder="p-fedcba9876543210", at=12.5)]
            uploads.save_ledger(path, entries)
            self.assertEqual(uploads.load_ledger(path), entries)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_a_bad_folder_is_dropped(self):
        for folder in ("../x", "s-0123456789abcdef\n"):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "uploads.json")
                uploads.save_ledger(path, [entry(folder=folder), entry()])
                self.assertEqual(uploads.load_ledger(path), [entry()])

    def test_a_malformed_entry_is_dropped(self):
        bad = [entry(at=True), entry(at="1"), entry(pane_id=3), entry(path=None),
               entry(ref=5), entry(remote=5), entry(session=5), entry(host=None)]
        missing = entry()
        del missing["path"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "uploads.json")
            uploads.save_ledger(path, bad + [missing, "x", entry()])
            self.assertEqual(uploads.load_ledger(path), [entry()])

    def test_a_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(uploads.load_ledger(os.path.join(tmp, "nope.json")), [])

    def test_a_half_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "uploads.json")
            with open(path, "w") as handle:
                handle.write('[{"host"')
            self.assertEqual(uploads.load_ledger(path), [])

    def test_a_non_list_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "uploads.json")
            with open(path, "w") as handle:
                handle.write('{"host": "local"}')
            self.assertEqual(uploads.load_ledger(path), [])


class LocalSweepTests(unittest.TestCase):
    def make_file(self, root, folder, name, mtime):
        directory = os.path.join(root, folder)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "w") as handle:
            handle.write("x")
        os.utime(path, (mtime, mtime))
        return path

    def test_delete_removes_only_matching_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_file(tmp, "s-0123456789abcdef", "a.txt", 0)
            self.make_file(tmp, "keep-me", "a.txt", 0)
            uploads.delete_local_folders(tmp, ["s-0123456789abcdef", "keep-me"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "s-0123456789abcdef")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "keep-me")))

    def test_delete_refuses_a_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_file(tmp, "s-0123456789abcdef", "a.txt", 0)
            uploads.delete_local_folders(tmp, ["s-0123456789abcdef\n"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "s-0123456789abcdef")))

    def test_delete_ignores_a_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads.delete_local_folders(tmp, ["s-0123456789abcdef"])

    def test_delete_does_not_follow_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.join(tmp, "outside")
            os.makedirs(outside)
            kept = os.path.join(outside, "keep.txt")
            with open(kept, "w") as handle:
                handle.write("x")
            root = os.path.join(tmp, "root")
            os.makedirs(root)
            os.symlink(outside, os.path.join(root, "s-0123456789abcdef"))
            uploads.delete_local_folders(root, ["s-0123456789abcdef"])
            self.assertFalse(os.path.lexists(os.path.join(root, "s-0123456789abcdef")))
            self.assertTrue(os.path.exists(kept))

    def test_expire_removes_old_files_then_empty_folders(self):
        now = 1_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_file(tmp, "s-0123456789abcdef", "old.txt", now - 73 * 3600)
            new = self.make_file(tmp, "p-fedcba9876543210", "new.txt", now - 3600)
            uploads.expire_local(tmp, 72 * 3600, now)
            self.assertFalse(os.path.exists(old))
            self.assertFalse(os.path.exists(os.path.dirname(old)))
            self.assertTrue(os.path.exists(new))

    def test_expire_leaves_unmatched_names_alone(self):
        now = 1_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            stray = self.make_file(tmp, "notes", "old.txt", now - 100 * 3600)
            uploads.expire_local(tmp, 72 * 3600, now)
            self.assertTrue(os.path.exists(stray))

    def test_expire_does_not_follow_a_symlinked_folder(self):
        now = 1_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.join(tmp, "outside")
            kept = self.make_file(tmp, "outside", "old.txt", now - 100 * 3600)
            root = os.path.join(tmp, "root")
            os.makedirs(root)
            os.symlink(outside, os.path.join(root, "s-0123456789abcdef"))
            uploads.expire_local(root, 72 * 3600, now)
            self.assertTrue(os.path.exists(kept))

    def test_expire_with_no_root_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads.expire_local(os.path.join(tmp, "nope"), 3600, 1_000_000.0)


class RemoteSweepScriptTests(unittest.TestCase):
    def test_delete_script_names_each_folder(self):
        script = uploads.remote_delete_script(["s-0123456789abcdef", "p-fedcba9876543210"])
        self.assertIn("rm -rf -- s-0123456789abcdef p-fedcba9876543210", script)
        self.assertIn('cd "$HOME/.cache/herdr-remote/uploads" 2>/dev/null || exit 0', script)

    def test_delete_script_refuses_a_bad_folder(self):
        with self.assertRaises(ValueError):
            uploads.remote_delete_script(["s-0123456789abcdef", "../../"])

    def test_delete_script_refuses_a_trailing_newline(self):
        with self.assertRaises(ValueError):
            uploads.remote_delete_script(["s-0123456789abcdef\n"])

    def test_expire_script_uses_the_ttl(self):
        self.assertIn("-mmin +4320", uploads.remote_expire_script(4320))

    @unittest.skipUnless(shutil.which("sh") and shutil.which("find"), "needs sh and find")
    def test_expire_script_only_touches_upload_folders(self):
        old = 1_000_000.0
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, ".cache", "herdr-remote", "uploads")
            paths = {}
            for folder in ("s-0123456789abcdef", "s-notes"):
                os.makedirs(os.path.join(root, folder))
                paths[folder] = os.path.join(root, folder, "old.txt")
                with open(paths[folder], "w") as handle:
                    handle.write("x")
                os.utime(paths[folder], (old, old))
            subprocess.run(["sh", "-c", uploads.remote_expire_script(60)],
                           env={**os.environ, "HOME": home}, check=True)
            self.assertFalse(os.path.exists(paths["s-0123456789abcdef"]))
            self.assertTrue(os.path.exists(paths["s-notes"]))


if __name__ == "__main__":
    unittest.main()
