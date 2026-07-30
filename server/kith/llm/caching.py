"""Making OpenRouter's prompt cache actually work, per provider.

A tool loop is the ideal shape for prompt caching and the worst shape to get wrong. Each
round re-sends everything before it: the persona, all 51 tool schemas, and every tool
result so far. Sixteen rounds of that is the same ~8,700-token prefix billed sixteen
times, plus a conversation that grows monotonically.

The catch is that providers do not agree on how caching is asked for, and OpenRouter
passes that difference through:

* **Automatic** — OpenAI, DeepSeek, Groq, Moonshot, Grok, Z.AI, Gemini 2.5 implicit.
  Nothing to send. Reads bill at 0.1x to 0.5x.
* **Explicit** — Anthropic, Alibaba Qwen, Gemini per-block. Without a ``cache_control``
  breakpoint there is **no caching at all**.

That second case was silently costing real money. Measured on ``claude-sonnet-5`` with an
identical 13.3k-token prefix:

    no breakpoint     $0.006492, then $0.006492   — never cached, every round full price
    with breakpoint   $0.033370, then $0.002721   — writes once at 1.25x, then reads at 0.1x

So a Claude round was paying full price forever, and Claude is what this project's own
model picker recommends as the strongest option. Sending the breakpoint is worth ~12x on
the prefix from the second round on.

Sending one where it is not needed is harmless — verified 200 on Moonshot — but it is
still only sent to the families that need it, so a request carries nothing it cannot use.

Reference: https://openrouter.ai/docs/guides/best-practices/prompt-caching
"""

from __future__ import annotations

import uuid
from typing import Any

#: Model-id prefixes whose providers need an explicit ``cache_control`` breakpoint.
#: Matched on the author segment because that is what identifies the provider family —
#: ``anthropic/claude-opus-5``, ``qwen/qwen3-coder-plus``, ``google/gemini-3.6-flash``.
NEEDS_BREAKPOINT = ("anthropic/", "qwen/", "google/")

#: Smallest prefix worth a breakpoint, in tokens. Providers refuse to cache below their
#: own minimum (1,024 for most, 4,096 for Anthropic's Opus and Haiku 4.5 and for Gemini
#: Pro), and a write that is never read still bills at 1.25x. Set at the highest of
#: those, so a breakpoint is only ever sent where every provider in the family will
#: honour it.
MIN_CACHEABLE_TOKENS = 4_096

#: Rough chars-per-token for English prose and JSON. Only used to decide whether a
#: prefix clears the minimum, where being off by 15% changes nothing.
_CHARS_PER_TOKEN = 3.7

#: A conversation this long is worth caching incrementally as well as at the prefix:
#: by the tenth round of a research turn the tool results dwarf the persona.
MIN_CONVERSATION_CHARS = MIN_CACHEABLE_TOKENS * _CHARS_PER_TOKEN


def needs_breakpoint(model: str) -> bool:
    """Does this model's provider require an explicit breakpoint to cache at all?"""
    return model.strip().lower().startswith(NEEDS_BREAKPOINT)


def big_enough(chars: int) -> bool:
    """Is the cacheable prefix over the smallest size any provider will cache?

    Counted in characters over the WHOLE prefix, not just the system text. On Anthropic
    the tool definitions sit ahead of the system prompt in the cache ordering, so a
    breakpoint on the system block caches both — and measuring only the system message
    is how this first shipped doing nothing at all: a 9,082-character persona looked too
    small, while persona plus 51 tool schemas is 32,205 characters and comfortably over.
    """
    return chars >= MIN_CACHEABLE_TOKENS * _CHARS_PER_TOKEN


def apply(messages: list[dict[str, Any]], model: str, prefix_extra_chars: int = 0) -> list[dict[str, Any]]:
    """Mark the cacheable boundaries in a message list.

    Two breakpoints, which is the shape a tool loop wants and well inside Anthropic's
    limit of four:

    1. **The end of the system prompt.** The persona and tool schemas are byte-identical
       on every round of every turn, so this is the prefix that pays off most. For
       Anthropic the cache covers tools *and* system, since tools sit ahead of system in
       their ordering.
    2. **The last message.** The conversation only ever grows, so round N reads what
       round N-1 wrote. Without this, the tool results — which are most of a long turn —
       are re-billed in full every round.

    ``prefix_extra_chars`` is anything cached alongside the system prompt but not part of
    the message list — the tool schemas, which are their own request field yet share the
    cached region.

    Returns a new list; the caller's messages are not touched, because they are the
    agent loop's live history and mutating them would leak cache markers into the next
    round's copy.
    """
    if not needs_breakpoint(model) or not messages:
        return messages

    marked = [dict(message) for message in messages]

    for message in marked:
        if message.get("role") != "system":
            continue
        text = str(message.get("content") or "")
        if big_enough(len(text) + prefix_extra_chars):
            message["content"] = _with_breakpoint(text)
        break

    # Only when there is enough conversation to be worth a second write. Below that the
    # 1.25x write costs more than the read saves.
    body = sum(len(str(m.get("content") or "")) for m in marked[1:])
    if body >= MIN_CONVERSATION_CHARS:
        last = marked[-1]
        # A tool result or an assistant turn with tool_calls has content that other
        # fields depend on; only plain string content is safe to restructure.
        if isinstance(last.get("content"), str) and last["content"]:
            last["content"] = _with_breakpoint(last["content"])

    return marked


def _with_breakpoint(text: str) -> list[dict[str, Any]]:
    """One text block carrying the cache marker.

    ``ephemeral`` with no TTL takes the provider's default (five minutes on Anthropic
    and Qwen). A one-hour TTL is available but bills writes at 2x instead of 1.25x, and
    a tool loop re-reads its prefix within seconds — the long TTL is for a prefix reused
    across sessions, which this is not.
    """
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def session_id(stored: dict[str, Any], remember) -> str:
    """A stable id that keeps his requests landing on the same upstream host.

    OpenRouter spreads a model's traffic across providers, and a cache lives on one
    host — so consecutive rounds of the same turn could each land somewhere cold. This
    is OpenRouter's own answer to that (``session_id``, max 256 chars) and it is a
    better one than pinning a provider by name: stickiness is a preference, so
    availability still falls back, whereas a pin does not.

    Persisted rather than per-process, so a restart does not throw away a warm cache.
    """
    existing = str(stored.get(SESSION_KEY) or "").strip()
    if existing:
        return existing
    fresh = f"kith-{uuid.uuid4().hex[:16]}"
    remember(fresh)
    return fresh


SESSION_KEY = "cache_session_id"
