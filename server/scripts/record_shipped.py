"""Regenerate `server/shipped.json` — every version of a bundled file Kith has ever released.

Run this when cutting a release, after tagging the previous one:

    .venv/bin/python scripts/record_shipped.py

## Why a file and not a computation

`infra/seed.py` has to answer one question about a persona fragment sitting in someone's data
directory: *did we write that, or did they?* A hash of what the current build ships answers it
only for someone already on the current build. Everyone else is holding bytes from a release
that is no longer anywhere on their machine — the bundle was replaced by the update — so the
only way to recognise those bytes is to have written them down while we still had them.

Git has them, which is what this reads. But git is not on the machine that runs the app, and
`./check` is deliberately runnable on a tree built from `git ls-files` alone, so the answer is
committed as data rather than derived at runtime or at test time.

## What it does not contain

The current working tree. The question this file answers is "was this an *older* build's
bytes", and `seed.py` compares against what it is shipping separately. Including HEAD would
make every brand-new fragment look like one an earlier release had already placed — and a
fragment that looks previously-shipped is one that is never delivered to anybody.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
#: The trees `seed.py` reconciles. Keys are written relative to `server/`, so one file covers
#: both and the keys match what `settings.BUNDLED_*_DIR` resolve to.
TREES = ("server/persona", "server/skills")
OUT = HERE / "shipped.json"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=HERE.parent, check=True).stdout


def _blob(sha: str) -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", sha], capture_output=True, cwd=HERE.parent, check=True
    ).stdout


def _table() -> dict:
    # Release tags only. A branch or a lightweight marker like `beta` is not something anyone
    # downloaded, and counting it as shipped would let an unreleased draft of a fragment be
    # mistaken for a user's own edit — which reads as "they changed it" and means we never
    # update it again.
    tags = sorted(t for t in _git("tag").split() if t.startswith("v"))
    if not tags:
        raise SystemExit("no v* tags — nothing has shipped yet")

    history: dict[str, set[str]] = {}
    for tag in tags:
        for line in _git("ls-tree", "-r", tag, "--", *TREES).splitlines():
            meta, path = line.split("\t", 1)
            key = path.split("/", 1)[1]  # "server/persona/00-who.md" -> "persona/00-who.md"
            history.setdefault(key, set()).add(hashlib.sha256(_blob(meta.split()[2])).hexdigest())

    return {
        "note": "Every version of a bundled file that has been released. See "
        "scripts/record_shipped.py and infra/seed.py.",
        "tags": tags,
        "paths": {key: sorted(shas) for key, shas in sorted(history.items())},
    }


def _rendered(table: dict) -> str:
    return json.dumps(table, indent=2) + "\n"


def main(argv: list[str]) -> int:
    """`--check` verifies rather than writes, which is what the release workflow runs.

    Forgetting to regenerate this costs nothing today and everything one release later: the
    files this build ships go unrecorded, so the release *after* it cannot recognise them, and
    every copy still holding them is reclassified as somebody's own work and stops receiving
    corrections for good. There is no symptom in between — which is why it is a gate and not a
    line in a checklist.
    """
    table = _table()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != _rendered(table):
            print(
                f"{OUT.name} is out of date — run `python scripts/record_shipped.py` and commit "
                f"the result ({len(table['paths'])} paths across {len(table['tags'])} releases).",
                file=sys.stderr,
            )
            return 1
        print(f"{OUT.name} is up to date ({len(table['paths'])} paths)")
        return 0

    OUT.write_text(_rendered(table), encoding="utf-8")
    print(f"{OUT.relative_to(HERE)}: {len(table['paths'])} paths across {len(table['tags'])} releases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
