"""Streaming client for any OpenAI-compatible chat endpoint.

Lets Kith think in the cloud (OpenRouter, NVIDIA NIM, OpenAI, DeepSeek, …) instead
of on the local machine, without changing the agent loop. It mirrors
``ollama.stream_once``'s event shape exactly:

- ``{"type": "delta", "role": "reasoning"|"text", "text": str}``
- ``{"type": "turn", "content": str, "tool_calls": list, "stats": {...}}``
- ``{"type": "error", "message": str}``

The agent loop threads tool history in Ollama's shape (tool results keyed by
``tool_name``, arguments as dicts); we translate that to OpenAI's shape
(``tool_call_id`` + string arguments) here, so nothing upstream needs to know
which provider is in use.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import requests

from kith.config import Config
from kith.llm import caching

# Optionally pin OpenRouter to one upstream host. Default routing spreads requests
# across ~20 providers, so consecutive rounds land on different (cold) caches and
# prefix caching rarely hits. Pinning a caching-capable host keeps every round on
# the same warm cache. Set e.g. KITH_OR_PROVIDER=DeepInfra ; watch the effect in
# /api/usage → cacheHitRate. Empty = OpenRouter's default routing.


def is_openrouter(config: Config) -> bool:
    """Is this endpoint OpenRouter?

    Decides whether the vendor extensions above are safe to send. Also used by the
    search module, which needs OpenRouter's `web` plugin — so the check lives here
    once rather than being spelled slightly differently in two places.
    """
    return "openrouter.ai" in (config.base_url or "")


def _refuses_reasoning(response: requests.Response) -> bool:
    """Is this 400 specifically about the reasoning switch?

    Matched on the provider's words rather than retried blindly, so a genuine bad
    request still surfaces as one instead of being quietly sent twice.
    """
    try:
        return "reasoning" in response.text.lower()
    except requests.exceptions.RequestException:
        return False


def _session_id() -> str:
    """The persisted stickiness id, read through the config store."""
    from kith.config import CONFIG_DB_PATH
    from kith.infra.db import config_store

    stored = config_store.load_settings(CONFIG_DB_PATH)
    return caching.session_id(
        stored,
        lambda fresh: config_store.update_settings(CONFIG_DB_PATH, {caching.SESSION_KEY: fresh}),
    )


def _pinned_provider() -> str:
    """The upstream to pin OpenRouter to, read per request because it is editable.

    Imported at call time: this module is the transport and importing a service at
    module scope would make the dependency cycle real.
    """
    from kith.services import tuning

    return str(tuning.value("openrouter_provider"))


def stream_once(
    messages: list[dict[str, Any]],
    config: Config,
    host: str | None = None,  # unused; kept for a common signature with ollama_client
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
) -> Iterator[dict]:
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": config.model,
        # Cache breakpoints go on after translation, because they mark boundaries in the
        # wire-shape messages — and only for providers that need them (see llm.caching).
        # The tool schemas are cached with the system prompt on Anthropic, so their
        # size counts toward whether the prefix clears the provider's minimum. The
        # persona marks where the prompt stops being identical between requests —
        # everything after it carries the clock, so it is the only region that can be
        # cached across turns rather than only within one.
        "messages": caching.apply(
            _to_openai(messages),
            config.model,
            prefix_extra_chars=len(json.dumps(tools or [])),
            persona=config.system or "",
        ),
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    # OpenRouter extensions, sent ONLY to OpenRouter. Both are non-standard, and a
    # strict OpenAI-compatible host rejects the whole request with a 400 rather than
    # ignoring what it does not recognise — NVIDIA NIM answers
    # "Validation: Unsupported parameter(s): `usage`, `provider`". Sending them
    # unconditionally made this module OpenRouter-only in practice while claiming to
    # support any compatible endpoint.
    if is_openrouter(config):
        # Usage accounting: returns cache-hit tokens and real cost, which is how we
        # confirm prompt caching is actually working.
        payload["usage"] = {"include": True}
        # Keeps a turn's rounds landing on the same upstream host, so the cache one
        # round wrote is the cache the next one reads. A preference, not a pin —
        # availability still falls back.
        payload["session_id"] = _session_id()
        pinned = _pinned_provider()
        if pinned:
            # Fallbacks stay on so availability never breaks; the pin is a strong
            # preference that keeps every round on the same warm cache.
            payload["provider"] = {"order": [pinned], "allow_fallbacks": True}
        # Whether he reasons before answering. Sent only to OpenRouter, where it is a
        # documented extension — a strict OpenAI-compatible host rejects the whole
        # request rather than ignoring an unknown key. Until now this setting reached
        # Ollama only, so on a cloud model the switch did nothing at all.
        payload["reasoning"] = {"enabled": bool(config.think)}
    if tools:
        payload["tools"] = tools
        # "none" is how the API says "you may not call anything this turn". It matters
        # that the schemas are still sent: a model mid-way through a tool-using turn,
        # asked to stop by prose alone, keeps emitting calls — as *text*, in whatever
        # pseudo-markup it was trained on (``<FUNCTION>web_search(...)</FUNCTION>``).
        # That text is indistinguishable from an answer, so it reaches the transcript,
        # the journal, and anything he files. Withholding the schemas caused it;
        # sending them with tool_choice="none" is the fix.
        payload["tool_choice"] = tool_choice
    if config.num_predict and config.num_predict > 0:
        payload["max_tokens"] = config.num_predict

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        # Harmless attribution some providers (e.g. OpenRouter) like to see.
        "HTTP-Referer": "http://localhost",
        "X-Title": "Kith",
    }

    started = time.time()
    try:
        # (connect, read): the read timeout is the gap between streamed chunks — a
        # stalled/rate-limited connection aborts instead of hanging the loop forever.
        response = requests.post(url, json=payload, headers=headers, stream=True, timeout=(10, 90))
    except requests.exceptions.RequestException as exc:
        yield {"type": "error", "message": f"Could not reach the cloud model at {url}: {exc}"}
        return

    # Some models cannot have reasoning turned off — "Reasoning is mandatory for this
    # endpoint and cannot be disabled", HTTP 400. Asking for it is still right, because
    # on every other model it saves real tokens; being refused just means dropping the
    # request to reason less, not failing the turn.
    if response.status_code == 400 and "reasoning" in payload and _refuses_reasoning(response):
        response.close()
        payload.pop("reasoning")
        try:
            response = requests.post(url, json=payload, headers=headers, stream=True, timeout=(10, 90))
        except requests.exceptions.RequestException as exc:
            yield {"type": "error", "message": f"Could not reach the cloud model at {url}: {exc}"}
            return

    if response.status_code != 200:
        detail = ""
        try:
            detail = response.text[:400]
        except requests.exceptions.RequestException:
            pass
        response.close()
        yield {
            "type": "error",
            "message": f"Cloud model returned {response.status_code}" + (f": {detail}" if detail else ""),
        }
        return

    # SSE arrives as text/event-stream with no charset, and requests then falls
    # back to latin-1 — which turns every em dash and accent into mojibake.
    response.encoding = "utf-8"

    answer = ""
    calls: dict[int, dict] = {}
    usage: dict | None = None
    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if chunk.get("error"):
                yield {"type": "error", "message": str(chunk["error"])}
                return
            if chunk.get("usage"):
                usage = chunk["usage"]

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}

            reasoning = delta.get("reasoning") or delta.get("reasoning_content")
            if reasoning:
                yield {"type": "delta", "role": "reasoning", "text": reasoning}
            content = delta.get("content")
            if content:
                answer += content
                yield {"type": "delta", "role": "text", "text": content}

            for part in delta.get("tool_calls") or []:
                index = part.get("index", 0)
                slot = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
                if part.get("id"):
                    slot["id"] = part["id"]
                function = part.get("function") or {}
                if function.get("name"):
                    slot["name"] = function["name"]
                if function.get("arguments"):
                    slot["arguments"] += function["arguments"]
    except requests.exceptions.RequestException as exc:
        response.close()
        yield {"type": "error", "message": f"cloud stream interrupted: {exc}"}
        return
    finally:
        response.close()

    tool_calls = [
        {"id": slot["id"], "function": {"name": slot["name"], "arguments": slot["arguments"]}}
        for _, slot in sorted(calls.items())
        if slot["name"]
    ]
    yield {
        "type": "turn",
        "content": answer,
        "tool_calls": tool_calls,
        "stats": _stats(usage, time.time() - started),
    }


def reachable(config: Config) -> bool:
    """Cheap check that the cloud endpoint + key work (lists models)."""
    try:
        resp = requests.get(
            f"{config.base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=8,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def _to_openai(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the agent loop's (Ollama-flavoured) history into OpenAI shape.

    Tool results follow their assistant tool-call turn in order, so we pair them
    to the freshly-minted tool_call ids positionally.
    """
    out: list[dict] = []
    pending_ids: list[str] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            pending_ids = []
            tool_calls = []
            for i, call in enumerate(message["tool_calls"]):
                function = call.get("function") or {}
                call_id = call.get("id") or f"call_{len(out)}_{i}"
                arguments = function.get("arguments")
                arguments = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
                tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": function.get("name", ""), "arguments": arguments},
                    }
                )
                pending_ids.append(call_id)
            out.append(
                {"role": "assistant", "content": message.get("content") or None, "tool_calls": tool_calls}
            )
        elif role == "tool":
            call_id = pending_ids.pop(0) if pending_ids else f"call_{len(out)}"
            out.append({"role": "tool", "tool_call_id": call_id, "content": message.get("content", "")})
        else:
            out.append({"role": role, "content": message.get("content", "")})
    return out


def _stats(usage: dict | None, elapsed: float) -> dict[str, float]:
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    # Prefix-cache hits: prompt tokens billed at a fraction because the provider
    # reused them from a prior round. This is what makes a long loop affordable —
    # surfacing it lets us confirm the stable-prefix ordering is actually caching.
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)
    # Writes bill at 1.25x-2x and reads at 0.1x-0.5x, so a run that is all writes and no
    # reads is worse than no caching. Counting both is the only way to tell them apart.
    written = int(details.get("cache_write_tokens") or 0)
    tps = completion / elapsed if elapsed > 0 and completion else 0.0
    return {
        "promptTokens": prompt,
        "responseTokens": completion,
        "cachedTokens": cached,
        "cacheWriteTokens": written,
        "tokensPerSecond": round(tps, 1),
        "totalSeconds": round(elapsed, 2),
        "loadSeconds": 0.0,
    }
