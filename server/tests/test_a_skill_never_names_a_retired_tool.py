"""A skill that names a tool which no longer exists is worse than one that says nothing.

A skill is read as instructions and followed literally. When it names a retired tool he calls
it, gets a refusal, and the refusal arrives in the middle of a plan with nothing to suggest the
*instruction* was the problem rather than the situation.

This is not hypothetical and it is not rare. `running-a-project` documented `order_milestones`
and `unlink_milestones` for weeks after both were folded into `update_milestone`, plus
`check_remote` after it became `publish`; `planning-a-task` still recommended `outline` after it
became `repo_map`. Four wrong instructions across two skills, and nothing failed loudly enough
for anyone to notice — a retired name still *resolves*, so the call works and only the guidance
is wrong.

Which is exactly why this is a test and not a convention. The rot is silent, it happens whenever
a tool is renamed by someone not reading the skills, and it is one string comparison to catch.

Scoped to the skills that ship in git. The rest are installed into the data folder at runtime and
are the installer's business, not this repository's — `.audit/stale_tools.py` next to them checks
those on demand.
"""

from __future__ import annotations

import re

import pytest

from kith import tools
from kith.settings import BUNDLED_SKILLS_DIR

#: A word in backticks, or one followed by "(" — how a skill names a tool.
MENTION = re.compile(r"`([a-z][a-z0-9_]{2,40})`|\b([a-z][a-z0-9_]{2,40})\(")


def _shipped():
    if not BUNDLED_SKILLS_DIR.is_dir():
        return []
    return sorted(p for p in BUNDLED_SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())


def _retired() -> dict[str, str]:
    """Both tables, because they fail differently.

    `ALIASES` is a rename that still works and always will — a skill using one is teaching a
    spelling nobody maintains. `RETIRED` is a tool that was *removed*, its name kept only so the
    call returns an explanation instead of "unknown tool" — a skill using one is teaching a tool
    that is gone.
    """
    from kith.tools.aliases import ALIASES, RETIRED

    stale = {name: f"{one.now} (it was retired, not renamed)" for name, one in RETIRED.items()}
    stale.update({name: str(target) for name, target in ALIASES.items()})
    return stale


@pytest.mark.parametrize("skill", _shipped(), ids=lambda p: p.name)
class TestEveryToolASkillNamesStillExists:
    def test_it_names_no_retired_tool(self, skill):
        stale = _retired()
        said = {a or b for a, b in MENTION.findall((skill / "SKILL.md").read_text())}
        wrong = sorted(f"`{w}` -> use `{stale[w]}`" for w in said & set(stale))
        assert not wrong, (
            f"{skill.name} tells him to use a tool that no longer goes by that name:\n  " + "\n  ".join(wrong)
        )

    def test_it_names_no_tool_one_typo_from_a_real_one(self, skill):
        """The other half, and the one a rename does not cause: a name that was never right.

        Strict on purpose — one edit, not two. A looser version flagged `add_page`, `add_slide`
        and `read_excel`, which are python-docx, python-pptx and pandas calls in skills that are
        *about* those libraries. A skill is prose, prose is full of snake_case that is not a
        tool, and the only safe signal is a name one character away from a real one.
        """
        known = set(tools.names())
        stale = _retired()
        said = {a or b for a, b in MENTION.findall((skill / "SKILL.md").read_text())}
        wrong = []
        for word in sorted(said - known - set(stale)):
            for name in known:
                if _one_edit_apart(word, name):
                    wrong.append(f"`{word}` — did you mean `{name}`?")
                    break
        assert not wrong, f"{skill.name} names something that is nearly a tool:\n  " + "\n  ".join(wrong)


def _one_edit_apart(word: str, name: str) -> bool:
    if word == name or abs(len(word) - len(name)) > 1:
        return False
    short, long = sorted((word, name), key=len)
    if len(short) == len(long):
        return sum(a != b for a, b in zip(short, long, strict=True)) == 1
    return any(long[:cut] + long[cut + 1 :] == short for cut in range(len(long)))


class TestTheGuardItself:
    def test_it_would_catch_the_one_that_shipped(self):
        """The regression, pinned. `order_milestones` sat in `running-a-project` for weeks."""
        stale = _retired()
        assert "order_milestones" in stale
        assert "update_milestone" in stale["order_milestones"]

    def test_it_does_not_flag_a_library_call(self):
        """`add_picture` is python-pptx. A guard that cries wolf gets switched off."""
        known = set(tools.names())
        assert not any(_one_edit_apart("add_picture", name) for name in known)
        assert not any(_one_edit_apart("read_excel", name) for name in known)

    def test_a_shipped_skill_was_actually_checked(self):
        """Parametrising over a directory that turns out to be empty is a suite that passes by
        finding nothing. Named separately so that failure is legible."""
        assert _shipped(), f"no shipped skills found under {BUNDLED_SKILLS_DIR}"
