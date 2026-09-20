"""The persona and the shipped skills may not name a tool that has been retired.

`test_the_persona_only_names_skills_that_are_in_the_box` has guarded the skills half of this
for a while. Nothing guarded the tools half, and it went wrong in the worst available place.

`persona/30-how-you-read.md` is the fragment that decides how far a long job gets — "find
first, then read", the whole context discipline. It said: "With code, `outline` gives you its
shape". `outline` was retired into `repo_map` at some point after that sentence was written.

It is not a hard failure, because `aliases.RETIRED` redirects the call. It is worse than one:
the model is told to reach for a name that is not in its own tool list, so the instruction and
the schema block disagree, and the live name — `repo_map` — appears nowhere in any prose at
all. Measured over nineteen days: `outline` called once, `repo_map` nine times, `find_symbol`
once, against `grep` 177 and `read_file` 1,584. The cheap shape-first path the persona exists
to recommend is the one nobody takes.

Only backticked tokens are checked. The prose is full of shell (`wc -l`) and paths
(`packages/api`), and a bare word like "publish" or "changes" is ordinary English — backticks
are how this codebase already marks a name as a name.
"""

from __future__ import annotations

import re

import kith.tools  # noqa: F401  — importing registers every tool
from kith import settings
from kith.tools import aliases
from kith.tools.registry import all_tools

#: A backticked token that could be a tool name: snake_case, no spaces, no slashes or dots.
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)`")


def _prose() -> dict[str, str]:
    """Every piece of text that ships inside the prompt, by where it came from."""
    out: dict[str, str] = {}
    persona = settings.SERVER_ROOT / "persona"
    if persona.is_dir():
        for one in sorted(persona.glob("*.md")):
            out[f"persona/{one.name}"] = one.read_text(errors="replace")
    bundled = settings.BUNDLED_SKILLS_DIR
    if bundled.is_dir():
        for one in sorted(bundled.rglob("SKILL.md")):
            out[f"skills/{one.parent.name}"] = one.read_text(errors="replace")
    return out


def test_no_shipped_prose_names_a_retired_tool():
    """The precise, no-false-positive check: a backticked name that `aliases.RETIRED` knows
    about is a name that used to be a tool and is not one now."""
    wrong: list[str] = []
    for where, text in _prose().items():
        for token in set(_BACKTICKED.findall(text)):
            gone = aliases.RETIRED.get(token)
            if gone is not None:
                wrong.append(f"{where} says `{token}`, which is now `{gone.now}`")
    assert not wrong, "shipped prose names retired tools:\n  " + "\n  ".join(sorted(wrong))


def test_the_prose_that_teaches_reading_names_the_tool_that_does_it():
    """`repo_map` is the tool that answers "what shape is this code" without reading it, and
    it is the direct answer to the measured problem that 54% of every read is of a file already
    read. A fragment about how to read that does not name it is not doing its job."""
    how = _prose().get("persona/30-how-you-read.md", "")
    assert "`repo_map`" in how


def test_every_backticked_tool_name_is_one_we_actually_have():
    """A softer sweep than the first, and it only fires on names the registry once knew. A
    backticked word that was never a tool (a shell command, a field name) is ignored — this is
    about drift, not vocabulary."""
    known = set(all_tools()) | set(aliases.RETIRED)
    stale = []
    for where, text in _prose().items():
        for token in set(_BACKTICKED.findall(text)):
            if token in aliases.RETIRED and token not in all_tools():
                stale.append(f"{where}: `{token}`")
    assert not stale, "\n".join(stale)
    assert known  # the registry loaded at all
