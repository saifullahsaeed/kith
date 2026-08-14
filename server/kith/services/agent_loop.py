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
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from kith.domain.chat import Config
from kith.domain.tool_markup import ToolMarkupFilter
from kith.domain.tooling import ToolHost
from kith.kernel import session_context
from kith.llm import ledger, ollama, openai_compat
from kith.llm.budget import ContextBudget, conversation_chars
from kith.services import compaction, tuning
from kith.services.turn import frozen
from kith.services.turn.history import (
    _FOLD_ABOVE_SHARE,
    _MAX_FOLDS,
    _compact_call_arguments,
    _compact_images,
    _compact_tool_history,
    _drop_oldest_exchange,
    _room_is_tight,
    _tool_result_message,
)
from kith.services.turn.meter import _record, measured

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


def _stream_once(messages, config: Config, host, tools=None, tool_choice: str = "auto", routing=None):
    """Route to the cloud model when a key+endpoint are set, else local Ollama."""
    if config.api_key and config.base_url:
        return openai_compat.stream_once(
            messages, config, host, tools=tools, tool_choice=tool_choice, routing=routing
        )
    # Ollama has no tool_choice; withholding the schemas is the only lever there.
    return ollama.stream_once(messages, config, host, tools=None if tool_choice == "none" else tools)


# How many tool rounds a single turn may take before we make it wrap up. A long
# loop is fine — that's how real agents do multi-step work; what has to stay small
# is the *payload each round carries* (see the grep/ranged-read/spill-to-file
# tools and prompt-cache alignment). The thrash-guard stops genuine spinning.


def _summarise(prompt: str, *, config: Config, host: str, routing=None) -> str:
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
    for event in _stream_once(asked, plain, host, tools=None, tool_choice="none", routing=routing):
        # `delta`/`role: text` is what both providers emit for prose — reasoning arrives on the
        # same event type under a different role and is not the note.
        if event.get("type") == "delta" and event.get("role") == "text":
            text += str(event.get("text") or "")
        elif event.get("type") == "error":
            return ""
    return text.strip()


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
    routing=None,
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
        for event in _stream_once(convo, config, host, tools=schemas, routing=routing):
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
    routing=None,
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
            folded = compaction.fold(convo, partial(_summarise, config=config, host=host, routing=routing))
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
    tool_host: ToolHost,
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

    with session_context.a_turn():
        yield from _run_turn(
            messages,
            config,
            host,
            agent_db_path,
            tool_host,
            max_rounds=max_rounds,
            allow=allow,
            conversation_id=conversation_id,
        )


def _run_turn(
    messages: list[dict[str, Any]],
    config: Config,
    host: str,
    agent_db_path: Path,
    tool_host: ToolHost,
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
    # Everything this turn settles before its first round — the session id, the spill target,
    # the budget and reserve, the routing, the MCP snapshot and the ledger's name sets. Each is
    # read once and never re-read, and `services/turn/frozen.py` holds the reasons why. Unpacked
    # into locals here rather than reached through, so the round loop below reads as it did.
    turn = frozen.begin(config, agent_db_path, conversation_id, max_rounds)
    config, room, offload_result = turn.config, turn.room, turn.offload
    budget, reserve, landing_effort = turn.budget, turn.reserve, turn.landing_effort
    routing = turn.routing
    mcp_names, custom_names = tool_host.mcp_names, tool_host.custom_names

    call_index = 0
    seen_calls: dict[str, int] = {}  # (name+args) -> times run, to stop thrashing
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
        schemas = tool_host.schemas(only=allow)

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
            routing=routing,
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
            convo, round_config, host, schemas, retries, routing=routing
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
                results = [_run(batch[0], tool_host.run, permitted)]
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
                        pool.map(lambda step, allow=permitted: _run(step, tool_host.run, allow), batch)
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
    yield from _final_answer(convo, config, host, schemas, routing=routing)


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


def _run(step: dict, run: Callable[..., Any], allow: set[str] | None = None) -> Any:
    if step["repeat"]:
        return {
            "note": "You've already made this exact call twice and it didn't move things "
            "forward. Stop repeating it — take a different approach, or give your final answer."
        }
    return run(step["name"], step["arguments"], allow)


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
    convo: list[dict[str, Any]],
    config: Config,
    host: str,
    schemas: list[dict] | None = None,
    routing=None,
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
    for event in _stream_once(convo, config, host, tools=schemas, tool_choice="none", routing=routing):
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
