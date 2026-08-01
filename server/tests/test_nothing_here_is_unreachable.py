"""Code that nothing can reach, checked by machine instead of by noticing.

Three separate incidents in this codebase share one shape, and none of them failed a test,
tripped the linter, or stopped the server booting:

* `order_milestones` and `unlink_milestones` were in no allow-list, so the persona and a
  skill both told him to call a tool he could not call. It cost a real project — four
  milestones, no edges, and he worked the last one first. `link_folder` had the identical
  bug one commit later, and asking the wider question found thirteen more.
* `_give_up` kept calling a repository that had been deleted. Everything passed, because
  nothing invoked it.
* `embeddings.reembed` — "refresh a memory's vector after its content changed" — was never
  called by anything, so editing a memory left recall matching the words you deleted.

What they have in common is that the broken thing is not executed, so execution proves
nothing about it. These assertions are the substitute.
"""

from __future__ import annotations

import ast
import collections
import pathlib
import re

import pytest

import kith.tools  # noqa: F401  — importing registers every tool
from kith.autonomy.toolsets import _ALLOW
from kith.tools.registry import all_tools

SERVER = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = SERVER / "kith"


class TestEveryToolCanBeReached:
    def test_no_tool_is_missing_from_every_allow_list(self):
        registered = set(all_tools())
        reachable: set[str] = set()
        for allowed in _ALLOW.values():
            reachable |= registered if allowed is None else set(allowed)
        stranded = sorted(registered - reachable)
        assert not stranded, (
            f"{len(stranded)} tool(s) exist but no tick mode may call them: {stranded}. "
            "He will be told about them and refused when he tries."
        )

    def test_no_allow_list_names_a_tool_that_does_not_exist(self):
        registered = set(all_tools())
        for mode, allowed in _ALLOW.items():
            if allowed is None:
                continue
            ghosts = sorted(set(allowed) - registered)
            assert not ghosts, f"mode {mode!r} permits tools that were removed: {ghosts}"

    def test_every_allow_list_belongs_to_a_mode_the_runner_can_produce(self):
        """An allow-list for a mode nothing emits is indistinguishable from one that works.

        `reflect`, `consolidate` and `curious` sat here for a day after the scheduled inner
        life was deleted, describing what he may do in states he could no longer enter.
        """
        source = (PACKAGE / "autonomy" / "runner.py").read_text(encoding="utf-8")
        emitted = set(re.findall(r'mode, self\._current = "(\w+)"', source))
        assert emitted, "could not find the mode assignments — this test needs rewriting"
        orphans = sorted(set(_ALLOW) - emitted)
        assert not orphans, f"allow-lists for modes the runner never enters: {orphans}"


class TestNothingIsDefinedAndForgotten:
    """Undecorated functions that nothing anywhere refers to.

    Decorated ones are excluded: a Flask route or an event hook is called by its framework
    and looks unreferenced to a grep. Anything with a leading underscore *and* no reference
    is the dangerous case, because it is private by intent — if nothing in the package uses
    it, nothing can.
    """

    @staticmethod
    def _unreferenced() -> list[tuple[str, str, int]]:
        defined: list[tuple[str, str, int]] = []
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.decorator_list:
                    defined.append((node.name, str(path.relative_to(SERVER)), node.lineno))

        blob = "\n".join(
            p.read_text(encoding="utf-8")
            for p in list(PACKAGE.rglob("*.py")) + list((SERVER / "tests").rglob("*.py"))
        )
        uses = collections.Counter(re.findall(r"\b\w+\b", blob))
        seen = collections.Counter(name for name, _, _ in defined)
        return [
            (name, path, line)
            for name, path, line in defined
            if not name.startswith("__") and seen[name] == 1 and uses[name] <= 1
        ]

    def test_no_private_helper_is_stranded(self):
        stranded = [f"{p}:{line} {name}" for name, p, line in self._unreferenced() if name.startswith("_")]
        assert not stranded, (
            "private functions nothing refers to — either wire them up or delete them; "
            f"a stranded one cannot be proven correct by running the suite: {stranded}"
        )

    def test_and_no_public_one_either(self):
        stranded = [
            f"{p}:{line} {name}" for name, p, line in self._unreferenced() if not name.startswith("_")
        ]
        assert not stranded, (
            "functions with no caller anywhere in the package or the tests. `reembed` sat "
            f"like this while the bug it was written to fix stayed live: {stranded}"
        )


class TestTheRegistryAgreesWithItself:
    def test_every_tool_has_a_handler_and_a_description(self):
        for name, tool in all_tools().items():
            assert callable(tool.run), f"{name} has no handler"
            assert tool.description.strip(), f"{name} has no description for the model to read"

    def test_every_required_argument_is_a_declared_property(self):
        """A required name with no schema entry is a tool he can never call correctly."""
        for name, tool in all_tools().items():
            missing = sorted(set(tool.required) - set(tool.properties))
            assert not missing, f"{name} requires arguments it does not declare: {missing}"

    @pytest.mark.parametrize("name", sorted(all_tools()))
    def test_the_schema_a_provider_sees_is_well_formed(self, name):
        schema = all_tools()[name].schema()
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] == name
        assert isinstance(function["parameters"]["properties"], dict)
