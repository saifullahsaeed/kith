"""One budget for the present-state block, and the rule for who gets it.

The block that tells him where he is used to be seven independent caps with no arbiter:
``project_context.LIMITS`` at {8, 10, 6}, ``memory_context`` at 30 core and 8 recent, the
channel at 8, a project description at 600 characters, three milestones ahead. Each of those
numbers is defensible on its own and none of them knows about the others, so nothing in the
codebase could answer "how much is all of this together". Measured on a real database: a
conversation about nothing carried 13,362 characters before the person typed a word, of which
about 10,500 was another project's memories.

Raising or lowering any one of those constants cannot fix that, which is why this is a module
and not a smaller number. A total exists, somebody has to own it, and the sources have to
compete rather than each take what it feels entitled to.

**Pure on purpose.** No database, no clock, no config. The decision about what to drop from a
prompt is exactly the kind of thing that needs to be testable at a hundred cases a second, and
every previous attempt in this codebase to make that decision inside the code that also
*fetches* the content ended up untested. The caller gathers, this ranks, the caller joins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Tiers. Lower is paid first, and the gaps are deliberate — a tier is a statement about what
#: kind of thing the block is, not a priority number to be nudged.
#:
#: ESSENTIAL is not "important". It is "wrong without it": the clock, and the fact that someone
#: is waiting on a reply. Both are tiny and both make the turn incorrect by their absence, so
#: they are paid before the budget is consulted rather than ranked inside it.
ESSENTIAL = 0
#: The subject of the conversation — the project he is in, the files he has already opened.
#: Leaving one of these out does not make the turn wrong; it makes it repeat work.
WORTH_HAVING = 10
#: Background that is true but not asked for: other projects, older memories, plugin state.
#: This is the tier the isolation complaint lives in, and the tier that should lose first.
NICE_TO_HAVE = 20


@dataclass(frozen=True)
class Block:
    """One candidate region of the present-state block."""

    #: Stable name, for the ledger and for tests. Never shown to the model.
    key: str
    #: What would be inserted, already formatted.
    text: str
    #: Which tier it is paid in.
    tier: int = WORTH_HAVING
    #: How much this has to do with the conversation in hand, in [0, 1]. Only ever compared
    #: against other blocks in the same tier — an absolute value here means nothing.
    score: float = 0.0
    #: Whether the block is a list whose tail can be dropped. A paragraph is not: cutting one
    #: mid-sentence reads as corruption and costs the same as leaving it out.
    trimmable: bool = False


def allocate(blocks: list[Block], budget: int) -> list[Block]:
    """The blocks that fit, in the order they were given.

    Essentials first and unconditionally. Everything else competes by tier, then by relevance,
    and takes what is left when its turn comes — so a block losing does not end the auction,
    because something smaller behind it may still fit. That "keep going" rule is what stops one
    enormous memory from silently evicting the whole rest of the block.

    Returned in the caller's order rather than the order they won in. Allocation is a ranking
    problem; the order a prompt is read in is a separate decision the caller already made, and
    sorting the output by score would put the clock underneath the task list.
    """
    chosen: dict[str, Block] = {}
    spent = 0

    for candidate in blocks:
        if candidate.tier == ESSENTIAL and candidate.text:
            chosen[candidate.key] = candidate
            spent += len(candidate.text)

    rest = [b for b in blocks if b.tier != ESSENTIAL and b.text]
    # Stable within a tie: `sorted` keeps the caller's order for blocks that rank equally, so
    # two equally relevant blocks are resolved by the order the prompt wants rather than by
    # whatever the dict happened to yield.
    for candidate in sorted(rest, key=lambda b: (b.tier, -b.score)):
        room = budget - spent
        if room <= 0:
            break
        fitted = _fit(candidate, room)
        if fitted is None:
            # Skipped, not stopped. The budget may still hold something smaller further down.
            continue
        chosen[fitted.key] = fitted
        spent += len(fitted.text)

    return [chosen[b.key] for b in blocks if b.key in chosen]


def _fit(candidate: Block, room: int) -> Block | None:
    """The block as it can be afforded, or None if it cannot be."""
    if len(candidate.text) <= room:
        return candidate
    if not candidate.trimmable:
        return None
    return _shorten(candidate, room)


def _shorten(candidate: Block, room: int) -> Block | None:
    """Drop whole lines off the tail until it fits.

    A trimmable block is a heading followed by items, so the heading is kept and the tail goes.
    If only the heading survives the block is dropped entirely: a heading with nothing under it
    is a promise the block does not keep, and it costs tokens to make it.
    """
    lines = candidate.text.split("\n")
    while len(lines) > 1 and len("\n".join(lines)) > room:
        lines.pop()
    if len(lines) <= 1:
        return None
    return Block(
        key=candidate.key,
        text="\n".join(lines),
        tier=candidate.tier,
        score=candidate.score,
        trimmable=candidate.trimmable,
    )


# --------------------------------------------------------------------------- #
# Relevance — how a memory earns a slot.
# --------------------------------------------------------------------------- #

#: Words that match everything and therefore distinguish nothing. Not a linguistic stoplist:
#: without it every memory scores against every conversation on "the", "that" and "this", which
#: is arithmetically the same as having no scoring at all — and the failure is invisible,
#: because a uniform score still produces a confident-looking ranking.
_COMMON = frozenset(
    [
        "about",
        "after",
        "again",
        "against",
        "all",
        "also",
        "and",
        "any",
        "are",
        "because",
        "been",
        "before",
        "being",
        "between",
        "both",
        "but",
        "can",
        "come",
        "could",
        "did",
        "does",
        "doing",
        "done",
        "down",
        "each",
        "even",
        "every",
        "for",
        "from",
        "get",
        "got",
        "had",
        "has",
        "have",
        "here",
        "how",
        "into",
        "its",
        "just",
        "like",
        "made",
        "make",
        "many",
        "more",
        "most",
        "much",
        "must",
        "need",
        "not",
        "now",
        "off",
        "once",
        "one",
        "only",
        "other",
        "our",
        "out",
        "over",
        "own",
        "same",
        "should",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "thing",
        "things",
        "this",
        "those",
        "through",
        "too",
        "under",
        "until",
        "upon",
        "use",
        "used",
        "using",
        "very",
        "was",
        "way",
        "well",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "within",
        "without",
        "would",
        "you",
        "your",
    ]
)

#: Below this, a word is too short to be evidence. "api" and "css" are real losses; "was", "the"
#: and "for" are the reason. The stoplist above handles the common short words it can name, and
#: this handles the long tail it cannot.
_MIN_TERM = 4


def _singular(word: str) -> str:
    """`invoices` and `invoice` are the same evidence. Nothing more ambitious than that.

    A real stemmer is a dependency and a much larger behaviour change; a plural is the one
    inflection that actually costs matches here, because this vocabulary is nouns — invoice,
    task, sync, token, milestone — and a memory and a question about the same noun routinely
    disagree about its number.

    Words ending in `ss` are left alone: `class`, `process` and `address` are not plurals, and
    stripping them produces a stem that matches nothing while destroying one that did.
    """
    if len(word) > _MIN_TERM and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def terms(text: str) -> set[str]:
    """The distinctive words of a piece of text, lowercased and de-pluralised.

    Deliberately crude — no stemmer, no embedding. An embedding would be better and costs a
    model call on the prompt-assembly path, which is the one place in this codebase that must
    never wait on a network. Crude and instant beats accurate and conditional here: the job is
    to tell "missile apogee" from "button colour", not to rank two near-synonyms.
    """
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {_singular(w) for w in words if len(w) >= _MIN_TERM and w not in _COMMON}


def relevance(about: str, content: str) -> float:
    """How much the conversation and a memory are talking about the same things, in [0, 1].

    The **overlap coefficient** — the shared vocabulary over the *smaller* of the two — because
    the two sides have wildly different lengths and no asymmetric measure survives that.

    This was first written as "how much of the memory is already on the table", dividing by the
    memory's own vocabulary, on the reasoning that conversations are long and memories short.
    Measured against the real database, the reverse is true: Kith's memories run past two
    thousand characters and a question is a dozen words, so a memory needed a third of its
    entire vocabulary present in one short question — arithmetically impossible. Every recall
    memory scored near zero and the level became unreachable rather than quieter. The best
    question anyone could ask about the ballistic-sim memory scored 0.04 against a bar of 0.34.

    Dividing by the smaller side is symmetric, so neither a long memory nor a long conversation
    is punished for its length, and the question it answers — "of the narrower subject here, how
    much do these two share" — is the one actually being asked.

    Zero when either side has nothing distinctive to say. An empty conversation must score
    nothing rather than everything, or the first turn of a new chat pulls in the lot, which is
    precisely the conversation this exists to keep clean.
    """
    wanted = terms(content)
    spoken = terms(about)
    if not wanted or not spoken:
        return 0.0
    return len(wanted & spoken) / min(len(wanted), len(spoken))


#: How much of a recall memory has to already be in the conversation before it rides along.
#:
#: Not a knob to tune when something does not surface — `recall` is a tool, and a memory that
#: does not clear this is exactly one call away. The number is a statement about what "related"
#: means: a third of a memory's distinctive vocabulary already on the table is a real signal,
#: and anything much lower re-admits the whole database on incidental word overlap.
RELEVANT_ENOUGH = 0.34


def eligible_memories(memories: list[dict], about: str) -> list[dict]:
    """The memories allowed to arrive without being asked for, core first.

    Core is unconditional — that is what the level means, and what `set_memory_level` promises
    the model when it puts something there. Recall has to earn it, which is *also* what the
    level means: "kept, retrieved when you reach for it" was always the contract, and the
    recency channel that used to inject the eight most recent non-core memories broke it for
    every memory in the database, since with two levels "non-core" is just "recall".

    Core keeps the caller's order; recall is sorted by how much it has to do with the
    conversation. Core leads regardless — it is "the front of your mind" and the block says so
    in words, which reads wrong underneath something he had to be reminded of.
    """
    core = [m for m in memories if m.get("level") == "core"]
    scored = [
        (relevance(about, str(m.get("content") or "")), index, m)
        for index, m in enumerate(memories)
        if m.get("level") != "core"
    ]
    earned = [
        m for score, _, m in sorted(scored, key=lambda row: (-row[0], row[1])) if score >= RELEVANT_ENOUGH
    ]
    return core + earned
