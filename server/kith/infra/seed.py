"""Keeping the files Kith ships in step with the copies people own.

The persona and the bundled skills are shipped as files and then handed over: they are copied
into the data directory on first run and edited from the Settings screen ever after. That
hand-over is deliberate — a frozen build unpacks to a temporary directory that is deleted when
the app closes, so the fragments inside the bundle cannot be the ones anybody edits.

What it cost was every later change. The rule was "copy when the folder is absent", which is
the only rule available to code that cannot tell a file it wrote from a file somebody rewrote,
and it meant an update reached new installs only. The release this module was written for is
the plain case: the persona gained the paragraph that teaches `send_builder`, and `send_builder`
was that release's headline feature. Everyone already running Kith would have got the feature
and none of the sentence that makes him reach for it. In the same release `running-a-project`
stopped naming four tools that no longer exist — a fix whose entire value is for people who
already have the broken copy, delivered to precisely the people who do not.

Both halves of that are right, which is why this is not a flag. An update must not overwrite a
persona somebody has spent a month shaping, and an update that cannot deliver a correction is
not an update. Telling them apart needs one fact the old code never kept: **which bytes we
wrote.**

## The two records

`shipped.json` is every version of a bundled file that has been *released* — generated from the
release tags by `scripts/record_shipped.py`. It is what lets us recognise an untouched file
belonging to a build that is no longer on the machine, which is the situation of everybody who
is about to update.

`seeded.json`, in the data directory, is what this module last wrote there. It answers a
different question — *was this path ever placed here at all* — and only it can, because a path
that is missing is either one somebody deleted or one that has never existed for them, and the
two want opposite treatment.

## The four answers

    absent, never placed here and never released   it is new    -> write it
    absent, we placed it or a release carried it   they removed it -> leave it absent
    present, and the bytes are ones we shipped     untouched    -> write the new version
    present, and the bytes are nobody's but theirs theirs       -> leave it alone
    present, ours, and no longer shipped at all    retired      -> remove it

The fifth exists because a retired file is not inert. A skill's description sits in every
prompt, so the `drawing-a-canvas` skill that taught ```html fences as live pages would have kept
teaching it on every machine that already had it, long after the fence stopped running. Only a
file whose bytes are positively ours is removed; an edited one is theirs and stays.

The last one is the one that has to be wrong in the safe direction, and it is: anything this
module cannot positively recognise as its own is treated as somebody's work and not touched.
The cost of being wrong that way is a correction that does not land and can be applied by hand;
the cost the other way is somebody's writing destroyed by a background task on launch.

A data directory with no live folder at all is not reconciled file-by-file — it is a first run,
and everything is copied. Reconciling it instead would read every absent path as "released once,
so they must have deleted it" and produce an install with no persona.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from kith import settings

#: Never copied into a live folder, and never reasoned about. `.DS_Store` is macOS's, and the
#: caches are produced by running a skill's own scripts — none of them are ours to reconcile.
_IGNORED = {".DS_Store", "__pycache__", ".pytest_cache", ".ruff_cache"}


@dataclass
class Report:
    """What a sync did, for the log. Empty lists are the normal outcome of a second launch."""

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.updated or self.removed)

    def __str__(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.updated:
            parts.append(f"{len(self.updated)} updated")
        if self.removed:
            parts.append(f"{len(self.removed)} retired")
        if self.kept:
            parts.append(f"{len(self.kept)} left as edited")
        return ", ".join(parts) or "nothing to do"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in _IGNORED for part in path.relative_to(root).parts)
    )


def released() -> dict[str, list[str]]:
    """Every released version of every bundled file, by path. `{}` when the table is missing.

    Missing is survivable and means "recognise nothing", which collapses this module back to
    the behaviour it replaced: new files arrive, existing ones are left alone. That is the
    right way to lose this file — a build that forgot to carry it delivers less, rather than
    deciding an unrecognised persona is disposable.
    """
    try:
        loaded = json.loads(settings.SHIPPED_HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    paths = loaded.get("paths")
    return paths if isinstance(paths, dict) else {}


def _manifest_path(live: Path | None = None) -> Path:
    """Where the record of our own writes belongs: beside the tree it describes.

    It used to be one global path, read and written no matter which folder `sync` was actually
    reconciling — which is fine for the app, where there is only ever one, and quietly wrong
    everywhere else. Anything that syncs into a scratch directory (a test, a check, a script)
    recorded those writes against the *live* manifest, and the manifest's meaning is "we already
    placed this". So a bundled file that had never been installed anywhere real was marked as
    delivered, and the next boot skipped it. Permanently, and silently: nothing fails, the skill
    simply is not there.

    That is not hypothetical. It happened to `designing-a-screen` twice in one session — once
    from a one-off check and once from the persona test suite, whose fixture redirects the skills
    directory but could not redirect this.

    Deriving it from `live` makes the record follow the tree, so a sync into a temp folder writes
    a temp manifest and the real one is only touched by a real install.
    """
    if live is None:
        return settings.SEED_MANIFEST_PATH
    return live.parent / settings.SEED_MANIFEST_PATH.name


def _read_manifest(live: Path | None = None) -> dict[str, str]:
    try:
        loaded = json.loads(_manifest_path(live).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded.get("paths", {}) if isinstance(loaded, dict) else {}


def _write_manifest(paths: dict[str, str], live: Path | None = None) -> None:
    """Replace the manifest in one step.

    Written to a neighbour and renamed because a half-written manifest is worse than none: the
    entries that survived would be read as provenance on the next launch, and a truncated hash
    matches nothing, so every fragment it named would be reclassified as somebody's own edit
    and stop receiving updates forever. `os.replace` is atomic within a filesystem, which this
    is by construction.
    """
    target = _manifest_path(live)
    body = json.dumps(
        {
            "note": "What Kith placed in this folder, so an update can tell its own files from "
            "yours. Delete it and updates stop arriving for files you have edited.",
            "paths": dict(sorted(paths.items())),
        },
        indent=2,
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(body + "\n", encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        # The same bargain the rest of this module strikes: an unwritable data directory is
        # already fatal for the databases, and a sync that cannot record itself is a sync that
        # will be recomputed next launch, not one that breaks anything.
        pass


def sync(name: str, bundled: Path, live: Path) -> Report:
    """Reconcile one shipped tree against the copy at `live`. Returns what changed.

    `name` prefixes every key in both records ("persona", "skills"), so the two trees share one
    manifest and one history file and their keys cannot collide.
    """
    report = Report()
    if not bundled.is_dir() or bundled.resolve() == live.resolve():
        # The second case is a source checkout, where the folder Kith reads *is* the folder in
        # the repository. There is no copy to reconcile and syncing would mean the app rewriting
        # its own tracked files on launch.
        return report

    first_run = not live.exists()
    manifest = _read_manifest(live)
    history = released()
    touched = False

    bundled_keys: set[str] = set()
    for source in _files(bundled):
        relative = source.relative_to(bundled).as_posix()
        bundled_keys.add(f"{name}/{relative}")
        key = f"{name}/{relative}"
        target = live / relative
        try:
            shipped = source.read_bytes()
        except OSError:
            continue
        shipped_hash = digest(shipped)

        if first_run or not target.exists():
            # Never placed here by us, never carried by a release they could have had, and not
            # a first run — so it is genuinely new and they have not had the chance to refuse
            # it. Anything else absent is a removal, and a removal is an answer.
            if not first_run and (key in manifest or key in history):
                continue
            if _write(target, shipped):
                report.added.append(key)
                manifest[key] = shipped_hash
                touched = True
            continue

        try:
            current = digest(target.read_bytes())
        except OSError:
            continue
        if current == shipped_hash:
            # Already the shipped bytes. Recording it is the point of this branch: it is how a
            # file that predates the manifest earns provenance, so the *next* release can
            # update it.
            if manifest.get(key) != shipped_hash:
                manifest[key] = shipped_hash
                touched = True
            continue

        recognised = set(history.get(key, ()))
        if key in manifest:
            recognised.add(manifest[key])
        if current in recognised:
            if _write(target, shipped):
                report.updated.append(key)
                manifest[key] = shipped_hash
                touched = True
        else:
            # Theirs. The manifest entry is deliberately left as it was rather than moved to
            # their hash — it records what *we* last wrote, and overwriting it with their bytes
            # would make their own edit look like ours to the next release.
            report.kept.append(key)

    if not first_run and _retire(name, live, bundled_keys, manifest, history, report):
        touched = True

    if touched:
        _write_manifest(manifest, live)
    if report:
        # To `~/.kith/server.log`, which the shell pipes stdout into — silent on every launch
        # that changes nothing, and the one place somebody can check after an update whether a
        # correction actually landed on their machine. `flush` because stdout to a pipe is
        # block-buffered, and a line that appears an hour later is not a startup line.
        print(f"{name}: {report}", flush=True)
    return report


def _retire(
    name: str,
    live: Path,
    bundled_keys: set[str],
    manifest: dict[str, str],
    history: dict[str, list[str]],
    report: Report,
) -> bool:
    """Remove files we once shipped, no longer ship, and nobody has edited. True if the manifest
    changed.

    The manifest key goes with the file. Keeping it would make a later release that re-ships the
    same path read the absence as "they removed it" and never deliver it again.
    """
    prefix = f"{name}/"
    changed = False
    candidates = {key for key in (*manifest, *history) if key.startswith(prefix)} - bundled_keys
    for key in sorted(candidates):
        target = live / key[len(prefix) :]
        if not target.is_file():
            continue
        try:
            current = digest(target.read_bytes())
        except OSError:
            continue
        recognised = set(history.get(key, ()))
        if key in manifest:
            recognised.add(manifest[key])
        if current not in recognised:
            # Theirs, or edited from ours — either way somebody's work, so it stays.
            report.kept.append(key)
            continue
        try:
            target.unlink()
        except OSError:
            continue
        report.removed.append(key)
        if manifest.pop(key, None) is not None:
            changed = True
        # Folders the retirement emptied, up to but never including the live root.
        parent = target.parent
        while parent != live and live in parent.parents:
            leftovers = [p for p in parent.iterdir() if p.name not in _IGNORED]
            if leftovers:
                break
            for junk in parent.iterdir():
                if junk.is_dir():
                    shutil.rmtree(junk, ignore_errors=True)
                else:
                    junk.unlink(missing_ok=True)
            parent.rmdir()
            parent = parent.parent
    return changed


def _write(target: Path, data: bytes) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return True
    except OSError:
        return False
