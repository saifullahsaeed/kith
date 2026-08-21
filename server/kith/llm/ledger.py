"""Where the context actually went, by category.

`ContextBudget` answers "will the next round fit". This answers "what is in there", which is
a different question and the one nobody could answer. Kith knew its prompts were large — a
measured floor of ~20k tokens a round — and had no way to say how that split between the
persona, the tool schemas, the conversation and the files he had read. Every decision about
what to trim was therefore made blind, by recency, which is why the trimming reached for the
file he still needed while fifteen copies of it sat further up.

Three consumers, one source of truth:

* the meter, so the person can see the window filling and what is filling it;
* the compaction threshold, which should fire on the whole prompt rather than on a hand-rolled
  sum of `content` lengths that missed the tool block entirely;
* eviction, which can now prefer a stale category over a merely older message.

Everything is counted in **tokens**, using the ratio `ContextBudget` calibrated against what
the provider actually reported for the last round. A ledger built on the seed ratio is a guess;
one built on a measurement matches the bill, and matching the bill is the whole point of
showing someone a number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from kith.llm.budget import SEED_CHARS_PER_TOKEN, message_chars

#: Tool results that are really *instructions* rather than findings. Broken out because they
#: behave differently from both: a skill is large, read once, and stays relevant for the whole
#: turn, so it is the last thing worth evicting and the person deserves to see its cost.
_SKILL_TOOLS = ("read_skill",)

#: Tool results that are a *view of the codebase*. The bulk category on any coding turn, and
#: the one where duplicates collect — the same file read fifteen times in one session.
_CODE_TOOLS = (
    "read_file",
    "outline",
    "repo_map",
    "grep",
    "glob",
    "list_files",
    "changes",
    "diagnostics",
    "references",
    "definition",
)


@dataclass(frozen=True)
class Line:
    """One category's contribution."""

    key: str
    label: str
    tokens: int

    def share_of(self, window: int) -> float:
        """Fraction of the window, or 0.0 when the window is unknown.

        0.0 rather than raising: an unknown window is the honest state on a local model, and a
        ledger is still useful without one — the absolute numbers are real, only the
        percentages are unavailable.
        """
        return (self.tokens / window) if window > 0 else 0.0


@dataclass(frozen=True)
class Ledger:
    """A categorised account of one request's context."""

    window: int
    lines: tuple[Line, ...]
    chars_per_token: float

    @property
    def used(self) -> int:
        return sum(line.tokens for line in self.lines)

    @property
    def free(self) -> int:
        """Tokens left, or 0 when the window is unknown or already exceeded."""
        return max(0, self.window - self.used) if self.window > 0 else 0

    @property
    def share(self) -> float:
        return (self.used / self.window) if self.window > 0 else 0.0

    def past(self, share: float) -> bool:
        """Has the window filled beyond `share`?

        **False when the window is unknown**, and callers must treat that as "cannot say"
        rather than "no". `config.context_window` is 0 on a local model or a model adopted
        before its window was recorded, and every guard in this codebase treats a guessed
        window as more dangerous than no window — see `ContextBudget.window`.
        """
        return self.window > 0 and self.share > share

    def of(self, key: str) -> int:
        return next((line.tokens for line in self.lines if line.key == key), 0)

    def as_wire(self) -> dict[str, Any]:
        """For the API, and therefore for the meter."""
        return {
            "window": self.window,
            "used": self.used,
            "free": self.free,
            "share": round(self.share, 4),
            # Sent because a reading is not only read — it is also adjusted. `/fold` changes the
            # window without taking a turn, so it has to convert the characters it removed into
            # the tokens this reading is denominated in, and that conversion is only correct in
            # the ratio this reading was actually costed with. Recomputing from the seed there
            # would quietly re-price the whole conversation at a guess. Absent on any reading
            # recorded before this field existed; callers fall back to the seed.
            "charsPerToken": round(self.chars_per_token, 4),
            "lines": [
                {
                    "key": line.key,
                    "label": line.label,
                    "tokens": line.tokens,
                    "share": round(line.share_of(self.window), 4),
                }
                for line in self.lines
                if line.tokens > 0
            ],
        }


def _tokens(chars: int, ratio: float) -> int:
    return int(chars / ratio) if ratio > 0 else 0


def take(
    convo: list[dict[str, Any]],
    schemas: list[dict] | None = None,
    *,
    persona: str = "",
    window: int = 0,
    chars_per_token: float = SEED_CHARS_PER_TOKEN,
    mcp_names: frozenset[str] | tuple[str, ...] = (),
    custom_names: frozenset[str] | tuple[str, ...] = (),
) -> Ledger:
    """Account for everything a request will carry.

    ``persona`` is the stable head of the system prompt — the region that is byte-identical on
    every request Kith ever makes, and therefore the only one cached *across* turns. Split out
    because "system prompt: 6k" hides the useful fact, which is that most of it is free.

    ``mcp_names`` and ``custom_names`` separate the three kinds of tool schema. They are passed
    in rather than looked up, for the same reason `tool_schemas` takes `mcp` as an argument:
    this runs inside the request path and must not touch a database or a subprocess.
    """
    ratio = chars_per_token if chars_per_token > 0 else SEED_CHARS_PER_TOKEN
    mcp = frozenset(mcp_names)
    custom = frozenset(custom_names)

    system_chars = persona_chars = live_chars = directive_chars = 0
    said_chars = tool_chars = skill_chars = code_chars = image_chars = 0

    for message in convo:
        role = message.get("role")
        size = message_chars(message)
        if role == "system" and message.get("_live"):
            # The block at the tail — what is true only right now. Its own line because it is
            # the one region rewritten on every single turn, and therefore the one place where
            # adding context is nearly free: everything ahead of it stays cached, so this is
            # where anything new will go. Inside "System prompt" a block that has grown to ten
            # thousand tokens is indistinguishable from a large persona, and the number a
            # person checks would not be able to show it.
            live_chars += size
        elif role == "system" and message.get("_directive"):
            # What the harness told the turn to do, mid-turn — the landing nudge, a dead round,
            # an empty one, the budget running out. Its own line for the same reason `live` has
            # one: it is a cost the harness imposes rather than one the conversation earned, and
            # folded into either "System prompt" or "Messages" it is a number nobody can act on.
            # They used to arrive as `role: "user"` and land in Messages, which made a turn that
            # was nudged four times look like a turn where the person said four more things.
            directive_chars += size
        elif role == "system":
            text = str(message.get("content") or "") if isinstance(message.get("content"), str) else ""
            # The persona sits at the head of the system prompt, so its cost comes out of that
            # message rather than being counted twice.
            head = len(persona) if persona and text.startswith(persona) else 0
            persona_chars += head
            system_chars += max(0, size - head)
        elif role == "tool":
            # `tool_name`, not `name`: this loop tags its results with the former, and reading the
            # wrong key silently sorted every tool result into "other" — a ledger that is quietly
            # wrong about its biggest category is worse than no ledger.
            name = str(message.get("tool_name") or message.get("name") or "")
            if name in _SKILL_TOOLS:
                skill_chars += size
            elif name in _CODE_TOOLS:
                code_chars += size
            else:
                tool_chars += size
        else:
            # An image rides as its own user message; counting it as conversation would put a
            # 800-token picture in with the sentence that asked about it.
            if _has_image(message):
                image_chars += size
            else:
                said_chars += size

    built_in_chars = mcp_chars = custom_chars = 0
    for schema in schemas or []:
        name = str(((schema.get("function") or {}).get("name")) or "")
        size = len(json.dumps(schema))
        if name in mcp:
            mcp_chars += size
        elif name in custom:
            custom_chars += size
        else:
            built_in_chars += size

    lines = (
        Line("persona", "Persona", _tokens(persona_chars, ratio)),
        Line("system", "System prompt", _tokens(system_chars, ratio)),
        Line("built_in_tools", "System tools", _tokens(built_in_chars, ratio)),
        Line("mcp_tools", "MCP tools", _tokens(mcp_chars, ratio)),
        Line("custom_tools", "His own tools", _tokens(custom_chars, ratio)),
        Line("live", "Where he is right now", _tokens(live_chars, ratio)),
        Line("directives", "Turn directives", _tokens(directive_chars, ratio)),
        Line("messages", "Messages", _tokens(said_chars, ratio)),
        Line("code", "Code he has read", _tokens(code_chars, ratio)),
        Line("tool_results", "Other tool results", _tokens(tool_chars, ratio)),
        Line("skills", "Skills", _tokens(skill_chars, ratio)),
        Line("images", "Pictures", _tokens(image_chars, ratio)),
    )
    return Ledger(window=window, lines=lines, chars_per_token=ratio)


def _has_image(message: dict[str, Any]) -> bool:
    parts = message.get("content")
    return isinstance(parts, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in parts
    )


#: The argument that says what a call was *about*, in the order to look for it. The first four are
#: the precedence `conversations._let_go_of_old_results` uses when it names a trimmed result back
#: to him — the two have to agree about what a call was about, or the screen and the stub describe
#: the same read in different words.
#:
#: `name` and `id` are here because of what a real conversation did without them: `read_skill`
#: takes `name` and `view_task` takes `id`, so 191 different skills and 87 different tasks each
#: grouped under one empty subject and were reported as near-total waste. A subject list that is
#: too short does not merely lose detail — it invents repeats.
_SUBJECT_KEYS = ("path", "pattern", "command", "query", "name", "id")

#: The `Line.key`s that `itemise` can break down, in the order a screen should show them. Every
#: other line — persona, system prompt, live block, the three kinds of tool schema — comes from
#: config rather than from the conversation, and `messages` and `images` have no call to name. A
#: caller uses this to know which categories open into something and which are just a number.
ITEMISED_KEYS = ("code", "tool_results", "skills")

#: The tools whose second call makes the first one redundant — the ones that *look* at something
#: rather than doing something to it.
#:
#: This is the difference between reading a file twice and editing it twice. A read is a look at
#: something with a current state, so the later look supersedes the earlier: the earlier copy
#: describes a file that has since changed, which is worse than not having it at all. An edit is
#: an act. The second does not make the first redundant; it happened too.
#:
#: Without this the finished screen led with `edit_file ×10` on one file, priced at 2,500 tokens
#: "you could drop without losing anything" — nine edits that were nothing of the kind. `shell` is
#: deliberately out: `ls` is a look and `rm -rf` is not, and the arguments cannot tell them apart.
#: Where it cannot be known, claim nothing — an overstated figure is one the person has to go and
#: check, which is worth less than no figure.
_A_LOOK = frozenset(_CODE_TOOLS + _SKILL_TOOLS)


@dataclass(frozen=True)
class Item:
    """One tool call's contribution, and how many times it was the same call."""

    #: The `Line.key` this rolls up into, so an item can be shown under the category it is part of.
    key: str
    tool: str
    subject: str
    calls: int
    tokens: int
    #: What could be dropped without losing anything: every copy but the newest. Zero when the
    #: call happened once. This is the number worth sorting by — a large file read once is the
    #: cost of the work, while sixty copies of a small one is the thing to remove.
    wasted: int


def itemise(
    convo: list[dict[str, Any]],
    *,
    chars_per_token: float = SEED_CHARS_PER_TOKEN,
) -> tuple[Item, ...]:
    """Which calls filled the tool-result categories, and which of them were the same call again.

    `take` answers "how much"; this answers "what of". They are different questions and the second
    is the one you can act on: 300k of files he needed once and 300k of one file read sixty times
    are the same figure and completely different problems. Measured on one real conversation here,
    before any of this existed: 3,241 code reads, 2.3M tokens of them repeats, one file read 64
    times.

    Deliberately a second walk over **the same list** `take` is given, rather than a rebuild from
    the stored log. The log has more in it — it predates every fold and every trim — so itemising
    it would describe a window that no longer exists and disagree with the meter by millions of
    tokens. Whatever list the caller measured is the list this subdivides.

    Only the three tool-result categories. Persona, system prompt, live block and tool schemas do
    not come from the conversation at all, and `messages` and `images` have no call to name.
    """
    ratio = chars_per_token if chars_per_token > 0 else SEED_CHARS_PER_TOKEN

    # (key, tool, subject) -> [total chars, calls, newest call's chars]
    groups: dict[tuple[str, str, str], list[int]] = {}
    pending: dict[str, Any] = {}

    for message in convo:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            pending = _arguments_of(message)
            continue
        if role != "tool":
            continue

        name = str(message.get("tool_name") or message.get("name") or "")
        key = "skills" if name in _SKILL_TOOLS else "code" if name in _CODE_TOOLS else "tool_results"
        subject = ""
        for wanted in _SUBJECT_KEYS:
            value = pending.get(wanted)
            # `str`/`int` rather than `str` alone: a task id arrives as a number, and treating
            # "not text" as "no subject" put every task he opened into one group.
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                subject = str(value)
                break
        # Consumed: the next tool result without its own assistant turn in front of it has no
        # arguments of its own, and inheriting the previous call's would name the wrong file.
        pending = {}

        size = message_chars(message)
        entry = groups.setdefault((key, name, subject), [0, 0, 0])
        entry[0] += size
        entry[1] += 1
        entry[2] = size

    items = [
        Item(
            key=key,
            tool=tool,
            subject=subject,
            calls=calls,
            tokens=_tokens(chars, ratio),
            # Two conditions, and both are about not overclaiming. The subject has to say these
            # really were the same call — without one, all this knows is that a tool ran twice.
            # And the tool has to be one whose later call supersedes its earlier one, or ten
            # edits to a file get priced as nine stale copies of the tenth.
            wasted=_tokens(chars - newest, ratio) if subject and tool in _A_LOOK else 0,
        )
        for (key, tool, subject), (chars, calls, newest) in groups.items()
    ]
    items = _reconcile(items, groups, ratio)
    # Worst waste first, then largest — so the screen opens on the thing to act on rather than on
    # whatever happened to be read last.
    return tuple(sorted(items, key=lambda item: (-item.wasted, -item.tokens)))


def _arguments_of(message: dict[str, Any]) -> dict[str, Any]:
    """The first tool call's arguments, whichever shape they arrived in.

    `conversations.full_messages` rebuilds them as dicts; `openai_compat._to_openai` turns them
    into JSON text on the way to a provider. Both shapes reach this, and a string that quietly
    fell through would blank every subject on exactly the path a real request takes.
    """
    calls = message.get("tool_calls") or []
    if not calls:
        return {}
    arguments = (calls[0].get("function") or {}).get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            return {}
    return arguments if isinstance(arguments, dict) else {}


def _reconcile(
    items: list[Item],
    groups: dict[tuple[str, str, str], list[int]],
    ratio: float,
) -> list[Item]:
    """Make each category's items add up to exactly what `take` reports for that category.

    `take` converts a category's characters to tokens *once*, at the end. Converting each item on
    its own and adding those up is a different sum — three results of 4,003 characters are 3,002
    tokens counted together and 3,000 counted apart — and real reads are never round numbers, so
    this is the ordinary case rather than the edge one. A detail screen whose rows do not add up
    to the total printed above them is not a detail screen; it is two numbers, one of which is
    wrong, with no way to tell which.

    Fixed by largest remainder: the category total is the measured one, and the rounding loss is
    handed to the items that lost the most of it.
    """
    out = list(items)
    for key in ITEMISED_KEYS:
        mine = [index for index, item in enumerate(out) if item.key == key]
        if not mine:
            continue
        chars = sum(groups[(out[i].key, out[i].tool, out[i].subject)][0] for i in mine)
        short = _tokens(chars, ratio) - sum(out[i].tokens for i in mine)
        # Whoever was rounded down hardest gets the token back, one each, until the sums agree.
        by_remainder = sorted(
            mine,
            key=lambda i: (groups[(out[i].key, out[i].tool, out[i].subject)][0] / ratio) % 1,
            reverse=True,
        )
        for i in by_remainder[:short]:
            out[i] = replace(out[i], tokens=out[i].tokens + 1)
    return out


def items_as_wire(items: tuple[Item, ...]) -> list[dict[str, Any]]:
    """For the API, and therefore for the detail screen."""
    return [
        {
            "key": item.key,
            "tool": item.tool,
            "subject": item.subject,
            "calls": item.calls,
            "tokens": item.tokens,
            "wasted": item.wasted,
        }
        for item in items
    ]
