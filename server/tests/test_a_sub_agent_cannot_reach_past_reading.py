"""A sub-agent may look at anything and change nothing, and that has to be true by construction.

The boundary is the only reason delegating is safe. A worker searches in a scratchpad nobody
reads, so nothing it does is visible in the conversation — which is exactly why it must not be
able to do anything that outlives the scratchpad. A `write_file` from in there is a change to
the codebase that the agent holding the plan never saw happen.

Two halves, and the second is the one that rots. Saying "read-only" in a tool description is
free; keeping the list right in six months when someone adds a tool is not. So the
classification is exhaustive — every registered tool is either given or withheld, by name —
and this fails the moment one is neither. The failure is the point: the person adding a tool
is the only person who knows which side it belongs on, and they are asked at the only moment
they know.
"""

from __future__ import annotations

from pathlib import Path

from kith import tools
from kith.tools import delegation


class TestEveryToolIsClassified:
    def test_nothing_is_left_unclassified(self):
        """Add a tool, and this asks you which side of the wall it is on."""
        registry = set(tools.names())
        missing = sorted(registry - delegation.GIVEN - delegation.WITHHELD)
        assert not missing, (
            f"{missing} are registered but neither given to a sub-agent nor withheld from one. "
            "Add each to GIVEN or WITHHELD in tools/delegation.py — a tool that can change "
            "anything belongs in WITHHELD."
        )

    def test_nothing_classified_has_since_disappeared(self):
        """The other direction: a renamed or deleted tool leaves a name here that means nothing."""
        registry = set(tools.names())
        stale = sorted((delegation.GIVEN | delegation.WITHHELD) - registry)
        assert not stale, f"{stale} are classified in tools/delegation.py but no longer registered"

    def test_the_two_lists_do_not_overlap(self):
        assert not (delegation.GIVEN & delegation.WITHHELD)


class TestTheWallHolds:
    def test_no_tool_that_changes_anything_is_given(self):
        """Named individually rather than derived, so this test cannot drift with the code it checks."""
        for name in (
            "write_file",
            "edit_file",
            "edit_files",
            "delete_file",
            "rename_symbol",
            "commit",
            "publish",
            "shell",
            "start_process",
            "run_tests",
            "add_task",
            "create_project",
            "remember",
        ):
            assert name not in delegation.GIVEN, f"a sub-agent must not be able to {name}"

    def test_a_sub_agent_cannot_spawn_another(self):
        """The one call that turns a worker into an unbounded tree of them."""
        assert "delegate_subtask" not in delegation.GIVEN

    def test_a_sub_agent_cannot_ask_anybody_anything(self):
        """`ask` holds a turn open until someone answers, and nobody is watching a scratchpad —
        the question would be put to an empty room while the main turn waits on a tool call
        that never comes back."""
        assert "ask" not in delegation.GIVEN
        assert "reach_out" not in delegation.GIVEN


class TestTheWallIsEnforcedAndNotMerelyDeclared:
    def test_naming_a_withheld_tool_anyway_is_refused(self, db: Path):
        """Being shown a smaller list is not the same as having one.

        This is the failure mode the `allow` gate in `run_tool` was added for: every
        allow-list in the codebase used to reach only `tool_schemas(only=...)`, which decides
        what the model is *shown*, while execution resolved any name against the whole
        registry. A model names a tool for all sorts of reasons — the persona mentions it, a
        skill it just read names it — and before the gate, naming it was enough.
        """
        answer = tools.run_tool(
            "write_file",
            {"path": "escaped.txt", "content": "hello"},
            db,
            allow=set(delegation.GIVEN),
        )
        assert answer["ok"] is False
        assert "not available" in answer["error"]

    def test_a_given_tool_still_runs_under_the_same_gate(self, db: Path):
        """The gate has to be a gate and not a wall: the read tools must still work."""
        answer = tools.run_tool("list_projects", {}, db, allow=set(delegation.GIVEN))
        assert answer["ok"] is True
