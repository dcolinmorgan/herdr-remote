#!/usr/bin/env python3
"""Keep the release version in sync across the build scripts, and check it against a tag.

Seven numbers in five files decide what a release is called and what the built-in updaters
compare against. They live apart because each build runs on its own machine with its own
toolchain, and they drift: as this was written herdi-win said 0.7.3 while herdi-mac said
0.7.5, and the two plugin manifests had sat at 0.5.0 through four releases.

Drift is not cosmetic. Both updaters take the version from the release tag and compare it
against the version compiled into the running app (herdi-win/Services/Updater.cs:112,
herdi-mac/Sources/Updater.swift:78), so an app built at 0.7.3 and shipped under tag v0.7.4
offers an update, installs it, and offers it again forever.

    check-versions.py               report what each file says
    check-versions.py 0.7.5         exit 1 unless every file agrees on 0.7.5
    check-versions.py --set 0.7.5   rewrite every one of them to 0.7.5
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).name

# (file, what it is, pattern whose group 1 is the version, how many components it carries)
SITES = [
    ("herdi-win/Herdi.Win.csproj", "<Version>", r"<Version>([\d.]+)</Version>", 3),
    ("herdi-win/Herdi.Win.csproj", "<AssemblyVersion>", r"<AssemblyVersion>([\d.]+)</AssemblyVersion>", 4),
    ("herdi-win/Herdi.Win.csproj", "<FileVersion>", r"<FileVersion>([\d.]+)</FileVersion>", 4),
    # Names the zips build.ps1 -Zip hands to the release; Updater matches assets by name.
    ("herdi-win/build.ps1", "$version", r"^\$version = '([\d.]+)'", 3),
    # Becomes CFBundleShortVersionString, which dmg.sh then reads back to name the DMG.
    ("herdi-mac/build.sh", "VERSION", r'^VERSION="([\d.]+)"', 3),
    # What `herdr plugin` reports for an installed relay. These two are the reason the
    # list is here rather than in herdi-mac/build.sh: a mac-only `sed` on a BSD-only flag
    # cannot keep a number that both a Linux relay and a Windows tray also ship.
    ("herdr-plugin.toml", "version", r'^version = "([\d.]+)"', 3),
    ("relay/herdr-plugin.toml", "version", r'^version = "([\d.]+)"', 3),
]


def render(version, parts):
    """0.7.5 as the file wants it: three components, or four with a trailing .0."""
    return version if parts == 3 else version + ".0"


def find(text, path, label, pattern):
    match = re.search(pattern, text, re.M)
    if match is None:
        sys.exit(f"error: no {label} found in {path} — the pattern in {SELF} needs updating")
    return match


def replace(text, match, new):
    """Splice a new version into group 1, leaving the rest of the matched text alone."""
    whole, start = match.group(0), match.start(0)
    rebuilt = whole[: match.start(1) - start] + new + whole[match.end(1) - start :]
    return text[: start] + rebuilt + text[match.end(0) :]


def main(argv):
    setting = bool(argv) and argv[0] == "--set"
    if setting:
        argv = argv[1:]
    wanted = argv[0] if argv else None
    if wanted is not None:
        wanted = wanted.lstrip("v")
        if not re.fullmatch(r"\d+\.\d+\.\d+", wanted):
            sys.exit(f"error: expected a version like 0.7.5, got {wanted!r}")
    elif setting:
        sys.exit(f"usage: {SELF} --set X.Y.Z")

    texts = {}
    rows = []
    for path, label, pattern, parts in SITES:
        if path not in texts:
            texts[path] = (ROOT / path).read_text(encoding="utf-8")
        match = find(texts[path], path, label, pattern)
        if setting:
            texts[path] = replace(texts[path], match, render(wanted, parts))
            rows.append((path, label, render(wanted, parts), True, None))
        else:
            found = match.group(1)
            expected = render(wanted, parts) if wanted else None
            rows.append((path, label, found, expected is None or found == expected, expected))

    width = max(len(f"{path} {label}") for path, label, *_ in rows)
    for path, label, found, ok, expected in rows:
        mark = "" if ok else f"  <- expected {expected}"
        print(f"{f'{path} {label}':<{width}}  {found}{mark}")

    if setting:
        for path, text in texts.items():
            (ROOT / path).write_text(text, encoding="utf-8")
        print(f"\nSet to {wanted}. Commit these, then tag v{wanted}.")
        return 0

    if wanted is None:
        # Nothing to check against, but files that disagree with each other are the
        # same bug one tag early, so say so rather than printing a tidy table.
        spread = {".".join(found.split(".")[:3]) for _, _, found, _, _ in rows}
        if len(spread) > 1:
            print(f"\nwarning: these do not agree: {', '.join(sorted(spread))}")
        return 0

    if any(not ok for *_, ok, _ in rows):
        sys.stdout.flush()
        print(
            f"\nerror: the files above disagree with the tag. Run `{SELF} --set {wanted}`,"
            "\ncommit, and move the tag — shipping a build whose own version differs from"
            "\nthe tag leaves every install updating in a loop.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
