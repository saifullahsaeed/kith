"""The tool registry: every tool complete, callable, and reachable.

Tools used to be two parallel lists — 51 schema declarations in one place, 51
handlers in another — with nothing checking they matched. A schema with no handler
was an error only the model discovered; a handler with no schema was dead code.
Co-locating them makes that impossible by construction, and these tests hold the
line on the parts construction cannot enforce: that the schemas are well formed,
that every declared argument is real, and that dispatch behaves when it is asked
for something that does not exist.

Deliberately no test asserting a specific tool count. That would fail every time
someone adds a tool, which trains people to update the number without reading why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith import tools
from kith.tools import registry
from kith.tools.aliases import ALIASES

ALL = registry.all_tools()


def test_there_are_tools() -> None:
    assert ALL, "no tools registered — the package imports are the registration"


@pytest.mark.parametrize("name", sorted(ALL))
def test_schema_is_well_formed(name: str) -> None:
    schema = ALL[name].schema()
    assert schema["type"] == "function"
    function = schema["function"]
    assert function["name"] == name
    assert function["description"].strip(), f"{name} has no description"
    parameters = function["parameters"]
    assert parameters["type"] == "object"
    assert isinstance(parameters["properties"], dict)
    assert isinstance(parameters["required"], list)


@pytest.mark.parametrize("name", sorted(ALL))
def test_required_arguments_are_declared(name: str) -> None:
    """A required argument missing from properties is invisible to the model — it
    would be told the call is invalid without being told what to send."""
    entry = ALL[name]
    undeclared = set(entry.required) - set(entry.properties)
    assert not undeclared, f"{name} requires {sorted(undeclared)} but does not declare them"


@pytest.mark.parametrize("name", sorted(ALL))
def test_every_property_has_a_type(name: str) -> None:
    for argument, shape in ALL[name].properties.items():
        assert isinstance(shape, dict), f"{name}.{argument} is not a schema object"
        assert "type" in shape or "enum" in shape, f"{name}.{argument} has neither type nor enum"


@pytest.mark.parametrize("name", sorted(ALL))
def test_handler_takes_path_and_args(name: str) -> None:
    """Uniform signature — dispatch passes positionally, so it is not optional."""
    import inspect

    parameters = list(inspect.signature(ALL[name].run).parameters)
    assert parameters[:2] == ["path", "args"], f"{name} handler takes {parameters}"


def test_registering_a_duplicate_is_refused() -> None:
    """Last-wins would silently change what Kith can do, with nothing to show it."""
    existing = next(iter(ALL))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ALL[existing])


def test_scoping_returns_only_what_was_asked_for() -> None:
    """Mode scoping is a token saving on every round, so it has to actually scope."""
    wanted = {"journal", "list_tasks"}
    scoped = tools.tool_schemas(None, only=wanted)
    assert {s["function"]["name"] for s in scoped} == wanted


def test_scoping_ignores_names_that_do_not_exist() -> None:
    scoped = tools.tool_schemas(None, only={"journal", "nonexistent_tool"})
    assert {s["function"]["name"] for s in scoped} == {"journal"}


def test_unknown_tool_reports_rather_than_raises(db: Path) -> None:
    """A bad tool name must reach the model as a result it can act on."""
    result = tools.run_tool("definitely_not_a_tool", {}, db)
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


def test_unknown_tool_suggests_the_real_one(db: Path) -> None:
    """He invents names; a bare refusal teaches him nothing and he guesses again."""
    assert "shell" in tools.run_tool("run_command", {}, db)["error"]
    assert "update_task" in tools.run_tool("mark_task_as_doing", {}, db)["error"]


def test_a_typo_is_matched_by_spelling(db: Path) -> None:
    assert "journal" in tools.run_tool("jurnal", {}, db)["error"]


def test_an_unguessable_name_still_gets_direction(db: Path) -> None:
    error = tools.run_tool("zzzz_qqqq_xxxx", {}, db)["error"]
    assert "tools you were given" in error


@pytest.mark.parametrize("alias,real", sorted(ALIASES.items()))
def test_every_alias_points_at_a_real_tool(alias: str, real: str) -> None:
    """An alias to a renamed or deleted tool would send him somewhere that isn't there."""
    assert real in ALL, f"alias {alias!r} points at {real!r}, which is not registered"
    assert alias not in ALL, f"alias {alias!r} is also a real tool name"


def test_a_failing_tool_is_reported_not_raised(db: Path) -> None:
    """The stream must survive a tool blowing up — the model gets told instead."""
    result = tools.run_tool("view_task", {"id": 10**9}, db)
    assert isinstance(result, dict)
    assert "ok" in result
