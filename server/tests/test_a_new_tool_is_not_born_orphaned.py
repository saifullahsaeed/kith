"""A tool nobody points at is a tool he will not reach for, however good its own description is.

Adding a tool updates that tool's description and nothing else. So every new tool starts
invisible at the moment it applies — and the moment it applies is always while he is holding a
*different* tool. `send_builder` shipped and went unused for four runs; `plan_work` shipped and
was pointed at by nothing at all. Neither was a wording problem in its own text, which was fine.

This is the same shape as `test_a_skill_never_names_a_retired_tool`, one layer down: guidance
that is correct in isolation and unreachable in practice. Both are cheap to check and impossible
to keep right by hand.
"""

from __future__ import annotations

import re

import pytest

from kith import tools

#: Tools whose whole value is that something *else* suggests them. A person asks for a shell
#: command directly; nobody asks for an errand by name, so if `shell` does not mention one, one
#: is never sent. Listed rather than inferred, because "would he think of this unprompted" is a
#: judgement and the list is where that judgement is written down.
MUST_BE_SUGGESTED = {
    "delegate_subtask": "the tools whose output it replaces",
    "send_builder": "the tools you hold when you notice repetition",
    "follow_up": "the tools that produce the report it follows up",
    "plan_work": "the board tools it replaces a dozen calls of",
}


def _descriptions() -> dict[str, str]:
    out = {}
    for schema in tools.tool_schemas():
        fn = schema.get("function", schema)
        out[fn["name"]] = fn.get("description", "")
    return out


@pytest.mark.parametrize("name", sorted(MUST_BE_SUGGESTED))
def test_something_else_points_at_it(name: str):
    said = _descriptions()
    who = sorted(other for other, text in said.items() if other != name and re.search(rf"\b{name}\b", text))
    assert who, (
        f"nothing points at `{name}`, so it is only ever used when asked for by name. "
        f"Name it in {MUST_BE_SUGGESTED[name]}."
    )


def test_no_tool_description_names_a_retired_tool():
    """The same rot as in skills, and it happened here too.

    `delegate_subtask` told him "read_file and outline take a list of paths" for as long as
    `outline` had been `repo_map`. Matched in the same sentence as a live tool name, because
    several retired names are ordinary English — `run`, `search`, `log`, `history` — and a
    bare-word match flags every description that contains a verb.
    """
    from kith.tools.aliases import RETIRED

    live = set(tools.names())
    wrong = []
    for name, text in _descriptions().items():
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            gone = {w for w in RETIRED if re.search(rf"\b{w}\b", sentence)}
            if gone and any(re.search(rf"\b{t}\b", sentence) for t in live):
                wrong += [f"{name} says `{w}` (now `{RETIRED[w].now}`)" for w in sorted(gone)]
    # `read_skill` legitimately says "references" about a skill's own files, which is the word
    # and not the retired tool. Exempted by name rather than by loosening the rule.
    wrong = [w for w in wrong if not w.startswith("read_skill says `references`")]
    assert not wrong, "a tool description names a tool that no longer goes by that name:\n  " + "\n  ".join(
        wrong
    )


def test_the_whole_surface_stays_affordable():
    """Every description is re-sent on every round of every turn, so this is the one budget paid
    per round rather than per turn. Not a target — a tripwire, so that growth is a decision."""
    said = _descriptions()
    total = sum(len(text) for text in said.values())
    assert total < 32_000, (
        f"{total:,} chars of tool description (~{total // 4:,} tokens) on every round. "
        "Either something got long, or a tool was added without trimming one."
    )
