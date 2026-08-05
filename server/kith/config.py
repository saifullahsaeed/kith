"""Configuration for the Kith server.

Resolves the effective chat settings from three layers, highest priority first:
environment variables, the config database (see ``db/config_store.py``), then
built-in defaults. The persona lives separately, in the ``persona/`` folder.
Per-request overrides from a caller are applied on top (see ``merge_overrides``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from kith import settings
from kith.infra.db import config_store
from kith.services import skills
from kith.services.persona import load_persona


# Re-exported from settings so callers have one import for "where things are", and
# there is still exactly one place these are read from the environment.
def ollama_host() -> str:
    """Where Ollama listens. A function, not a constant: it is editable in settings,
    and a constant read at import could not reflect a change."""
    from kith.services import tuning

    return str(tuning.value("ollama_host"))


DATA_DIR = settings.DATA_DIR
CONFIG_DB_PATH = DATA_DIR / "config.db"  # server configuration
AGENT_DB_PATH = DATA_DIR / "agent.db"  # the agent's memory/workspace


@dataclass(frozen=True)
class Config:
    """A resolved set of chat parameters."""

    model: str
    num_ctx: int
    num_predict: int
    system: str
    think: bool
    #: How hard to think before answering: "" (leave it to the provider), "none", "minimal",
    #: "low", "medium", "high", "xhigh" or "max" — OpenRouter's full reasoning-effort scale
    #: (`kith.llm.openai_compat.REASONING_EFFORTS`). Only sent to models whose OpenRouter
    #: entry lists `reasoning` in supported_parameters — sending it elsewhere is a 400 on
    #: the whole request, not a field quietly ignored.
    effort: str = ""
    #: OpenRouter stickiness for this conversation, so its rounds land on one warm cache.
    #: Blank falls back to the install-wide id.
    session_id: str = ""
    base_url: str = ""  # OpenAI-compatible cloud endpoint; blank = local Ollama
    api_key: str = ""  # cloud API key; when set (with base_url), chat runs in the cloud
    #: How many tokens this model can hold, or 0 when nobody knows.
    #:
    #: A fact about the model rather than a setting, which is why it is resolved here and is
    #: not in the tuning registry. 0 is load-bearing and must never be replaced by a guess: a
    #: window guessed too high never fires and every turn 400s; guessed too low, it truncates
    #: work that would have fitted. "I don't know" has to stay expressible.
    context_window: int = 0


def default_config() -> Config:
    """Resolve the effective default config: env > config DB > built-in default.

    Settings persist in the config database, so they survive restarts and can be
    changed at runtime. The persona is merged from the ``persona/`` folder on
    each call; ``KITH_SYSTEM`` bypasses it with a single inline prompt.
    """
    stored = config_store.load_settings(CONFIG_DB_PATH)
    return Config(
        model=_str_setting("KITH_MODEL", stored, "model", "qwen3:4b"),
        num_ctx=_int_setting("KITH_NUM_CTX", stored, "num_ctx", 40960),
        num_predict=_int_setting("KITH_NUM_PREDICT", stored, "num_predict", 8192, allow_unlimited=True),
        # Persona, then the skill index. The order matters for money: `config.system` is
        # exactly the region llm.caching treats as the stable head, so anything appended here
        # is cached across requests rather than re-billed on each one. It also has to be
        # stable text — a count or a timestamp in there would move the seam and invalidate
        # the persona sitting in front of it.
        system=settings.SYSTEM_PROMPT_OVERRIDE or (load_persona() + skills.index()),
        think=_bool_setting("KITH_THINK", stored, "think", True),
        effort=_str_setting("KITH_EFFORT", stored, "effort", ""),
        base_url=_str_setting("KITH_BASE_URL", stored, "base_url", "").rstrip("/"),
        api_key=_str_setting("KITH_API_KEY", stored, "api_key", ""),
        context_window=_context_window(stored),
    )


#: Where the chosen model's window is kept, **paired with the model id it describes**.
#:
#: Declared here rather than beside the other connection keys because this is the module that
#: reads it on every request, and the reader is the one that cannot be allowed to drift.
#: `ConnectionManager.adopt` imports it to write.
#:
#: The pairing is the point. `PATCH /api/config` writes `model` without going through
#: adoption, so a bare number would outlive the model it was measured for — and a stale
#: window is worse than none in both directions: too high and the guard never fires while
#: every turn 400s, too low and it truncates work that would have fitted.
CONTEXT_KEY = "model_context"


def _context_window(stored: dict) -> int:
    """How much room this model has, or 0 when we cannot say honestly.

    Local first, and it is not a fallback: for Ollama we *send* `num_ctx`, so that number is
    the window by construction — more authoritative than any catalogue.

    For a cloud model it comes from the catalogue, captured when the model was adopted, and
    it is stored **with the model id it describes**. That pairing is the whole point.
    `PATCH /api/config` can change `model` without going through adoption, so a bare number
    would silently outlive the model it was measured for — and a stale window is worse than
    no window in both directions. Left at 200,000 after a switch to a 32k model the guard
    never fires and every turn 400s; left at 32,000 after a switch upward it truncates work
    that would have fitted. Mismatched, this collapses into the already-honest 0.
    """
    if not _str_setting("KITH_BASE_URL", stored, "base_url", "").strip():
        return _int_setting("KITH_NUM_CTX", stored, "num_ctx", 40960)
    raw = stored.get(CONTEXT_KEY)
    try:
        noted = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (TypeError, ValueError):
        return 0
    model = _str_setting("KITH_MODEL", stored, "model", "qwen3:4b")
    if str(noted.get("model") or "") != model:
        return 0
    try:
        return max(0, int(noted.get("context") or 0))
    except (TypeError, ValueError):
        return 0


def to_wire(config: Config) -> dict:
    """Serialise a config for the HTTP API (camelCase, JS-friendly)."""
    return {
        "model": config.model,
        "numCtx": config.num_ctx,
        "numPredict": config.num_predict,
        "system": config.system,
        "think": config.think,
        "effort": config.effort,
        "baseUrl": config.base_url,
        "apiKeySet": bool(config.api_key),
        "capabilities": model_capabilities(),
    }


def model_capabilities() -> dict:
    """What the model in use can be given, as recorded when it was chosen.

    A local model gets the conservative answer rather than a guess: Ollama does not
    publish modalities, and offering an image button that silently drops the image is
    worse than not offering one.
    """
    unknown = {"known": False, "images": False, "files": False, "reasoning": False, "modalities": ["text"]}
    stored = config_store.load_settings(CONFIG_DB_PATH)
    raw = stored.get("model_capabilities")
    if not raw:
        return unknown
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return unknown
    return {
        # `known` separates "this model cannot take an image" from "nobody has asked yet",
        # which are the same False and want opposite treatment: an attach button must not
        # appear on a guess, while an effort dial is safe to offer optimistically because
        # the transport already retries a reasoning-related 400 without it.
        "known": True,
        "images": bool(parsed.get("images")),
        "files": bool(parsed.get("files")),
        "reasoning": bool(parsed.get("reasoning")),
        "modalities": list(parsed.get("modalities") or ["text"]),
    }


def merge_overrides(base: Config, overrides: dict) -> Config:
    """Apply validated per-request overrides on top of a base config.

    Unknown or invalid fields are ignored (the base value wins), so a caller can
    send as little or as much as it likes. An empty ``system`` string is honoured
    (it clears the persona) — only a missing/non-string ``system`` falls back.
    """
    model = overrides.get("model")
    num_ctx = overrides.get("numCtx")
    num_predict = overrides.get("numPredict")
    system = overrides.get("system")
    think = overrides.get("think")
    effort = overrides.get("effort")

    merged_ctx = num_ctx if isinstance(num_ctx, int) and num_ctx > 0 else base.num_ctx

    return Config(
        model=model.strip() if isinstance(model, str) and model.strip() else base.model,
        num_ctx=merged_ctx,
        num_predict=(
            num_predict
            if isinstance(num_predict, int) and (num_predict > 0 or num_predict == -1)
            else base.num_predict
        ),
        system=system if isinstance(system, str) else base.system,
        think=think if isinstance(think, bool) else base.think,
        effort=effort if isinstance(effort, str) else base.effort,
        # The cloud endpoint/key are server settings, never per-request overrides.
        base_url=base.base_url,
        api_key=base.api_key,
        # Carried deliberately. This function rebuilds the object field by field, so a field
        # left out here is not inherited — it silently resets to the dataclass default on
        # every chat request. The window would then be right for a tick and zero for chat,
        # which is the harder of the two to notice.
        #
        # On the local path it tracks the *merged* num_ctx rather than the base one, because
        # that is the number actually sent to Ollama: a caller who overrides numCtx for one
        # request has changed the window for that request.
        context_window=merged_ctx if not base.base_url else base.context_window,
    )


def _str_setting(env_key: str, settings: dict[str, Any], key: str, default: str) -> str:
    env = os.environ.get(env_key)
    if env:
        return env
    value = settings.get(key)
    return value if isinstance(value, str) and value else default


def _int_setting(
    env_key: str,
    settings: dict[str, Any],
    key: str,
    default: int,
    *,
    allow_unlimited: bool = False,
) -> int:
    env = _parse_int(os.environ.get(env_key), allow_unlimited)
    if env is not None:
        return env
    value = settings.get(key)
    # bool is a subclass of int, so exclude it explicitly.
    if isinstance(value, int) and not isinstance(value, bool) and _valid_int(value, allow_unlimited):
        return value
    return default


def _bool_setting(env_key: str, settings: dict[str, Any], key: str, default: bool) -> bool:
    raw = os.environ.get(env_key)
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off")
    value = settings.get(key)
    return value if isinstance(value, bool) else default


def _parse_int(raw: str | None, allow_unlimited: bool) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if _valid_int(value, allow_unlimited) else None


def _valid_int(value: int, allow_unlimited: bool) -> bool:
    return value > 0 or (allow_unlimited and value == -1)
