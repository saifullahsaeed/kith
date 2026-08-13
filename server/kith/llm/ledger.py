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
from dataclasses import dataclass
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

    system_chars = persona_chars = live_chars = 0
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
