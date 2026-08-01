"""Nothing tells him to use a tool he cannot call.

`order_milestones` was registered, described in the persona as *the* way to say what has to
happen first, named again in the `running-a-project` skill — and absent from every allow-list.
So it was never callable, in any mode, ever.

The failure mode is what makes this worth a test. A missing tool is normally loud: the call
fails and he is told. An *unoffered* tool is silent — he never reaches for it, does the rest of
the job, and the result looks complete. It cost a real project: four milestones in an obvious
chain, no edges between them, and all four offered as ready to work at once, which is exactly
the "package the release before the thing is built" failure the roadmap exists to prevent.

Checking against the registry is not enough and that is the trap I fell into: the registry is
every tool that exists, the allow-list is every tool he is given. Instructions have to be
checked against the second.
"""

from __future__ import annotations

import re
from pathlib import Path

from kith import settings
from kith.autonomy.toolsets import _ALLOW
from kith.services import skills
from kith.tools import registry

#: Tool names as they appear in prose — `like_this` in backticks, or bare `like_this(...)`.
_MENTION = re.compile(r"`([a-z][a-z0-9_]{3,})`|\b([a-z][a-z0-9_]{3,})\(")


def _registered() -> set[str]:
    return {schema["function"]["name"] for schema in registry.schemas()}


def _mentioned(text: str) -> set[str]:
    """Tool names a document names, filtered to ones that really are tools."""
    found = {a or b for a, b in _MENTION.findall(text)}
    return found & _registered()


def _work_modes() -> list[str]:
    """The modes in which he does work someone asked for, rather than inner life.

    Reflection and consolidation legitimately have narrow sets — a reflective tick should not
    be filing tasks — so the guarantee is about the working modes.
    """
    return [mode for mode in ("start", "reply") if mode in _ALLOW]


def _documents() -> dict[str, str]:
    docs = {}
    persona = settings.SERVER_ROOT / "persona"
    for path in sorted(persona.glob("*.md")):
        docs[f"persona/{path.name}"] = path.read_text()
    directives = settings.SERVER_ROOT / "kith" / "autonomy" / "directives"
    for path in sorted(directives.glob("*.md")):
        docs[f"directives/{path.name}"] = path.read_text()
    for skill in skills.installed():
        body = Path(skill.path) / "SKILL.md" if not str(skill.path).endswith(".md") else Path(skill.path)
        if body.is_file():
            docs[f"skill/{skill.name}"] = body.read_text()
    return docs


def test_every_tool_the_persona_and_directives_name_is_callable():
    broken: list[str] = []
    for name, text in _documents().items():
        if not name.startswith(("persona/", "directives/")):
            continue
        for tool in sorted(_mentioned(text)):
            unavailable = [mode for mode in _work_modes() if tool not in _ALLOW[mode]]
            if unavailable:
                broken.append(f"{name} tells him to use `{tool}`, not offered in {unavailable}")
    assert not broken, "instructions naming tools he cannot call:\n  " + "\n  ".join(broken)


def test_every_tool_a_skill_names_is_callable():
    broken: list[str] = []
    for name, text in _documents().items():
        if not name.startswith("skill/"):
            continue
        for tool in sorted(_mentioned(text)):
            unavailable = [mode for mode in _work_modes() if tool not in _ALLOW[mode]]
            if unavailable:
                broken.append(f"{name} tells him to use `{tool}`, not offered in {unavailable}")
    assert not broken, "skills naming tools he cannot call:\n  " + "\n  ".join(broken)


def test_the_roadmap_can_actually_be_ordered():
    """The specific one, named, because it is the whole reason this file exists.

    A roadmap he can create but not sequence is worse than no roadmap: it looks like a plan
    and behaves like a list.
    """
    for mode in _work_modes():
        assert "order_milestones" in _ALLOW[mode], f"cannot order milestones in {mode}"
        assert "unlink_milestones" in _ALLOW[mode], f"cannot fix a wrong order in {mode}"


def test_a_tool_description_never_points_at_a_tool_he_lacks():
    """The one that actually caused it, and the surface I first forgot to check.

    `add_milestone` says: "If you are laying out a fresh roadmap and do not have the ids yet,
    add them all first and then call order_milestones with the ids in order." He was laying out
    a fresh roadmap. He did not have the ids. He added them all first. And then the tool he had
    just been told to call was not among the ones he had been given.

    So the instruction did not merely fail to help — it walked him into the failure, and left a
    roadmap that looked finished. A tool description is an instruction with the shortest
    possible distance to being acted on, which makes it the worst place for a dangling pointer.
    """
    broken = []
    for schema in registry.schemas():
        fn = schema["function"]
        text = fn.get("description") or ""
        for param in (fn.get("parameters") or {}).get("properties", {}).values():
            text += " " + str(param.get("description") or "")
        for tool in sorted(_mentioned(text) - {fn["name"]}):
            unavailable = [mode for mode in _work_modes() if tool not in _ALLOW[mode]]
            if unavailable:
                broken.append(f"`{fn['name']}` points at `{tool}`, not offered in {unavailable}")
    assert not broken, "tool descriptions pointing at tools he cannot call:\n  " + "\n  ".join(broken)


def test_no_allow_list_names_a_tool_that_does_not_exist():
    registered = _registered()
    stale = {mode: sorted(set(allowed) - registered) for mode, allowed in _ALLOW.items()}
    stale = {mode: names for mode, names in stale.items() if names}
    # A name in the list that no longer exists is a silent no-op that hides a rename.
    assert not stale, f"allow-lists naming tools that do not exist: {stale}"


#: Tools deliberately not offered in any unattended mode. Being on this list is a decision;
#: being absent from it and from every allow-list is an accident, and the difference is the
#: whole point of the test below.
WITHHELD_ON_PURPOSE: set[str] = set()


def test_every_registered_tool_is_reachable_somewhere():
    """A tool in no allow-list is dead code that looks like a capability.

    This is the third time in two days: `order_milestones` (a roadmap he could create and
    never order), then `link_folder` one commit later, and then eleven more found by asking
    the question directly — he could fire a due reminder and not cancel it, could use a tool
    he had built and never build one, could read his own memory and not add to it.

    The earlier tests here only cover tools something *mentions*. That is the narrower
    question, and it let all thirteen through: nothing named them, so nothing noticed they
    were unreachable. This asks the wider one, which is the one that actually matters —
    registered means offered somewhere, or explicitly withheld and written down.
    """
    registered = _registered()
    reachable = set().union(*_ALLOW.values())
    dead = sorted(registered - reachable - WITHHELD_ON_PURPOSE)
    assert not dead, (
        "these tools exist and are offered in no mode, so he can never call them "
        f"unattended — add them to a toolset, or to WITHHELD_ON_PURPOSE with a reason: {dead}"
    )


def test_the_withheld_list_does_not_name_tools_that_are_gone():
    # Otherwise it becomes a place stale names go to be forgotten, and the next real
    # omission hides among them.
    stale = sorted(WITHHELD_ON_PURPOSE - _registered())
    assert not stale, f"WITHHELD_ON_PURPOSE names tools that no longer exist: {stale}"


def test_a_directive_never_asks_for_something_the_toolset_forbids():
    """Naming a real tool is not enough — it has to be reachable when the instruction applies.

    The chat directive says to file "a project and its first milestone's tasks". But
    `create_project` is a delegation tool, and tripping that guardrail narrows the toolset to
    `_LANDING_TOOLS | _PLANNING_TOOLS` for the rest of the turn — which did not include
    `add_task`. So the one instruction the directive gives about setting work up was
    impossible to carry out from the moment he followed the first half of it.

    It went unseen because the allow-lists were advisory: he called `add_task` anyway and it
    ran. Enforcing them turned a latent contradiction into a wall, and a real run found it
    within seven rounds — he created the project, added the milestone, then said "the
    task-creation operation wasn't available in this session, so I couldn't honestly file the
    milestone's tasks", after retrying `add_milestone` three times looking for a way through.
    """
    from kith.api.routes.chat import CHAT_DIRECTIVE
    from kith.services.agent_loop import _DELEGATION_TOOLS, _LANDING_TOOLS, _PLANNING_TOOLS

    after_delegating = _LANDING_TOOLS | _PLANNING_TOOLS
    # Everything the guardrail can be tripped by, plus what the directive then asks for.
    for tool in _DELEGATION_TOOLS:
        assert tool in after_delegating or tool == "create_project", (
            f"{tool} trips the guardrail and cannot be repeated, which is intended"
        )
    assert "add_task" in after_delegating, (
        "the directive asks for a project AND its tasks; filing them must survive the "
        "guardrail that creating the project trips"
    )
    assert "add_milestone" in after_delegating
    # And the directive really is the thing making that promise, so this fails loudly if the
    # wording changes rather than passing on a rule nobody states any more.
    assert "milestone's tasks" in CHAT_DIRECTIVE
