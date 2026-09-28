"""Folding the old part of a long conversation into a running brief.

History is replayed in full on every turn, so a conversation that runs for days grows a prompt
without bound. This folds everything but the most recent turns into a brief and keeps the
recent ones verbatim, which is how the field bounds the same axis (Claude's compaction, Cursor's
self-summarisation).

**Rolling, not per-turn.** Summarising a growing history on every turn would cost a model
call each time and defeat the point. So a brief, once made, covers the first *N* turns and is
reused for as long as the prompt it makes stays under the fold point; only when that is crossed
is it regenerated, and the new brief is built on the old one (`brief.carried`).

**One fold point, measured one way.** `fold_at` is the only threshold, and this module and the
in-turn fold (`agent_loop._make_room`) both compare the whole request against it — every message
serialised, tool-call arguments included, plus the tool block — in tokens, at the ratio the
provider's own bills calibrated. There used to be two thresholds that agreed on "80% of the
window" and disagreed on what they measured, and 80% of a million tokens meant folding at
838,860: measured Sep 13-27, rounds over 300k tokens were 48% of all rounds and 80% of the spend.

**The fold request is the conversation's own prompt.** See `services.brief`: the prompt a turn
would send, cut where the fold ends, with the instruction appended — so it is read from the cache
the last turn left on the same host, and the instruction is the last thing the model sees.

**Never fatal.** The summariser is a model call and model calls fail. If it returns nothing,
the prompt is exactly what the last turn sent — a large prompt is a worse turn, a broken
summariser must not be a broken one.

`compact` is pure but for the ``summarize`` callback, so the policy is testable without a real
model. `fold` resolves the thresholds, makes the call and accounts for it; the caller persists
the returned brief — see ``services.turn.prompt`` / ``routes.chat``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from math import inf
from typing import Any

from kith.llm.budget import MAX_RATIO, MIN_RATIO, SEED_CHARS_PER_TOKEN, conversation_chars, message_chars
from kith.services import brief
from kith.services.brief import is_a_brief

#: Marks the folded block so a reader — and the model — can tell a summary of the past from
#: something that was actually said in it.
_SUMMARY_HEADER = "[Summary of the earlier part of this conversation]"

#: What the brief stands in for, as the instruction's subject. The system prompt at the head of
#: the request is not part of it and is never folded.
_REPLACES = "The conversation above"

#: The most of a window the fold point may take, for a model too small for `fold_at_tokens`.
#: The rest is the room the turn after a fold needs: the answer, the next few rounds, and the
#: fold's own request, which carries a brief on top of what it folds.
_MOST_OF_A_WINDOW = 0.8


def fold_at(window: int) -> int:
    """How many tokens a request may carry before its history is folded. The one threshold.

    `fold_at_tokens`, unless the model's window is so small that 80% of it comes first. Not a
    share of the window for a large one, which is what it was and what the numbers argued
    against: the price of a round is linear in its size, so the fold point is the ceiling on
    what one cache miss can cost — $0.12 for an uncached 800k round on DeepSeek, against $0.0024
    for the same round cached — and a share of a million tokens never came close to binding.

    An unknown window folds at the setting. It is a size the person chose, not a guess at the
    window, which is the thing every guard here refuses to invent.
    """
    from kith.services import tuning

    at = int(tuning.value("fold_at_tokens"))
    return min(at, int(window * _MOST_OF_A_WINDOW)) if window > 0 else at


def fold_due(tokens: int, window: int) -> bool:
    """Has a request of this many tokens passed the fold point?"""
    return tokens > fold_at(window)


def prior_brief(conversation_id: str) -> dict:
    """The brief to build on: the latest one that is a brief, else the latest at all.

    Only the latest used to count, so one bad fold stood until the next — reused on every turn,
    and carried forward into the next fold, which is the one thing that made its loss
    permanent. Falling back past it resumes from the last good fold instead, and the stretch
    after that is summarised again. A conversation whose briefs all predate the headings
    (2026-08-17) has no good one and keeps its latest, exactly as before.
    """
    from kith.services import conversations

    briefs = conversations.summaries(conversation_id) if conversation_id else []
    good = [one for one in briefs if is_a_brief(one["text"])]
    return (good or briefs or [{}])[-1]


def _chars_per_token(conversation_id: str) -> float:
    """The ratio the conversation's last turn was costed at, else the seed.

    The in-turn check divides by a ratio calibrated against what the provider billed, and this
    one has to use the same, or the two disagree about where the fold point is. A check here
    that read a conversation as just under it while the turn read it as just over would leave
    every turn folding in-turn — a fold that is thrown away when the turn ends — and none of
    them folding here, where it is kept.
    """
    if not conversation_id:
        return SEED_CHARS_PER_TOKEN
    from kith.services import conversations

    reading = conversations.latest_reading(conversation_id).get("context") or {}
    try:
        ratio = float(reading.get("charsPerToken") or 0)
    except (TypeError, ValueError):
        ratio = 0.0
    return min(MAX_RATIO, max(MIN_RATIO, ratio)) if ratio > 0 else SEED_CHARS_PER_TOKEN


def _head_chars(config, framing, schemas, tool_chars: int) -> int:
    """The part of every request that is not history: the head the turn puts first, and the
    tool block. Measured the way the in-turn check measures a whole request."""
    if framing is not None:
        head = framing([])
    else:
        persona = str(getattr(config, "system", "") or "").strip()
        head = [{"role": "system", "content": persona}] if persona else []
    if schemas:
        return conversation_chars(head, schemas)
    return conversation_chars(head) + int(tool_chars or 0)


def _budget(config, conversation_id: str, head_chars: int) -> int:
    """How many characters of history fit under the fold point, beside the head and tools."""
    window = int(getattr(config, "context_window", 0) or 0)
    return max(0, int(fold_at(window) * _chars_per_token(conversation_id)) - head_chars)


def fold(
    history: list[dict[str, Any]],
    config,
    conversation_id: str,
    host: str = "",
    tool_chars: int = 0,
    force: bool = False,
    *,
    framing: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    schemas: list[dict] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Compact ``history`` using the settings' thresholds and the conversation's stored brief.

    The impure companion to :func:`compact`: it reads the knobs, reads the last brief off the
    transcript, makes the call and accounts for it. Returns the same
    ``(messages, new_brief_or_None)`` — the caller persists a non-None brief.

    ``framing`` turns history messages into the prompt a turn sends for them — the persona in
    front, each message as the turn renders it — and ``schemas`` is the turn's tool block. Both
    exist so the fold request is byte-for-byte the prompt the last turn sent, up to where the
    fold ends, which is what makes it a cache read. Without them the request is still valid and
    still a brief, just billed in full.

    The call is charged to the session and written to the transcript as a `stats` line tagged
    ``purpose: "fold"``, whatever it came back as: a refused brief was still paid for, and the
    fold calls this replaced were invisible to the meter, the session budget and every audit.

    ``force`` is what makes a fold something you can ask for. The fold point is the right test for
    a turn defending itself — a fold costs a model call — and the wrong test for a person who
    has just asked: it is the whole reason `/fold` once declined with "not enough here to be
    worth folding" on a conversation of six hundred thousand tokens. Asked for, everything but
    the recent turns is old enough to summarise; nothing else about the fold changes.
    """
    from kith.services import tuning

    frame = framing or (lambda part: list(part))
    budget = _budget(config, conversation_id, _head_chars(config, framing, schemas, tool_chars))

    def summarize(request: list[dict[str, Any]]) -> str:
        settled, routing = _lineage(config, conversation_id)
        reply = _summarize(frame(request), settled, host, tools=schemas, routing=routing)
        _account(conversation_id, reply)
        return reply.text

    return compact(
        history,
        summarize,
        prior_brief(conversation_id),
        max_chars=1 if force else budget,
        keep_recent=int(tuning.value("history_keep_recent")),
        keep_chars=budget // 2,
        max_fold_chars=budget,
    )


def fold_dry(
    history: list[dict[str, Any]],
    config,
    conversation_id: str = "",
    *,
    tool_chars: int = 0,
    framing: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    schemas: list[dict] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """What the fold would leave behind, without paying for one.

    The read-only twin of :func:`fold`, for showing someone the prompt their next turn will send.
    Same knobs, same stored brief, same :func:`compact` — and a summariser that always comes back
    empty, so no model is called and nothing is persisted.

    That is not a trick played on `compact`: an empty summary is its documented fail-safe ("a big
    prompt beats a broken turn"), so this is the path it already takes whenever the summariser is
    down. The preview is therefore byte-exact in the two cases that cover almost every
    conversation — under the fold point, or a stored brief that still covers the tail — and in
    the third it declines to invent one.

    The second value is that third case: a fold is due, and the next real turn will pay for one
    before it sends. It is exactly "`compact` asked for a summary", so it cannot disagree with
    the real fold about whether one was due.
    """
    from kith.services import tuning

    budget = _budget(config, conversation_id, _head_chars(config, framing, schemas, tool_chars))
    asked: list[bool] = []
    folded, _ = compact(
        history,
        lambda _request: asked.append(True) or "",  # never summarise — see above
        prior_brief(conversation_id),
        max_chars=budget,
        keep_recent=int(tuning.value("history_keep_recent")),
        keep_chars=budget // 2,
        max_fold_chars=budget,
    )
    return folded, bool(asked)


def _lineage(config, conversation_id: str):
    """The config and routing the conversation's own turns use, so the fold lands where they do.

    A fold used to route through `routing.resolved`, the path for requests that belong to no
    conversation, and with no session id — so it went to whichever host ranked first that
    minute, never the one holding the conversation's cache. Settled here exactly as
    `turn.frozen.begin` settles a turn, and only when a fold is actually made.
    """
    from kith.services import routing as routing_policy
    from kith.services.turn import frozen
    from kith.settings import AGENT_DB_PATH

    settled = frozen._session_for(config, AGENT_DB_PATH, conversation_id)
    return settled, routing_policy.for_conversation(settled, AGENT_DB_PATH, conversation_id)


def _summarize(messages: list[dict[str, Any]], config, host: str, *, tools=None, routing=None) -> brief.Reply:
    """Ask the model for a brief. Never raises — a fold must never break a turn.

    The config is the turn's own but for the answer cap, reasoning included. Turning reasoning
    off for the fold was cheaper per call and is the one change here that could cost the cache:
    on some providers a different thinking setting invalidates the cached messages, and a cache
    miss on the prompt costs far more than the reasoning does. Uses the same transport the turn
    itself would (cloud when a key and endpoint are set, else local Ollama).
    """
    from kith.llm import ollama, openai_compat

    window = int(getattr(config, "context_window", 0) or 0)
    capped = replace(config, num_predict=brief.tokens(window))
    try:
        if capped.api_key and capped.base_url:
            stream = openai_compat.stream_once(messages, capped, host, tools=tools, routing=routing)
        else:
            stream = ollama.stream_once(messages, capped, host, tools=tools)
        return brief.read(stream)
    except Exception as exc:
        return brief.Reply("", None, f"failed: {exc}")


def _account(conversation_id: str, reply: brief.Reply) -> None:
    """Charge a fold call to the session and write it down, whatever it came back as.

    The same three places a round's cost goes: the process meter, the session budget, and a
    `stats` line in the transcript — the event every reader of a turn's cost already sums, tagged
    so that a reader who cares can tell a fold from a round. Written from here rather than by the
    route, because this is the one place every fold call passes through, and a fold made before
    a turn begins has no turn stream to carry it.
    """
    from kith.services import conversations, host_costs
    from kith.services.activity import feed
    from kith.services.turn.meter import _record, measured

    stats = measured(reply.stats)
    if not stats:
        return
    _record(stats)
    # And the host's running bill, which a round gets from the turn recorder and a fold made
    # before the turn would otherwise never reach — `host_costs.fill` counts this line from the
    # transcript, so leaving it out here would make the record disagree with its own refill.
    host_costs.observe(stats)
    try:
        feed.charge_session(
            conversation_id, int(stats.get("uncachedTokens") or 0), float(stats.get("costUsd") or 0.0)
        )
        conversations.record_event(
            conversation_id, "stats", {"stats": stats, "purpose": "fold", "outcome": reply.outcome}
        )
    except Exception:
        # Accounting. The fold already happened; failing to write down what it cost must not
        # take the turn with it.
        pass


def compact(
    history: list[dict[str, Any]],
    summarize: Callable[[list[dict[str, Any]]], str],
    prior: dict[str, Any] | None = None,
    *,
    max_chars: float,
    keep_recent: int,
    keep_chars: float = inf,
    max_fold_chars: float = inf,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Fold old turns into a brief. Returns ``(messages, new_brief_or_None)``.

    ``new_brief`` is ``{"through": n, "text": ...}`` when a fresh summary was made and should
    be persisted, or ``None`` when the history was left as-is or an existing brief was reused.

    ``summarize(messages) -> str`` is handed the fold request: the prompt as the last turn sent
    it, up to where this fold ends, with the instruction as the last message. It returns the
    brief, or "" when there is none to be had.

    ``max_chars`` is the history's room under the fold point. ``keep_chars`` bounds the recent
    turns kept verbatim, and ``max_fold_chars`` how much new territory one call may read.

    Sized with :func:`kith.llm.budget.message_chars` rather than a bare ``len(content)`` —
    ``history`` carries replayed tool calls (see ``conversations.full_messages``), and a
    ``{"role": "assistant", "tool_calls": [...]}`` message has no string ``content`` at all.
    Measuring only string content made one of those invisible, however large its arguments.
    """
    prior = prior or {}
    covered = int(prior.get("through") or 0)
    earlier = str(prior.get("text") or "")
    count = len(history)

    # A stored cursor only means anything against the shape it was cut from. Replayed tool
    # calls (`conversations.full_messages`) changed that shape from under any brief made
    # before them: the same numeric index that used to land between two turns can now land
    # inside one — between a tool call and its own result. Replaying from there is not "a
    # slightly wrong resume point", it is a message list a provider will refuse outright.
    # Distrust the cursor whenever it no longer points at an actual boundary, and start over
    # rather than build on a number that has stopped meaning what it once did.
    if covered and not (covered == count or (covered < count and history[covered].get("role") == "user")):
        covered, earlier = 0, ""

    # What the last turn sent: the brief, the questions it may not compress, and everything
    # since. Measured whole — the brief and the questions ride on every request too — against
    # the same room the first fold is judged by.
    #
    # Deliberately *not* a message count. An earlier version compared `count - covered`
    # against a multiple of `keep_recent`, which folds on a made-up turn cadence with no idea
    # whether the window is anywhere near full: two one-line replies and a 4,000-token tool dump
    # both count as "one turn" to a message counter. Measured against a real 2-day, 40-fold
    # conversation, it was still folding every ~9 messages long after the accumulated text was
    # nowhere near what the window could hold.
    sent = _as_sent(history, covered, earlier)
    if _chars(sent) <= max_chars:
        return sent, None

    # (Re)fold everything but the most recent turns.
    cut = _turn_aware_cut(history, keep_recent, keep_chars)
    # How far past `covered` one call may read. A conversation that fell behind before this
    # ever ran can owe far more new territory than a brief is sized to compress; catching up
    # over a few bounded folds beats one asked to compress all of it into the same few
    # thousand tokens, which is how a brief comes back cut off.
    cut = _capped_cut(history, covered, cut, max_fold_chars)
    if cut <= covered:
        # Nothing new precedes the protected tail — leave it alone rather than pay for a
        # summary of nothing, and send what the last turn sent.
        return sent, None

    # The prompt the last turn sent, up to the cut: `sent` ends with `history[covered:]`, so the
    # protected tail is its last `count - cut` items. Nothing after the cut is shown — the brief
    # is of exactly what it replaces, and the instruction does not have to say where to stop.
    continuing = bool(earlier) and is_a_brief(earlier)
    request = [
        *sent[: len(sent) - (count - cut)],
        {
            "role": "user",
            "content": brief.instruction(
                _REPLACES, earlier=_SUMMARY_HEADER if earlier else "", continuing=continuing
            ),
        },
    ]
    fresh = (summarize(request) or "").strip()
    if not fresh:
        # Summariser failed — a big prompt beats a broken turn, and the prompt that is sent is
        # the one the last turn sent: its brief, not the whole history the brief replaced.
        return sent, None
    text = brief.carried(earlier, fresh) if continuing else fresh
    # The brief covers what he did; these are the questions it was not allowed to compress.
    return (
        [_summary_message(text), *_what_they_asked(history, cut), *history[cut:]],
        {"through": cut, "text": text},
    )


def _as_sent(history: list[dict[str, Any]], covered: int, earlier: str) -> list[dict[str, Any]]:
    """The history as a turn sends it, given the brief that covers its first ``covered`` items."""
    if not earlier:
        return history
    return [_summary_message(earlier), *_what_they_asked(history, covered), *history[covered:]]


def _chars(messages: list[dict[str, Any]]) -> int:
    return sum(message_chars(message) for message in messages)


#: Ceiling on the verbatim questions carried past a fold, in characters.
#:
#: Generous on purpose, because the measurement says it never binds: across two of the longest
#: real conversations here — 3,133 messages and 2.27M characters, and 453 messages and 1.03M —
#: everything the person said came to 1.10% and 0.45% of the total. This exists for the one
#: shape that could break that, someone pasting a document per message, and it keeps the most
#: recent rather than the first: an old question that has been answered is the one worth losing.
_KEEP_ASKED_CHARS = 60_000


def _theirs(message: dict[str, Any]) -> bool:
    """Is this a message the person wrote? A wake rides in their slot and is not them."""
    return message.get("role") == "user" and not message.get("_wake")


def _what_they_asked(history: list[dict[str, Any]], upto: int) -> list[dict[str, Any]]:
    """The person's own messages from the part being folded away, kept word for word.

    **A fold may compress what he said and did. It may not compress what they asked.**

    The summariser is handed a transcript that is overwhelmingly his own output — measured on a
    real conversation, tool results 38%, his prose 27%, and the person's words 0.3% — so it
    faithfully summarises the work and drops the questions. One fold turned six turns into
    "CI optimization is pushed in ce06d61", which was true, and lost a question about milestones
    that had never been answered. Neither is fixed by asking the model to try harder — so the
    questions are not offered to the summariser's judgement at all.

    **Only the person's.** A wake — a reminder firing, a background run coming back — is stored as
    `system` and replayed in the person's slot, marked `_wake`. Counted here it outweighed them:
    in one conversation wakes were 94% of the characters this kept, so the ceiling below spent
    itself on the harness talking and dropped the person's oldest real requests first.

    Their words and nothing else rides with them: the present-state block a message was sent with
    is harness context from the moment it was sent, and a copy carried here is not where it was
    sent, so it buys neither accuracy nor cache.

    Costs about one per cent, which is the number that makes this obvious rather than clever.
    """
    asked = [m for m in history[:upto] if _theirs(m)]
    kept: list[dict[str, Any]] = []
    spent = 0
    for message in reversed(asked):  # newest first, so a ceiling drops the oldest
        words = {"role": "user", "content": message.get("content") or ""}
        size = message_chars(words)
        if spent + size > _KEEP_ASKED_CHARS and kept:
            break
        kept.append(words)
        spent += size
    kept.reverse()
    return kept


def _summary_message(text: str) -> dict[str, Any]:
    # `_summary` marks this as folded *conversation*, not system instruction. It rides as a
    # `system` message because that is what a summary of the past is to the model — but for the
    # ledger it is the conversation, compressed, and counting it under "Who he is" (which is
    # where every unmarked system message lands) filed a summary of the chat under the persona.
    # Stripped before the wire like every other internal key; see `openai_compat._to_openai`.
    return {"role": "system", "content": f"{_SUMMARY_HEADER}\n{text}", "_summary": True}


def _turn_starts(history: list[dict[str, Any]]) -> list[int]:
    """Index of each turn's own user message, in order.

    A ``role == "user"`` entry is always exactly one turn boundary and nothing else can be
    mistaken for one: every synthetic user-role message the live loop injects mid-turn —
    an in-turn fold's own summary, a picture — lives only in that turn's in-memory ``convo`` and
    is never written to the transcript as a message, so ``conversations.full_messages`` never
    manufactures one of these that isn't real. A wake is a boundary too — it starts a turn — and
    is told apart where that matters (`_theirs`).
    """
    return [i for i, m in enumerate(history) if m.get("role") == "user"]


def _turn_aware_cut(history: list[dict[str, Any]], keep_recent: int, keep_chars: float = inf) -> int:
    """Where to fold up to, keeping at most the last ``keep_recent`` of the person's turns whole.

    Counts turns, not list items — replayed tool calls make a turn anywhere from two items to
    hundreds. A cut can safely land on any turn boundary and never needs to check for an open
    tool call the way the in-turn fold does (``compaction._open_calls_at``): a turn's own calls
    are always strictly between its opening message and the next one.

    **The person's turns, not the harness's.** Wakes are boundaries, and they are kept when they
    fall inside the protected stretch, but they are not counted towards it. Measured: at one fold
    the protected tail was five wakes and the new message, so the person's own last request —
    one turn further back — was folded away while a reminder's prose stayed verbatim.

    **And no more than ``keep_chars`` of them.** A fold is only worth its call if what it leaves
    is well under the fold point, and the recent turns can be most of a conversation: a turn of
    heavy reading replays its results whole for the next two turns. Protecting them regardless
    left a prompt still over the line after a fold, which folds again on the very next turn.
    The turn being answered is kept whatever it weighs — it is the question.

    ``keep_recent`` is a ceiling on what stays untouched, not a precondition a short
    conversation must clear before folding is allowed to help: the first turn is never
    protected, so a conversation with fewer turns than ``keep_recent`` still folds whatever
    precedes its most recent ones. Only a single turn (or none) has nothing before it to fold,
    and returns 0.
    """
    starts = _turn_starts(history)
    if len(starts) <= 1:
        return 0
    if keep_recent <= 0:
        return len(history)
    cut = starts[-1]
    kept = 1 if _theirs(history[cut]) else 0
    tail = _chars(history[cut:])
    for start in reversed(starts[1:-1]):
        if kept >= keep_recent:
            break
        tail += _chars(history[start:cut])
        if tail > keep_chars:
            break
        cut = start
        kept += 1 if _theirs(history[start]) else 0
    return cut


def _capped_cut(history: list[dict[str, Any]], covered: int, cut: int, ceiling: float) -> int:
    """The furthest turn boundary between ``covered`` and ``cut`` whose new territory —
    ``history[covered:boundary]`` — still fits ``ceiling``.

    ``_turn_aware_cut`` answers "how far *should* this fold reach"; this answers "how much of
    that can one brief honestly compress". They can disagree by a lot: a conversation that fell
    behind before folding ever ran on it can owe hundreds of thousands of tokens of new
    territory, and a brief asked to compress all of it runs into its own output cap.

    Always advances at least one turn boundary when ``cut > covered``: a single turn whose own
    content alone exceeds ``ceiling`` still has to move forward once, rather than stall here
    permanently waiting for a smaller one that a real conversation will never produce.
    """
    if cut <= covered:
        return cut
    starts = set(_turn_starts(history))
    total = 0
    best = covered
    for i in range(covered, cut):
        total += message_chars(history[i])
        if (i + 1) in starts or i + 1 == cut:
            if total <= ceiling or best == covered:
                best = i + 1
            else:
                break
    return best
