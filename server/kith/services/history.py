"""Folding the old part of a long conversation into a running brief.

History is replayed in full on every turn — only the prose (``conversations.messages``
drops the tool middle), but all of it — so a conversation that runs for days grows a prompt
without bound. This folds everything but the most recent turns into a short brief and keeps
the recent ones verbatim, which is how the field bounds the same axis (Claude's compaction,
Cursor's self-summarisation).

**Rolling, not per-turn.** Summarising a growing history on every turn would cost a model
call each time and defeat the point. So a brief, once made, covers the first *N* turns and is
reused as long as only a handful of turns have accrued since; only when enough new turns pile
up is it regenerated, folding the previous brief forward.

**Never fatal.** The summariser is a model call and model calls fail. If it returns nothing,
the full history is replayed exactly as before — a large prompt is a worse turn, a broken
summariser must not be a broken one.

Pure but for the ``summarize`` callback, so the policy is testable without a real model. The
caller resolves the thresholds from settings, does the model call, and persists the returned
brief; see ``services.history_context`` / ``routes.chat``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from kith.llm.budget import message_chars
from kith.services.compaction import _as_text

#: Marks the folded block so a reader — and the model — can tell a summary of the past from
#: something that was actually said in it.
_SUMMARY_HEADER = "[Summary of the earlier part of this conversation]"

#: Room for a brief, as a share of the window — the same reasoning as `_FOLD_ABOVE_SHARE`
#: below, applied to the output side, and for the same reason. A fixed number was here and it
#: was the constant this module elsewhere argues against: 1,600 tokens is the right size for a
#: paragraph and the wrong size for the six sections `_INSTRUCTION` now asks for. The cap and
#: the ambition have to agree, or the cap wins silently and the prompt is decoration.
#:
#: The floor is the old value, so a small local model is unchanged and only a model with room
#: to spare gets more of it. The ceiling exists because a fold is worth doing only when the
#: brief is far smaller than what it replaces; past a few thousand tokens a summariser is
#: writing another transcript.
_SUMMARY_SHARE = 0.04
_SUMMARY_MIN_TOKENS = 1_600
_SUMMARY_MAX_TOKENS = 4_000

#: The exact headings, so the shape is one thing rather than six places that must agree. Read
#: by the tests, which is the point: a section quietly dropped from the instruction should fail
#: something, not merely produce a worse summary that nobody notices for a month.
SECTIONS = (
    "What they asked for",
    "What was decided",
    "What was done",
    "What was found",
    "What is still open",
    "Where it stands",
)

#: How the model is asked to fold.
#:
#: This replaces one paragraph that named six things to preserve *inside a blob* and then said
#: "be concise" three different ways. What came back was a blob — accurate, and useless: a real
#: fold of six turns produced four lines about CI sharding and lost every question that had
#: been asked, including ones never answered.
#:
#: Two things were wrong with it and neither was the model.
#:
#: **It asked for a paragraph.** Six concerns listed in a sentence get a sentence that gestures
#: at all six. A heading per concern makes the model go and look for each one separately; that
#: search is what a section is *for*, and without it the easy material crowds out the rest.
#:
#: **It never mentioned the person.** Decisions, facts, files, threads, current work — every
#: item was about the work and not one was about who asked for it. On a transcript that is
#: 0.3% their words, an instruction that does not name them produces a summary without them in
#: it, which is exactly what happened. `_what_they_asked` fixes that mechanically and does not
#: depend on the model cooperating; this makes the brief itself carry the intent, which the
#: verbatim messages alone do not — a run of questions is not the same as knowing what was
#: wanted, and the verbatim carry has a ceiling that eventually drops the oldest ones.
#:
#: **Intent, though, and explicitly not a replay.** The first draft asked for every request
#: "quoted in their own words", and run against a real 146,000-character conversation it did
#: exactly that: fifty-odd verbatim lines, down to "keepgoimh" and "wheere are we", and then it
#: hit the output cap in the middle of section two. Five of the six sections never got written.
#: The words were already being carried by `_what_they_asked`, so the brief was paying twice
#: for them and starving everything only it can say — the same failure as the paragraph it
#: replaced, with a different section doing the crowding out.
#:
#: Deliberately not shaped around code. Kith's conversations are spreadsheets, audits, research
#: and websites at least as often, and a section called "files and code" would tilt every fold
#: toward the one kind of work that happens to name its artifacts in backticks.
_INSTRUCTION = (
    "You are compressing the earlier part of a conversation so it can be carried forward in "
    "less space. What you write REPLACES that part entirely — it is the only memory of it that "
    "survives, so anything you leave out is gone.\n"
    "\n"
    "Write these six sections, in this order, using these exact headings. Keep a heading with "
    "'nothing' under it rather than dropping it.\n"
    "\n"
    "## What they asked for\n"
    "What the person wanted, in the order it was asked for. The intent behind each request — "
    "NOT a replay of their messages, which are carried forward separately and word for word. "
    "Quote their own phrasing only where a paraphrase would lose something: a constraint, a "
    "preference, a correction, a standing rule.\n"
    "\n"
    "## What was decided\n"
    "Each choice made and the reason it was made, plus any constraint or preference they "
    "stated that still applies.\n"
    "\n"
    "## What was done\n"
    "The work actually carried out, naming things exactly: files and paths, records, "
    "identifiers, commands, figures. A name you half-remember is worse than no name.\n"
    "\n"
    "## What was found\n"
    "Facts established and measurements taken, and every problem hit — with how it was "
    "resolved, or that it was not.\n"
    "\n"
    "## What is still open\n"
    "Questions asked and not answered, work deferred, anything known to be broken, unverified "
    "or waiting on someone.\n"
    "\n"
    "## Where it stands\n"
    "What was being worked on at the moment this text ends, and the next step if there is an "
    "obvious one.\n"
    "\n"
    "Quote rather than paraphrase wherever the exact words carry the meaning: what the person "
    "said, error text, identifiers, figures. A short verbatim extract beats a longer "
    "description of one. Invent nothing — if it is not in the text, it does not go in.\n"
    "\n"
    "If the text opens with [Summary so far], that is your own earlier brief. Carry its content "
    "forward into the matching sections and add to it. Do not summarise it again: compressing a "
    "summary is how a conversation forgets."
)


#: Share of the window this may fill before folding — the same threshold `agent_loop`'s
#: in-turn fold already uses (`_FOLD_ABOVE_SHARE`), so the two mechanisms agree on what "full"
#: means instead of each guessing their own number. A fixed character count could not do this:
#: it is either far too small next to a 1M-token cloud model (folding — and paying for a
#: summary — on a conversation that is nowhere near full) or far too large next to a 40K local
#: one (never folding until a request outright fails). Matching Claude Code's own compaction,
#: which fires as a share of whatever window is actually in play, not a constant.
_FOLD_ABOVE_SHARE = 0.8

#: Rough chars-per-token, matching `llm/budget.SEED_CHARS_PER_TOKEN`. Only used to turn a token
#: window into a character budget; real calibration happens per-round inside a turn, which this
#: runs before.
_CHARS_PER_TOKEN = 3.7

#: Ceiling on how much new territory one summarisation call may be asked to read, when the
#: model's own window is unknown. Deliberately not `max_chars` — that is the threshold for
#: when the *whole conversation* is worth folding, not what a single *request* can actually
#: carry. Large enough that no ordinary fold ever brushes against it, so it only engages when
#: something upstream already went wrong: a conversation that outgrew this much in one sitting
#: before a fold ever ran (a stale brief left behind by a data-shape change, a summariser that
#: has been failing silently) — see `_fold_input_ceiling`, `_capped_cut`.
_FLAT_FOLD_INPUT_CHARS = 200_000


def _fold_input_ceiling(window: int) -> int:
    """How much new territory one summarisation call may read, scaled to the model's window.

    Half the window, in characters — the same margin `_budget_chars` reserves against the
    main prompt, applied here to the summariser's own much smaller request (an instruction
    and a block of text, no persona or tool schemas riding along, so it can afford to use
    more of the window than the main turn's netted-out share).
    """
    if window <= 0:
        return _FLAT_FOLD_INPUT_CHARS
    return int(window * _CHARS_PER_TOKEN * 0.5)


def _budget_chars(config, tool_chars: int = 0) -> int:
    """How many characters of conversation prose may accumulate before folding.

    Scaled to the model's real window when one is known. The persona and the tool schemas
    share that same window and are roughly fixed per install, so they are netted out first —
    counting only the conversation and ignoring everything else in the same request is the
    exact mistake `llm/ledger`'s docstring calls out (schemas alone have run to ~11k tokens on
    a full toolset). What is left over is the conversation's actual slice of the 80%.

    `tool_chars` is how big that tool block is, measured by the caller. It used to be an
    `agent_db_path` this function took *solely* to reach the tool registry and total the
    schemas itself — which made the module that folds a conversation import the adapter layer.
    A number is what it wanted; the path was how it went to get one.

    Falls back to the flat `history_max_chars` knob when the window is unknown (a local model,
    or one adopted before its window was recorded) — an unknown window means there is no share
    to compute, and guessing one is worse in both directions: guess high and a small model
    fails mid-turn, guess low and every turn folds for nothing.
    """
    window = int(getattr(config, "context_window", 0) or 0)
    if window <= 0:
        from kith.services import tuning

        return int(tuning.value("history_max_chars"))

    return max(
        0,
        int(window * _CHARS_PER_TOKEN * _FOLD_ABOVE_SHARE) - len((config.system or "").strip()) - tool_chars,
    )


def fold(
    history: list[dict[str, Any]],
    config,
    conversation_id: str,
    host: str = "",
    tool_chars: int = 0,
    force: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Compact ``history`` using the settings' thresholds and the conversation's stored brief.

    The impure companion to :func:`compact`: it reads the knobs, reads the last brief off the
    transcript, and supplies a real (fail-safe) summariser. Returns the same
    ``(messages, new_brief_or_None)`` — the caller persists a non-None brief.
    """
    from kith.services import conversations, tuning

    # `force` is what makes a fold something you can ask for.
    #
    # The budget is the right test for a turn defending itself: fold only when the window is
    # actually tight, because a fold costs a model call. It is the wrong test for a person who
    # has just asked, and it is the whole reason `/fold` looked broken — the command ran, the
    # endpoint answered, and the fold declined with "not enough here to be worth folding" on a
    # conversation of six hundred thousand tokens, because six hundred thousand is still inside
    # a 1.05M window.
    #
    # Asked for, the budget becomes one character: everything but `keep_recent` is old enough to
    # summarise. Nothing else about the fold changes — same summariser, same brief, same
    # accumulation onto the previous one.
    max_chars = 1 if force else _budget_chars(config, tool_chars)
    keep_recent = int(tuning.value("history_keep_recent"))
    prior = conversations.latest_summary(conversation_id) if conversation_id else {}
    window = int(getattr(config, "context_window", 0) or 0)
    return compact(
        history,
        lambda text: _summarize(text, config, host),
        prior,
        max_chars=max_chars,
        keep_recent=keep_recent,
        max_fold_chars=_fold_input_ceiling(window),
    )


def fold_dry(
    history: list[dict[str, Any]],
    config,
    conversation_id: str = "",
    *,
    tool_chars: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """What the fold would leave behind, without paying for one.

    The read-only twin of :func:`fold`, for showing someone the prompt their next turn will send.
    Same knobs, same stored brief, same :func:`compact` — and a summariser that always comes back
    empty, so no model is called and nothing is persisted.

    That is not a trick played on `compact`: an empty summary is its documented fail-safe ("a big
    prompt beats a broken turn"), so this is the path it already takes whenever the summariser is
    down. The preview is therefore byte-exact in the two cases that cover almost every
    conversation — under the budget, or a stored brief that still covers the tail — and in the
    third it declines to invent one.

    The second value is that third case: a fold is due, and the next real turn will pay for one
    before it sends. The caller has to say so out loud, because a screen whose whole purpose is
    that its numbers are the real numbers must not quietly show a prompt about to be replaced.
    """
    from kith.services import conversations, tuning

    max_chars = _budget_chars(config, tool_chars)
    prior = conversations.latest_summary(conversation_id) if conversation_id else {}
    folded, fresh = compact(
        history,
        lambda _text: "",  # never summarise — see above
        prior,
        max_chars=max_chars,
        keep_recent=int(tuning.value("history_keep_recent")),
        max_fold_chars=_fold_input_ceiling(int(getattr(config, "context_window", 0) or 0)),
    )
    # `compact` returns the list untouched both when nothing needed folding and when a fold was
    # due but went unpaid for. Only the second is worth reporting, and what separates them is
    # whether the conversation is over the budget at all.
    unchanged = fresh is None and len(folded) == len(history)
    return folded, bool(unchanged and sum(message_chars(m) for m in history) > max_chars)


def _summary_tokens(window: int) -> int:
    """How much room the brief gets, scaled to the window it will live in.

    An unknown window gets the floor. That is the honest answer rather than a cautious one:
    there is no share to take of a number nobody recorded, and the floor is what this was for
    its whole life before the sections existed.
    """
    if window <= 0:
        return _SUMMARY_MIN_TOKENS
    return int(min(_SUMMARY_MAX_TOKENS, max(_SUMMARY_MIN_TOKENS, window * _SUMMARY_SHARE)))


def _summarize(text: str, config, host: str) -> str:
    """Ask the model for a brief. Returns "" on any failure — a fold must never break a turn.

    Reasoning stays off. Six headings turn this from a judgement into an extraction — go
    through the text and find what belongs under each one — and extraction is the shape of work
    that reasoning tokens buy the least on, while a fold happens on every long conversation and
    is paid for every time. Uses the same transport the turn itself would (cloud when a key and
    endpoint are set, else local Ollama).
    """
    from kith.llm import ollama, openai_compat

    prompt = [{"role": "system", "content": _INSTRUCTION}, {"role": "user", "content": text}]
    window = int(getattr(config, "context_window", 0) or 0)
    slim = replace(config, think=False, effort="", num_predict=_summary_tokens(window))
    from kith.services import tuning

    try:
        if slim.api_key and slim.base_url:
            # The same steering the turn's own rounds get. `stream_once` defaults to
            # `Routing()` when handed nothing, and defaulting here would quietly stop honouring
            # a pinned provider or a price ceiling on this one call — a behaviour change hiding
            # inside a layering fix.
            stream = openai_compat.stream_once(prompt, slim, host, tools=None, routing=tuning.routing())
        else:
            stream = ollama.stream_once(prompt, slim, host, tools=None)
        parts: list[str] = []
        for event in stream:
            if event.get("type") == "error":
                return ""
            if event.get("type") == "delta" and event.get("role") == "text":
                parts.append(event.get("text") or "")
        return "".join(parts).strip()
    except Exception:
        return ""


def compact(
    history: list[dict[str, Any]],
    summarize: Callable[[str], str],
    prior: dict[str, Any] | None = None,
    *,
    max_chars: int,
    keep_recent: int,
    max_fold_chars: float = _FLAT_FOLD_INPUT_CHARS,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Fold old turns into a brief. Returns ``(messages, new_brief_or_None)``.

    ``new_brief`` is ``{"through": n, "text": ...}`` when a fresh summary was made and should
    be persisted, or ``None`` when the history was left as-is or an existing brief was reused.

    Sized with :func:`kith.llm.budget.message_chars` rather than a bare ``len(content)`` —
    ``history`` can now carry replayed tool calls (see ``conversations.full_messages``), and
    a ``{"role": "assistant", "tool_calls": [...]}`` message has no string ``content`` at
    all. Measuring only string content did not just under-count one of those, it made it
    invisible: zero, every time, however large its arguments actually were.
    """
    prior = prior or {}
    covered = int(prior.get("through") or 0)
    brief = str(prior.get("text") or "")
    count = len(history)

    # A stored cursor only means anything against the shape it was cut from. Replayed tool
    # calls (`conversations.full_messages`) changed that shape from under any brief made
    # before today: the same numeric index that used to land between two turns can now land
    # inside one — between a tool call and its own result. Replaying from there is not "a
    # slightly wrong resume point", it is a message list a provider will refuse outright.
    # Distrust the cursor whenever it no longer points at an actual boundary, and start over
    # rather than build on a number that has stopped meaning what it once did.
    if covered and not (covered >= count or history[covered].get("role") == "user"):
        covered, brief = 0, ""

    prose = sum(message_chars(m) for m in history)
    # Short, and never folded before — leave it byte-for-byte as it came in.
    if prose <= max_chars and not brief:
        return history, None

    # A brief already covers all but a recent stretch: reuse it, no model call, as long as
    # that stretch hasn't grown past the same real budget the first-ever fold is judged
    # against.
    #
    # Deliberately *not* a message count. The first version of this fix compared
    # `count - covered` against a multiple of `keep_recent` — messages, not size — which
    # brought back the module's own "rolling, not per-turn" claim but for the wrong reason:
    # it folds on a made-up turn cadence with no idea whether the window it was actually sized
    # against is anywhere near full. Two one-line replies and a 4,000-token tool dump both
    # count as "one turn" to a message counter; they are nothing alike in the room they cost.
    # And it is *permanent* once a brief exists: `prose <= max_chars and not brief` above only
    # ever runs before the very first fold, so every fold after that was answering to a
    # cadence, not a budget, for the rest of the conversation's life — measured against a real
    # 2-day, 40-fold conversation: still folding every ~9 messages long after the actual
    # accumulated text was nowhere near what the window could hold.
    tail_chars = sum(message_chars(m) for m in history[covered:])
    if brief and 0 <= covered <= count and tail_chars <= max_chars:
        return [_summary_message(brief), *_what_they_asked(history, covered), *history[covered:]], None

    # (Re)fold everything but the most recent `keep_recent` turns.
    cut = _turn_aware_cut(history, keep_recent)
    if cut <= covered:
        # Nothing new precedes the protected tail — leave it alone rather than pay for a
        # summary of nothing.
        return history, None
    # How far past `covered` one call may read. Deliberately not `max_chars` — that is the
    # trigger threshold for the *whole* conversation, not what one *request* can actually
    # digest. A conversation that fell behind before this ever ran (replayed tool calls make
    # old turns far bigger than a flat message count once assumed) can owe far more new
    # territory than any one call could read: catching up over a handful of bounded,
    # always-successful calls beats one call asked to read all of it, which is exactly the
    # shape that makes the request fail outright rather than merely cost more.
    cut = _capped_cut(history, covered, cut, max_fold_chars)
    text = _as_text(history[covered:cut])
    if brief:
        # Carry the previous brief forward rather than dropping what it captured.
        text = f"[Summary so far]\n{brief}\n\n[Continued]\n{text}"
    fresh = (summarize(text) or "").strip()
    if not fresh:
        return history, None  # summariser failed — a big prompt beats a broken turn
    # The brief covers what he did; these are the questions it was not allowed to compress.
    return (
        [_summary_message(fresh), *_what_they_asked(history, cut), *history[cut:]],
        {"through": cut, "text": fresh},
    )


#: Ceiling on the verbatim questions carried past a fold, in characters.
#:
#: Generous on purpose, because the measurement says it never binds: across two of the longest
#: real conversations here — 3,133 messages and 2.27M characters, and 453 messages and 1.03M —
#: everything the person said came to 1.10% and 0.45% of the total. This exists for the one
#: shape that could break that, someone pasting a document per message, and it keeps the most
#: recent rather than the first: an old question that has been answered is the one worth losing.
_KEEP_ASKED_CHARS = 60_000


def _what_they_asked(history: list[dict[str, Any]], upto: int) -> list[dict[str, Any]]:
    """The person's own messages from the part being folded away, kept word for word.

    **A fold may compress what he said and did. It may not compress what they asked.**

    The summariser is handed a transcript that is overwhelmingly his own output — measured on a
    real conversation, tool results 38%, his prose 27%, and the person's words 0.3% — so it
    faithfully summarises the work and drops the questions. One fold turned six turns into
    "CI optimization is pushed in ce06d61", which was true, and lost a question about milestones
    that had never been answered. The instruction it runs under asks it to preserve "unfinished
    threads"; it cannot weigh a thread it can barely see.

    This is the same failure as a new message losing to the previous task in the live prompt,
    one layer down. Both are the person being outweighed by volume, and neither is fixed by
    asking the model to try harder — so the questions are not offered to the summariser's
    judgement at all.

    Costs about one per cent, which is the number that makes this obvious rather than clever.
    """
    asked = [m for m in history[:upto] if m.get("role") == "user"]
    kept: list[dict[str, Any]] = []
    spent = 0
    for message in reversed(asked):  # newest first, so a ceiling drops the oldest
        size = message_chars(message)
        if spent + size > _KEEP_ASKED_CHARS and kept:
            break
        kept.append(message)
        spent += size
    kept.reverse()
    return kept


def _summary_message(brief: str) -> dict[str, Any]:
    # `_summary` marks this as folded *conversation*, not system instruction. It rides as a
    # `system` message because that is what a summary of the past is to the model — but for the
    # ledger it is the conversation, compressed, and counting it under "Who he is" (which is
    # where every unmarked system message lands) filed a summary of the chat under the persona.
    # Stripped before the wire like every other internal key; see `openai_compat._to_openai`.
    return {"role": "system", "content": f"{_SUMMARY_HEADER}\n{brief}", "_summary": True}


def _turn_starts(history: list[dict[str, Any]]) -> list[int]:
    """Index of each turn's own user message, in order.

    A ``role == "user"`` entry is always exactly one turn boundary and nothing else can be
    mistaken for one: every synthetic user-role message the live loop injects mid-turn —
    the landing directive, a dropped-exchange note, an in-turn fold's own summary — lives
    only in that turn's in-memory ``convo`` and is never written to the transcript, so
    ``conversations.full_messages`` never manufactures one of these that isn't real.
    """
    return [i for i, m in enumerate(history) if m.get("role") == "user"]


def _turn_aware_cut(history: list[dict[str, Any]], keep_recent: int) -> int:
    """Where to fold up to, keeping at most the last ``keep_recent`` turns whole.

    Counts turns, not list items — a flat ``len(history) - keep_recent`` was the right
    arithmetic back when a turn was reliably two items (one user, one assistant); replayed
    tool calls make that no longer true, and a turn-heavy exchange could be twenty items to
    a plain one's two. A cut can safely land on any turn boundary and never needs to check
    for an open tool call the way the in-turn fold does (``compaction._open_calls_at``) —
    a turn's own calls are always strictly between its opening user message and the next
    one, so a boundary between turns can never fall inside a call/result pairing.

    ``keep_recent`` is a ceiling on what stays untouched, not a precondition a short
    conversation must clear before folding is allowed to help at all: a conversation with
    fewer turns than ``keep_recent`` still folds whatever precedes its most recent ones,
    rather than folding nothing and quietly disabling the one mechanism relied on to bound
    growth. Only a single turn (or none) has nothing before it to fold, and returns 0.
    """
    starts = _turn_starts(history)
    if len(starts) <= 1:
        return 0
    protect = min(max(0, keep_recent), len(starts) - 1)
    return starts[len(starts) - protect] if protect else len(history)


def _capped_cut(history: list[dict[str, Any]], covered: int, cut: int, ceiling: float) -> int:
    """The furthest turn boundary between ``covered`` and ``cut`` whose new territory —
    ``history[covered:boundary]`` — still fits ``ceiling``.

    ``_turn_aware_cut`` answers "how far *should* this fold reach"; this answers "how much of
    that can one request actually carry". They can disagree by a lot: a conversation that
    fell behind before folding ever ran on it can owe millions of characters of new territory,
    and asking one summarisation call to read all of it either exceeds the model's own input
    limit outright or is simply too slow to be worth attempting. Splitting the difference over
    several turns, each comfortably under the ceiling, means every one of those calls actually
    succeeds — the alternative is the same oversized call failing, silently, forever.

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
