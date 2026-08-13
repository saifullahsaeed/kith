"""What a conversation is working on, and how it comes to be working on it.

`conversations.project_id` was added with the sessions change and then sat null forever: read
on every chat turn to decide which project's memory to show, written by nobody. So two
sessions saw the same project memory, the same board, and the same everything — which is
precisely what "two projects at once" was supposed to stop.

Two halves here. The binding: he adopts a project by working on one, because a tool he has to
remember to call is a tool he will not call. And the scoping: a tick advances the session
whose turn it is, on that session's work and nobody else's.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import conversations
from kith.tools import projects as project_tools
from kith.tools import tasks as task_tools


def working(db, conversation_id: str) -> None:
    """Mark a session as working *without* starting the background loop.

    `keep_working` calls `ensure_loop`, and the loop then races the explicit `_tick()` these
    tests make — which showed up as every spy being called twice, an hour of "why is this
    prompt built two times". The flag is the only part under test here; the thread is
    tested where it belongs, in test_a_session_that_keeps_working.
    """
    repo.conversations.set_working(db, conversation_id, True)


@pytest.fixture
def session_id(db):
    return conversations.start(db, "a piece of work")["id"]


class TestAdoptingAProject:
    """He binds the session by working, not by declaring."""

    def test_starting_a_project_claims_it_for_this_session(self, db, session_id):
        with session_context.working_in(session_id):
            made = project_tools.create_project(db, {"name": "Workout tracker"})
        assert repo.conversations.project_of(db, session_id) == made["id"]

    def test_filing_a_task_under_a_project_claims_it(self, db, session_id):
        project = repo.projects.add_project(db, "Circulars", "")
        with session_context.working_in(session_id):
            task_tools.add_task(
                db,
                {
                    "goal": "read the archive",
                    "description": "done when the archive text is saved to a file",
                    "project_id": project["id"],
                },
            )
        assert repo.conversations.project_of(db, session_id) == project["id"]

    def test_a_task_filed_under_only_a_milestone_still_claims_the_project(self, db, session_id):
        """The repository resolves the project from the milestone, so reading the argument
        would have missed the case a roadmap actually produces."""
        project = repo.projects.add_project(db, "Circulars", "")
        milestone = repo.projects.add_milestone(db, project["id"], "Scrape it")
        with session_context.working_in(session_id):
            task_tools.add_task(
                db,
                {
                    "goal": "write the scraper",
                    "description": "done when scraper.py fetches the page",
                    "milestone_id": milestone["id"],
                },
            )
        assert repo.conversations.project_of(db, session_id) == project["id"]

    def test_picking_up_someone_elses_task_claims_its_project(self, db, session_id):
        """The ordinary case a create-only rule would miss: carrying on with work that was
        laid out yesterday, where the first thing he touches already exists."""
        project = repo.projects.add_project(db, "Laid out yesterday", "")
        task = repo.tasks.add_task(db, "the first step", project_id=project["id"])
        with session_context.working_in(session_id):
            task_tools.update_task(db, {"id": task["id"], "status": "working"})
        assert repo.conversations.project_of(db, session_id) == project["id"]

    def test_adding_a_milestone_claims_the_project(self, db, session_id):
        project = repo.projects.add_project(db, "Roadmapped", "")
        with session_context.working_in(session_id):
            project_tools.add_milestone(db, {"project_id": project["id"], "title": "Ship it"})
        assert repo.conversations.project_of(db, session_id) == project["id"]

    def test_linking_a_folder_claims_the_project(self, db, session_id, tmp_path):
        project = repo.projects.add_project(db, "Existing codebase", "")
        with session_context.working_in(session_id):
            project_tools.link_folder(db, {"id": project["id"], "folder": str(tmp_path / "repo")})
        assert repo.conversations.project_of(db, session_id) == project["id"]

    def test_finishing_a_project_does_not_claim_it(self, db, session_id):
        """A session whose project has just been marked done has nothing left to do. Binding
        it there would leave it pointed at closed work and taking no steps."""
        project = repo.projects.add_project(db, "Finished", "")
        with session_context.working_in(session_id):
            project_tools.update_project(db, {"id": project["id"], "status": "done"})
        assert repo.conversations.project_of(db, session_id) is None

    def test_two_sessions_end_up_on_two_projects(self, db):
        one = conversations.start(db, "one")["id"]
        two = conversations.start(db, "two")["id"]
        with session_context.working_in(one):
            first = project_tools.create_project(db, {"name": "Workout tracker"})
        with session_context.working_in(two):
            second = project_tools.create_project(db, {"name": "Circular watcher"})

        assert repo.conversations.project_of(db, one) == first["id"]
        assert repo.conversations.project_of(db, two) == second["id"]

    def test_a_tool_called_with_no_session_binds_nothing(self, db):
        """A test, a script, or a tick with nobody working. Not an error — there is simply
        no session to claim anything."""
        assert session_context.current() == ""
        made = project_tools.create_project(db, {"name": "Nobody's"})
        assert made["id"]  # it still works, it just does not claim


class TestTheContextIsPerThread:
    def test_two_threads_do_not_see_each_others_session(self, db):
        """Two chats streaming at once must not cross. A ContextVar is per-thread, which is
        the property the whole design leans on."""
        import threading

        seen: dict[str, str] = {}

        def chat(name: str) -> None:
            with session_context.working_in(name):
                # Long enough for the other thread to be inside its own `with`.
                threading.Event().wait(0.05)
                seen[name] = session_context.current()

        threads = [threading.Thread(target=chat, args=(name,)) for name in ("alpha", "beta")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert seen == {"alpha": "alpha", "beta": "beta"}

    def test_it_is_empty_again_afterwards(self, db, session_id):
        with session_context.working_in(session_id):
            pass
        assert session_context.current() == ""


# Two classes stood here — `TestWhatASessionMayWorkOn`, whose docstring said "`_in_scope` is the
# whole of it", and `TestATickAdvancesOneSession` — both emptied when the tests for the
# self-directed loop were retired and left as shells with one uncalled helper between them. The
# helper still patched `tasks_awaiting_kith`, which no longer exists; nothing noticed, because
# nothing called it. `_in_scope` does not exist either: the confinement it named is now
# `session_context.foreign_project`, tested in test_a_conversation_stays_in_its_project.py.


class TestTheTickSeesTheProjectMemory:
    """A conversation has been shown `.kith/memory.md` since it existed. A tick never was.

    Which is exactly backwards: the unattended step is the one with nobody around to remind
    him how the thing is built.
    """


class TestSayingItByHand:
    """The picker in the interface, for when his guess is wrong or you want to say up front."""

    def call(self, db, monkeypatch, conversation_id: str, payload: dict):
        """The real route function, in a request context pointed at a temp database.

        Not a full app: building one means reloading `kith.settings` and `kith.config` under
        a temp environment and putting them back, which is a page of fixture for a handler
        that reads one field. The route module holds its own reference to AGENT_DB_PATH, so
        that is the thing to redirect.
        """
        from flask import Flask

        from kith.api.routes import conversations as route

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        with Flask(__name__).test_request_context(json=payload):
            out = route.set_conversation_project(conversation_id)
        body, status = out if isinstance(out, tuple) else (out, 200)
        return status, body.get_json()

    def test_it_binds_on_the_first_pick(self, db, monkeypatch):
        project = repo.projects.add_project(db, "By hand", "")
        session = conversations.start(db, "a session")["id"]

        status, body = self.call(db, monkeypatch, session, {"projectId": project["id"]})
        assert (status, body["projectId"]) == (200, project["id"])

    def test_it_does_not_unbind_once_picked(self, db, monkeypatch):
        """The one-way door. Unbinding used to be how you corrected a wrong guess — now a new
        conversation is, so asking to undo a pick must fail loudly rather than quietly work."""
        project = repo.projects.add_project(db, "By hand", "")
        session = conversations.start(db, "a session")["id"]
        self.call(db, monkeypatch, session, {"projectId": project["id"]})

        status, body = self.call(db, monkeypatch, session, {"projectId": None})

        assert status == 409
        assert "error" in body
        assert repo.conversations.project_of(db, session) == project["id"]

    def test_it_does_not_move_to_a_different_project_either(self, db, monkeypatch):
        first = repo.projects.add_project(db, "First pick", "")
        second = repo.projects.add_project(db, "Second thoughts", "")
        session = conversations.start(db, "a session")["id"]
        self.call(db, monkeypatch, session, {"projectId": first["id"]})

        status, body = self.call(db, monkeypatch, session, {"projectId": second["id"]})

        assert status == 409
        assert "error" in body
        assert repo.conversations.project_of(db, session) == first["id"]

    def test_re_picking_the_same_project_is_not_an_error(self, db, monkeypatch):
        """Not every second call is an attempted move — confirming what is already true
        must succeed, since a UI that re-sends the current value on every render is not
        expressing a change of mind."""
        project = repo.projects.add_project(db, "By hand", "")
        session = conversations.start(db, "a session")["id"]
        self.call(db, monkeypatch, session, {"projectId": project["id"]})

        status, body = self.call(db, monkeypatch, session, {"projectId": project["id"]})

        assert (status, body["projectId"]) == (200, project["id"])

    def test_a_project_that_does_not_exist_is_refused(self, db, monkeypatch):
        session = conversations.start(db, "a session")["id"]
        status, _ = self.call(db, monkeypatch, session, {"projectId": 999})
        assert status == 404

    def test_a_conversation_that_does_not_exist_is_refused(self, db, monkeypatch):
        project = repo.projects.add_project(db, "Real", "")
        status, _ = self.call(db, monkeypatch, "nope", {"projectId": project["id"]})
        assert status == 404

    def test_nonsense_is_refused_rather_than_coerced(self, db, monkeypatch):
        session = conversations.start(db, "a session")["id"]
        status, _ = self.call(db, monkeypatch, session, {"projectId": "soon"})
        assert status == 400

    def test_the_listing_says_what_each_session_is_on(self, db):
        project = repo.projects.add_project(db, "Visible", "")
        session = conversations.start(db, "a session")["id"]
        repo.conversations.set_project(db, session, project["id"])

        row = next(one for one in conversations.recent(db) if one["id"] == session)
        # Without this the interface has no way to show which project a session is on, which
        # is the whole question "how do I run two projects" turns on.
        assert row["projectId"] == project["id"]
        assert row["working"] is False
