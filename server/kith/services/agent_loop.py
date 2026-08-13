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
import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from kith import tools
from kith.domain.chat import Config
from kith.domain.tool_markup import ToolMarkupFilter
from kith.llm import ledger, ollama, openai_compat
from kith.llm.budget import ContextBudget, conversation_chars
from kith.services import compaction, tuning

# Tools that may run concurrently with each other. The bar is deliberately high:
# each one must be network-bound (so overlapping actually saves wall-clock), free
# of side effects, and indifferent to what the others are doing. Everything else —
# every database write, every shell command, anything touching his files — stays
# strictly serial, because with those the order *is* the meaning.
_PARALLEL_SAFE = frozenset({"web_search", "fetch_url", "browse_page", "search_sources"})

# Tools whose entire contract is "call again with the same arguments to check on it" — see
# `run_tests`'s own tool description, and `check_process`, which is watching something
# precisely because it has not changed yet. The thrash-guard below exists to catch calling
# the same thing again with nothing to show for the last attempt; for these two, calling
# again *is* what showing something for it looks like, not a sign of being stuck.
_POLL_TOOLS = frozenset({"run_tests", "check_process"})


#: How many times one round's model call is attempted before the turn gives up on it.
#:
#: Three, and the reason it is not more is that the fallback is not "die" — it is landing, which
#: is itself a request. A provider still refusing after three tries spread over seven seconds is
#: not going to be talked round by a fourth, and every attempt costs the person wall-clock while
#: they watch nothing happen.
_ROUND_ATTEMPTS = 3


#: 1s, 2s, 4s. Short, because the failures this catches are transient by definition — a dropped
#: connection, an upstream briefly out of instances — and a long wait on a turn someone is
#: watching is indistinguishable from a hang.
def _backoff(attempt: int) -> float:
    return 1.0 * (2 ** (attempt - 1))


#: A failure worth sending the same request into again. Matched on the message because that is
#: what the transport gives us: `_stream_once` flattens every provider failure into
#: `{"type": "error", "message": str}`, and re-plumbing a code through both providers to serve
#: one decision is a larger change than the decision is worth.
#:
#: Transport failures and 5xx/429 are the whole retryable set. A 400 or a 401 is the server
#: saying the request is wrong or unpaid, and it will say it again — only slower. Context
#: overflow has its own path (`kind == "context_overflow"`), which compacts and is not this.
_RETRYABLE = re.compile(
    r"could not reach|connection aborted|connection reset|timed out|timeout"
    r"|\b(429|500|502|503|504)\b",
    re.I,
)


def _worth_retrying(event: dict) -> bool:
    if event.get("kind") == "context_overflow":
        return False
    return bool(_RETRYABLE.search(str(event.get("message") or "")))


#: The machine has no route to the provider at all — DNS did not resolve, the connection was
#: refused, the interface is down. Distinguished from the rest of `_RETRYABLE` because it is the
#: one class of failure where *landing* is pointless: landing is itself a model call, so with the
#: network down it cannot do anything but fail three more times. A timeout or a 502 is the
#: opposite case — the network is up and the provider is merely unwell, so the reserve is worth
#: spending on writing down what was found.
#:
#: These fail *instantly*, which is the thing to know about them. `getaddrinfo` with no network
#: returns EAI_NONAME in 0.02s — measured — rather than waiting out a timeout, so the whole
#: six-attempt sequence takes about six seconds and nearly all of it is our own backoff. Half of
#: those six are the doomed landing round, which is what this saves; the other half is why the
#: retry has to leave a written record, since three seconds of a status line is not something a
#: person can be expected to have seen.
_NETWORK_DOWN = re.compile(
    r"nameresolutionerror|failed to resolve|name or service not known"
    r"|nodename nor servname|temporary failure in name resolution"
    r"|connection refused|network is unreachable|no route to host",
    re.I,
)


def _unreachable(event: dict) -> bool:
    return bool(_NETWORK_DOWN.search(str(event.get("message") or "")))


def _gave_up(event: dict, attempts: int, since: float) -> str:
    """The failure, plus what was done about it before giving up.

    The retry is otherwise invisible in hindsight: the "reconnecting" line is a live thing that
    the error then replaces, so a turn that tried six times ends up showing exactly what a turn
    that tried once shows. That is survivable when the failures are slow. It is not when they are
    a dead network — those return in hundredths of a second, so the entire sequence is over in
    about six, and asking afterwards whether the retry ran at all is a question the screen
    genuinely cannot answer. It was a reporting failure, not a retry one.
    """
    message = str(event.get("message") or "")
    if attempts <= 1:
        return message
    seconds = time.time() - since if since else 0.0
    waited = f" over {seconds:.0f}s" if seconds >= 1 else ""
    return f"{message}\n\n(tried {attempts} times{waited} before giving up)"


def _is_repeat(name: str, seen: int) -> bool:
    """Has this exact call been made enough times already to be a stall, not progress?

    One free repeat before flagging: a single retry is often legitimate, and it takes a
    second one with the same arguments and nothing changed to look like a loop rather than
    persistence. `_POLL_TOOLS` are exempt outright, regardless of `seen` — blocking them
    after two checks would mean a test suite or a build that takes ten minutes can only ever
    be checked on twice before the model is told to stop trying and answer without knowing
    the result.
    """
    return seen >= 2 and name not in _POLL_TOOLS


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
# capped at a third of the budget below, since a turn's budget varies (a caller
# ticks get 16, not the full MAX_ROUNDS) and a fixed reserve could otherwise eat
# most of a short turn.

# What he may still do once he's landing: record, deliver, check things off, hand
# back. Notably *not* search or fetch — the point of the reserve is that gathering
# is over. read_file stays because his working notes are where the answer lives.
_LANDING_TOOLS = frozenset(
    {
        "add_deliverable",
        "check_item",
        "add_checklist_item",
        "update_task",
        "view_task",
        "list_tasks",
        "write_file",
        # The two that were missing, and their absence was exactly backwards. This list had
        # `write_file` — rewrite the whole file, lossy, the one `edit_file` exists to replace —
        # and not `edit_file`. So a turn that reached its landing rounds mid-implementation was
        # left holding only the dangerous tool, on existing source it had not fully read.
        #
        # He noticed, and stopped, and said so on the task: "the available file tool exposes
        # read/write only, not an edit/patch operation, and rewriting these existing files
        # wholesale would risk unrelated code loss. Please provide/enable an edit-capable
        # tool." Which is `edit_file`'s own docstring read back to us, correctly, by something
        # we had quietly disarmed — and it cost a whole turn plus a task parked on a question
        # that had a one-line answer.
        "edit_file",
        "edit_files",
        "read_file",
        # Finishing includes checking that what you just wrote works, and then saving the
        # point. None of these is *gathering*, which is the only thing the reserve exists to
        # stop — and `commit` being absent from the one phase whose whole job is "land it" is
        # part of why five hours of work ended with no commits at all.
        "check_code",
        "diagnostics",
        "run_tests",
        "changes",
        "commit",
        "take_note",
        "journal",
        "remember",
        "reach_out",
        # The third time this list has been caught forbidding what the directive beside it
        # commands — see `edit_file` above, and `add_task` in `_PLANNING_TOOLS` below. This one
        # is the worst of the three, because `_LANDING_DIRECTIVE` names the tool outright: "and
        # `ask` if you need something from your person — it waits for the answer."
        #
        # It was not there. So the one moment the harness tells him to put a question to his
        # person is the one moment he cannot, and with nothing else to reach for a turn that is
        # genuinely stuck can only stop — which reads, from the other side, as him giving up
        # rather than as him being unable to speak. Asked what he wanted here, his person was
        # unambiguous: stopping when there is really nothing to go on is fine, but a question is
        # almost always preferable to a stop.
        #
        # Landing is also precisely when a question is most likely to be worth asking: the
        # gathering is over, so anything still missing is not going to be found by looking
        # harder. And it costs nothing to keep — `ask` blocks on an answer, so it cannot be the
        # tool a turn spins on.
        "ask",
    }
)

# Filing a task used to stop the turn, and that mechanism is gone. What stood here was a
# `delegated` latch: `add_task` or `create_project` succeeding meant "he has decided this
# happens later", so the doing-tools were taken away and a directive told him to stop working
# and describe the plan instead.
#
# It was right for a conversation that was an intake desk. `CHAT_DIRECTIVE` opened with
# "CAPTURE, DON'T DO (most important)" — file it, refuse to touch a work tool, say when you'll
# get to it — and against that, filing a task really was the end of the turn.
#
# That design was reversed (see `routes/chat.CHAT_DIRECTIVE`, which now says the opposite in
# as many words) and this outlived it. The two texts ended up in direct contradiction on the
# same tool call:
#
#     CHAT_DIRECTIVE 2:  "...A project and its first milestone's tasks... Then start on the
#                         first task in the same breath."
#     the directive:     "You've handed that to yourself as work for later, so stop working
#                         on it now."
#
# Measured over 523 recorded turns: 51 ended with a narrowed toolset and 13 of those were this,
# firing on messages like "ok lets start on this you know everything dont wait for me", "ok lets
# start with that", and "go ahed then" — three rounds in, right after filing the task those very
# messages asked for. It was the single largest cause of a turn that announced a plan and did
# nothing, and the plan it announced was the one it had just been told to stop executing.
#
# There is a real failure underneath it — filing a task and then burning nineteen rounds on it
# immediately is neither delegating nor finishing — but that is a *budget* concern, and the
# landing reserve is already the mechanism for budget. It does not need a second one keyed off
# a tool name that now means the opposite of what it meant when this was written.

#: Consecutive dead rounds a turn will absorb before it stops trying to work and starts trying
#: to land. Counted consecutively and reset by any round that succeeds, because the question
#: this is asking is "is the provider out?", and a blip at round 2 tells you nothing about
#: round 25.
#:
#: One, so a single dead round is absorbed and two in a row is an outage. The measured case for
#: absorbing the first: turn 1244 on 2026-08-12, round 2 of a 40-round budget. One round died
#: after its three attempts, the loop called that landing, and the next call went out with the
#: toolset cut from 69 schemas to the 19 landing ones — visible in the turn's own ledger as
#: built_in_tools dropping 10,601 -> 3,392 tokens — carrying a directive that told him he was
#: near the end of his budget. He was on round 2. He did as he was told: wrote a seven-point
#: plan of what he was about to do, called nothing, and ended the turn. His person asked "what
#: are you waiting for then man", and the next turn — same work, full toolset — did all of it
#: in 16 rounds and committed.
#:
#: The narrowing is not free either: swapping the tools block rewrites the cached prefix, so
#: that round billed 153,516 cache-*write* tokens and read 0.
_FAILED_ROUNDS_BEFORE_LANDING = 1

#: Spent when one round's model call died and the turn is carrying on regardless. Its job is to
#: account for the gap, because from inside the conversation there is one: the dead round left
#: no assistant message at all, so the history reads as a request he answered with silence. The
#: likeliest thing to conclude from that is that he already replied, which is how a turn talks
#: itself into stopping.
#:
#: Deliberately not `_LANDING_DIRECTIVE`. Landing says "you are near the end of your tool
#: budget", and after one bad round that is simply false — saying it costs the turn the work it
#: was in the middle of, which is the whole of what this constant exists to stop.
_ROUND_FAILED_DIRECTIVE = (
    "(The last request to the model failed and could not be completed. That was the provider, "
    "not you, and not anything about the work — nothing you did earlier in this turn was lost, "
    "it is all still above. You have your full toolset and the rest of your rounds. Carry on "
    "from where you were.)"
)

#: Spent when a turn changed code and recorded nothing about the project. Deliberately not
#: the generic landing nudge: "leave something behind" reads as "file a comment", which he was
#: already doing, and the comment is about the task rather than about the project. The thing
#: missing is the sentence a session next week needs and cannot work out again cheaply.
_LANDING_DIRECTIVE = (
    "(You're near the end of this turn's tool budget, so stop gathering — you have enough. "
    "Spend what's left LANDING the work: write what you've found into your working file, "
    "add_deliverable for anything finished, check_item the checklist steps you've actually "
    "completed, and `ask` if you need something from your person — it waits for the answer. "
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


#: Arguments that can be an entire file. Worth everything while the call is being made and
#: nothing once it has run — the result already says whether it worked.
_BULKY_ARGS = ("content", "new", "old")


#: What replaces an exchange dropped for room. Byte-stable on purpose — no count, no token
#: figure. A note reading "4 earlier steps were dropped" becomes "5 earlier steps" next time
#: and invalidates the whole prefix from that point on every single round, which is the exact
#: bug the compaction code sits next to a comment about.
_DROPPED_NOTE = (
    "(Earlier steps of this turn were set aside to make room. Their results are gone from "
    "this conversation — what you wrote down survives, so work from your notes and files.)"
)


def _drop_oldest_exchange(convo: list[dict[str, Any]]) -> bool:
    """Make room by forgetting the turn's oldest complete exchange. True if one went.

    An *exchange* is one assistant message that called tools plus the tool results that
    answered it. They go together or not at all: a provider rejects a `tool` message whose
    `tool_call_id` has no matching call, so dropping half would turn "running out of room"
    into a 400 with no obvious cause.

    The leading messages are never touched. Those are the persona and the request, and the
    persona is the whole of what the prompt cache holds — dropping it would free a few
    thousand tokens and cost the cached prefix on every remaining round.
    """
    first_exchange = next(
        (i for i, m in enumerate(convo) if m.get("role") == "assistant" and m.get("tool_calls")),
        None,
    )
    if first_exchange is None:
        return False

    end = first_exchange + 1
    while end < len(convo) and convo[end].get("role") == "tool":
        end += 1
    # Leave at least one exchange in place: a turn with no evidence of what it just did is
    # worse than one that is slightly over budget, and the next round would drop the round
    # that was about to save the work.
    if not any(m.get("role") == "assistant" and m.get("tool_calls") for m in convo[end:]):
        return False

    already_noted = any(m.get("_dropped") for m in convo[:first_exchange])
    convo[first_exchange:end] = (
        [] if already_noted else [{"role": "user", "content": _DROPPED_NOTE, "_dropped": True}]
    )
    return True


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


#: Share of the model's context that may fill before the history is folded into a summary.
#:
#: Not near the brim, because `ContextBudget` is the overflow guard and this is not — and a
#: valve that only opens at the last moment opens during the rounds that can least afford to
#: spend one on a summary. Not near the middle either: every round below this threshold is a
#: round where the whole history re-sends at cache-read price, which is the cheap outcome.
_FOLD_ABOVE_SHARE = 0.8

#: How many folds a single turn will pay for before it gives up and lets `_drop_oldest_exchange`
#: take over. Each fold costs a model call and summarises the previous summary, so the third one
#: is buying very little with real money.
_MAX_FOLDS = 2

#: Rough chars-per-token, matching `llm/caching.py`. Only used for the no-window fallback below,
#: where being a little pessimistic is the safe direction.
_CHARS_PER_TOKEN = 3.7

#: What the old char-budget fallback compares against when the window is unknown. Same figure
#: the shaving path has always used, so an install with no known window behaves exactly as
#: before rather than inheriting a threshold derived from a window nobody can name.
_UNKNOWN_WINDOW_CHARS = 80_000


def _room_is_tight(convo: list[dict[str, Any]], window: int) -> bool:
    """Should this conversation's history be reduced before the next request?

    ``window`` is the model's context in tokens, or 0 when nobody knows — and 0 is
    load-bearing here exactly as it is in `config.context_window`. An unknown window means we
    cannot say what fraction of it we are using, so the honest answer is to fall back to an
    absolute character budget and behave as this loop always did. Guessing a window would be
    worse in both directions: guess high and a small model 400s mid-turn, guess low and every
    turn is reduced for nothing.
    """
    held = sum(len(str(message.get("content") or "")) for message in convo)
    if window <= 0:
        return held > _UNKNOWN_WINDOW_CHARS
    return held > window * _CHARS_PER_TOKEN * _FOLD_ABOVE_SHARE


def _summarise(prompt: str, *, config: Config, host: str) -> str:
    """One text-only model call, for folding a long turn into notes.

    Deliberately not the turn's own conversation: the fold is a fresh, tool-less request whose
    entire input is the stretch being summarised. Reusing the live `convo` would send the very
    thing we are trying to shrink, and offering tools would invite it to go and do more work
    instead of writing the note.

    Cheap in the only sense that matters here — it is charged once and every round after it
    reads a cached prefix again.
    """
    asked = [
        {"role": "system", "content": "You write dense, specific handover notes to yourself."},
        {"role": "user", "content": prompt},
    ]
    # Un-stick the reasoning effort and the session id: this is not part of the conversation's
    # cache lineage and should not be pinned to it.
    plain = replace(config, effort="", session_id="")
    text = ""
    for event in _stream_once(asked, plain, host, tools=None, tool_choice="none"):
        # `delta`/`role: text` is what both providers emit for prose — reasoning arrives on the
        # same event type under a different role and is not the note.
        if event.get("type") == "delta" and event.get("role") == "text":
            text += str(event.get("text") or "")
        elif event.get("type") == "error":
            return ""
    return text.strip()


#: Below this a duplicate is not worth replacing — the pointer is not free either, and a short
#: result carries its own answer.
_DEDUPE_MIN_CHARS = 400


def _already_in(convo: list[dict[str, Any]], name: str, content: str) -> int | None:
    """Where this exact result already sits in the turn, or None.

    ``None`` rather than 0 for "not found", because 0 is a real index. In a live turn `convo[0]`
    is always the system prompt so a tool result can never land there, which is precisely what
    would have made the sentinel version an invisible bug: correct in production, wrong the
    moment anything built a conversation that starts with a tool result — and silently disabling
    the whole mechanism if the message order ever changed.

    Deliberately an *insert-time* question rather than a sweep over the history, and that
    distinction is the whole design. Sweeping is the obvious implementation — walk the turn,
    collapse the older copies — and it reintroduces the bug this file was just fixed for: it
    rewrites a message that has already been sent, so the prompt prefix changes and everything
    after that point re-bills uncached. On a long turn, breaking the prefix at an early message
    to save one duplicate costs far more than the duplicate did.

    Asking before appending keeps the history **append-only**, which is the property the cache
    needs. Nothing already sent is ever touched.
    """
    if len(content) < _DEDUPE_MIN_CHARS:
        return None
    for index, message in enumerate(convo):
        if message.get("role") != "tool" or message.get("_deduped"):
            continue
        if str(message.get("tool_name") or "") == name and message.get("content") == content:
            return index
    return None


def _tool_result_message(convo: list[dict[str, Any]], name: str, payload: str) -> dict[str, Any]:
    """The message to append for a tool result — the content, or a pointer to an identical one.

    Reading the same file twice puts two byte-identical copies in the window, and the second
    carries nothing the first lacks. Worth real money: in one measured session Kith read
    `ModelsSettings.tsx` fifteen times and `.kith/memory.md` thirteen, and 54% of every read he
    made was of a file he had already read.

    Unlike stubbing, this loses nothing — the content is still in the conversation, once, and
    the pointer says where. And unlike stubbing it does not tempt him to read the file *again*
    to recover it, which is how a 1,200-character stub turns into fifteen full reads.

    Two reads of a file he edited in between are not identical, so both survive. That difference
    is the record of his own change and is the last thing to collapse.
    """
    if _already_in(convo, name, payload) is None:
        return {"role": "tool", "tool_name": name, "content": payload}
    return {
        "role": "tool",
        "tool_name": name,
        "content": (
            f"[identical to what `{name}` returned earlier in this turn — that result is still "
            "above, in full, and unchanged. Nothing has been withheld and there is no need to "
            "read it again.]"
        ),
        "_deduped": True,
    }


def _compact_tool_history(convo: list[dict[str, Any]], offload=None) -> None:
    """Stub the oldest tool outputs once the live set outgrows its char budget.

    Tool output re-sends in full on every subsequent round, so a long research turn
    would balloon without pruning. The pruning has to be sized to the model actually
    in use: a hard "keep the last four" is ruinous on a 1M-context model, where he
    forgets the six searches he ran two rounds ago and re-runs them, round after round.

    So the live set is bounded by characters rather than by count — keep the newest
    results whole until the budget is spent, and only then start stubbing. All three
    numbers are settings, read here rather than at import so raising them for a
    bigger model takes effect on the next turn instead of the next restart.

    ``offload(name, content) -> path`` spills an evicted result to a file first, so the
    stub can point him at the full text rather than truncating it away. Optional and
    defaulted off: without it — a caller with no conversation to file under, or a test —
    the stub is the older, lossy trim, unchanged.
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
            saved = offload(name, content) if offload else ""
            if saved:
                # The tail is kept on disk, one read_file away, instead of thrown out.
                msg["content"] = (
                    content[:stub_chars] + f"\n…[the full {len(content):,}-character {name} output was "
                    f"moved to {saved} to save room — read_file it if you still need the rest]"
                )
            else:
                msg["content"] = (
                    content[:stub_chars] + f"\n…[earlier {name} output trimmed to save room — "
                    "if you still need it, save what matters to a file next time; re-run the tool to see it again]"
                )
            msg["_stubbed"] = True


@dataclass
class _Retries:
    """What the turn has lost to the provider so far.

    Kept at turn level rather than per round so the count a person sees runs 1,2,3,4 instead of
    restarting at 1 for each round — "attempt 2, attempt 3, attempt 2, attempt 3" reads like the
    retry is going backwards. It is also what the final error reports having tried.
    """

    #: Model calls made and lost across the whole turn.
    attempts: int = 0
    #: When the first one was lost, for the "over 7s" in the giving-up message. 0 means none yet.
    first_failed_at: float = 0.0

    def lost(self) -> None:
        self.attempts += 1
        self.first_failed_at = self.first_failed_at or time.time()


def _send_round(
    convo: list[dict[str, Any]],
    config: Config,
    host: str,
    schemas: list[dict],
    retries: _Retries,
) -> Iterator[dict]:
    """One round's model call, attempted up to `_ROUND_ATTEMPTS` times.

    Returns ``(content, tool_calls, stats, failure)`` — ``failure`` is None when a call got
    through, and the caller decides what a dead round costs. Driven with ``yield from``, which
    forwards the reasoning/answer deltas and the `retrying` notices and hands back that tuple.

    Retrying *here* is safe in a way retrying the turn is not, and the difference is the whole
    design. The tools of every previous round have already run and their results are already in
    `convo`; repeating this call repeats a model request and nothing else. The client's own
    retry deliberately stops the moment a response body exists, because by then he may have
    written files and committed — that reasoning applies to the turn, not to one round in it.

    What is *not* retried is a round that already emitted. A second attempt may answer
    differently, and the person would watch half of one answer followed by all of another. That
    round is over, and it is the caller's problem from there.
    """
    content = ""
    tool_calls: list[dict] = []
    stats: dict | None = None
    failure: dict | None = None

    for attempt in range(1, _ROUND_ATTEMPTS + 1):
        failure = None
        spoke = False
        content, tool_calls, stats = "", [], None
        for event in _stream_once(convo, config, host, tools=schemas):
            kind = event["type"]
            if kind == "delta":
                spoke = True
                yield event  # forward reasoning/answer tokens
            elif kind == "error":
                failure = event
                break
            elif kind == "turn":
                content = event["content"]
                tool_calls = event["tool_calls"]
                stats = event["stats"]
        if failure is None:
            break
        retries.lost()
        if spoke or not _worth_retrying(failure) or attempt == _ROUND_ATTEMPTS:
            break
        yield {
            "type": "retrying",
            # What is about to be tried, counted across the turn — see `_Retries`.
            "attempt": retries.attempts,
            "message": str(failure.get("message") or ""),
        }
        time.sleep(_backoff(attempt))

    return content, tool_calls, stats, failure


def _make_room(
    convo: list[dict[str, Any]],
    schemas: list[dict],
    room: ContextBudget,
    *,
    take_reading,
    config: Config,
    host: str,
    offload=None,
) -> Iterator[dict]:
    """Reduce the turn's history until the next request fits. Returns the reading after.

    A generator because the fold is worth announcing — it costs a model call and several
    seconds, and before the `compacting` event existed that wait was indistinguishable from a
    slow provider. Driven with ``book = yield from _make_room(...)``, which forwards the events
    and hands back the return value.

    Three moves, cheapest first, and the order is the whole design:

    1. **Fold** the middle into a summary. Costs one model call, happens at most `_MAX_FOLDS`
       times, and produces a *new stable prefix* — the round after a fold is byte-identical to
       the fold, so caching resumes immediately.
    2. **Shave** — stub old tool results, drop seen images, strip bulky call arguments. Lossy,
       and it rewrites the middle of the history every time it runs, so the prefix cache dies
       with it.
    3. **Drop** whole exchanges. Guaranteed to free room and guaranteed to break the cache,
       which is exactly why it is last.

    Two pressure signals feed it and they used to get two different responses. ``over`` is a
    share of the whole window — the fold's own trigger, 80% by default. ``tight`` is `room`'s
    absolute ceiling, which also charges for the biggest single round-to-round growth seen so
    far, and can fire well under ``over`` on a turn that already had one huge round in it.
    ``over`` tried a real fold first and only fell back to shaving; ``tight`` went straight to
    dropping, with no fold attempt at all. Measured on a real turn: cache held above 99.8% for
    several rounds, then one ``tight``-triggered drop sent the next round out at 3.2% cached —
    400,483 tokens re-billed in full for a conversation that had grown by 5,424 since the last
    one. Both signals now get the same first response.

    The toolset is deliberately untouched throughout. Reusing the `landing` latch to free room
    was the obvious move and is wrong: it is one-way, so context pressure at round 3 of a
    40-round turn would remove shell and every file tool for the remaining 37 and leave him
    structurally unable to do what he was asked. Trim the history; leave the capability alone.
    """
    book = take_reading()

    over = book.past(_FOLD_ABOVE_SHARE) if config.context_window > 0 else _room_is_tight(convo, 0)
    tight = room.is_tight(conversation_chars(convo, schemas))
    if over or tight:
        folded = False
        if compaction.already_folded(convo) < _MAX_FOLDS:
            yield {"type": "compacting", "used": book.used, "window": book.window}
            folded = compaction.fold(convo, partial(_summarise, config=config, host=host))
        if not folded:
            # Either it has been folded as often as is worth paying for, or there was no safe
            # place to cut. Fall back to the older shaving, which is lossy and breaks the
            # prefix — and is still better than a turn that dies on a 400.
            _compact_tool_history(convo, offload)
            _compact_images(convo)
            _compact_call_arguments(convo)
        book = take_reading()

    # Still tight after folding or shaving — the fold was exhausted, or neither move freed
    # enough room.
    dropped_any = False
    while room.is_tight(conversation_chars(convo, schemas)) and _drop_oldest_exchange(convo):
        dropped_any = True
    if dropped_any:
        book = take_reading()

    return book


def stream_agent(
    messages: list[dict[str, Any]],
    config: Config,
    host: str,
    agent_db_path: Path,
    max_rounds: int | None = None,
    allow: set[str] | None = None,
    conversation_id: str = "",
) -> Iterator[dict]:
    """Run the tool loop for one turn.

    A thin wrapper so the turn has a boundary a tool can see. `_run_turn` below is the loop
    itself; this exists only to open and close `session_context.a_turn()` around it, which is
    what lets `read_skill` know it has already been called — measured at 78 opens of 11
    skills on one project, eight of them in a single turn.

    Wrapped rather than indented: `_run_turn` is a generator several hundred lines long, and
    setting a context variable inside a generator sets it in whoever called `next()`, which
    is not the same thing and leaks.
    """
    from kith.services import session_context

    with session_context.a_turn():
        yield from _run_turn(
            messages,
            config,
            host,
            agent_db_path,
            max_rounds=max_rounds,
            allow=allow,
            conversation_id=conversation_id,
        )


def _run_turn(
    messages: list[dict[str, Any]],
    config: Config,
    host: str,
    agent_db_path: Path,
    max_rounds: int | None = None,
    allow: set[str] | None = None,
    conversation_id: str = "",
) -> Iterator[dict]:
    """Run the tool loop.

    ``conversation_id`` picks the OpenRouter stickiness id. A conversation is the right
    unit for it: every round in it shares a prompt prefix, and shares it with nothing else,
    so keeping one conversation on one upstream is what keeps its cache warm.
    """
    convo = list(messages)
    # Spills an aged-out tool result to a file this turn can read back, instead of trimming
    # its tail away. None when there is no conversation to file it under, in
    # which case the compactor falls back to the older in-place trim.
    offload_result = None
    if conversation_id:
        from kith.services import conversations
        from kith.services import offload as offload_svc

        session = conversations.session_id(agent_db_path, conversation_id)
        if session:
            config = replace(config, session_id=session)
        # Per-turn: a past turn's tool output never re-enters the prompt, so last turn's
        # spill can refer to nothing and is dead weight. Clear it, then this turn writes fresh.
        offload_svc.clear(conversation_id)
        offload_result = partial(offload_svc.save, conversation_id)
    call_index = 0
    seen_calls: dict[str, int] = {}  # (name+args) -> times run, to stop thrashing
    budget = max_rounds or tuning.value("max_rounds")
    reserve = min(tuning.value("landing_reserve"), max(2, budget // 3))
    # Read once, same as `reserve` above: recording, delivering, ticking off, handing back is
    # not a reasoning-heavy phase, and reasoning is billed as output tokens whether or not any
    # of it is shown. Blank means "leave every round exactly as it was" — no override built.
    landing_effort = str(tuning.value("landing_effort") or "").strip().lower()
    # Every MCP tool, frozen for this turn. Taken once rather than per round on purpose: the
    # tools block is part of the cached prompt prefix, so a server dying — or being switched
    # off in another tab — would shrink it mid-turn and discard the whole cache on the next
    # round. Held even when the server has gone; the *call* then fails with something
    # readable, which costs one tool result instead of the entire prefix.
    from kith.services.mcp import manager as mcp_manager

    mcp_tools = mcp_manager.snapshot()
    # Which schemas came from where, so the ledger can tell three costs apart that look
    # identical once they are all in the tools block. Resolved once per turn for the same reason
    # the snapshot is: these are inputs to a per-round accounting and must not touch a database
    # inside the request path.
    mcp_names = frozenset(str(((schema.get("function") or {}).get("name")) or "") for schema in mcp_tools)
    try:
        from kith.services import custom_tools as custom_tools_svc

        custom_names = frozenset(
            str(((schema.get("function") or {}).get("name")) or "")
            for schema in custom_tools_svc.schemas(agent_db_path)
        )
    except Exception:
        # Accounting. A ledger that cannot separate his own tools from the built-ins is still
        # a useful ledger, and must not be able to take down the turn.
        custom_names = frozenset()
    # Whether the four semantic tools are worth their schema, resolved once for the same
    # reason and with the same consequence if it changed mid-turn. A handful of `stat` calls,
    # not a server start — see `manager.any_available`.
    from kith.tools.semantics import available as language_server_available

    has_language_server = language_server_available()
    # How much room is left, learned from what the provider charges each round.
    #
    # `num_predict` is -1 on a default install — the sentinel for "no limit" — so it cannot
    # be used as the answer reserve directly. Falling back to the answer cap gives a real
    # number, and a real number is the whole point: the threshold is absolute, because a
    # percentage of the window is wrong at both ends.
    wanted_out = config.num_predict if config.num_predict > 0 else tuning.value("max_answer_tokens")
    room = ContextBudget(window=config.context_window, reserve=int(wanted_out))
    # The tool list as the last round actually saw it, kept for the forced final answer.
    # That request used to build its own with `tool_schemas(agent_db_path)` and no `only`,
    # so a narrowed round offering six tools ended by sending all fifty-nine — a different
    # tools block from every other round in the turn, which on the providers that need an
    # explicit breakpoint sits ahead of the system prompt and rewrites the whole cached
    # prefix for the one request the turn cannot skip.
    schemas: list[dict] = []
    landing = False
    #: What this turn has lost to the provider, counted across the whole turn — see `_Retries`.
    retries = _Retries()
    #: Rounds that died in a row, reset by any round that comes back. See
    #: `_FAILED_ROUNDS_BEFORE_LANDING` for why consecutive and why the first one is absorbed.
    failed_rounds = 0

    for round_index in range(budget):
        # Re-read tools each round so a tool Kith just built is usable right away.
        # `allow` scopes the toolset to the current mode (fewer tokens, sharper focus).
        #
        # Built before the history is reduced rather than after, so the ledger below sees the
        # tool block. The old ordering reduced first and counted `content` lengths only, which
        # missed 11,000 tokens of schemas — the single largest fixed cost in the prompt — and
        # therefore decided how tight the room was from roughly half the evidence.
        schemas = tools.tool_schemas(
            agent_db_path, only=allow, mcp=mcp_tools, language_server=has_language_server
        )

        # Hand the reserve over to landing — once, so the directive isn't repeated.
        if not landing and round_index >= budget - reserve:
            landing = True
            convo.append({"role": "user", "content": _LANDING_DIRECTIVE})
        if landing:
            schemas = [s for s in schemas if s["function"]["name"] in _LANDING_TOOLS]

        # What he was actually offered this round, and — from here on — what he may actually
        # run. Derived from the finished list rather than from `allow`, so both narrowings
        # above are enforced by the same line and a third one added later cannot forget to be.
        #
        # Until now none of them were enforced at all. Every one only ever reached
        # `tool_schemas(only=...)`, which decides what the model is *shown*; `run_tool`
        # resolved any name against the whole registry and ran it. Measured: `breakout` mode
        # offers six tools, and calling `remember` or `add_task` from it both succeeded. So
        # the landing reserve — the thing that stops a turn gathering until it runs out of
        # rounds — was a suggestion, and a model that named a search tool anyway got one.
        permitted = {s["function"]["name"] for s in schemas}

        # What is in the window, by category — the whole request, tool block included. Three
        # things read this: the threshold below, the meter the person sees, and anything later
        # that wants to evict by what a thing *is* rather than by how old it is.
        #
        # Costed with the ratio `room` calibrated against what the provider actually charged for
        # the last round, so the number shown matches the bill instead of a constant.
        #
        # `schemas` is bound as a default rather than closed over. Every call below happens in
        # the same round that defined this, so closing over it reads correctly today — but
        # `schemas` is rebound each round by the three narrowings above, so a call that ever
        # outlived its round would silently cost the wrong toolset. Binding it says which round's
        # tools this reading is of.
        def take_reading(schemas: list[dict] = schemas) -> ledger.Ledger:
            return ledger.take(
                convo,
                schemas,
                persona=config.system or "",
                window=config.context_window,
                chars_per_token=room.chars_per_token,
                mcp_names=mcp_names,
                custom_names=custom_names,
            )

        book = yield from _make_room(
            convo,
            schemas,
            room,
            take_reading=take_reading,
            config=config,
            host=host,
            offload=offload_result,
        )

        # Taken last, so it always describes the request about to be sent — not a reading from
        # before the last thing that could still change it.
        yield {"type": "context", "context": book.as_wire()}

        # Measured now, before the request, so it describes what was actually sent.
        sent = conversation_chars(convo, schemas)

        # Only the request itself is lighter — `config` elsewhere in this loop (context
        # window, persona, num_predict) is untouched, and the override does not survive past
        # this one call.
        round_config = replace(config, effort=landing_effort) if landing and landing_effort else config

        content, tool_calls, stats, failure = yield from _send_round(
            convo, round_config, host, schemas, retries
        )

        if failure is not None:
            # Out of attempts, or a failure not worth repeating. Dying here is what threw away
            # five rounds of finished work on a sixth-round timeout, so the turn does not die —
            # but *how* it survives depends on what the failure says about the provider, and
            # for a long time it did not, which was the bug.
            #
            # Not at all when the machine has no route to the provider: every recovery below is
            # a model call and there is nothing to make one on — more failures and more backoff,
            # spent proving what the last three already established. Same when `landing` is
            # already set, which means the landing round itself failed.
            if landing or _unreachable(failure):
                yield {**failure, "message": _gave_up(failure, retries.attempts, retries.first_failed_at)}
                return

            failed_rounds += 1
            # A transport failure or a 5xx says the provider is unwell and says nothing about
            # the request — so the request is still good, and the honest recovery is to note the
            # gap and go again with everything intact. Landing here instead is what ended a
            # turn on round 2 of 40 with a plan and no work; see
            # `_FAILED_ROUNDS_BEFORE_LANDING` for the measurement.
            #
            # A 400 or a 401 is the opposite: the server calling the request itself wrong or
            # unpaid, and it will say so again. There the narrowed toolset is not a cost but the
            # point — it is a *different, smaller* request, and one that may well get through
            # where the failed one could not.
            if _worth_retrying(failure) and failed_rounds <= _FAILED_ROUNDS_BEFORE_LANDING:
                convo.append({"role": "user", "content": _ROUND_FAILED_DIRECTIVE})
                continue
            landing = True
            convo.append({"role": "user", "content": _LANDING_DIRECTIVE})
            continue

        # The provider came back, so whatever went wrong before is not an outage. Reset here
        # rather than at the top of the round: a round is only survived once its model call has
        # actually returned, and the top of the loop is several hundred lines too early to know.
        failed_rounds = 0

        # What that request actually cost, against how big it was — the one measurement the
        # budget is built on. Taken from `sent`, captured before the call, because `convo`
        # has grown by the time we get here and dividing by the wrong size would calibrate
        # the ratio against a prompt that was never sent.
        if stats:
            room.observe(int(stats.get("promptTokens") or 0), sent)

        # Count and surface every round's tokens — tool rounds are the bulk of the
        # cost, so counting only final answers hides almost all of it.
        stats = measured(stats)
        _record(stats)
        if stats:
            yield {"type": "stats", "stats": stats}

        if not tool_calls:
            # He's finished talking, so the turn is over. Work that ends having recorded
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
            # He is finished talking, so the turn is over.
            #
            # There used to be a branch here that spent the landing reserve when a turn
            # ended having recorded nothing — because for an unattended step that was a
            # failure: the next one would start from the same blank slate and redo the
            # work. In a conversation it is the ordinary outcome. Answering a question is
            # the deliverable and there is nothing to file, and the one time this was
            # turned on for chat it doubled the cost of every trivial message — two model
            # requests each carrying the full persona and 51 tool schemas, ~18,500 tokens
            # to answer one word — with the second reply him puzzling at a directive that
            # made no sense: "I haven't been researching anything this turn."
            #
            # Nothing runs unattended now, so the case it existed for cannot occur.
            return

        # Record the assistant's tool-calling turn so the model has context.
        #
        # `content` is omitted rather than sent empty when he called tools without saying
        # anything first. Most rounds have a preamble — "let me look at the config" — and the
        # ones that do not were sending `"content": ""`, which a provider is entitled to
        # reject and one did: two turns died thirty seconds apart on
        #
        #     400 — the message at position 54 with role 'assistant' must not be empty
        #
        # Both at zero prompt tokens, so the request never ran; and because the offending
        # message was already in the turn's history, retrying rebuilt the same conversation
        # and hit the same wall. A turn that cannot be retried is a turn that is simply lost.
        # The tool-calling schema has always allowed content to be absent — that is what a
        # message which *is* the tool call looks like.
        turn: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
        if content:
            turn["content"] = content
        convo.append(turn)

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
            repeat = _is_repeat(name, seen)
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
                yield {"type": "tool_result", "id": step["id"], "name": step["name"], "result": result}
                image = _image_from(result)
                if image:
                    # A tool result is a JSON string and cannot carry an image part, so the
                    # picture arrives as the next message instead. Without this he could take a
                    # screenshot and never see it — which is exactly what he was doing while
                    # redesigning a UI. The data URI is taken out of the tool result so the
                    # same 600KB is not also sitting there as base64 text.
                    convo.append(
                        _tool_result_message(convo, step["name"], json.dumps(_without_image(result)))
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
                convo.append(_tool_result_message(convo, step["name"], json.dumps(result)))

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
