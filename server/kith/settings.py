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
* **Chat settings are NOT here.** Model, context size, persona and the API key are
  runtime-editable from the UI and live in the config database — see ``config.py``.
  The split is deliberate: this file is what an operator sets before launch, that
  one is what the user changes while it runs.

``describe()`` at the bottom returns the whole picture, which the server logs at
startup so a misconfigured run says so rather than behaving oddly.
"""

from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: The ``server/`` directory — the root of everything shipped alongside the code.
#:
#: Derived once, here, rather than in each module that needs it. Modules used to walk
#: up from their own ``__file__``, which meant moving a file into a subpackage
#: silently changed where it looked: the persona directory resolved to an empty path
#: and Kith started with NO personality, with only a "0 fragments" line in the log
#: to say so.
SERVER_ROOT = _HERE.parent


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

#: Databases. Overridable so a packaged app can put them in the user's data dir.
DATA_DIR = Path(_text("KITH_DATA_DIR") or (SERVER_ROOT / "data"))

#: The built UI to serve from this process. Empty = something else serves it
#: (the Vite dev server), and the SPA routes are not registered at all.
UI_DIST = _text("KITH_UI_DIST")

#: Persona fragments. Empty = the bundled ``persona/`` directory.
PERSONA_DIR = _text("KITH_PERSONA_DIR")

#: Where the bundled persona fragments live when PERSONA_DIR is unset.
DEFAULT_PERSONA_DIR = SERVER_ROOT / "persona"

#: Build context for Kith's sandbox image.
SANDBOX_BUILD_DIR = SERVER_ROOT / "sandbox"

#: His clock. Reminders and schedules are interpreted in this zone.
TIMEZONE = _text("KITH_TZ", "UTC")

#: Replaces the whole persona with one inline prompt. For experiments — it bypasses
#: the ``persona/`` fragments entirely rather than adding to them.
SYSTEM_PROMPT_OVERRIDE = _text("KITH_SYSTEM")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

#: Where Ollama listens. Normalised so a bare "host:port" works too.
_raw_ollama = _text("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_HOST = _raw_ollama if _raw_ollama.startswith("http") else f"http://{_raw_ollama}"

#: Embeddings run locally even when chat is in the cloud — they are small and constant.
EMBED_MODEL = _text("KITH_EMBED_MODEL", "nomic-embed-text")

#: Pin OpenRouter to one upstream host. Default routing spreads requests across ~20
#: providers, so consecutive rounds hit cold prefix caches. Empty = their routing.
OPENROUTER_PROVIDER = _text("KITH_OR_PROVIDER")


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

#: A SearXNG instance. Free and private, but its public engines get blocked.
SEARCH_URL = _text("KITH_SEARCH_URL", "http://127.0.0.1:8888").rstrip("/")

#: ``auto`` tries SearXNG then OpenRouter's web plugin; or force one.
SEARCH_PROVIDER = _text("KITH_SEARCH_PROVIDER", "auto").lower()

#: Pinned rather than left to OpenRouter's default: native search varies by model and
#: prices by context size, while Exa behaves identically and costs a flat amount.
SEARCH_ENGINE = _text("KITH_SEARCH_ENGINE", "exa")

#: Carrier model for the search plugin. Empty = whatever he thinks with, which is
#: right for a cheap model; point this at a cheap slug if he moves to an expensive one.
SEARCH_MODEL = _text("KITH_SEARCH_MODEL")


# --------------------------------------------------------------------------- #
# The agent loop
# --------------------------------------------------------------------------- #

#: Tool rounds a single turn may take before it is made to wrap up.
MAX_ROUNDS = _number("KITH_MAX_ROUNDS", 40)

#: Rounds held back at the end of a turn for *landing* the work. Without a reserve,
#: research expands to fill the whole budget and the turn produces nothing durable.
LANDING_RESERVE = _number("KITH_LANDING_RESERVE", 4)

#: Characters of tool output kept in full. Sized for the model in use: a 1M-context
#: cloud model can hold a whole turn's research, and forgetting it makes him re-fetch.
LIVE_TOOL_CHARS = _number("KITH_LIVE_TOOL_CHARS", 240_000)

#: Floor on how many recent results stay whole, so a few huge pages cannot squeeze
#: out what he just read.
KEEP_FULL_TOOL_RESULTS = _number("KITH_KEEP_FULL_TOOL_RESULTS", 6)

#: How much of a trimmed result survives — enough to see WHAT it was about.
TOOL_STUB_CHARS = _number("KITH_TOOL_STUB_CHARS", 1_200)


def describe() -> dict[str, object]:
    """Everything configurable and its effective value, for the startup log."""
    return {
        "data_dir": str(DATA_DIR),
        "ui_dist": UI_DIST or "(not served here)",
        "persona_dir": PERSONA_DIR or f"(bundled: {DEFAULT_PERSONA_DIR})",
        "system_override": bool(SYSTEM_PROMPT_OVERRIDE),
        "timezone": TIMEZONE,
        "ollama_host": OLLAMA_HOST,
        "embed_model": EMBED_MODEL,
        "openrouter_provider": OPENROUTER_PROVIDER or "(their routing)",
        "search": {
            "provider": SEARCH_PROVIDER,
            "searxng": SEARCH_URL,
            "engine": SEARCH_ENGINE,
            "carrier_model": SEARCH_MODEL or "(the chat model)",
        },
        "loop": {
            "max_rounds": MAX_ROUNDS,
            "landing_reserve": LANDING_RESERVE,
            "live_tool_chars": LIVE_TOOL_CHARS,
            "keep_full_tool_results": KEEP_FULL_TOOL_RESULTS,
            "tool_stub_chars": TOOL_STUB_CHARS,
        },
    }
