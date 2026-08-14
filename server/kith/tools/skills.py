"""Reading a skill's instructions.

The system prompt already lists what is installed — name and what it is for, nothing more.
This is how he gets the rest, and it is a tool rather than a chunk of prompt for the reason
the whole Agent Skills format exists: instructions for a kind of work he is not currently
doing are pure cost. Ten installed skills add about a thousand cached tokens to every
request; ten installed skills *inlined* would add forty thousand live ones, and make him
worse at the task by burying the relevant page in thirty-nine others.

There is deliberately no tool for installing one. A skill is executable instructions from
somewhere else, and the standard's own documentation is blunt about the risk: a malicious
skill can direct an agent to run code that has nothing to do with its stated purpose. Kith
can *use* what his person installed; choosing what to trust stays with the person, in the
interface, where there is a folder to look at and a diff to read.
"""

from __future__ import annotations

from pathlib import Path

from kith.kernel import session_context
from kith.services import skills as registry
from kith.tools.params import STR
from kith.tools.registry import tool

#: Distinct skills one turn may open before it is told what that is costing. Three is not a
#: refusal — a job can legitimately need a few — it is the point at which "which skills might
#: apply" has stopped being reconnaissance and started being the work.
_SKILLS_BEFORE_SAYING_SO = 3


@tool(
    "read_skill",
    "Load one installed skill's instructions, by name. The skills listed in your prompt "
    "show only what each one is for; this is how you get the actual steps. Read it BEFORE "
    "starting a task it covers, not after getting stuck — and only when it genuinely "
    "applies, since its instructions take up room you need for the work itself. The reply "
    "also lists the skill's other files (references, scripts) and its folder, so you can "
    "read one with read_file or run one with shell if the instructions tell you to.",
    {"name": {**STR, "description": "The skill's name, exactly as listed in your prompt."}},
    required=("name",),
)
def read_skill(path: Path, args: dict):
    """Load a skill, unless this turn already has it.

    Measured over one project: 78 opens of 11 distinct skills. `frontend-design` eight times,
    `webapp-testing` eight times, `verification-before-completion` seven — and four separate
    turns that opened **eight skills each** before doing any work. At a median 2,171 tokens a
    skill that is about seventeen thousand tokens of instructions loaded speculatively, then
    carried on every remaining round of the turn.

    His own persona already says not to: "reading one speculatively wastes the context you
    would need to do the job." It was advice, and advice loses to the pull of wanting to be
    thorough. This makes it structural — the second read of the same skill in one turn returns
    a sentence instead of the file, because the file is already above and re-reading it buys
    a duplicate.
    """

    name = str(args.get("name") or "").strip()
    notes = session_context.turn_notes()
    already = notes.setdefault("skills_read", set())

    if name in already:
        return {
            "name": name,
            "note": (
                f"You already opened `{name}` in this turn — its instructions are above, "
                "unchanged. Re-reading it would only add a second copy."
            ),
        }

    found = registry.read(name)
    if not isinstance(found, dict) or found.get("error"):
        return found

    already.add(name)
    if len(already) > _SKILLS_BEFORE_SAYING_SO:
        return {
            **found,
            "note": (
                f"That is {len(already)} skills opened in this one turn ({', '.join(sorted(already))}). "
                "Each one's instructions stay with you for every remaining step, so this is a "
                "large part of the room you have left to actually do the work in. Work from "
                "what you have rather than opening more."
            ),
        }
    return found
