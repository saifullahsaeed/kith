"""The agentic loop.

Wraps the single-turn Ollama client in a ReAct-style loop: call the model with
the tool declarations; if it asks to call tools, run them against the agent's
database, feed the results back, and call the model again — until it produces a
final answer with no tool calls (or a round cap is hit).

Yields the same delta/stats/error events as a plain chat, plus:
- ``{"type": "tool_call", "id", "name", "arguments"}``   — the agent is calling a tool
- ``{"type": "tool_result", "id", "name", "result"}``    — what the tool returned
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from kith import tools
from kith.config import Config
from kith.domain.tool_markup import ToolMarkupFilter
from kith.llm import ollama, openai_compat
from kith.services import tuning

# Tools that may run concurrently with each other. The bar is deliberately high:
# each one must be network-bound (so overlapping actually saves wall-clock), free
# of side effects, and indifferent to what the others are doing. Everything else —
# every database write, every shell command, anything touching his files — stays
# strictly serial, because with those the order *is* the meaning.
_PARALLEL_SAFE = frozenset({"web_search", "fetch_url", "browse_page", "search_sources"})

# Enough to collapse the batches of six searches he actually makes, low enough
# that a round can't open dozens of sockets (or docker execs) at once.

# Rounds held back at the end of every turn for *landing* the work. Without a
# reserve, research expands to fill the entire budget: he spends all 40 rounds
# gathering, gets cut off mid-sentence, and the turn ends having produced nothing
# durable — so the next turn re-reads its notes and researches the same ground
# again, forever. The last few rounds are therefore taken away from gathering and
# given to writing it down.
#
# Landing is cheap — writing a file and ticking off checklist items is a couple of
# rounds, and several calls fit in one round — so this stays small. It is also
# capped at a third of the budget below, since a tick's budget varies (autonomy
# ticks get 16, not the full MAX_ROUNDS) and a fixed reserve could otherwise eat
# most of a short turn.

# What he may still do once he's landing: record, deliver, tick things off, hand
# back. Notably *not* search or fetch — the point of the reserve is that gathering
# is over. read_file stays because his working notes are where the answer lives.
_LANDING_TOOLS = frozenset(
    {
        "add_deliverable",
        "check_item",
        "add_checklist_item",
        "update_task",
        "comment_on_task",
        "ask_on_task",
        "view_task",
        "list_tasks",
        "write_file",
        "read_file",
        "take_note",
        "journal",
        "remember",
        "reach_out",
    }
)

# Handing work to himself for later. Creating a task or a project is a decision that
# the work happens *on a future tick* — so continuing to research it in the same turn
# is doing the thing he just decided to defer, and doing it with the rounds he has
# left rather than the whole budget a tick would give it. He would file a task and then
# burn nineteen tool calls on it immediately, which is neither delegating nor finishing.
_DELEGATION_TOOLS = frozenset({"add_task", "create_project"})

# What he may still do once he has delegated: finish describing the plan and tell you
# about it. Gathering is over — that is the point.
_PLANNING_TOOLS = frozenset(
    {
        "add_milestone",
        "add_checklist_item",
        "update_project",
        "update_task",
        "view_task",
        "list_tasks",
        "comment_on_task",
        "ask_on_task",
        "take_note",
        "journal",
        "remember",
    }
)

_DELEGATED_DIRECTIVE = (
    "(You've handed that to yourself as work for later, so stop working on it now — "
    "that's what the task is for, and you'll have a whole tick's budget for it. "
    "Finish describing the plan if it needs it, then tell them what you've set up and "
    "what you'll do first.)"
)

# Anything that leaves a trace behind after the turn ends. Broader than the loop
# detector's notion of progress in autonomy.py — that one deliberately excludes
# write_file (he "wrote files" while looping, but they were raw page dumps). Here
# the question is only "did he record ANYTHING", so writing a file counts.
_PERSISTED_TOOLS = frozenset(
    {
        "write_file",
        "add_deliverable",
        "check_item",
        "add_checklist_item",
        "update_task",
        "comment_on_task",
        "ask_on_task",
        "take_note",
        "journal",
        "remember",
        "reach_out",
        "update_project",
        "update_milestone",
    }
)

_LANDING_DIRECTIVE = (
    "(You're near the end of this turn's tool budget, so stop gathering — you have enough. "
    "Spend what's left LANDING the work: write what you've found into your working file, "
    "add_deliverable for anything finished, check_item the checklist steps you've actually "
    "completed, and comment_on_task or ask_on_task if you need something from your person. "
    "Research you never wrote down is research you'll have to redo next time.)"
)


def _stream_once(messages, config: Config, host, tools=None, tool_choice: str = "auto"):
    """Route to the cloud model when a key+endpoint are set, else local Ollama."""
    if config.api_key and config.base_url:
        return openai_compat.stream_once(messages, config, host, tools=tools, tool_choice=tool_choice)
    # Ollama has no tool_choice; withholding the schemas is the only lever there.
    return ollama.stream_once(messages, config, host, tools=None if tool_choice == "none" else tools)


# How many tool rounds a single turn may take before we make it wrap up. A long
# loop is fine — that's how real agents do multi-step work; what has to stay small
# is the *payload each round carries* (see the grep/ranged-read/spill-to-file
# tools and prompt-cache alignment). The thrash-guard stops genuine spinning.

# Process-wide token meter. Every model call — chat and autonomy alike — flows
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
    """Total tokens spent since the server started (chat + autonomy).

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


#: Arguments that can be an entire file. Worth everything while the call is being made and
#: nothing once it has run — the result already says whether it worked.
_BULKY_ARGS = ("content", "new", "old")


def _compact_call_arguments(convo: list[dict[str, Any]]) -> None:
    """An old write keeps its filename and lets go of the file.

    The third channel into the conversation, and the last one nothing pruned. `read_file` and
    `shell` cap their *output* at 8,000 characters, and the compactor trims those results as
    they age — but `write_file`'s `content` argument IS the file, and it travels on the
    assistant message rather than the tool result. So writing a 40KB file put 40KB in the
    conversation for every remaining round of the turn, past every limit in the system,
    because none of them are looking at the calls.

    Safe to drop for the same reason a seen picture is: by the time it ages out, the call has
    run and its result is right there saying so. The arguments stay valid JSON with the path
    intact, so he can still see what he wrote and where.
    """
    keep_whole = tuning.value("keep_full_tool_results")
    stub_chars = tuning.value("tool_stub_chars")
    calls = [i for i, m in enumerate(convo) if m.get("role") == "assistant" and m.get("tool_calls")]
    for i in calls[:-keep_whole] if keep_whole > 0 else calls:
        for call in convo[i].get("tool_calls") or []:
            function = call.get("function") or {}
            raw = function.get("arguments")
            if not isinstance(raw, str) or len(raw) <= stub_chars:
                continue
            try:
                args = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(args, dict):
                continue
            trimmed = False
            for key in _BULKY_ARGS:
                value = args.get(key)
                if isinstance(value, str) and len(value) > stub_chars:
                    args[key] = f"…[{len(value):,} characters, sent earlier in this turn]"
                    trimmed = True
            if trimmed:
                function["arguments"] = json.dumps(args)


def _compact_images(convo: list[dict[str, Any]]) -> None:
    """Let go of pictures he has already looked at.

    An image rides as its own user message, and `_compact_tool_history` only ever walked
    messages with ``role == "tool"`` — so a picture, once in, re-sent in full on every
    remaining round of the turn and nothing could take it out. Fifteen rounds of that is
    fifteen times the cost of looking once.

    Dropping it is safe in a way dropping a tool result is not: he has already seen it, and
    the round he saw it in is where he says what it showed. That sentence stays. If he needs
    another look the file is still on disk and reading it again costs one image, not fifteen.

    The label is kept — "Here is work/page-1.jpg:" — so he can tell the difference between a
    picture he looked at and one he never opened.
    """
    keep = tuning.value("keep_images")
    seen = [i for i, m in enumerate(convo) if _carries_image(m)]
    stale = seen[:-keep] if keep > 0 else seen
    for i in stale:
        parts = convo[i].get("content")
        if not isinstance(parts, list):
            continue
        label = next(
            (p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"),
            "An image was here.",
        )
        convo[i]["content"] = (
            f"{label} [you looked at this picture earlier; it has been set down to save room. "
            "Read the file again if you need another look.]"
        )


def _carries_image(message: dict[str, Any]) -> bool:
    parts = message.get("content")
    return isinstance(parts, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in parts
    )


def _compact_tool_history(convo: list[dict[str, Any]]) -> None:
    """Stub the oldest tool outputs once the live set outgrows its char budget.

    Tool output re-sends in full on every subsequent round, so a long research turn
    would balloon without pruning. The pruning has to be sized to the model actually
    in use: a hard "keep the last four" is ruinous on a 1M-context model, where he
    forgets the six searches he ran two rounds ago and re-runs them, tick after tick.

    So the live set is bounded by characters rather than by count — keep the newest
    results whole until the budget is spent, and only then start stubbing. All three
    numbers are settings, read here rather than at import so raising them for a
    bigger model takes effect on the next turn instead of the next restart.
    """
    keep_whole = tuning.value("keep_full_tool_results")
    char_budget = tuning.value("live_tool_chars")
    stub_chars = tuning.value("tool_stub_chars")
    tool_positions = [i for i, m in enumerate(convo) if m.get("role") == "tool"]

    # Walk newest-first, spending the budget on the most recent results.
    keep: set[int] = set()
    spent = 0
    for rank, i in enumerate(reversed(tool_positions)):
        size = len(convo[i].get("content") or "")
        if rank < keep_whole or spent + size <= char_budget:
            keep.add(i)
            spent += size
        else:
            break  # everything older than the first eviction goes too

    for i in tool_positions:
        if i in keep:
            continue
        msg = convo[i]
        content = msg.get("content") or ""
        if len(content) > stub_chars and not msg.get("_stubbed"):
            name = msg.get("tool_name", "tool")
            msg["content"] = (
                content[:stub_chars] + f"\n…[earlier {name} output trimmed to save room — "
                "if you still need it, save what matters to a file next time; re-run the tool to see it again]"
            )
            msg["_stubbed"] = True


def stream_agent(
    messages: list[dict[str, Any]],
    config: Config,
    host: str,
    agent_db_path: Path,
    max_rounds: int | None = None,
    allow: set[str] | None = None,
    expect_durable: bool = False,
    conversation_id: str = "",
) -> Iterator[dict]:
    """Run the tool loop.

    ``expect_durable`` says whether a turn that records nothing is a failure. For an
    autonomy tick it is — the whole point of a tick is to leave something behind, and one
    that finishes empty means the next tick redoes the work. For a chat turn it is the
    normal outcome: answering a question is the deliverable, and there is nothing to file.

    ``conversation_id`` picks the OpenRouter stickiness id. A conversation is the right
    unit for it: every round in it shares a prompt prefix, and shares it with nothing else,
    so keeping one conversation on one upstream is what keeps its cache warm.
    """
    convo = list(messages)
    if conversation_id:
        from kith.services import conversations

        session = conversations.session_id(agent_db_path, conversation_id)
        if session:
            config = replace(config, session_id=session)
    call_index = 0
    seen_calls: dict[str, int] = {}  # (name+args) -> times run, to stop thrashing
    budget = max_rounds or tuning.value("max_rounds")
    reserve = min(tuning.value("landing_reserve"), max(2, budget // 3))
    # Every MCP tool, frozen for this turn. Taken once rather than per round on purpose: the
    # tools block is part of the cached prompt prefix, so a server dying — or being switched
    # off in another tab — would shrink it mid-turn and discard the whole cache on the next
    # round. Held even when the server has gone; the *call* then fails with something
    # readable, which costs one tool result instead of the entire prefix.
    from kith.services.mcp import manager as mcp_manager

    mcp_tools = mcp_manager.snapshot()
    # The tool list as the last round actually saw it, kept for the forced final answer.
    # That request used to build its own with `tool_schemas(agent_db_path)` and no `only`,
    # so a breakout tick offering six tools ended by sending all fifty-nine — a different
    # tools block from every other round in the turn, which on the providers that need an
    # explicit breakpoint sits ahead of the system prompt and rewrites the whole cached
    # prefix for the one request the turn cannot skip.
    schemas: list[dict] = []
    landing = False
    delegated = False  # did he hand this to a future tick?
    persisted = False  # did anything this turn leave a trace?
    nudged = False  # the "don't walk away empty-handed" nudge fires at most once

    for round_index in range(budget):
        # Keep only the most recent tool outputs full; stub older ones. Without this,
        # a page he fetched 30 tool-calls ago re-sends in full every round — that's
        # what turned one browse-heavy tick into 500k tokens. Stubbing forces him to
        # ACT on what he just read (write it to his working file) instead of hoarding
        # dozens of pages in context, and keeps the loop affordable enough to reach
        # the "compile & deliver" phase.
        # Three channels carry bulk into a conversation, and for a long time only the first
        # was watched: tool results, pictures, and the arguments of a write. Every one of
        # them re-sends in full on every round until something trims it.
        _compact_tool_history(convo)
        _compact_images(convo)
        _compact_call_arguments(convo)
        # Re-read tools each round so a tool Kith just built is usable right away.
        # `allow` scopes the toolset to the current mode (fewer tokens, sharper focus).
        schemas = tools.tool_schemas(agent_db_path, only=allow, mcp=mcp_tools)

        # Hand the reserve over to landing — once, so the directive isn't repeated.
        if not landing and round_index >= budget - reserve:
            landing = True
            convo.append({"role": "user", "content": _LANDING_DIRECTIVE})
        if landing:
            schemas = [s for s in schemas if s["function"]["name"] in _LANDING_TOOLS]
        elif delegated:
            # Not the same as landing: he keeps his remaining rounds and may still
            # flesh out the plan. What he loses is the ability to *do* the work —
            # no searching, no fetching, no shell.
            allowed = _LANDING_TOOLS | _PLANNING_TOOLS
            schemas = [s for s in schemas if s["function"]["name"] in allowed]

        # What he was actually offered this round, and — from here on — what he may actually
        # run. Derived from the finished list rather than from `allow`, so all three
        # narrowings above are enforced by the same line and a fourth one added later cannot
        # forget to be.
        #
        # Until now none of them were enforced at all. Every one only ever reached
        # `tool_schemas(only=...)`, which decides what the model is *shown*; `run_tool`
        # resolved any name against the whole registry and ran it. Measured: `breakout` mode
        # offers six tools, and calling `remember` or `add_task` from it both succeeded. So
        # the landing reserve — the thing that stops a turn gathering until it runs out of
        # rounds — was a suggestion, and a model that named a search tool anyway got one.
        permitted = {s["function"]["name"] for s in schemas}

        content = ""
        tool_calls: list[dict] = []
        stats: dict | None = None

        for event in _stream_once(convo, config, host, tools=schemas):
            kind = event["type"]
            if kind == "delta":
                yield event  # forward reasoning/answer tokens
            elif kind == "error":
                yield event
                return
            elif kind == "turn":
                content = event["content"]
                tool_calls = event["tool_calls"]
                stats = event["stats"]

        # Count and surface every round's tokens — tool rounds are the bulk of the
        # cost, so counting only final answers hides almost all of it.
        stats = measured(stats)
        _record(stats)
        if stats:
            yield {"type": "stats", "stats": stats}

        if not tool_calls:
            # He's finished talking. For a TICK, a turn that ends having recorded
            # nothing is a turn that never happened: the next one starts from the same
            # blank slate and redoes the same work. Measured on real ticks — every one
            # that produced durable output was one that ran out of rounds and hit the
            # landing phase by accident; every early-finishing turn produced nothing.
            # So the reserve can't be gated on exhausting the budget. Spend it here,
            # once, and only when there's genuinely nothing to show.
            #
            # In CHAT it is the opposite. Saying "why what?" is a complete answer and
            # there is nothing to file, so nudging him to land work he never started
            # doubled the cost of every trivial message — two model requests each
            # carrying the full persona and 51 tool schemas, ~18,500 tokens to answer
            # one word — and the second reply was him puzzling at a directive that made
            # no sense: "I haven't been researching anything this turn."
            if not expect_durable or persisted or landing or nudged or round_index >= budget - 1:
                return
            nudged = True
            landing = True
            if content:
                convo.append({"role": "assistant", "content": content})
            convo.append({"role": "user", "content": _LANDING_DIRECTIVE})
            continue

        # Record the assistant's tool-calling turn so the model has context.
        convo.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        # Resolve every call in the round up front (ids, thrash-guard) so the only
        # thing left is running them — which lets a run of network-bound calls go
        # out concurrently instead of queueing behind each other.
        planned = []
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            arguments = _arguments(function.get("arguments"))
            call_id = f"c{call_index}"
            call_index += 1

            # Thrash-guard: if the model repeats the exact same call, don't keep
            # running it — feed back a nudge to change approach or wrap up.
            sig = f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}"
            seen = seen_calls.get(sig, 0)
            seen_calls[sig] = seen + 1
            repeat = seen >= 2
            planned.append({"id": call_id, "name": name, "arguments": arguments, "repeat": repeat})

        for batch in _batches(planned):
            for step in batch:
                yield {
                    "type": "tool_call",
                    "id": step["id"],
                    "name": step["name"],
                    "arguments": step["arguments"],
                }

            if len(batch) == 1:
                results = [_run(batch[0], agent_db_path, permitted)]
            else:
                # He asks for six searches at once and each takes seconds; run them
                # together. Order of the *results* is still the order he asked in, so
                # the transcript he reads back is unchanged.
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    # `permitted` bound as a default rather than closed over: the lambda is
                    # consumed inside this iteration so a late read would be safe today, but
                    # the gate is the one value in here that must never be read from the
                    # wrong round.
                    results = list(
                        pool.map(lambda step, allow=permitted: _run(step, agent_db_path, allow), batch)
                    )

            # strict: results is a map over batch, so a length mismatch is a bug, not input.
            for step, result in zip(batch, results, strict=True):
                worked = isinstance(result, dict) and result.get("ok", True)
                if step["name"] in _PERSISTED_TOOLS and worked:
                    persisted = True
                if step["name"] in _DELEGATION_TOOLS and worked and not delegated:
                    delegated = True
                    if tuning.value("stop_after_delegating"):
                        convo.append({"role": "user", "content": _DELEGATED_DIRECTIVE})
                    else:
                        delegated = False  # the guardrail is switched off
                yield {"type": "tool_result", "id": step["id"], "name": step["name"], "result": result}
                image = _image_from(result)
                if image:
                    # A tool result is a JSON string and cannot carry an image part, so the
                    # picture arrives as the next message instead. Without this he could take a
                    # screenshot and never see it — which is exactly what he was doing while
                    # redesigning a UI. The data URI is taken out of the tool result so the
                    # same 600KB is not also sitting there as base64 text.
                    convo.append(
                        {
                            "role": "tool",
                            "tool_name": step["name"],
                            "content": json.dumps(_without_image(result)),
                        }
                    )
                    convo.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"Here is {result.get('path', 'the image')}:"},
                                {"type": "image_url", "image_url": {"url": image}},
                            ],
                        }
                    )
                    continue
                convo.append({"role": "tool", "tool_name": step["name"], "content": json.dumps(result)})

    # Out of tool budget — force a final answer so there's always a reply.
    yield from _final_answer(convo, config, host, schemas)


def _image_from(result: Any) -> str:
    """A data URI a tool wants the model to look at, or "".

    One key, checked in one place. Tools that produce pictures — reading a screenshot today,
    rendering something tomorrow — opt in by returning it, and nothing else in the loop needs
    to know which tools those are.
    """
    if not isinstance(result, dict):
        return ""
    inner = result.get("result") if isinstance(result.get("result"), dict) else result
    value = inner.get("image") if isinstance(inner, dict) else None
    return value if isinstance(value, str) and value.startswith("data:image/") else ""


def _is_data_uri(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("data:image/")


def _without_image(result: Any) -> Any:
    """The tool result with the base64 taken out, at whichever level it sits.

    This existed as ``{**result, "image": "(shown below)"}`` and did nothing, which cost a
    real afternoon and is worth writing down. A tool result arrives wrapped — ``{"ok": true,
    "result": {...}}`` — and the data URI is on the *inner* dict. Spreading the outer one and
    setting ``image`` there added a decorative key beside the envelope and left all 228,000
    characters of base64 exactly where they were.

    So every picture went into the conversation twice: once as a real image part, which a
    provider counts as a few hundred tokens, and once as raw base64 text, which it counts at
    roughly one token per character. Measured on the turn that found this: the prompt went
    from 61,844 tokens to 616,415 in a single round and stayed there for fifteen more —
    2.8 million tokens to look at three pages of a CV, with the comment directly above the
    line claiming the opposite.

    Both levels are cleared, because being right about only the shape we happen to send today
    is what produced the bug in the first place.
    """
    if not isinstance(result, dict):
        return result
    out = dict(result)
    inner = out.get("result")
    if isinstance(inner, dict) and _is_data_uri(inner.get("image")):
        out["result"] = {**inner, "image": _SHOWN}
    if _is_data_uri(out.get("image")):
        out["image"] = _SHOWN
    return out


#: What replaces a data URI once the picture is travelling as a real image part.
_SHOWN = "(shown to you as a picture below)"


def _run(step: dict, agent_db_path: Path, allow: set[str] | None = None) -> Any:
    if step["repeat"]:
        return {
            "note": "You've already made this exact call twice and it didn't move things "
            "forward. Stop repeating it — take a different approach, or give your final answer."
        }
    return tools.run_tool(step["name"], step["arguments"], agent_db_path, allow=allow)


def _batches(planned: list[dict]) -> Iterator[list[dict]]:
    """Split a round's calls into groups that may run together.

    Consecutive parallel-safe calls travel as one batch; anything else goes alone.
    Splitting on *consecutive* runs rather than gathering all safe calls keeps the
    relative order of safe and unsafe work intact — so a write that he sequenced
    after a fetch still happens after it.
    """
    at_once = tuning.value("max_parallel")
    batch: list[dict] = []
    for step in planned:
        if step["name"] in _PARALLEL_SAFE and not step["repeat"] and len(batch) < at_once:
            batch.append(step)
            continue
        if batch:
            yield batch
            batch = []
        if step["name"] in _PARALLEL_SAFE and not step["repeat"]:
            batch.append(step)  # a full batch just flushed; start the next
        else:
            yield [step]
    if batch:
        yield batch


def _final_answer(
    convo: list[dict[str, Any]], config: Config, host: str, schemas: list[dict] | None = None
) -> Iterator[dict]:
    """The last round: he must answer, and may not call anything.

    The schemas are still sent, with ``tool_choice="none"`` to forbid using them. That
    combination looks redundant and is not: asked to stop by prose alone, with the tool
    definitions removed from the request, a model part-way through a tool-using turn
    keeps producing calls as *prose* — ``<FUNCTION>web_search(query="…")</FUNCTION>`` —
    which is indistinguishable from an answer and lands in the transcript and in
    whatever he files. Telling the API rather than the model is what actually stops it.
    """
    convo.append(
        {
            "role": "user",
            "content": (
                "(You've used your tool budget for this turn. Stop calling tools and give "
                "your best final answer now with what you have.)"
            ),
        }
    )
    stats: dict | None = None
    # Belt and braces for a model that narrates a call anyway: nothing can run at this
    # point, so the markup is pure noise — and it would otherwise be stored as if it
    # were his answer. Stateful because a tag can straddle two deltas.
    scrub = ToolMarkupFilter()
    for event in _stream_once(convo, config, host, tools=schemas, tool_choice="none"):
        kind = event["type"]
        if kind == "delta":
            if event.get("role") == "text":
                text = scrub.feed(event["text"])
                if not text:
                    continue
                event = {**event, "text": text}
            yield event
        elif kind == "error":
            yield event
            return
        elif kind == "turn":
            stats = event["stats"]
    tail = scrub.flush()
    if tail:
        yield {"type": "delta", "role": "text", "text": tail}
    stats = measured(stats)
    _record(stats)
    if stats:
        yield {"type": "stats", "stats": stats}


def _arguments(raw: Any) -> dict:
    """Ollama usually gives tool arguments as an object; tolerate a JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return {}
