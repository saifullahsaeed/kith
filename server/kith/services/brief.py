"""What a fold writes, how it is asked for, and how a reply is told apart from one.

Two folds share this. `services.history` folds the older turns of a conversation into a brief
that is stored and replayed on every later turn; `services.compaction` folds the middle of one
long turn into notes that last until the turn ends. They used to have two instructions, two
shapes and two ideas of what a failed reply looks like, and only one of them checked at all.

**The request is the conversation's own prompt, with this appended as its last message.** Both
folds used to send a fresh two-message request — the instruction as a system message, then the
history rendered as a transcript (`--- user:` / `--- assistant:`) — and measured over two weeks
(Sep 13-27, ten folds) that request failed half the time. Three briefs stopped at the output cap
with their last section missing; two came back as the next chat reply, because a model handed a
long dialogue and a system message at the far end of it continues the dialogue. It was also a
request no host had ever seen, so every token of it — up to ~520k — was billed uncached, and it
went to a different host from the conversation's own. Sending what the turn would have sent
anyway, cut at the fold point, makes the fold a cache read on the host that already holds it,
and puts the instruction where the model looks last.

**A reply is a brief only if it could be one.** A tool call, a reply cut off at the output cap,
or prose without the headings is a failed fold, never a worse brief — the brief is the only
memory of what it replaces. `read` is the one place that decides.

**A brief built on a brief carries its past forward in code.** Asking the model to "carry the
earlier brief forward" did not survive a fixed output cap: between two consecutive folds of one
real conversation "What was done" went from 6,455 characters to 2,421 and "What was found" from
4,018 to 1,950, while the cap was already binding. So the sections that only ever grow are
carried by `carried`, word for word, and the model is asked only for what is new under them;
the two that describe the present are rewritten whole each time, because the present is what
changed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

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

#: The sections that only ever grow. What someone asked for, what was decided, done and found
#: stay true after the next fold — so they are carried forward by `carried`, and a later fold
#: adds to them rather than rewriting them. The last two describe the present, which is the
#: part that moves, so each fold replaces them.
ACCUMULATING = SECTIONS[:4]

#: How many of :data:`SECTIONS` a reply must carry to be taken as a brief. A majority, not all
#: six: the instruction asks for every one, but a brief that dropped a section is still a brief,
#: and one with none is not. A brief that lost its tail to the output cap used to pass this with
#: five of six; it is now refused on its `finishReason` instead, which is the actual fact.
_SECTIONS_TO_COUNT = 4

#: Room for a brief, as a share of the window — the output side of the same argument the fold
#: threshold makes about the input. The floor is what a small local model has always had.
_SHARE = 0.04
MIN_TOKENS = 1_600

#: The ceiling on one fold's reply. A runaway guard, not a size target, and that is why it moved.
#:
#: At 4,000 it was the size target: measured Sep 13-27, three of ten folds stopped at exactly
#: 4,000 tokens with "Where it stands" missing, and one ended mid-list ("…deliberately
#: deferred.\n-"). A cut-off brief was then stored as though it were whole. It is refused now
#: (`read`), so a ceiling that real briefs reach would turn into a fold that fails on every turn.
#: A ceiling costs nothing until it is reached, and what bounds a brief's size now is the
#: instruction and the chaining — each fold writes only what is new since the last one.
MAX_TOKENS = 8_000


def tokens(window: int) -> int:
    """How much room the brief gets, scaled to the window it will live in.

    An unknown window gets the floor. That is the honest answer rather than a cautious one:
    there is no share to take of a number nobody recorded.
    """
    if window <= 0:
        return MIN_TOKENS
    return int(min(MAX_TOKENS, max(MIN_TOKENS, window * _SHARE)))


#: How each section is asked for.
#:
#: The sections replace one paragraph that named six things to preserve *inside a blob* and then
#: said "be concise" three different ways. What came back was a blob — accurate, and useless: a
#: real fold of six turns produced four lines about CI sharding and lost every question that had
#: been asked. A heading per concern makes the model go and look for each one separately.
#:
#: **The person comes first.** On a transcript that is 0.3% their words, an instruction that does
#: not name them produces a summary without them in it. **Intent, not a replay:** a first draft
#: asked for every request "quoted in their own words", and on a real 146,000-character
#: conversation it wrote fifty-odd verbatim lines, down to "keepgoimh", and hit the output cap in
#: section two. Their words are already carried separately (`history._what_they_asked`).
#:
#: Deliberately not shaped around code. Kith's conversations are spreadsheets, audits, research
#: and websites at least as often, and a section called "files and code" would tilt every fold
#: toward the one kind of work that happens to name its artifacts in backticks.
_GUIDANCE = {
    "What they asked for": (
        "What the person wanted, in the order it was asked for. The intent behind each request — "
        "NOT a replay of their messages, which are carried forward separately and word for word. "
        "Quote their own phrasing only where a paraphrase would lose something: a constraint, a "
        "preference, a correction, a standing rule."
    ),
    "What was decided": (
        "Each choice made and the reason it was made, plus any constraint or preference they "
        "stated that still applies."
    ),
    "What was done": (
        "The work actually carried out, naming things exactly: files and paths, records, "
        "identifiers, commands, figures. A name you half-remember is worse than no name."
    ),
    "What was found": (
        "Facts established and measurements taken, and every problem hit — with how it was "
        "resolved, or that it was not. Include what was tried and did not work, so that it is "
        "not tried again."
    ),
    "What is still open": (
        "Questions asked and not answered, work deferred, anything known to be broken, unverified "
        "or waiting on someone."
    ),
    "Where it stands": (
        "What was being worked on at the moment this text ends, and the next step if there is an obvious one."
    ),
}


def instruction(replaces: str, *, earlier: str = "", continuing: bool = False) -> str:
    """The message appended to the prompt to ask for a brief of it.

    ``replaces`` names what the brief stands in for, as the subject of a sentence — "The
    conversation above". ``earlier`` is the header an earlier summary already in the prompt is
    marked with, if there is one. With ``continuing`` that summary is a brief of this shape and
    is kept (see `carried`), so only what is new is asked for; without it, the summary is an
    older shape that this brief replaces along with everything else.

    Opens by telling the model to stop, because the message it follows is a conversation that is
    mid-flight and the most natural thing to do with one is to carry on with it — which is what
    two of ten real folds did.
    """
    lines = [
        "Stop here. Do not reply to the conversation and do not use any tool. "
        f"{replaces} is being compressed so the work can carry on in less space, and what you "
        "write now REPLACES it entirely — it is the only memory of it that survives, so anything "
        "you leave out is gone.",
        "",
        "Write these six sections, in this order, using these exact headings, and nothing before "
        "the first one. Keep a heading with 'nothing' under it rather than dropping it.",
    ]
    for heading in SECTIONS:
        lines += ["", f"## {heading}", _GUIDANCE[heading]]
    lines += [
        "",
        "Quote rather than paraphrase wherever the exact words carry the meaning: what the person "
        "said, error text, identifiers, figures. A short verbatim extract beats a longer "
        "description of one. Invent nothing — if it is not in the text, it does not go in.",
    ]
    if earlier and continuing:
        kept = ", ".join(f"'{heading}'" for heading in ACCUMULATING)
        lines += [
            "",
            f"The summary marked {earlier} above is your own earlier brief, and it is kept: its "
            f"sections {kept} are carried forward word for word, and what you write under those "
            "four headings is added beneath them. So under those four, write ONLY what is new "
            "since that brief, or 'nothing' — repeating what it already says only makes it "
            "longer. 'What is still open' and 'Where it stands' replace the earlier ones, so "
            "write each of those two whole, as things stand now, keeping whatever in the earlier "
            "ones is still true. Do not summarise the earlier brief again: compressing a summary "
            "is how a conversation forgets.",
        ]
    elif earlier:
        lines += [
            "",
            f"The summary marked {earlier} above is an older summary in a different shape, and it "
            "is replaced along with everything else: carry its content into the matching "
            "sections. Do not summarise it again — compressing a summary is how a conversation "
            "forgets.",
        ]
    return "\n".join(lines)


def is_a_brief(text: str) -> bool:
    """Did the summariser write a brief, or something else?

    A fold stores whatever comes back as the only memory of what it replaced, so an answer that
    is not a brief is not "a worse brief" — it is the conversation erased. Measured 2026-09-26: on
    a 500k-token conversation the summariser answered as if it were the next turn ("Two things:
    it's finished, and I was wrong about one of my own numbers…"), 1,270 characters with no
    heading at all, and that replaced 3,478 transcript entries.
    """
    return sum(f"## {section}" in text for section in SECTIONS) >= _SECTIONS_TO_COUNT


@dataclass(frozen=True)
class Reply:
    """What one fold call came back with."""

    #: The brief, from its first heading on — or "" when the reply is not one.
    text: str
    #: What the call cost, whatever it came back as. A refused brief was still paid for.
    stats: dict[str, Any] | None
    #: "brief", or why it was refused, for the transcript. Written down because the last round
    #: of this took a morning of matching transcript lines to provider logs to reconstruct.
    outcome: str


def read(events: Iterable[dict[str, Any]]) -> Reply:
    """Take one fold call's stream and decide whether it produced a brief.

    Refused, in the order checked:

    * **an error** — nothing came back, or the stream died part-way;
    * **a tool call** — the request carries the conversation's own tools, because a different
      tools block is a different prompt and would not be read from cache. Offering them is
      therefore unavoidable, and a model that takes one has not written a brief;
    * **cut off** — `finishReason` of `length`. A brief that ran into the cap has lost its end,
      and the end is "Where it stands", the one part a resumed turn needs first;
    * **not a brief** — prose without the headings, which is what a model continuing the
      conversation writes.
    """
    parts: list[str] = []
    stats: dict[str, Any] | None = None
    called = False
    for event in events:
        kind = event.get("type")
        if kind == "error":
            return Reply("", stats, f"failed: {str(event.get('message') or '')[:200]}")
        # Prose only: reasoning arrives on the same event type under another role and is not the
        # brief.
        if kind == "delta" and event.get("role") == "text":
            parts.append(str(event.get("text") or ""))
        elif kind == "turn":
            stats = event.get("stats")
            called = bool(event.get("tool_calls"))
    text = "".join(parts).strip()
    if called:
        return Reply("", stats, "called a tool")
    if str((stats or {}).get("finishReason") or "") == "length":
        return Reply("", stats, "cut off")
    if not is_a_brief(text):
        return Reply("", stats, "not a brief")
    return Reply(carried("", text), stats, "brief")


#: A heading line, matched on the heading alone so "## What was done:" and "## what was done"
#: still count. `###` sub-headings inside a section are left to the section.
_HEADING = re.compile(r"^##\s+(?P<name>[^#].*?)\s*:?\s*$")

#: What a model writes under a heading when there is nothing new to say — "nothing", "- None.",
#: "Nothing new since the earlier brief." Spelled out rather than "starts with nothing", so a real
#: item that happens to open with the word ("Nothing in the logs explains the 502s") stays one.
_NOTHING = re.compile(
    r"(nothing|none|n/?a)(\s+(new|further|more|else|to add|yet))*(\s+since\b[^\n]*)?[\s.!]*", re.I
)


def _sections(text: str) -> dict[str, str]:
    """The body under each of :data:`SECTIONS`, keyed by heading. Anything before the first
    heading is dropped — it is the preamble ("Here is the brief:") the instruction asks for
    none of."""
    known = {heading.lower(): heading for heading in SECTIONS}
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        match = _HEADING.match(line.strip())
        name = known.get(match.group("name").strip().lower()) if match else None
        if name is not None:
            current = found.setdefault(name, [])
            continue
        if current is not None:
            current.append(line)
    return {name: "\n".join(body).strip() for name, body in found.items()}


def _says_nothing(body: str) -> bool:
    return bool(_NOTHING.fullmatch(body.strip(" \t\n-*•_")) or not body.strip())


def carried(prior: str, fresh: str) -> str:
    """The brief to store: the earlier one's lasting sections, with what is new added beneath.

    ``prior`` is the earlier brief, or "" when there is none. ``fresh`` is what the model just
    wrote — under a continuing instruction, only the new items for the first four sections and
    the whole of the last two. The result is always the six sections in order, so every stored
    brief has one shape however the model laid out its reply.

    A present-tense section the model left out keeps the earlier one: a slightly stale "Where it
    stands" is worse than a current one and better than none.
    """
    before, now = _sections(prior), _sections(fresh)
    out = []
    for heading in SECTIONS:
        old, new = before.get(heading, ""), now.get(heading, "")
        if heading in ACCUMULATING:
            kept = [part for part in (old, new) if not _says_nothing(part)]
            body = "\n".join(kept)
        else:
            body = new if heading in now else old
        out.append(f"## {heading}\n{body.strip() or 'nothing'}")
    return "\n\n".join(out)
