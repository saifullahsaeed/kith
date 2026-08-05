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
from kith.llm import budget, caching

#: OpenRouter's full reasoning-effort scale, descending. Confirmed against their own
#: SDK types (``ReasoningEffort``/``ChatRequestReasoningEffort``, both generated from
#: their OpenAPI spec) rather than guessed — a value outside this set used to fall
#: through to the plain enabled/disabled switch below, silently overriding whatever
#: was actually asked for.
REASONING_EFFORTS: tuple[str, ...] = ("max", "xhigh", "high", "medium", "low", "minimal", "none")

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


def _body(response) -> str:
    """The response body, or "" if reading it fails.

    A streamed response whose connection dropped raises on `.text`, and a classifier that
    raises while deciding why a request failed turns one failure into two.
    """
    try:
        return response.text
    except requests.exceptions.RequestException:
        return ""


def _refuses_reasoning(response: requests.Response) -> bool:
    """Is this 400 specifically about the reasoning switch?

    Matched on the provider's words rather than retried blindly, so a genuine bad
    request still surfaces as one instead of being quietly sent twice.
    """
    return "reasoning" in _body(response).lower()


def _session_id() -> str:
    """The persisted stickiness id, read through the config store."""
    from kith.config import CONFIG_DB_PATH
    from kith.infra.db import config_store

    stored = config_store.load_settings(CONFIG_DB_PATH)
    return caching.session_id(
        stored,
        lambda fresh: config_store.update_settings(CONFIG_DB_PATH, {caching.SESSION_KEY: fresh}),
    )


def _routing_options(config: Config) -> dict[str, Any]:
    """The OpenRouter routing, fallback and privacy fields, resolved from settings.

    Pure — settings in, payload fragment out — so the policy can be tested without opening a
    socket. `session_id` is assembled in `stream_once` instead: unlike this and `reasoning`,
    it touches storage.
    """
    from kith.services import tuning

    out: dict[str, Any] = {}
    provider: dict[str, Any] = {}

    pinned = _pinned_provider()
    if pinned:
        # A strong preference, not a lock: fallbacks stay on so availability never breaks,
        # while every round of a turn is steered at the same warm host.
        provider["order"] = [pinned]
        provider["allow_fallbacks"] = True

    # Which host to prefer when nothing is pinned, and this is the gap that cost real money.
    #
    # Pinning was dropped in favour of the session id, on the reasoning that stickiness asks for
    # the same host without giving up fallbacks. The stickiness works — and that is the problem.
    # It keeps a session *together*; nothing makes it land somewhere *cheap*. So a session that
    # happens to open on an expensive upstream stays there for its whole life, which is exactly
    # the shape in the data: 23:24, 23:25 and 23:30 all at $4.12–6.59 per million uncached
    # prompt tokens, then 15:54 through 15:58 all at ~$1.01, against a usual $0.13. Contiguous
    # blocks at one rate, not scattered outliers. One model, up to fifty times the price,
    # decided by whichever door the session came in through.
    #
    # `sort` costs nothing to set and is not a lock: it orders the pool, and fallbacks still
    # apply if the cheapest is down. Combined with the session id it means sticking to a cheap
    # host rather than sticking to an arbitrary one.
    order_by = str(tuning.value("prefer_provider_by")).strip()
    if order_by and not pinned:
        provider["sort"] = order_by

    # And a ceiling, for the case `sort` cannot cover: every cheap host is busy and the fallback
    # is the $6.59 one. Off by default because the right number is per-model and a figure set too
    # low takes the model off the air entirely — the same "goes dark" trade as the flags below.
    ceiling = float(tuning.value("max_prompt_price"))
    if ceiling > 0:
        provider["max_price"] = {"prompt": ceiling}

    if tuning.value("require_provider_parameters"):
        # Only route to upstreams that support everything this request sends — tools,
        # reasoning, caching — so a cheaper host can't silently drop a feature we paid for.
        provider["require_parameters"] = True
    if tuning.value("zero_data_retention"):
        # For a "runs on your machine" agent reaching the cloud: exclude any provider that
        # may log or train on the request, and restrict routing to zero-data-retention hosts.
        provider["data_collection"] = "deny"
        provider["zdr"] = True
    if provider:
        out["provider"] = provider

    fallback = str(tuning.value("fallback_model")).strip()
    if fallback and fallback != config.model:
        # If the primary errors, rate-limits or is down, OpenRouter tries the next model;
        # billing is by whichever actually served. This is what keeps an unattended tick
        # alive through an outage instead of stalling it mid-task.
        out["models"] = [config.model, fallback]

    return out


def _reasoning_options(config: Config) -> dict[str, Any]:
    """Whether he reasons before answering, and how hard.

    Pure, for the same reason `_routing_options` is: a payload fragment out of settings alone
    can be tested without opening a socket. Sent only to OpenRouter — where it is a documented
    extension, and a strict OpenAI-compatible host 400s the whole request rather than ignoring
    an unknown key — never to Ollama, which reads `config.think` on its own instead.

    `effort` beats `enabled` when it is set: they are alternative spellings of the same field,
    and sending both makes the provider pick, which is not a decision to leave to it. Blank
    effort means "you decide", which is the right default — the sensible amount of thinking
    for a model is a thing its maker knows better.
    """
    effort = (config.effort or "").strip().lower()
    if effort in REASONING_EFFORTS:
        return {"reasoning": {"effort": effort}}
    return {"reasoning": {"enabled": bool(config.think)}}


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
        # Usage accounting — cache-hit tokens and real cost — is how we confirm prompt
        # caching is working. It used to be opted into with `usage: {include: true}`;
        # OpenRouter now returns the full breakdown on every response automatically and
        # that request field is deprecated with no effect, so it is no longer sent. The
        # reader in `_stats` is unchanged: it takes whatever `usage` the response carries.
        # Keeps a turn's rounds landing on the same upstream host, so the cache one
        # round wrote is the cache the next one reads. A preference, not a pin —
        # availability still falls back.
        #
        # Per conversation whenever we know which one, and that now covers a session's
        # ticks as well as its chat turns. It used to cover only chat: a tick passed no
        # conversation and fell through to the install-wide id below, so one session kept
        # two copies of the same cached persona warm on two different hosts. The fallback
        # is still there and still right — a step run with nobody working belongs to no
        # session, so the install-wide id is the honest answer for it.
        payload["session_id"] = config.session_id or _session_id()
        # Provider routing (a pinned upstream), model fallback, privacy/capability
        # preferences, and whether/how hard he reasons — all resolved from settings and
        # assembled in one tested place.
        payload.update(_routing_options(config))
        payload.update(_reasoning_options(config))
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
    # Classified before the reasoning retry, and the order matters. That retry fires on any
    # 400 whose body merely *contains* "reasoning" — and an overflow message often does, since
    # providers list a reasoning-token breakdown in it. Left second, an over-budget request
    # would be sent a whole second time before anything noticed the real cause.
    if budget.looks_like_overflow(response.status_code, _body(response)):
        detail = _body(response)[:200]
        response.close()
        yield {
            "type": "error",
            "kind": "context_overflow",
            "message": (f"That conversation outgrew the model's context window. The provider said: {detail}"),
        }
        return

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
    # Who actually served this request. OpenRouter puts it on every chunk and nothing read it,
    # so a bill could not be attributed to an upstream — which is the one fact you need to pin
    # one. Measured across 1,071 recorded rounds of the same model: the usual rate is
    # $0.13 per million uncached prompt tokens, with a clump at $1.01–1.10 and an hour on
    # 2026-08-01 at $4.12–6.59. Same `openai/gpt-5.6-luna`, up to fifty times the price, and no
    # record of which host did it.
    served_by = ""
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
            # Sent on each chunk; taken from the first that carries it. Not every
            # OpenAI-compatible host sets it, so this stays blank rather than guessing.
            if not served_by and chunk.get("provider"):
                served_by = str(chunk["provider"])

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
        "stats": _stats(usage, time.time() - started, config.model, served_by),
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
            # Content may be a string or already a list of parts (an image the person
            # attached). Passed through as-is either way: rebuilding it here would mean
            # this function knowing about modalities, which is the route's business.
            out.append({"role": role, "content": message.get("content", "")})
    return out


def _stats(
    usage: dict | None, elapsed: float, model: str = "", provider: str = ""
) -> dict[str, float]:
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

    # What it actually cost, in dollars, as the provider billed it.
    #
    # OpenRouter has been returning this all along — we ask for it explicitly with
    # `usage: {include: true}`, and the comment where we do says it gives us "real cost" —
    # and it was read nowhere. So every surface reported spend as a token count, which is a
    # proxy that drifts: a cached prompt token and a fresh one cost an order of magnitude
    # apart, and a reasoning token and a text token do not cost the same either. There is no
    # need to estimate a number the invoice already contains.
    cost = float(usage.get("cost") or 0.0)

    # Reasoning is charged as output and reported separately, and on the measured call it was
    # 31 of 63 completion tokens — half the response, with nothing on screen saying so. A
    # reasoning model's "response tokens" is mostly thinking, and someone reading a total
    # would reasonably assume it was text.
    completion_details = usage.get("completion_tokens_details") or {}
    reasoning = int(completion_details.get("reasoning_tokens") or 0)

    tps = completion / elapsed if elapsed > 0 and completion else 0.0
    return {
        # Which model produced this row. Without it a bill spanning a model switch cannot
        # be attributed after the fact — the token counts alone say nothing about price.
        "model": model,
        # And which upstream served it, which turns out to matter more than the model does.
        # One model on OpenRouter is many hosts at many prices; the same `gpt-5.6-luna` round
        # has billed anywhere from $0.13 to $6.59 per million uncached prompt tokens on this
        # install. Blank for a plain OpenAI-compatible endpoint that does not say.
        "provider": provider,
        "promptTokens": prompt,
        "responseTokens": completion,
        "reasoningTokens": reasoning,
        "cachedTokens": cached,
        "cacheWriteTokens": written,
        "costUsd": cost,
        "tokensPerSecond": round(tps, 1),
        "totalSeconds": round(elapsed, 2),
        "loadSeconds": 0.0,
    }
