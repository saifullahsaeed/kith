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

from kith.services import skills as registry
from kith.tools.params import STR
from kith.tools.registry import tool


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
    return registry.read(str(args.get("name") or ""))
