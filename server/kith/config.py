"""The effective chat configuration.

Resolved from three layers, highest priority first: environment variables, the config database
(see ``infra/db/config_store.py``), then the built-in defaults that database is seeded with.
Per-request overrides from a caller go on top — see ``merge_overrides``.

The persona is not part of this layering; it lives in the ``persona/`` folder and is merged in
by ``default_config``.
"""

from __future__ import annotations

import json
import os
from typing import Any

from kith import settings
from kith.domain.chat import Config
from kith.infra.db import config_store
from kith.services import skills
from kith.services.persona import load_persona

#: Where the chosen model's window is kept, **paired with the model id it describes**, and
#: where its capabilities are kept alongside. Both are declared here, in the module that reads
#: them on every request, because the reader is the half that cannot be allowed to drift;
#: `ConnectionManager` imports these to write.
CONTEXT_KEY = "model_context"
CAPABILITIES_KEY = "model_capabilities"

#: The built-in fallbacks, taken from the seed the config database is created with rather than
#: written out again. `default_config` and `_context_window` both have to agree on the model
#: and the window, and a second copy of a literal is how two readers stop agreeing.
_DEFAULT_MODEL = str(config_store.DEFAULT_SETTINGS["model"])
_DEFAULT_NUM_CTX = int(config_store.DEFAULT_SETTINGS["num_ctx"])
_DEFAULT_NUM_PREDICT = int(config_store.DEFAULT_SETTINGS["num_predict"])
_DEFAULT_THINK = bool(config_store.DEFAULT_SETTINGS["think"])


def ollama_host() -> str:
    """Where Ollama listens.

    A function, not a constant: it is editable in settings, and a constant read at import could
    not reflect a change.
    """
    from kith.services import tuning

    return str(tuning.value("ollama_host"))


def default_config() -> Config:
    """Resolve the effective config: env > config database > built-in default."""
    stored = config_store.load_settings(settings.CONFIG_DB_PATH)
    return Config(
        model=_str_setting("KITH_MODEL", stored, "model", _DEFAULT_MODEL),
        num_ctx=_int_setting("KITH_NUM_CTX", stored, "num_ctx", _DEFAULT_NUM_CTX),
        num_predict=_int_setting(
            "KITH_NUM_PREDICT", stored, "num_predict", _DEFAULT_NUM_PREDICT, allow_unlimited=True
        ),
        # Persona, then the skill index. The order matters for money: `config.system` is exactly
        # the region llm.caching treats as the stable head, so anything appended here is cached
        # across requests rather than re-billed on each one. It also has to be stable text — a
        # count or a timestamp would move the seam and invalidate the persona in front of it.
        system=settings.SYSTEM_PROMPT_OVERRIDE or (load_persona() + skills.index()),
        think=_bool_setting("KITH_THINK", stored, "think", _DEFAULT_THINK),
        effort=_str_setting("KITH_EFFORT", stored, "effort", ""),
        base_url=_str_setting("KITH_BASE_URL", stored, "base_url", "").rstrip("/"),
        api_key=_str_setting("KITH_API_KEY", stored, "api_key", ""),
        context_window=_context_window(stored),
    )


def _context_window(stored: dict) -> int:
    """How much room this model has, or 0 when we cannot say honestly.

    Local first, and not as a fallback: for Ollama we *send* `num_ctx`, so that number is the
    window by construction — more authoritative than any catalogue.

    For a cloud model it comes from the catalogue, captured at adoption and stored with the
    model id it describes. The pairing is the point: `PATCH /api/config` writes `model` without
    going through adoption, so a bare number would outlive the model it was measured for. A
    stale window is worse than none in both directions — too high and the guard never fires
    while every turn 400s, too low and it truncates work that would have fitted. Mismatched,
    this collapses into the already-honest 0.
    """
    if not _str_setting("KITH_BASE_URL", stored, "base_url", "").strip():
        return _int_setting("KITH_NUM_CTX", stored, "num_ctx", _DEFAULT_NUM_CTX)
    noted = _json_setting(stored, CONTEXT_KEY)
    if noted is None:
        return 0
    if str(noted.get("model") or "") != _str_setting("KITH_MODEL", stored, "model", _DEFAULT_MODEL):
        return 0
    try:
        return max(0, int(noted.get("context") or 0))
    except (TypeError, ValueError):
        return 0


def model_capabilities() -> dict:
    """What the model in use can be given, as recorded when it was chosen.

    A local model gets the conservative answer rather than a guess: Ollama does not publish
    modalities, and offering an image button that silently drops the image is worse than not
    offering one.
    """
    stored = config_store.load_settings(settings.CONFIG_DB_PATH)
    parsed = _json_setting(stored, CAPABILITIES_KEY)
    if parsed is None:
        return {"known": False, "images": False, "files": False, "reasoning": False, "modalities": ["text"]}
    return {
        # `known` separates "this model cannot take an image" from "nobody has asked yet", which
        # are the same False and want opposite treatment: an attach button must not appear on a
        # guess, while an effort dial is safe to offer optimistically because the transport
        # already retries a reasoning-related 400 without it.
        "known": True,
        "images": bool(parsed.get("images")),
        "files": bool(parsed.get("files")),
        "reasoning": bool(parsed.get("reasoning")),
        "modalities": list(parsed.get("modalities") or ["text"]),
    }


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


def merge_overrides(base: Config, overrides: dict) -> Config:
    """Apply validated per-request overrides on top of a base config.

    Unknown or invalid fields are ignored (the base value wins), so a caller can send as little
    or as much as it likes. An empty ``system`` string is honoured — it clears the persona —
    so only a missing or non-string ``system`` falls back.

    This rebuilds the object field by field, so **a field left out here is not inherited**: it
    silently resets to the dataclass default on every chat request.
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
        # The cloud endpoint and key are server settings, never per-request overrides.
        base_url=base.base_url,
        api_key=base.api_key,
        session_id=base.session_id,
        # Carried, not because a caller may send it — it is installed downstream by
        # `services/turn/frozen.py` — but because every field must be listed here. Today this
        # only ever receives a fresh `default_config()`, where it is blank; the moment that
        # stops being true, an omission here silently unpins a conversation from its warm cache.
        # On the local path this tracks the *merged* num_ctx rather than the base one, because
        # that is the number actually sent to Ollama: a caller who overrides numCtx for one
        # request has changed the window for that request.
        context_window=merged_ctx if not base.base_url else base.context_window,
    )


def _json_setting(stored: dict[str, Any], key: str) -> dict | None:
    """Read a JSON-object setting, or None if it is absent or unreadable.

    Values round-trip through the store as JSON, but a hand-edited or half-written row can be
    anything, so every caller here has to treat "cannot read it" as "do not know".
    """
    raw = stored.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return None


def _str_setting(env_key: str, stored: dict[str, Any], key: str, default: str) -> str:
    env = os.environ.get(env_key)
    if env:
        return env
    value = stored.get(key)
    return value if isinstance(value, str) and value else default


def _int_setting(
    env_key: str,
    stored: dict[str, Any],
    key: str,
    default: int,
    *,
    allow_unlimited: bool = False,
) -> int:
    env = _parse_int(os.environ.get(env_key), allow_unlimited)
    if env is not None:
        return env
    value = stored.get(key)
    # bool is a subclass of int, so exclude it explicitly.
    if isinstance(value, int) and not isinstance(value, bool) and _valid_int(value, allow_unlimited):
        return value
    return default


def _bool_setting(env_key: str, stored: dict[str, Any], key: str, default: bool) -> bool:
    raw = os.environ.get(env_key)
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off")
    value = stored.get(key)
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
