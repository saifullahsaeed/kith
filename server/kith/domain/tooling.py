"""What the loop needs from the tool layer, and nothing else.

Two callables. `kith/tools/` is an adapter — it parses arguments, delegates, and serialises,
for the model rather than for HTTP — and nothing may import an adapter, which is why
`services/agent_loop.py` doing `from kith import tools` was the last arrow pointing the wrong
way in the tree.

The loop does not need the registry. It needs to know what to offer this round and how to run
one thing, and both are answers somebody else can supply. So the caller supplies them:
`tools.host(agent_db_path)` binds the path and the language-server question once, and
`api/routes/chat.py` — an adapter, allowed to import another — hands the result down.

**Deliberately not a Protocol, a registry or a plugin point.** It is a pair of functions in a
frozen box, because that is all the loop reads. The thing it makes possible is worth naming
though: a nested turn with a *narrower* host is how a subagent gets a smaller toolset without
the loop knowing what a subagent is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolHost:
    """The two questions a turn asks of the tool layer."""

    #: What to declare this round. `only` narrows to a phase's set — the landing reserve is
    #: the one caller that uses it — and `mcp` is the turn's frozen server snapshot, passed
    #: rather than fetched so the tools block stays byte-identical across the turn and the
    #: prompt cache survives. See `services/mcp/manager.snapshot`.
    schemas: Callable[..., list[dict[str, Any]]]

    #: Run one call. `allow` is the set permitted in this part of the turn, and it is enforced
    #: where execution happens rather than where declarations are built — a model that names a
    #: tool it was not offered still gets refused.
    run: Callable[..., dict[str, Any]]

    #: Which of the declared names came from an MCP server, and which he built himself.
    #:
    #: Provenance, for the ledger: once every schema is in one tools block, three costs that
    #: are worth telling apart look identical. It rides here because this is the object that
    #: knows — the alternative was the turn re-deriving both from a snapshot it also had to
    #: hold, which meant two places that could disagree about which tools exist.
    mcp_names: frozenset[str] = frozenset()


def many(args: dict, plural: str, singular: str) -> list[str]:
    """The values of a list-shaped argument, however they actually arrived.

    Three spellings are accepted and the reason is not politeness, it is that all three occur:

    * ``{"paths": ["a", "b"]}`` — the declared shape, what the schema asks for.
    * ``{"paths": "a"}`` — a model sending a bare string into an array parameter, which they do.
    * ``{"path": "a"}`` — the singular these tools used to take. Old tool calls are replayed
      out of the transcript on every resumed turn (`conversations.full_messages`), so the
      previous spelling has to keep working forever or reopening a week-old conversation fails
      on arguments that were valid when they were made.

    **Why plural at all.** Measured across 11,291 rounds of real work: 78-96% of every round
    that called a tool called exactly *one*, on every model tried. `persona/35-how-you-spend-a-
    round.md` was written to fix that on 2026-08-13 and did not move it — 77% single-call
    before it shipped, 88% after. Asking did not work, so the shape changed instead: batching
    is not a discipline you have to remember when the parameter is already a list.
    """
    found = args.get(plural)
    if found is None:
        found = args.get(singular)
    if found is None:
        return []
    if isinstance(found, str):
        return [found] if found.strip() else []
    if isinstance(found, list):
        return [str(one) for one in found if str(one).strip()]
    return [str(found)]
