"""What a turn cost, tallied for the whole process.

Every model call in Kith flows through the loop, so this is the one true figure for what he
costs. Lifted out of `agent_loop` unchanged: it shares nothing with the round loop but the
`_record` call after each response, which is why it is the first thing that could leave.

`_usage` is process-global by design — a second process gets its own tally, and a test that
wants a clean one saves and restores it (see `tests/test_what_it_cost.py`).
"""

from __future__ import annotations

# Process-wide token meter. Every model call flows
# through stream_agent, so this is the one true tally of what Kith costs. Read it
# with usage_snapshot(); it counts every round, not just final answers.
_usage = {
    "promptTokens": 0,
    "responseTokens": 0,
    "reasoningTokens": 0,
    "costUsd": 0.0,
    "cachedTokens": 0,
    "cacheWriteTokens": 0,
    "uncachedTokens": 0,
    "calls": 0,
}


def measured(stats: dict | None) -> dict | None:
    """Add what a request actually had to read, as opposed to what it was shown.

    ``promptTokens`` counts the whole prompt, and on a warm cache most of that is a
    re-read of bytes the provider already holds — 10,949 of 19,494 on a measured turn.
    Sixteen rounds of it looks like a quarter of a million tokens spent when the real
    figure is a fraction of that. ``uncachedTokens`` is the part that was new.

    One place for the subtraction, because it is the number people will read and two
    surfaces disagreeing about it would be worse than not showing it. Every model call
    in the process passes through here, chat and ticks alike.

    Ollama needs no special case: ``prompt_eval_count`` is already only the part it had
    to evaluate, and it reports no cache field, so the subtraction is a no-op and the
    number means the same thing on both transports.
    """
    if not stats:
        return stats
    prompt = int(stats.get("promptTokens") or 0)
    cached = int(stats.get("cachedTokens") or 0)
    return {**stats, "uncachedTokens": max(prompt - cached, 0)}


def usage_snapshot() -> dict:
    """Total tokens spent since the server started.

    `cachedTokens` is the slice of prompt tokens the provider served from its prefix
    cache, and `cacheWriteTokens` is what it charged 1.25x to *put* there — the real,
    on-your-traffic measure of whether caching is helping or just costing.

    Both are needed, because a hit rate on its own cannot tell the two apart. A
    breakpoint in the wrong place produced a full write on every single request and
    never a read, and the hit rate reported that as an unremarkable 0% — identical to
    having no caching at all, while actually costing 25% more. `cacheEfficiency` is the
    ratio that makes it visible: above 1 means reads are outrunning writes.

    `uncachedTokens` is the prompt side with the cache hits taken out — the tokens he
    actually made a provider read.

    `costUsd` is what all of the above is a proxy for, and it is not estimated: OpenRouter
    returns it per call and this adds it up. Measured on a real "say pong" turn — 38,116
    prompt tokens, 20,700 of them cached, for $0.002. Reading the token count as spend is
    off by whatever the cache saved, which here was more than half.

    `cacheEfficiency` is None when the provider reports no cache writes, which is most of
    them. It used to divide by `writes or 1`, so with zero writes it returned the read count
    unchanged — 20,700 from a field documented as "above 1 means reads are outrunning
    writes". A raw count wearing a ratio's name, and indistinguishable from a genuine
    20,700-to-1. Providers differ here: OpenAI caches automatically and charges nothing to
    write, so there is nothing to compare against and saying so is the only honest answer.
    """
    snap = dict(_usage)
    prompt = snap["promptTokens"] or 1
    snap["cacheHitRate"] = round(snap["cachedTokens"] / prompt, 3)
    writes = snap["cacheWriteTokens"]
    snap["cacheEfficiency"] = round(snap["cachedTokens"] / writes, 2) if writes else None
    snap["costUsd"] = round(snap["costUsd"], 6)
    return snap


def _record(stats: dict | None) -> None:
    if not stats:
        return
    _usage["promptTokens"] += int(stats.get("promptTokens") or 0)
    _usage["responseTokens"] += int(stats.get("responseTokens") or 0)
    _usage["cachedTokens"] += int(stats.get("cachedTokens") or 0)
    _usage["cacheWriteTokens"] += int(stats.get("cacheWriteTokens") or 0)
    _usage["uncachedTokens"] += int(stats.get("uncachedTokens") or 0)
    _usage["reasoningTokens"] += int(stats.get("reasoningTokens") or 0)
    # Dollars, as billed. Every other number here is a proxy for this one.
    _usage["costUsd"] += float(stats.get("costUsd") or 0.0)
    _usage["calls"] += 1
