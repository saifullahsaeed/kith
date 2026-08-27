"""Resolving a tunable's effective value, and changing it.

Precedence is **environment > file > default**, matching how chat config already
resolves. An operator who exported ``KITH_MAX_ROUNDS`` expects it to win over anything
a UI wrote, and expects to be told the UI can't override it rather than watching a
setting silently not apply.

Values are read live rather than snapshotted at import, which is what makes them
editable at all — the previous arrangement (module constants assigned once) meant even
changing the environment needed a restart.

**One file, and nothing else.** ``settings.json`` beside the databases, and the database
has been released from this duty entirely rather than kept as a mirror. Two stores that
both claim to hold a setting is the shape where someone edits one, reads the other, and
cannot work out why the app disagrees with the file in front of them — and the whole
reason for having a file is to be able to trust it. The one-time move out of the
database is in `_migrate_from_database`, which runs once and then has nothing to do.

Three things a file buys that a database row does not, and they are the reason for the
change rather than tidiness:

* It can be fixed when the app will not start. A bad value used to need sqlite.
* It can be copied between machines, and read in a diff.
* Kith can edit it himself, with the file tools he already has.

**External edits are picked up without a restart**, because the cache is keyed on the
file's modification time rather than merely being invalidated by our own writes. Editing
the file in another window and watching nothing happen is the failure everyone has had
with a config file once, and checking an `mtime` is cheaper than the read it guards.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from kith.domain.chat import Routing
from kith.domain.tuning import BY_KEY, GROUPS, TUNABLES, Tunable, for_key
from kith.settings import DATA_DIR

#: The prefix rows used to carry in the settings table, kept only so the one-time move
#: can find them. Nothing writes this any more.
PREFIX = "tune."

#: Written whenever the file is created, because a file a person is invited to edit
#: should say what it is. JSON has no comments, so this is a real key — ignored on read
#: like any key that is not a tunable.
_BANNER = "//"
_BANNER_TEXT = "Kith settings. Every key here is optional; delete one to go back to its default. Environment variables win over this file."

_cache: dict[str, Any] | None = None
_cache_stamp: tuple[float, int] | None = None
_lock = threading.RLock()

#: Which file to resolve against. ``None`` means the real one. A seam rather than a
#: parameter because ``value()`` is called from inside pure loop-detection code, where
#: threading a path through would defeat the point of it being pure — and because tests
#: must not read whichever values a developer happens to have set.
_file: Path | None = None


def use_file(path: Path | None) -> None:
    """Resolve against a different settings file from now on."""
    global _file
    with _lock:
        _file = path
    reload()


def settings_file() -> Path:
    """Where settings live. One place, and the only place."""
    return _file or (DATA_DIR / "settings.json")


def _stamp(path: Path) -> tuple[float, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_mtime, info.st_size)


def _stored() -> dict[str, Any]:
    """What the file says, re-read when the file has changed underneath us."""
    global _cache, _cache_stamp
    path = settings_file()
    now = _stamp(path)
    with _lock:
        if _cache is not None and now == _cache_stamp:
            return _cache
        _cache = _read(path)
        _cache_stamp = now
        return _cache


def _read(path: Path) -> dict[str, Any]:
    """The file as a dict, or an empty one.

    A malformed file resolves to defaults rather than raising. Refusing to start because
    someone left a trailing comma in a settings file is a worse outcome than running on
    the documented defaults — and every value in here has one.
    """
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # Said once, loudly, rather than swallowed: a file being ignored is exactly the
        # thing someone needs to be told, since the symptom is "my setting does nothing".
        print(f"[tuning] {path} is not readable JSON — using defaults until it is fixed")
        return {}
    if not isinstance(raw, dict):
        print(f"[tuning] {path} is not a JSON object — using defaults")
        return {}
    kept = {key: item for key, item in raw.items() if key != _BANNER and key in BY_KEY}
    # Said out loud for the same reason a malformed file is, and it is the same symptom: a key
    # nothing reads is a setting that does nothing. Two of them — `min_gap` and `task_tick_cap`,
    # left behind when the tick loop was removed — sat in a real settings file for months. They
    # could not be seen on the Advanced screen either, because that screen is drawn from the
    # declared knobs, so the only way to find them was to read the file and then grep the tree.
    #
    # Dropped as well as reported, so the next write leaves them out rather than carrying them
    # forward forever.
    stale = sorted(set(raw) - set(kept) - {_BANNER})
    if stale:
        print(f"[tuning] {path} sets {', '.join(stale)}, which nothing reads — ignoring")
    return kept


def _write(values: dict[str, Any]) -> None:
    """Replace the file, atomically.

    Temp-and-rename because the alternative is a window in which the file is half
    written, and the thing most likely to read it in that window is the app restarting
    after whatever made you edit it.
    """
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {_BANNER: _BANNER_TEXT, **dict(sorted(values.items()))}
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(body, indent=2) + "\n")
    temp.replace(path)


def reload() -> None:
    """Forget the cache. Called after a write, and safe to call at any time."""
    global _cache, _cache_stamp
    with _lock:
        _cache = None
        _cache_stamp = None


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

    # Read-modify-write under the lock. Two saves arriving together — the settings page
    # is one request per section — would otherwise each write the file from the state
    # they read at the start, and the second would drop the first.
    with _lock:
        _write({**_read(settings_file()), **cleaned})
    reload()
    return {key: value(key) for key in cleaned}


def reset(keys: list[str] | None = None) -> dict[str, Any]:
    """Put knobs back to their declared defaults by removing what was stored.

    Removed rather than written-as-default so "default" keeps meaning whatever the
    code says today — a stored copy would freeze this version's number and quietly
    diverge the next time a default is reconsidered. It is also what makes the file
    readable: what is in it is what someone chose, not a dump of every knob.
    """
    targets = [for_key(key).key for key in keys] if keys else [knob.key for knob in TUNABLES]
    with _lock:
        remaining = {k: v for k, v in _read(settings_file()).items() if k not in set(targets)}
        _write(remaining)
    reload()
    return {key: value(key) for key in targets}


def ensure_exists() -> Path:
    """Write an empty settings file if there is not one yet, and return where it is.

    A file nobody has created is a file nobody can edit, and "fix it when the app will not
    start" is the main reason for having one — so it exists from the first launch rather than
    from the first time somebody changes something in the UI. It also makes the Reveal button
    on the settings page work, which otherwise refuses a path that is not there.

    Empty but for its own explanation. Writing all thirty-one knobs out would freeze this
    version's defaults into somebody's file and quietly diverge the next time one is
    reconsidered — the same reason `reset` removes a key rather than storing the default.
    """
    path = settings_file()
    if not path.exists():
        _write({})
    return path


def _migrate_from_database() -> int:
    """Move settings out of the config database, once. Returns how many moved.

    The database has been released from this duty rather than kept as a mirror, so this
    runs once on the first start after upgrading and then has nothing left to find.

    **Order matters and is the whole care here.** The file is written and read back
    before a single row is deleted, so a failure at any point leaves the values in the
    database where the next start will find them again. Deleting first and writing after
    is the version of this that loses somebody's configuration.
    """
    path = settings_file()
    if path.exists():
        return 0  # the file is the store now; nothing to move

    from kith.infra.db import config_store
    from kith.settings import CONFIG_DB_PATH

    try:
        raw = config_store.load_settings(CONFIG_DB_PATH)
    except Exception:
        return 0  # no database yet, or not readable — a fresh install has nothing to move
    carried = {k[len(PREFIX) :]: v for k, v in raw.items() if k.startswith(PREFIX)}
    if not carried:
        return 0

    _write(carried)
    if _read(path) != carried:
        print("[tuning] could not write settings.json — leaving the old values in the database")
        return 0

    import sqlite3

    try:
        conn = sqlite3.connect(CONFIG_DB_PATH)
        try:
            conn.executemany("DELETE FROM settings WHERE key = ?", [(PREFIX + key,) for key in carried])
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # The file is already correct and is what gets read, so a failure to tidy the old
        # rows is untidy rather than wrong. They are never read again either way.
        pass
    reload()
    print(f"[tuning] moved {len(carried)} setting(s) into {path}")
    return len(carried)


def describe() -> dict:
    """Effective values only, for the startup log."""
    return {knob.key: value(knob.key) for knob in TUNABLES}


def _paths() -> list[dict]:
    """Every folder Kith uses, with what is in it.

    Sizes and counts are here because this is a desktop app and "where are my files and
    how much room are they taking" is a question about a folder on a disk, not a
    configuration value. ``open`` marks the ones worth a reveal-in-Finder button.
    """
    from kith import settings
    from kith.infra import workspace

    here = workspace.root()
    return [
        {
            "label": "His folder",
            "value": str(here),
            "env": "KITH_WORKSPACE",
            "bytes": _folder_size(here),
            "entries": sum(1 for item in here.iterdir() if item.name != workspace.INTERNAL_DIR),
            "open": True,
            # The one folder that is a choice rather than a consequence — and the only one
            # where the choice matters, since he works inside it without asking.
            "change": True,
            "pinned": bool(str(settings.WORKSPACE_DIR or "").strip()),
            "note": "Where he works. Everything he makes lands here.",
        },
        {
            "label": "His databases",
            "value": str(settings.DATA_DIR),
            "env": "KITH_DATA_DIR",
            "bytes": _folder_size(settings.DATA_DIR),
            "open": True,
            "note": "Memory, tasks, notes, the flight recorder.",
        },
        {
            # Listed because being able to find it is most of the point. Everything on this
            # page writes here, and it is the copy to reach for when the app will not start.
            "label": "These settings",
            "value": str(settings_file()),
            "env": "",
            "bytes": stamped[1] if (stamped := _stamp(settings_file())) else 0,
            "open": True,
            "note": "Everything on this page, as JSON you can edit. Only what you changed is in it.",
        },
        {
            "label": "Conversations",
            "value": str(here / workspace.INTERNAL_DIR / "conversations"),
            "env": "",
            "bytes": _folder_size(here / workspace.INTERNAL_DIR / "conversations"),
            "open": True,
            "note": "One readable transcript per conversation, never overwritten.",
        },
        {
            "label": "Persona fragments",
            "value": settings.PERSONA_DIR or str(settings.DEFAULT_PERSONA_DIR),
            "env": "KITH_PERSONA_DIR",
            "open": True,
            "note": "Who he is, in markdown you can edit.",
        },
        {
            "label": "Interface served from",
            "value": settings.UI_DIST or "(served elsewhere)",
            "env": "KITH_UI_DIST",
        },
    ]


def _folder_size(path: Path) -> int:
    """Bytes on disk, or 0 for a folder that is not there yet."""
    try:
        return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())
    except OSError:
        return 0


def routing() -> Routing:
    """The six routing knobs, resolved together.

    One place, because there are three callers — the loop, the history fold, and the tests
    that check a tuned value reaches the wire — and a mapping written out three times is a
    mapping that drifts once. `llm/openai_compat.py` used to read them itself, which made the
    transport import this module; it takes a `Routing` now and this is where one comes from.
    """
    return Routing(
        pinned=str(value("openrouter_provider")),
        prefer_by=str(value("prefer_provider_by")),
        max_prompt_price=float(value("max_prompt_price")),
        require_parameters=bool(value("require_provider_parameters")),
        zero_data_retention=bool(value("zero_data_retention")),
        fallback_model=str(value("fallback_model")),
    )
