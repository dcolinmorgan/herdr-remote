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
import os
import re
import shutil

UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FOLDER_RE = re.compile(r"^[sp]-[0-9a-f]{16}$")
STORED_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,89}$")

CHUNK_BYTES = 256 * 1024
REMOTE_ROOT = "$HOME/.cache/herdr-remote/uploads"

NAME_MAX_CHARS = 80
FALLBACK_NAME = "file"
UNSAFE_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")
FOLDER_HASH_CHARS = 16
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
    key = ref_key(ref)
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
