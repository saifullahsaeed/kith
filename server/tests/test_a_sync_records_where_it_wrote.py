"""The record of what Kith placed belongs beside what it placed.

`seed.sync` keeps a manifest so an update can tell its own files from ones somebody edited. That
manifest used to live at one global path, read and written no matter which folder was being
reconciled — correct for the app, which only ever has one, and quietly destructive everywhere
else.

The failure it produced is the worst shape a failure can have. The manifest means *we already
placed this*, so a sync into a scratch directory recorded the bundled files as delivered; the
next real boot read that, skipped them, and the skill was simply never installed. Nothing raised,
nothing logged, and the folder looked exactly like a folder where the file had been deliberately
removed.

It happened twice in one session to the same skill — once from a one-off check, once from the
persona suite, whose fixture redirects the skills directory but had no way to redirect this.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith import settings
from kith.infra import seed


@pytest.fixture
def bundled(tmp_path: Path) -> Path:
    """A shipped tree of one file, standing in for `server/skills`."""
    source = tmp_path / "bundled" / "a-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: a-skill\n---\n\nbody\n")
    return tmp_path / "bundled"


@pytest.fixture
def live(tmp_path: Path) -> Path:
    """A live tree that already exists, which is the situation of every install but the first."""
    place = tmp_path / "live" / "skills"
    place.mkdir(parents=True)
    return place


def test_a_sync_writes_its_record_beside_the_tree_it_synced(bundled, live):
    seed.sync("skills", bundled, live)
    assert (live.parent / "seeded.json").is_file()
    recorded = json.loads((live.parent / "seeded.json").read_text())["paths"]
    assert "skills/a-skill/SKILL.md" in recorded


def test_a_scratch_sync_never_touches_the_real_manifest(bundled, live):
    """The whole point. A check that syncs somewhere disposable must leave the install alone."""
    real = settings.SEED_MANIFEST_PATH
    before = real.read_text(encoding="utf-8") if real.exists() else None

    seed.sync("skills", bundled, live)

    after = real.read_text(encoding="utf-8") if real.exists() else None
    assert after == before


def test_a_file_recorded_elsewhere_is_still_delivered_here(bundled, live, tmp_path):
    """The consequence, end to end.

    Sync the same bundle into a scratch folder first — as a test or a one-off check would — and
    then into the folder that matters. The second sync must still place the file. Under the old
    global manifest it did not, and nothing said so.
    """
    scratch = tmp_path / "scratch" / "skills"
    scratch.mkdir(parents=True)
    seed.sync("skills", bundled, scratch)

    seed.sync("skills", bundled, live)
    assert (live / "a-skill" / "SKILL.md").is_file()


def test_an_edited_file_is_still_left_alone(bundled, live):
    """The behaviour the manifest exists for, which the scoping must not cost.

    A file somebody rewrote is theirs. Recording provenance per-tree must not turn an update into
    something that overwrites an edit.
    """
    seed.sync("skills", bundled, live)
    (live / "a-skill" / "SKILL.md").write_text("mine now\n")

    (bundled / "a-skill" / "SKILL.md").write_text("---\nname: a-skill\n---\n\nnewer body\n")
    seed.sync("skills", bundled, live)

    assert (live / "a-skill" / "SKILL.md").read_text() == "mine now\n"
