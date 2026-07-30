"""Resolving a tunable's effective value, and changing it.

Precedence is **environment > stored > default**, matching how chat config already
resolves. An operator who exported ``KITH_MAX_ROUNDS`` expects it to win over anything
a UI wrote, and expects to be told the UI can't override it rather than watching a
setting silently not apply.

Values are read live rather than snapshotted at import, which is what makes them
editable at all — the previous arrangement (module constants assigned once) meant even
changing the environment needed a restart. Live reads happen inside a tool loop, so
they are cached; only a write through here invalidates the cache, and writes only come
from this process.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kith.config import CONFIG_DB_PATH
from kith.domain.tuning import GROUPS, TUNABLES, Tunable, for_key
from kith.infra.db import config_store

#: Stored keys are prefixed so they cannot collide with chat config ("model",
#: "think") in the same key/value table.
PREFIX = "tune."

_cache: dict[str, Any] | None = None

#: Which database to resolve against. ``None`` means the real one. A seam rather than
#: a parameter because ``value()`` is called from inside pure loop-detection code,
#: where threading a path through would defeat the point of it being pure — and
#: because tests must not read whichever values a developer happens to have set.
_db: Path | None = None


def use_database(path: Path | None) -> None:
    """Resolve against a different config database from now on."""
    global _db
    _db = path
    reload()


def _database() -> Path:
    return _db or CONFIG_DB_PATH


def _stored() -> dict[str, Any]:
    global _cache
    if _cache is None:
        raw = config_store.load_settings(_database())
        _cache = {k[len(PREFIX) :]: v for k, v in raw.items() if k.startswith(PREFIX)}
    return _cache


def reload() -> None:
    """Forget the cache. Called after a write, and safe to call at any time."""
    global _cache
    _cache = None


def value(key: str) -> Any:
    """The effective value: environment, else stored, else the declared default."""
    knob = for_key(key)
    from_env = os.environ.get(knob.env)
    if from_env is not None and from_env.strip():
        try:
            return knob.coerce(from_env.strip())
        except ValueError:
            # A typo in an environment variable must not stop him working; the
            # documented default is a better outcome than a crash at import.
            return knob.default
    stored = _stored().get(key)
    if stored is None:
        return knob.default
    try:
        return knob.coerce(stored)
    except ValueError:
        return knob.default


def overridden_by_env(knob: Tunable) -> bool:
    """True when the environment is winning, so the UI can say the field is inert."""
    raw = os.environ.get(knob.env)
    return raw is not None and bool(raw.strip())


def snapshot() -> dict:
    """Everything, in the shape the settings UI needs.

    One request rather than one per knob, and it carries the declarations too so the
    UI has no second copy of the labels, bounds or help text to keep in step.
    """
    return {
        "groups": [
            {
                "key": group.key,
                "label": group.label,
                "blurb": group.blurb,
                "settings": [
                    {
                        **knob.public(),
                        "value": value(knob.key),
                        "isDefault": value(knob.key) == knob.default,
                        "fromEnv": overridden_by_env(knob),
                    }
                    for knob in TUNABLES
                    if knob.group == group.key
                ],
            }
            for group in GROUPS
        ],
        # Deployment paths, shown so someone can find their data without grepping.
        # Read-only on purpose: the databases are already open by the time anyone
        # could change this, so an editable field would be a lie.
        "paths": _paths(),
    }


def apply(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and store a batch of changes, returning their effective values.

    A batch rather than one at a time because related knobs constrain each other —
    raising the rounds per turn and the landing reserve together should be one save,
    not a moment where the reserve exceeds the budget.
    """
    if not updates:
        return {}

    cleaned: dict[str, Any] = {}
    for key, raw in updates.items():
        knob = for_key(key)  # raises ValueError on an unknown key
        cleaned[key] = knob.coerce(raw)

    config_store.update_settings(_database(), {PREFIX + k: v for k, v in cleaned.items()})
    reload()
    return {key: value(key) for key in cleaned}


def reset(keys: list[str] | None = None) -> dict[str, Any]:
    """Put knobs back to their declared defaults by removing what was stored.

    Removed rather than written-as-default so "default" keeps meaning whatever the
    code says today — a stored copy would freeze this version's number and quietly
    diverge the next time a default is reconsidered.
    """
    import sqlite3

    targets = [for_key(key).key for key in keys] if keys else [knob.key for knob in TUNABLES]
    conn = sqlite3.connect(_database())
    try:
        conn.executemany("DELETE FROM settings WHERE key = ?", [(PREFIX + key,) for key in targets])
        conn.commit()
    finally:
        conn.close()
    reload()
    return {key: value(key) for key in targets}


def describe() -> dict:
    """Effective values only, for the startup log."""
    return {knob.key: value(knob.key) for knob in TUNABLES}


def _paths() -> list[dict]:
    from kith import settings

    return [
        {"label": "His databases", "value": str(settings.DATA_DIR), "env": "KITH_DATA_DIR"},
        {
            "label": "Persona fragments",
            "value": settings.PERSONA_DIR or str(settings.DEFAULT_PERSONA_DIR),
            "env": "KITH_PERSONA_DIR",
        },
        {
            "label": "Interface served from",
            "value": settings.UI_DIST or "(served elsewhere)",
            "env": "KITH_UI_DIST",
        },
        {"label": "Sandbox build context", "value": str(settings.SANDBOX_BUILD_DIR), "env": ""},
    ]


def data_dir() -> Path:
    """Convenience for callers that only want the one path."""
    from kith import settings

    return settings.DATA_DIR
