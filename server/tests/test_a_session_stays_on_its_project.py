"""A session hired for one project does not reassign itself to another.

"My AI tried to change project in the same session when the selected project's task got
finished." Exactly what happened, and the mechanism was one line: every project write called
`adopt`, which set the conversation's project unconditionally. So a session that finished the
last task on the project it was pointed at went looking, touched a task on a different
project, and was silently moved there.

`_in_scope` already confines what a bound session may pick up — and that confinement is worth
nothing if any tool call can move the binding out from under it.
"""

from __future__ import annotations

import sys

import pytest

from kith.infra.db import repositories as repo
from kith.services import session_context


def runner_module():
    return sys.modules["kith.autonomy.runner"]


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
            db, "Something else", "normal", None, "", "todo", "kith", two_projects["other"]
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


class TestWhatMayStillMoveIt:
    def test_starting_a_project_here_claims_the_session(self, two_projects):
        """Creating one in this conversation *is* a statement about this session."""
        from kith.tools import registry

        db = two_projects["db"]
        with session_context.working_in("c-1"):
            made = registry.get("create_project").run(db, {"name": "Something New"})

        assert bound_to(db) == int(made["id"])

    def test_an_unbound_session_still_binds_on_first_touch(self, db):
        """The original point of deriving it rather than declaring it. Only *re*-binding is
        refused."""
        from kith.tools import registry

        repo.conversations.create(db, "c-2", "fresh", "kith-2")
        project = repo.projects.add_project(db, "First", "a project")

        with session_context.working_in("c-2"):
            registry.get("add_milestone").run(db, {"project_id": int(project["id"]), "title": "Step"})

        assert repo.conversations.project_of(db, "c-2") == int(project["id"])

    def test_the_person_can_always_move_a_session(self, two_projects):
        """Their choice does not come through `adopt` at all, so nothing he does can override
        it and nothing here may take it away."""
        db = two_projects["db"]
        repo.conversations.set_project(db, "c-1", two_projects["other"])
        assert bound_to(db) == two_projects["other"]


class TestSayingSoWhenItRunsOut:
    def test_a_finished_project_names_itself_and_the_work_it_is_not_taking(self, two_projects, monkeypatch):
        """Being told "caught up" by a session you pointed at one thing, while another project
        has work waiting, is confusing in a way naming the project fixes."""
        db = two_projects["db"]
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        repo.tasks.add_task(db, "Their work", "high", None, "", "todo", "kith", two_projects["other"])

        status, note = runner_module().AutonomyRunner()._why_idle(project=two_projects["hired"])

        assert "Client Portal" in status
        assert "Client Portal" in note
        assert "other projects" in note
        assert "new one" in note, "it has to say what to do about it"

    def test_with_nothing_waiting_anywhere_it_just_rests(self, two_projects, monkeypatch):
        db = two_projects["db"]
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)

        status, note = runner_module().AutonomyRunner()._why_idle(project=two_projects["hired"])

        assert "Client Portal" in status
        assert note == "", "nothing outstanding means nothing to say"

    def test_an_unbound_session_still_says_caught_up(self, db, monkeypatch):
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        status, note = runner_module().AutonomyRunner()._why_idle()
        assert status == "caught up — resting"
        assert note == ""
