"""Editing and viewing Kith's mind through one generic surface.

The UI talks to his brain by kind — ``POST /api/brain/note``,
``DELETE /api/brain/task/12`` — which keeps the frontend small. This package turns
that string into the right repository call:

* ``kinds`` — the table: one declaration per editable kind, listing only the
  operations it actually supports.
* ``views`` — the read side: the panel snapshot and the merged lifetime timeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kith.services.brain.kinds import KINDS, require
from kith.services.brain.views import snapshot, timeline

__all__ = ["KINDS", "create", "delete", "snapshot", "timeline", "update"]


def delete(path: Path, kind: str, key: str) -> bool:
    entry = require(kind, "remove")
    return bool(entry.remove(path, entry.key(key)))


def create(path: Path, kind: str, data: dict) -> dict:
    entry = require(kind, "add")
    return entry.add(path, data)


def update(path: Path, kind: str, key: str, data: dict) -> dict | Any | None:
    entry = require(kind, "edit")
    return entry.edit(path, entry.key(key), data)
