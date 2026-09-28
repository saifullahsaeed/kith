"""A sub-agent may look at anything and change nothing, and that has to be true by construction.

The boundary is the only reason delegating is safe. A worker searches in a scratchpad nobody
reads, so nothing it does is visible in the conversation — which is exactly why it must not be
able to do anything that outlives the scratchpad. A `write_file` from in there is a change to
the codebase that the agent holding the plan never saw happen.

Three lists now, not two, and the third is the interesting one. A *builder* may change files —
in its own `git worktree`, which is what makes that safe — so `WRITING` names the four tools
that separate it from a scout. Everything in `WITHHELD` is still withheld from both: one wall
with a small door in it, rather than a wall per role.

The half that rots is the classification. Saying "read-only" in a tool description is free;
keeping the list right in six months when someone adds a tool is not. So it is exhaustive —
every registered tool is given, writing, or withheld, by name — and this fails the moment one
is none of the three. The failure is the point: the person adding a tool is the only person
who knows which side it belongs on, and they are asked at the only moment they know.
"""

from __future__ import annotations

from pathlib import Path

from kith import tools
from kith.tools import workers


class TestEveryToolIsClassified:
    def test_nothing_is_left_unclassified(self):
        """Add a tool, and this asks you which side of the wall it is on."""
        registry = set(tools.names())
        missing = sorted(registry - workers.GIVEN - workers.WRITING - workers.CHECKING - workers.WITHHELD)
        assert not missing, (
            f"{missing} are registered but classified nowhere. Add each to GIVEN, WRITING, CHECKING "
            "or WITHHELD in tools/workers.py — a tool that edits a file in the worktree belongs "
            "in WRITING, one that runs the project's own checks inside the copy's boundary in "
            "CHECKING, and anything with an effect outside the worktree belongs in WITHHELD."
        )

    def test_nothing_classified_has_since_disappeared(self):
        """The other direction: a renamed or deleted tool leaves a name here that means nothing."""
        registry = set(tools.names())
        stale = sorted((workers.GIVEN | workers.WRITING | workers.CHECKING | workers.WITHHELD) - registry)
        assert not stale, f"{stale} are classified in tools/workers.py but no longer registered"

    def test_the_lists_do_not_overlap(self):
        named = (workers.GIVEN, workers.WRITING, workers.CHECKING, workers.WITHHELD)
        for i, one in enumerate(named):
            for other in named[i + 1 :]:
                assert not (one & other)


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
            assert name not in workers.GIVEN, f"a sub-agent must not be able to {name}"

    def test_an_agent_cannot_start_a_workflow(self):
        """The one call that would turn a workflow's agent into an unbounded tree of them."""
        assert "run_workflow" in workers.WITHHELD
        assert "run_workflow" not in workers.GIVEN
        assert "run_workflow" not in workers.WRITING

    def test_a_sub_agent_cannot_ask_anybody_anything(self):
        """`ask` holds a turn open until someone answers, and nobody is watching a scratchpad —
        the question would be put to an empty room while the main turn waits on a tool call
        that never comes back."""
        assert "ask" not in workers.GIVEN
        assert "reach_out" not in workers.GIVEN


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
            allow=set(workers.GIVEN),
        )
        assert answer["ok"] is False
        assert "not available" in answer["error"]

    def test_a_given_tool_still_runs_under_the_same_gate(self, db: Path):
        """The gate has to be a gate and not a wall: the read tools must still work."""
        answer = tools.run_tool("list_projects", {}, db, allow=set(workers.GIVEN))
        assert answer["ok"] is True


class TestABuilderIsAScoutPlusFourTools:
    """The role difference, asserted as a difference rather than as two lists.

    Written this way on purpose. Two independently-maintained sets would drift — a read tool
    added to one and not the other is a builder that cannot see what a scout can, which shows
    up as a builder inventing a file it was not allowed to look for.
    """

    def test_a_builder_gets_everything_a_scout_gets(self):
        builder = workers.ROLES["builder"]["given"]()
        assert builder >= workers.GIVEN

    def test_the_only_difference_is_writing_and_checking(self):
        builder = workers.ROLES["builder"]["given"]()
        assert builder - workers.GIVEN == set(workers.WRITING) | set(workers.CHECKING)

    def test_a_verifier_checks_but_cannot_write(self):
        verifier = workers.ROLES["verifier"]["given"]()
        assert verifier - workers.GIVEN == set(workers.CHECKING)
        assert not (verifier & workers.WRITING)

    def test_only_roles_that_work_in_a_copy_may_run_the_tests(self):
        """`run_tests` from a role with no copy would run in the real tree, unconfined."""
        for role, shape in workers.ROLES.items():
            if workers.CHECKING & shape["given"]():
                assert shape["copy"], f"a {role} may run tests but is not given a copy to run them in"

    def test_a_scout_still_cannot_write(self):
        scout = workers.ROLES["scout"]["given"]()
        assert not (scout & workers.WRITING)

    def test_no_role_reaches_past_its_worktree(self):
        """Nothing with an effect outside the private copy, for either role.

        Named individually rather than derived from WITHHELD, so this cannot drift with the
        code it is checking. A builder that could `commit` would be committing in a worktree
        nobody will ever look at; one that could `shell` would have side effects, which is
        what stops two of them sharing a round.
        """
        for role in workers.ROLES:
            given = workers.ROLES[role]["given"]()
            for name in ("commit", "publish", "shell", "start_process", "ask", "remember"):
                assert name not in given, f"a {role} must not be able to {name}"
