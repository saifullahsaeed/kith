"""Who he thinks he is, and how he feels right now.

Both tables are single-row by construction (``CHECK (id = 1)``) and are seeded by
the migrations, so these read and write row 1 rather than inserting.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Mood, SelfModel
from kith.infra.db.support import utc_now_iso

# Kept short on purpose: identity and mood are injected into every single prompt,
# so an essay here would cost tokens on every turn he takes.
_MOOD_LABEL_MAX = 40
_MOOD_NOTE_MAX = 200


def get_self(path: Path) -> dict:
    with session(path) as db:
        row = db.get(SelfModel, 1)
        return as_dict(row) if row else {"identity": "", "profile": "", "updated_at": None}


def set_self(path: Path, identity: str | None = None, profile: str | None = None) -> dict:
    with session(path) as db:
        row = db.get(SelfModel, 1)
        if row is None:  # migrations seed it, but never assume
            row = SelfModel(id=1, identity="", profile="", updated_at=utc_now_iso())
            db.add(row)
        if identity is not None:
            row.identity = identity.strip()
        if profile is not None:
            row.profile = profile.strip()
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def append_self_note(path: Path, note: str) -> dict:
    current = get_self(path)
    profile = (current["profile"] + "\n" if current["profile"] else "") + f"- {note.strip()}"
    return set_self(path, profile=profile.strip())


def get_mood(path: Path) -> dict:
    with session(path) as db:
        row = db.get(Mood, 1)
        return as_dict(row) if row else {"label": "", "energy": 60, "note": "", "updated_at": None}


def set_mood(
    path: Path, label: str | None = None, energy: int | None = None, note: str | None = None
) -> dict:
    with session(path) as db:
        row = db.get(Mood, 1)
        if row is None:
            row = Mood(id=1, updated_at=utc_now_iso())
            db.add(row)
        if label is not None:
            row.label = label.strip()[:_MOOD_LABEL_MAX]
        if energy is not None:
            row.energy = max(0, min(100, int(energy)))
        if note is not None:
            row.note = note.strip()[:_MOOD_NOTE_MAX]
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)
