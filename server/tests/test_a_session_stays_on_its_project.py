"""A session hired for one project does not reassign itself to another.

"My AI tried to change project in the same session when the selected project's task got
finished." Exactly what happened, and the mechanism was one line: every project write called
`adopt`, which set the conversation's project unconditionally. So a session that finished the
last task on the project it was pointed at went looking, touched a task on a different
project, and was silently moved there.

`_in_scope` already confines what a bound session may pick up — and that confinement is worth
nothing if any tool call can move the binding out from under it.

The same property was later made absolute rather than merely tool-shy: a conversation's
binding does not move for *anything* once set, including the person's own explicit choice
and the model's own "deliberate" act of starting a project from inside an already-bound
conversation. Wanting a different project is what a new conversation is for.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import conversations


@pytest.fixture
def two_projects(db):
    hired = repo.projects.add_project(db, "Client Portal", "the one it was hired for")
    other = repo.projects.add_project(db, "Gym Tracker", "somebody else's problem")
    repo.conversations.create(db, "c-1", "working on the portal", "kith-1")
    repo.conversations.set_project(db, "c-1", int(hired["id"]))
    return {"db": db, "hired": int(hired["id"]), "other": int(other["id"])}


def bound_to(db) -> int | None:
    return repo.conversations.project_of(db, "c-1")


class TestTheBindingSticks:
    def test_updating_another_projects_task_does_not_move_it(self, two_projects):
        """The exact failure. Bookkeeping on someone else's task is not a decision to
        change what this session is about."""
        from kith.tools import registry

        db = two_projects["db"]
        stray = repo.tasks.add_task(
            db, "Something else", "normal", "", "approved", "kith", two_projects["other"]
        )

        with session_context.working_in("c-1"):
            registry.get("update_task").run(db, {"id": int(stray["id"]), "priority": "high"})

        assert bound_to(db) == two_projects["hired"]

    def test_filing_a_task_elsewhere_does_not_move_it(self, two_projects):
        from kith.tools import registry

        db = two_projects["db"]
        with session_context.working_in("c-1"):
            registry.get("add_task").run(
                db,
                {
                    "goal": "A thing for the other project",
                    "description": "a real finish line so the brief check passes here",
                    "project_id": two_projects["other"],
                },
            )

        assert bound_to(db) == two_projects["hired"]

    def test_adding_a_milestone_elsewhere_does_not_move_it(self, two_projects):
        from kith.tools import registry

        db = two_projects["db"]
        with session_context.working_in("c-1"):
            registry.get("add_milestone").run(
                db, {"project_id": two_projects["other"], "title": "Their first step"}
            )

        assert bound_to(db) == two_projects["hired"]

    def test_touching_its_own_project_is_still_fine(self, two_projects):
        from kith.tools import registry

        db = two_projects["db"]
        with session_context.working_in("c-1"):
            registry.get("add_milestone").run(
                db, {"project_id": two_projects["hired"], "title": "Our next step"}
            )

        assert bound_to(db) == two_projects["hired"]

    def test_starting_a_second_project_here_is_refused_outright(self, two_projects):
        """Creating a project used to be the one *deliberate* act that could still move a bound
        session, then became an act that created the project and left the binding alone — the row
        was made, `_adoption_note` said the session had not moved, and the board grew another
        project nothing was working on. Nine projects, six on one folder, one called `placeholder`.

        Now it is refused. The note was trying to be a refusal."""
        from kith.tools import registry

        db = two_projects["db"]
        before = {p["name"] for p in repo.projects.list_projects(db)}
        with session_context.working_in("c-1"):
            out = registry.get("create_project").run(db, {"name": "Something New"})

        assert bound_to(db) == two_projects["hired"]
        assert "blocked" in out, out
        # Nothing was created, and the refusal says which project you are on — the answer is
        # always "start a new conversation for it", so it should name what it is refusing for.
        assert {p["name"] for p in repo.projects.list_projects(db)} == before
        assert "Client Portal" in out["blocked"] or "portal" in out["blocked"].lower()


class TestWhatBindsAnUnboundSession:
    """Only the first pick — by any means — ever takes. What follows tries a second one
    against an already-bound session and confirms none of it moves anything."""

    def test_starting_a_project_here_claims_an_unbound_session(self, db):
        """Creating one in this conversation *is* a statement about this session — as long
        as there was nothing to override."""
        from kith.tools import registry

        repo.conversations.create(db, "c-2", "fresh", "kith-2")
        with session_context.working_in("c-2"):
            made = registry.get("create_project").run(db, {"name": "Something New"})

        assert repo.conversations.project_of(db, "c-2") == int(made["id"])
        assert "note" not in made  # nothing to warn about when the bind actually took

    def test_an_unbound_session_still_binds_on_first_touch(self, db):
        """The original point of deriving it rather than declaring it. Only *re*-binding is
        refused."""
        from kith.tools import registry

        repo.conversations.create(db, "c-2", "fresh", "kith-2")
        project = repo.projects.add_project(db, "First", "a project")

        with session_context.working_in("c-2"):
            registry.get("add_milestone").run(db, {"project_id": int(project["id"]), "title": "Step"})

        assert repo.conversations.project_of(db, "c-2") == int(project["id"])

    def test_the_persons_own_first_pick_still_works(self, db):
        """The explicit route/service path — same repository call, same rule — still has to
        let a genuinely unbound conversation bind for the first time."""
        repo.conversations.create(db, "c-2", "fresh", "kith-2")
        project = repo.projects.add_project(db, "First", "a project")

        conversations.set_project(db, "c-2", int(project["id"]))

        assert repo.conversations.project_of(db, "c-2") == int(project["id"])


class TestThePersonCannotMoveItEitherOnceItIsBound:
    """The property this whole module is about used to have a hole: the person's own choice
    in the interface goes straight through the repository, bypassing `adopt` (and its
    refusal) entirely — so `adopt` being careful was not the same as the binding being
    unmovable. It is unmovable now because the repository itself refuses, which every path
    — tool-driven `adopt` and the person's own explicit pick alike — ultimately calls."""

    def test_the_repository_refuses_to_move_it(self, two_projects):
        db = two_projects["db"]
        changed = repo.conversations.set_project(db, "c-1", two_projects["other"])

        assert changed is False
        assert bound_to(db) == two_projects["hired"]

    def test_the_repository_refuses_to_unbind_it(self, two_projects):
        db = two_projects["db"]
        changed = repo.conversations.set_project(db, "c-1", None)

        assert changed is False
        assert bound_to(db) == two_projects["hired"]

    def test_re_setting_the_same_project_is_a_harmless_no_op(self, two_projects):
        """Not every write to an already-bound conversation is a move — asking for what it
        already has must succeed, not be read as an attempt to change it."""
        db = two_projects["db"]
        changed = repo.conversations.set_project(db, "c-1", two_projects["hired"])

        assert changed is True
        assert bound_to(db) == two_projects["hired"]

    def test_the_service_layer_raises_rather_than_silently_doing_nothing(self, two_projects):
        """The repository is silent by design — `adopt` needs that, since a session that
        fails to record what it is working on must not take down the work itself. The
        person asking through the explicit route is not bookkeeping, though, and deserves a
        real answer instead of a click that quietly did nothing."""
        db = two_projects["db"]

        with pytest.raises(conversations.ProjectLocked):
            conversations.set_project(db, "c-1", two_projects["other"])

        assert bound_to(db) == two_projects["hired"]
