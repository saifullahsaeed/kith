"""Every launch-time knob, in one place.

Kith's behaviour was once tunable through 21 environment variables read from 11 different
modules. Each read was reasonable alone, but together there was no way to answer "what can I
configure?" except by grepping, no way to see a default without opening the file that happened
to own it, and nothing stopping two modules disagreeing about the same value.

So the reads live here, as module-level constants named after what they do rather than after the
variable that sets them. Rules this follows:

* **Read once, at import.** These are process-level settings; re-reading per call would let the
  environment change under a running loop. The two exceptions are functions, and say why.
* **Defaults are the values, not a fallback buried in a call.** Reading this file tells you how
  Kith behaves with an empty environment.
* **Only what must be fixed before launch is here** — paths, and where the interface is served
  from. Everything that changes how he *behaves* (how long a turn runs, his pace, the stall
  thresholds, the addresses of things around him) lives in ``domain.tuning``, because those have
  to be editable from the app: a packaged desktop build has no shell to export a variable in.
  Chat settings — model, persona, the API key — live in the config database; see ``config.py``.

This module imports nothing from ``kith``, which is what lets every layer above reach for it
without a cycle. That is a constraint, not an accident — ``tests/test_the_layers_point_one_way.py``
enforces it.

``describe()`` at the bottom returns the whole picture, which the server logs at startup so a
misconfigured run says so rather than behaving oddly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# --------------------------------------------------------------------------- #
# Where things live
# --------------------------------------------------------------------------- #

#: True when running as a frozen binary rather than from a checkout. PyInstaller sets both of
#: these; nothing else does.
FROZEN = bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")

#: The root of everything shipped alongside the code: ``server/`` from a checkout, and the
#: bundle's own extraction directory when frozen.
#:
#: Derived once, here, rather than in each module that needs it. Modules used to walk up from
#: their own ``__file__``, which meant moving a file into a subpackage silently changed where it
#: looked: the persona directory resolved to an empty path and Kith started with NO personality,
#: with only a "0 fragments" line in the log to say so.
SERVER_ROOT = Path(sys._MEIPASS) if FROZEN else _HERE.parent  # type: ignore[attr-defined]

#: Where his databases go when nothing says otherwise.
#:
#: This is the one path that must NOT follow SERVER_ROOT. A frozen bundle unpacks to a temporary
#: directory and deletes it on exit, so defaulting the databases beside the code — right from a
#: checkout, and the only case that existed at first — would erase his memory, tasks, notes and
#: journal every time the app closed. The failure is silent and total: the app starts fine, with
#: nothing in it, every time.
#:
#: A dotted folder in the home directory rather than Application Support: it is the same on every
#: platform, and someone looking for their own data should find it without knowing a macOS
#: convention.
_DEFAULT_DATA_DIR = (Path.home() / ".kith") if FROZEN else (SERVER_ROOT / "data")

#: Databases. Overridable so a packaged app can put them somewhere else again.
DATA_DIR = Path(_text("KITH_DATA_DIR") or _DEFAULT_DATA_DIR)

# Both database paths are resolved here, at import, and `from kith.settings import CONFIG_DB_PATH`
# copies the value. The suite's isolation is built around that rather than against it:
# `tests/conftest.py` walks `sys.modules` and patches the attribute on *every* `kith.*` module
# that holds a copy, so a module-scope import is redirected like any other. Verified, not assumed
# — a probe module importing both at module scope resolves to the temp databases.
#
# The real trap is the other direction: that loop only touches names starting with `kith.`, so a
# **test module** that hoists `from kith.settings import CONFIG_DB_PATH` to its own top level
# keeps the developer's real path, silently and with nothing failing. Inside a test, read
# `kith.settings.CONFIG_DB_PATH` through the module.

#: Server configuration — chat settings, the tunables, the connection, the MCP server list.
CONFIG_DB_PATH = DATA_DIR / "config.db"

#: The agent's own memory: tasks, projects, notes, messages, the flight recorder.
AGENT_DB_PATH = DATA_DIR / "agent.db"

#: The built UI to serve from this process. Empty = something else serves it (the Vite dev
#: server), and the SPA routes are not registered at all.
#:
#: A frozen build carries the interface with it and defaults to serving it, which is what makes
#: the binary one self-contained thing rather than a server that needs to be told where its own
#: front end is. It also puts the page and the API on one origin by construction — which is what
#: lets the page be handed the API token in the document.
UI_DIST = _text("KITH_UI_DIST") or (str(SERVER_ROOT / "ui") if FROZEN else "")

#: Persona fragments. Empty = ``DEFAULT_PERSONA_DIR``.
PERSONA_DIR = _text("KITH_PERSONA_DIR")

#: The fragments that ship with Kith. Read once, to seed, and never written to.
BUNDLED_PERSONA_DIR = SERVER_ROOT / "persona"

#: Where his persona actually lives — the copy the Settings editor writes to.
#:
#: The second path that must NOT follow SERVER_ROOT, for the reason the databases must not, and
#: it was missed the first time. A packaged Kith wrote every persona edit into ``sys._MEIPASS``,
#: so the editor appeared to work, said "Saved", and threw the change away when the app closed.
#: Invisible from a checkout, where this is the repo folder and persists fine.
#:
#: So the bundled folder is a seed: copied into the data directory on first run (see
#: ``services.persona.persona_dir``) and read from there afterwards, which also means a reinstall
#: cannot overwrite what someone has written about who their Kith is. From a checkout it stays the
#: repo's own ``persona/``, because that is the copy a developer means to edit and commit.
DEFAULT_PERSONA_DIR = (DATA_DIR / "persona") if FROZEN else BUNDLED_PERSONA_DIR

#: The folder he works in, on your machine. Empty = ``~/Kith``.
#:
#: This replaced a Docker container. Kith is a desktop app that friends should be able to run
#: without installing anything, and "first install Docker" was the largest thing standing between
#: the project and that — while also being what stopped him reading a file you pointed at or
#: using a program you already have. What the container enforced is now enforced by
#: ``kith.infra.permissions`` instead.
WORKSPACE_DIR = _text("KITH_WORKSPACE")

#: The skills that ship with Kith. Seeded into ``skills_dir()`` on first run, then left alone.
#:
#: Two, and they are here for two different reasons.
#:
#: ``drawing-a-canvas`` because the persona points at it by name. A default persona that names a
#: skill nobody has is a dead pointer in every downloaded copy — it was written on a machine where
#: the skill happened to be installed, which is the class of bug this folder closes.
#:
#: ``running-a-project`` because projects, milestones and tasks are not an optional feature someone
#: turns on: every copy has a board, and the board is how he decides what to work on next. Without
#: this skill a fresh install still files tasks — it just files them badly, and the failure is the
#: quiet kind, a board that hands him the wrong thing and is convincing about it. The tunable
#: ``milestone_task_cap`` guards one symptom of that with a number; this is the part that cannot be
#: a number.
#:
#: Both are seeded once and then belong to whoever installed them. Adding a third is not free: a
#: description sits in the cached prefix on every request, and these two cost ~430 tokens together.
BUNDLED_SKILLS_DIR = SERVER_ROOT / "skills"

#: The variable naming the skills folder.
#:
#: Here rather than in ``services/skills.py`` because ``infra/permissions.py`` needs the answer —
#: it must know whether a path is inside the skills folder before allowing a write — and a
#: permission check reaching up into a service for that was the last ``infra -> services`` edge
#: that was not about behaviour at all.
SKILLS_DIR_KEY = "KITH_SKILLS_DIR"


def skills_dir() -> Path:
    """Where skills are installed. Read per call; does not create anything.

    A function, emphatically not a constant: two fixtures set ``KITH_SKILLS_DIR`` *after* this
    module is imported, which a constant resolved at import would ignore. It also does no
    ``mkdir`` — ``skills.root()`` owns creating the folder, because asking where something is
    should not make it exist.
    """
    configured = _text(SKILLS_DIR_KEY)
    return Path(configured).expanduser() if configured else DATA_DIR / "skills"


#: Replaces the whole persona with one inline prompt. For experiments — it bypasses the
#: ``persona/`` fragments entirely rather than adding to them.
SYSTEM_PROMPT_OVERRIDE = _text("KITH_SYSTEM")


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

#: Where a SearXNG instance usually lives.
#:
#: The address is part of what the option *means* rather than something an operator tunes, so it
#: would sit more naturally beside `SearchKind` in `domain/search.py` — where a second copy of it
#: used to live. It cannot: `settings` is the bottom of the tree and may not import `domain`,
#: while `domain` may import this. So the literal lives here and the domain type reads it, which
#: is the only arrangement that leaves exactly one of it. The interface shows this same value as
#: the placeholder under "SearXNG address" (`services/search_setup.py`), and a second copy is how
#: the advertised default and the one actually called come to disagree.
DEFAULT_SEARX_URL = "http://127.0.0.1:8888"

#: A SearXNG instance. Free and private, but its public engines get blocked.
SEARCH_URL = _text("KITH_SEARCH_URL", DEFAULT_SEARX_URL).rstrip("/")

#: ``auto`` tries SearXNG then OpenRouter's web plugin; or force one.
SEARCH_PROVIDER = _text("KITH_SEARCH_PROVIDER", "auto").lower()

#: What a saved search choice is stored under in the config database — the same names the two
#: variables above use, so a value means one thing wherever it is read from.
#:
#: Here rather than in the module that writes them. ``services/search_setup.py`` writes and
#: ``infra/websearch.py`` reads, and the reader was importing the name from the writer one layer
#: up, through a function-body import. A row's name is not storage.
SEARCH_KIND_KEY = "search_provider"
SEARCH_URL_KEY = "search_url"


def describe() -> dict[str, object]:
    """This file's values, for the startup log.

    The tunables belong in the same entry — a misconfigured run should say so once rather than
    leaving someone to work out which of two settings systems they were fighting — but they are
    joined on by the caller. Asking ``services.tuning`` for that half from here would make this
    module import a service, which is the one thing it must not do; ``_log_configuration``
    composes the two instead, and it costs the composition root two lines.
    """
    return {
        "paths": {
            "data_dir": str(DATA_DIR),
            "workspace": WORKSPACE_DIR or "(default: ~/Kith)",
            "skills_dir": str(skills_dir()),
            "persona_dir": PERSONA_DIR or str(DEFAULT_PERSONA_DIR),
            "ui_dist": UI_DIST or "(not served here)",
            "system_override": bool(SYSTEM_PROMPT_OVERRIDE),
        },
        "frozen": FROZEN,
        "search_provider": SEARCH_PROVIDER,
        "searxng": SEARCH_URL,
    }
