#!/usr/bin/env python3
"""Naming and placement for files a client uploads to an agent's machine.

The web app sends a file in chunks; the relay spools it and then puts it where the agent can read
it: a folder under `~/.cache/herdr-remote/uploads` on this host, or the same path under `$HOME` on a
remote one. Everything here is a pure helper with no relay state, so it can be tested alone.

Two rules shape the names. A client-supplied name never reaches a path or a remote shell word as
sent: `clean_name` reduces it to `A-Za-z0-9._-`, and the remote script refuses anything outside that
set again. And a folder is named by a hash, never by the pane id or the session ref, because both
contain characters (`:`) and values the operator did not choose.
"""
import hashlib
import json
import os
import re
import shutil
import stat

UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FOLDER_RE = re.compile(r"^[sp]-[0-9a-f]{16}$")
STORED_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,89}$")

CHUNK_BYTES = 256 * 1024
REMOTE_ROOT = "$HOME/.cache/herdr-remote/uploads"

NAME_MAX_CHARS = 80
FALLBACK_NAME = "file"
UNSAFE_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")
FOLDER_HASH_CHARS = 16
FOLDER_GLOB = "[sp]-" + "[0-9a-f]" * FOLDER_HASH_CHARS
FOLDER_MODE = 0o700
FILE_MODE = 0o600


def valid_upload_id(value):
    return isinstance(value, str) and UPLOAD_ID_RE.fullmatch(value) is not None


def clean_name(name):
    if not isinstance(name, str):
        return FALLBACK_NAME
    cleaned = UNSAFE_NAME_CHARS_RE.sub("_", name).lstrip(".")[:NAME_MAX_CHARS]
    return cleaned or FALLBACK_NAME


def ref_key(ref):
    if not isinstance(ref, dict):
        return None
    kind, value = ref.get("kind"), ref.get("value")
    if not isinstance(kind, str) or not isinstance(value, str) or not kind or not value:
        return None
    return f"{kind}:{value}"


def folder_for(pane_id, ref):
    """A session keeps one folder across pane ids; a pane with no session ref falls back to its id.

    The ref's `agent` field is left out of the key on purpose: it is metadata about the ref, and
    two reads of one session that disagree on it must still land in one folder.
    """
    return folder_for_key(pane_id, ref_key(ref))


def folder_for_key(pane_id, key):
    """`folder_for` from a ref already reduced by `ref_key`, which is what the poll keeps per host."""
    prefix, source = ("s", key) if key is not None else ("p", pane_id)
    return f"{prefix}-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:FOLDER_HASH_CHARS]}"


def stored_name(clean, token):
    return f"{token}-{clean}"


def place_local(spool_path, root, folder, name):
    """Copy, not move: LOG_DIR and ~/.cache can sit on different file systems."""
    target_dir = os.path.join(root, folder)
    os.makedirs(target_dir, mode=FOLDER_MODE, exist_ok=True)
    os.chmod(target_dir, FOLDER_MODE)
    final = os.path.join(target_dir, name)
    part = final + ".part"
    shutil.copyfile(spool_path, part)
    os.chmod(part, FILE_MODE)
    os.replace(part, final)
    os.remove(spool_path)
    return os.path.abspath(final)


def remote_write_script(folder, name):
    if not FOLDER_RE.fullmatch(folder) or not STORED_NAME_RE.fullmatch(name):
        raise ValueError("unsafe upload folder or name")
    return (
        f'umask 077; d="{REMOTE_ROOT}/{folder}"; '
        f'mkdir -p "$d" && cat > "$d/{name}.part" && mv "$d/{name}.part" "$d/{name}" '
        f"&& printf '%s\\n' \"$d/{name}\""
    )


LEDGER_STRING_FIELDS = ("host", "pane_id", "folder", "path")
LEDGER_OPTIONAL_STRING_FIELDS = ("remote", "session", "ref")


def valid_entry(entry):
    """A ledger entry is trusted only whole: its `folder` ends up in a recursive delete, so a field
    that is missing, mistyped or outside FOLDER_RE drops the entry rather than being repaired."""
    if not isinstance(entry, dict):
        return False
    if not all(isinstance(entry.get(key), str) for key in LEDGER_STRING_FIELDS):
        return False
    for key in LEDGER_OPTIONAL_STRING_FIELDS:
        if key not in entry or not (entry[key] is None or isinstance(entry[key], str)):
            return False
    at = entry.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    return FOLDER_RE.fullmatch(entry["folder"]) is not None


def load_ledger(path):
    """BLOCKING. A missing, unreadable or half-written file is an empty ledger: the sweep then has
    nothing to delete, and the TTL sweep still catches whatever was left behind."""
    try:
        with open(path) as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [entry for entry in loaded if valid_entry(entry)]


def save_ledger(path, entries):
    """BLOCKING. Temp file plus rename, so a crash mid-write cannot leave a half file behind."""
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(entries, handle)
    os.replace(tmp, path)


def ended_entries(entries, host, session, live):
    """The entries on this host and herdr session whose agent session is over.

    `live` maps each pane id still on that host to its `ref_key`. A pane that is gone ends its
    entries, and so does a pane whose ref changed or vanished, since the folder belonged to the old
    session. An entry written before the pane had a ref has nothing to compare, so it ends only with
    the pane itself.
    """
    ended = []
    for item in entries:
        if item["host"] != host or item["session"] != session:
            continue
        if item["pane_id"] not in live:
            ended.append(item)
        elif item["ref"] is not None and live[item["pane_id"]] != item["ref"]:
            ended.append(item)
    return ended


def delete_local_folders(root, folders):
    """BLOCKING. A symlink is unlinked, never followed: `rmtree` on one would delete its target."""
    for name in folders:
        if not isinstance(name, str) or not FOLDER_RE.fullmatch(name):
            continue
        target = os.path.join(root, name)
        try:
            mode = os.lstat(target).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            os.unlink(target)
        elif stat.S_ISDIR(mode):
            shutil.rmtree(target, ignore_errors=True)


def expire_local(root, ttl_seconds, now):
    """BLOCKING. Delete files older than the TTL from upload folders, then folders left empty.

    Only names matching FOLDER_RE directly under `root` are visited, and nothing is followed, so a
    stray directory or a symlink planted in the root is left alone.
    """
    try:
        folders = [e for e in os.scandir(root)
                   if FOLDER_RE.fullmatch(e.name) and e.is_dir(follow_symlinks=False)]
    except FileNotFoundError:
        return
    cutoff = now - ttl_seconds
    for folder in folders:
        for item in os.scandir(folder.path):
            if item.is_file(follow_symlinks=False) and item.stat(follow_symlinks=False).st_mtime < cutoff:
                os.unlink(item.path)
        try:
            os.rmdir(folder.path)
        except OSError:
            pass


def remote_delete_script(folders):
    """Every name is checked against FOLDER_RE before it is joined into the shell word list, so a
    value from the ledger file can never add a path, a flag or a second command."""
    if not all(isinstance(name, str) and FOLDER_RE.fullmatch(name) for name in folders):
        raise ValueError("unsafe upload folder")
    return f'cd "{REMOTE_ROOT}" 2>/dev/null || exit 0; rm -rf -- {" ".join(folders)}'


def remote_expire_script(ttl_minutes):
    """The shell globs spell out FOLDER_RE, hex digits included. `[sp]-*` would also match a folder
    like `s-notes` that the operator made under the root, and the sweep would delete its files.
    `int()` keeps the TTL a number, and `find` does not follow symlinks by default.
    """
    minutes = int(ttl_minutes)
    return (
        f'r="{REMOTE_ROOT}"; [ -d "$r" ] || exit 0; '
        f'find "$r" -mindepth 2 -maxdepth 2 -type f -path "$r/{FOLDER_GLOB}/*" -mmin +{minutes} -exec rm -f {{}} +; '
        f"find \"$r\" -mindepth 1 -maxdepth 1 -type d -name '{FOLDER_GLOB}' -empty -exec rmdir {{}} +"
    )
