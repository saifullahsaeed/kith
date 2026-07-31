"""Every knob, in one place.

Kith's behaviour was tunable through 21 environment variables read from 11 different
modules. Each read was reasonable on its own, but together they meant there was no
way to answer "what can I configure?" except by grepping, no way to see a default
without opening the file that happened to own it, and nothing stopping two modules
disagreeing about the same value.

So the reads live here, as module-level constants named after what they do rather
than after the variable that sets them. Rules this follows:

* **Read once, at import.** These are process-level settings; re-reading per call
  would let the environment change under a running loop.
* **Defaults are the values, not a fallback buried in a call.** Reading this file
  tells you how Kith behaves with an empty environment.
* **Only what must be fixed before launch is here.** Paths, and where the interface
  is served from. Everything that changes how he *behaves* — how long a turn runs,
  his pace, the stall thresholds, the addresses of things around him — moved to
  ``domain.tuning``, because those have to be editable from the app: a packaged
  desktop build has no shell to export a variable in. Chat settings (model, persona,
  the API key) live in the config database, see ``config.py``.

``describe()`` at the bottom returns the whole picture, which the server logs at
startup so a misconfigured run says so rather than behaving oddly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: True when running as a frozen binary rather than from a checkout. PyInstaller sets
#: both of these; nothing else does.
FROZEN = bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")

#: The root of everything shipped alongside the code: ``server/`` from a checkout, and the
#: bundle's own extraction directory when frozen.
#:
#: Derived once, here, rather than in each module that needs it. Modules used to walk
#: up from their own ``__file__``, which meant moving a file into a subpackage
#: silently changed where it looked: the persona directory resolved to an empty path
#: and Kith started with NO personality, with only a "0 fragments" line in the log
#: to say so.
SERVER_ROOT = Path(sys._MEIPASS) if FROZEN else _HERE.parent  # type: ignore[attr-defined]

#: Where his databases go when nothing says otherwise.
#:
#: This is the one path that must NOT follow SERVER_ROOT. A frozen bundle unpacks to a
#: temporary directory and deletes it on exit, so defaulting the databases beside the code —
#: which is right from a checkout and was the only case that existed — would mean his
#: memory, tasks, notes and journal were erased every time the app was closed. The failure
#: is silent and total: the app starts fine, with nothing in it, every time.
#:
#: A dotted folder in the home directory rather than Application Support: it is the same on
#: every platform, and someone looking for their own data should be able to find it without
#: knowing a macOS convention.
_DEFAULT_DATA_DIR = (Path.home() / ".kith") if FROZEN else (SERVER_ROOT / "data")


def _text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _number(name: str, default: int) -> int:
    """An unparseable value falls back rather than crashing at import.

    A typo in a tuning variable should not stop Kith starting — it should start with
    the documented default, and ``describe()`` shows what it actually used.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Where things live
# --------------------------------------------------------------------------- #

#: Databases. Overridable so a packaged app can put them somewhere else again.
DATA_DIR = Path(_text("KITH_DATA_DIR") or _DEFAULT_DATA_DIR)

#: The built UI to serve from this process. Empty = something else serves it
#: (the Vite dev server), and the SPA routes are not registered at all.
#:
#: A frozen build carries the interface with it and defaults to serving it, which is what
#: makes the binary one self-contained thing rather than a server that needs to be told
#: where its own front end is. It also puts the page and the API on one origin by
#: construction — which is what lets the page be handed the API token in the document.
UI_DIST = _text("KITH_UI_DIST") or (str(SERVER_ROOT / "ui") if FROZEN else "")

#: Persona fragments. Empty = the bundled ``persona/`` directory.
PERSONA_DIR = _text("KITH_PERSONA_DIR")

#: Where the bundled persona fragments live when PERSONA_DIR is unset.
DEFAULT_PERSONA_DIR = SERVER_ROOT / "persona"

#: The folder he works in, on your machine. Empty = ``~/Kith``.
#:
#: This replaced a Docker container. Kith is a desktop app that friends should be able to
#: run without installing anything, and "first install Docker" was the single largest thing
#: standing between the project and that — while also being what stopped him reading a file
#: you pointed at or using a program you already have. What the container enforced is now
#: enforced by kith.services.permissions instead.
WORKSPACE_DIR = _text("KITH_WORKSPACE")

#: Replaces the whole persona with one inline prompt. For experiments — it bypasses
#: the ``persona/`` fragments entirely rather than adding to them.
SYSTEM_PROMPT_OVERRIDE = _text("KITH_SYSTEM")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

#: A SearXNG instance. Free and private, but its public engines get blocked.
SEARCH_URL = _text("KITH_SEARCH_URL", "http://127.0.0.1:8888").rstrip("/")

#: ``auto`` tries SearXNG then OpenRouter's web plugin; or force one.
SEARCH_PROVIDER = _text("KITH_SEARCH_PROVIDER", "auto").lower()


def describe() -> dict[str, object]:
    """This file's values and the tunables', for the startup log.

    Both, in one place, because a misconfigured run should say so once rather than
    leaving someone to work out which of two settings systems they were fighting.
    """
    from kith.services import tuning

    return {
        "paths": {
            "data_dir": str(DATA_DIR),
            "ui_dist": UI_DIST or "(not served here)",
            "persona_dir": PERSONA_DIR or f"(bundled: {DEFAULT_PERSONA_DIR})",
            "system_override": bool(SYSTEM_PROMPT_OVERRIDE),
        },
        "search_provider": SEARCH_PROVIDER,
        "searxng": SEARCH_URL,
        "tunables": tuning.describe(),
    }
