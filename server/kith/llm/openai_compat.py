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
import re
import time
from collections.abc import Iterator
from typing import Any

import requests

from kith.domain import connection
from kith.domain.chat import Config, Routing
from kith.domain.enums import REASONING_EFFORTS
from kith.llm import budget, caching

#: What share of ``max_tokens`` each effort level hands to thinking, from OpenRouter's own
#: documentation: "max" allocates approximately 95% of max_tokens, xhigh 95%, high 80%,
#: medium 50%, low 20%, minimal 10%.
#:
#: This table is the whole bug, so it is worth stating plainly: **effort is not a dial on how
#: hard the model thinks, it is a fraction of the output ceiling** — thinking spends out of it
#: first and the answer gets what is left. Kith sent the answer cap (`num_predict`, 8,192,
#: labelled "Longest single answer") as that ceiling, with effort "max" beside it: a
#: 7,782-token claim on thinking out of 8,192, and the reply left with whatever thinking did
#: not want — as little as 410 tokens — on a turn whose rounds were writing 5,000-6,500-token
#: python scripts. OpenRouter's own docs carry the rule this violates: "max_tokens must be
#: strictly higher than the reasoning budget to ensure there are tokens available for the final
#: response after thinking."
#:
#: Measured, 2026-08-20 21:44-21:50 on `google/gemini-3.7-flash`: two rounds came back with
#: `finish_reason: length` and `responseTokens == reasoningTokens` — the whole response spent
#: thinking, no content and no tool call — and the turn died reporting an empty response and
#: advising a change of model. The model was never the problem.
#:
#: Confirmed against the live provider on 2026-08-21, one prompt asking for a long script, the
#: two payloads side by side:
#:
#:     max_tokens 8,192  + effort max          finish LENGTH  7,612 chars, cut off mid-script
#:     max_tokens 40,960 + reasoning 32,768    finish stop   63,520 chars, whole script
REASONING_SHARE: dict[str, float] = {
    "max": 0.95,
    "xhigh": 0.95,
    "high": 0.80,
    "medium": 0.50,
    "low": 0.20,
    "minimal": 0.10,
    "none": 0.0,
}

#: The share to reserve for when nobody has said how hard to think — `effort` blank and the
#: `think` toggle on, which is what a default install sends. The provider decides the amount
#: and we cannot know it, so the ceiling reserves the middle of the scale: enough that a
#: thinking model cannot eat the answer, not so much that the request stops resembling what
#: was asked for.
_UNSTATED_SHARE = REASONING_SHARE["medium"]

#: The most thinking any one round is given, whatever the arithmetic works out to. Inverting
#: the share table turns a modest answer cap into an enormous budget at the top of the scale —
#: 19x the cap at "max", so 155,648 tokens for an 8,192-token cap — and a budget no model can
#: honour is a request a provider rejects rather than clamps. Gemini's own thinking budgets top
#: out at 24,576-32,768 tokens and Anthropic's are bounded by `max_tokens`, which is what this
#: sits inside, so the highest of those is the honest ceiling on the ceiling.
MAX_THINKING_TOKENS = 32_768

# Optionally pin OpenRouter to one upstream host. Default routing spreads requests
# across ~20 providers, so consecutive rounds land on different (cold) caches and
# prefix caching rarely hits. Pinning a caching-capable host keeps every round on
# the same warm cache. Set e.g. KITH_OR_PROVIDER=DeepInfra ; watch the effect in
# /api/usage → cacheHitRate. Empty = OpenRouter's default routing.


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

    The judgement itself is `domain.connection.refuses_reasoning`, because the web search
    builds its own chat payload and needs the same answer; this wrapper is only the part
    that knows how to read a body off a streamed response.
    """
    return connection.refuses_reasoning(_body(response))


def _routing_options(config: Config, routing: Routing) -> dict[str, Any]:
    """The OpenRouter routing, fallback and privacy fields, resolved from settings.

    Pure — routing in, payload fragment out — so the policy can be tested without opening a
    socket, which it now genuinely is: it used to read all six values out of `services.tuning`
    through a function-body import, so "pure" was true of the arithmetic and false of the
    module. The caller resolves them; see `domain.chat.Routing`.
    """
    out: dict[str, Any] = {}
    provider: dict[str, Any] = {}

    pinned = routing.pinned
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
    # the shape in the data: 23:24, 23:25 and 23:30 all at $4.12-6.59 per million uncached
    # prompt tokens, then 15:54 through 15:58 all at ~$1.01, against a usual $0.13. Contiguous
    # blocks at one rate, not scattered outliers. One model, up to fifty times the price,
    # decided by whichever door the session came in through.
    #
    # `sort` costs nothing to set and is not a lock: it orders the pool, and fallbacks still
    # apply if the cheapest is down. Combined with the session id it means sticking to a cheap
    # host rather than sticking to an arbitrary one.
    order_by = routing.prefer_by.strip()
    if order_by and not pinned:
        provider["sort"] = order_by

    # And a ceiling, for the case `sort` cannot cover: every cheap host is busy and the fallback
    # is the $6.59 one. Off by default because the right number is per-model and a figure set too
    # low takes the model off the air entirely — the same "goes dark" trade as the flags below.
    ceiling = routing.max_prompt_price
    if ceiling > 0:
        provider["max_price"] = {"prompt": ceiling}

    if routing.require_parameters:
        # Only route to upstreams that support everything this request sends — tools,
        # reasoning, caching — so a cheaper host can't silently drop a feature we paid for.
        provider["require_parameters"] = True
    if routing.zero_data_retention:
        # For a "runs on your machine" agent reaching the cloud: exclude any provider that
        # may log or train on the request, and restrict routing to zero-data-retention hosts.
        provider["data_collection"] = "deny"
        provider["zdr"] = True
    if provider:
        out["provider"] = provider

    fallback = routing.fallback_model.strip()
    if fallback and fallback != config.model:
        # If the primary errors, rate-limits or is down, OpenRouter tries the next model;
        # billing is by whichever actually served. This is what keeps a long turn
        # alive through an outage instead of stalling it mid-task.
        out["models"] = [config.model, fallback]

    return out


def thinking_budget(config: Config) -> int:
    """How many tokens of thinking one round is allowed, as a number rather than a share.

    Zero when nothing is reasoning — and zero when there is no answer cap, which is not the
    same reason: with no `max_tokens` on the request there is no ceiling for a share to be
    taken out of, so there is nothing for thinking to starve and nothing to reserve against.
    A default install has a cap (8,192), so this is the ordinary case, not the exotic one.

    The arithmetic inverts `REASONING_SHARE`. The share is taken out of the whole ceiling, so
    for the answer to keep its cap the budget is what has to sit *beside* the cap:

        share x (cap + budget) = budget   ->   budget = cap x share / (1 - share)

    Capped at `MAX_THINKING_TOKENS`, which is what keeps "max" from asking for a budget no
    provider will grant.
    """
    cap = config.num_predict
    if cap <= 0:
        return 0
    effort = (config.effort or "").strip().lower()
    if effort in REASONING_SHARE:
        share = REASONING_SHARE[effort]
    elif config.think:
        share = _UNSTATED_SHARE
    else:
        share = 0.0
    if share <= 0:
        return 0
    return min(MAX_THINKING_TOKENS, int(cap * share / (1 - share)))


def output_ceiling(config: Config) -> int:
    """`max_tokens` for the request: what the answer may write, plus what thinking will take.

    These were one number for a long time and that is what broke: `num_predict` is labelled
    "Longest single answer" in the settings, the loop reserves context room by it, and the
    transport was handing that same figure to the provider as the ceiling on *everything the
    round produces* — thoughts included, and thoughts first. See `REASONING_SHARE`.

    0 means the person asked for no limit (`num_predict` -1, the sentinel), and the caller
    leaves `max_tokens` off the payload entirely.
    """
    if config.num_predict <= 0:
        return 0
    return config.num_predict + thinking_budget(config)


def _reasoning_options(config: Config) -> dict[str, Any]:
    """Whether he reasons before answering, and how hard.

    Pure, for the same reason `_routing_options` is: a payload fragment out of settings alone
    can be tested without opening a socket. Sent only to OpenRouter — where it is a documented
    extension, and a strict OpenAI-compatible host 400s the whole request rather than ignoring
    an unknown key — never to Ollama, which reads `config.think` on its own instead.

    One spelling per request. `effort`, `max_tokens` and `enabled` are alternative spellings of
    the same field, and sending two makes the provider pick, which is not a decision to leave
    to it. Blank effort means "you decide", which is the right default — the sensible amount of
    thinking for a model is a thing its maker knows better.

    **`max_tokens` is the spelling whenever there is an answer cap to protect**, because it is
    the only one that bounds thinking independently of the ceiling it shares with the answer.
    `effort` cannot: it *is* a share of that ceiling, so at the top of the scale there is no
    ceiling that leaves the cap intact — 95% of anything leaves 5%. Nothing is lost in the
    translation on the families that matter here, since a budget is what OpenRouter converts
    effort into for Gemini, Anthropic and Qwen anyway; on a model that only understands effort
    it converts back, by the same percentages, so a budget of 32,768 against a 40,960 ceiling
    arrives as "high" rather than "max". That is the one cost of this, and it is worth it: a
    "max" that cannot answer is not more thinking than a "high" that can.
    """
    effort = (config.effort or "").strip().lower()
    if effort in REASONING_EFFORTS:
        budget = thinking_budget(config)
        return {"reasoning": {"max_tokens": budget}} if budget else {"reasoning": {"effort": effort}}
    return {"reasoning": {"enabled": bool(config.think)}}


def stream_once(
    messages: list[dict[str, Any]],
    config: Config,
    host: str | None = None,  # unused; kept for a common signature with ollama_client
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    routing: Routing | None = None,
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
    if connection.is_openrouter(config.base_url):
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
        # every turn. It used to cover only some of them: one path passed no
        # conversation and fell through to an install-wide id read here, so one session kept
        # two copies of the same cached persona warm on two different hosts.
        #
        # The fallback is still there and still right — a step run with nobody working belongs
        # to no session, so the install-wide id is the honest answer for it — but it is
        # resolved by the caller now. Reading it here meant the transport opening the config
        # database, which was the tree's last `llm -> infra` edge, written as a function-body
        # import to keep Python from noticing. See `services/turn/frozen.install_session_id`.
        payload["session_id"] = config.session_id
        # Provider routing (a pinned upstream), model fallback, privacy/capability
        # preferences, and whether/how hard he reasons — all resolved from settings and
        # assembled in one tested place.
        payload.update(_routing_options(config, routing or Routing()))
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
    # The answer's cap plus the room thinking will take out of the ceiling, never the cap on
    # its own — see `output_ceiling`. Set outside the OpenRouter branch because the starvation
    # is not an OpenRouter behaviour: a reasoning model bills its thoughts as output tokens
    # against `max_tokens` whoever is hosting it, and whether or not we asked for any.
    ceiling = output_ceiling(config)
    if ceiling > 0:
        payload["max_tokens"] = ceiling

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        # Who is asking. One place decides it, because `infra.websearch` builds its own chat
        # request and has to say the same thing — see `domain.connection.attribution`.
        **connection.attribution(config.base_url),
    }

    started = time.time()
    try:
        # (connect, read): the read timeout is the gap between streamed chunks — a
        # stalled/rate-limited connection aborts instead of hanging the loop forever.
        response = requests.post(url, json=payload, headers=headers, stream=True, timeout=(10, 90))
    except requests.exceptions.RequestException as exc:
        yield {"type": "error", "message": f"Could not reach the cloud model at {url}: {exc}"}
        return

    # Only a failed request has a body worth reading, and this `if` is the whole reason
    # streaming works.
    #
    # `_body` is `response.text`, and `.text` on a streamed response downloads all of it. It was
    # called unconditionally, so every 200 had its entire answer pulled down here — and then
    # handed to `looks_like_overflow`, which returns False on the first line because the status
    # is not 400. The reply was fetched in full, discarded, and only then replayed to the loop
    # that thought it was streaming.
    #
    # What that looks like from a chair: ten seconds of nothing, then the whole answer at once,
    # which reads as a slow model rather than as a bug. Measured against a provider stub sending
    # eighteen tokens 150ms apart, every one of them arrived in the same millisecond.
    failed_body = _body(response) if response.status_code != 200 else ""

    # Some models cannot have reasoning turned off — "Reasoning is mandatory for this
    # endpoint and cannot be disabled", HTTP 400. Asking for it is still right, because
    # on every other model it saves real tokens; being refused just means dropping the
    # request to reason less, not failing the turn.
    # Classified before the reasoning retry, and the order matters. That retry fires on any
    # 400 whose body merely *contains* "reasoning" — and an overflow message often does, since
    # providers list a reasoning-token breakdown in it. Left second, an over-budget request
    # would be sent a whole second time before anything noticed the real cause.
    if budget.looks_like_overflow(response.status_code, failed_body):
        detail = failed_body[:200]
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
    # $0.13 per million uncached prompt tokens, with a clump at $1.01-1.10 and an hour on
    # 2026-08-01 at $4.12-6.59. Same `openai/gpt-5.6-luna`, up to fifty times the price, and no
    # record of which host did it.
    served_by = ""
    # Why the response stopped: "stop", "tool_calls", "length", "content_filter". Also arrived
    # on every response and was also read nowhere, and it is the difference between a model
    # that had nothing to say and one that was not given room to say it. Without it a round cut
    # off mid-thought is indistinguishable from a silent one, and the loop reported two of them
    # as "returned an empty response — try again, or switch model" while the provider was
    # saying `length` on both. See `REASONING_SHARE`.
    finish = ""
    generation = ""
    try:
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
            if not line.startswith("data:"):
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
            # The provider's own id for this round, on every chunk beside the one above and
            # likewise read nowhere. It is what makes a round in a transcript the same object
            # as a row in the provider's log, which is the difference between reading a bill
            # and guessing at one: diagnosing the truncation this file now handles meant
            # matching six rounds to six log rows by wall-clock and token count, because there
            # was no id to match them by. 29 rounds across 243 conversations were billed for
            # twice the prompt they sent (`promptTokens` exactly 2x `cachedTokens`, the cost
            # confirming one fresh copy and one cached), and that question is still open — an
            # id is how the next round of it gets answered rather than inferred.
            if not generation and chunk.get("id"):
                generation = str(chunk["id"])

            choices = chunk.get("choices") or []
            if not choices:
                continue
            # Carried on the last chunk of a stream, and null on every one before it, so the
            # last non-empty value is the answer. Kept rather than acted on here: what a
            # truncated round costs is the loop's decision, not the transport's.
            if choices[0].get("finish_reason"):
                finish = str(choices[0]["finish_reason"])
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
                slot = calls.setdefault(index, {"id": None, "name": "", "arguments": "", "said": 0})
                if part.get("id"):
                    slot["id"] = part["id"]
                function = part.get("function") or {}
                if function.get("name"):
                    slot["name"] = function["name"]
                if function.get("arguments"):
                    slot["arguments"] += function["arguments"]
                    progress = _writing(slot)
                    if progress is not None:
                        yield progress
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
        "stats": _stats(usage, time.time() - started, config.model, served_by, finish, generation),
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


#: How much a tool call's arguments must grow before saying so again.
#:
#: A `write_file` of a 21,591-character page arrives as roughly a thousand chunks, and one event
#: each would put a thousand frames on the wire to report one action. Every two thousand
#: characters is about ten updates for a page that size — enough that the number visibly moves,
#: few enough that the stream is still about the turn.
_WRITING_EVERY = 2_000


def _writing(slot: dict) -> dict | None:
    """Say that a tool call is still being written, if it has grown enough to be worth saying.

    Tool-call arguments were accumulated in silence. For a small call that is invisible and
    correct; for a large one it is the whole minute in which the interface shows nothing at all,
    and a person watching a page get written sees a spinner and concludes it has hung. The
    completed call arrives with `21,591 chars` on it — the one moment the size no longer needs
    reporting.

    The path is pulled out of the half-written JSON when it is there, because "writing
    wukong-site/index.html" is a different sentence from "writing". `path` is conventionally the
    first key of the tools that write, so it is usually complete long before the body is; when it
    is not, this returns nothing for it and the interface says what it does know.
    """
    grown = len(slot["arguments"])
    if grown - int(slot.get("said") or 0) < _WRITING_EVERY:
        return None
    slot["said"] = grown
    event = {"type": "writing", "name": slot.get("name") or "", "chars": grown}
    found = re.search(r'"(?:path|file|target)"\s*:\s*"((?:[^"\\]|\\.)*)"', slot["arguments"])
    if found:
        event["path"] = found.group(1)
    return event


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
    usage: dict | None,
    elapsed: float,
    model: str = "",
    provider: str = "",
    finish: str = "",
    generation: str = "",
) -> dict[str, str | int | float]:
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
        # Why the response stopped. It travels with the numbers rather than as its own field on
        # the `turn` event because this dict already goes everywhere the fact is needed — the
        # loop reads it to tell a cut-off round from a quiet one, and the transcript keeps it,
        # which is what makes a round diagnosable a day later instead of only while it happens.
        "finishReason": finish,
        # The provider's id for this round, so a number here can be taken back to the row it
        # came from. Blank on any host that does not send one.
        "generationId": generation,
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
