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

import sys

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations, session_context
from kith.tools import projects as project_tools
from kith.tools import tasks as task_tools


def runner_on(db, monkeypatch):
    """A runner pointed at a temp database. See test_a_session_that_keeps_working."""
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


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


class TestWhatASessionMayWorkOn:
    """`_in_scope` is the whole of it: which rows this session's tick is allowed to see."""

    def test_a_bound_session_sees_only_its_own_project(self):
        from kith.autonomy.runner import AutonomyRunner as R

        assert R._in_scope({"project_id": 1}, 1, {1, 2}) is True
        assert R._in_scope({"project_id": 2}, 1, {1, 2}) is False
        assert R._in_scope({"project_id": None}, 1, {1, 2}) is False

    def test_an_unbound_session_avoids_what_others_have_claimed(self):
        from kith.autonomy.runner import AutonomyRunner as R

        # One-off errands and unfiled work are its business.
        assert R._in_scope({"project_id": None}, None, {1}) is True
        # A project someone else is driving is not.
        assert R._in_scope({"project_id": 1}, None, {1}) is False
        # A project nobody is on still is.
        assert R._in_scope({"project_id": 7}, None, {1}) is True

    def test_with_nobody_working_the_whole_board_is_fair_game(self):
        """ "Run once" has always meant "take a step on anything", and still does."""
        from kith.autonomy.runner import AutonomyRunner as R

        assert R._in_scope({"project_id": 3}, None, set()) is True
        assert R._in_scope({"project_id": None}, None, set()) is True


class TestATickAdvancesOneSession:
    def _quiet(self, module, monkeypatch, runner):
        """Nothing due, nothing pending — so the tick reaches the work branches."""
        monkeypatch.setattr(runner, "_new_pending", lambda: [])
        monkeypatch.setattr(module.repo.tasks, "tasks_awaiting_kith", lambda _p: [])
        monkeypatch.setattr(module.repo.reminders, "due_reminders", lambda _p, _n: [])
        monkeypatch.setattr(module.repo.schedules, "due_schedules", lambda _p, _n: [])

    def test_it_works_the_session_project_and_not_the_other_one(self, db, monkeypatch):
        module = sys.modules["kith.autonomy.runner"]
        runner = runner_on(db, monkeypatch)
        self._quiet(module, monkeypatch, runner)

        mine = repo.projects.add_project(db, "Mine", "")
        theirs = repo.projects.add_project(db, "Theirs", "")
        repo.tasks.add_task(db, "my step", status="planned", project_id=mine["id"])
        repo.tasks.add_task(db, "their step", status="planned", project_id=theirs["id"])

        one = conversations.start(db, "one")["id"]
        two = conversations.start(db, "two")["id"]
        repo.conversations.set_project(db, one, mine["id"])
        repo.conversations.set_project(db, two, theirs["id"])
        working(db, one)
        working(db, two)

        # `one` was touched first, so it is longest-waiting and goes first.
        focused: list[str] = []
        monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(()))
        monkeypatch.setattr(
            module, "_focus_prompt", lambda focus, active: focused.append(focus["goal"]) or ""
        )

        runner._tick()
        assert focused == ["my step"]

        # And the next tick takes the other one, because the first went to the back.
        runner._tick()
        assert focused == ["my step", "their step"]

    def test_a_session_that_finishes_stops_only_itself(self, db, monkeypatch):
        """It used to stop every session. Invisible while the board was global and everyone
        ran out together; plainly wrong now — one project finishing would down the rest."""
        module = sys.modules["kith.autonomy.runner"]
        runner = runner_on(db, monkeypatch)
        self._quiet(module, monkeypatch, runner)

        empty = repo.projects.add_project(db, "Nothing left", "")
        busy = repo.projects.add_project(db, "Plenty left", "")
        repo.tasks.add_task(db, "still to do", status="planned", project_id=busy["id"])

        done_session = conversations.start(db, "finished")["id"]
        busy_session = conversations.start(db, "busy")["id"]
        repo.conversations.set_project(db, done_session, empty["id"])
        repo.conversations.set_project(db, busy_session, busy["id"])
        working(db, done_session)
        working(db, busy_session)

        monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(()))
        runner._tick()  # the finished one is longest-waiting, so it goes first

        assert not repo.conversations.is_working(db, done_session)
        assert repo.conversations.is_working(db, busy_session)

    def test_the_step_runs_bound_to_its_session(self, db, monkeypatch):
        """Which is what lets a tool that starts a project record whose project it is."""
        module = sys.modules["kith.autonomy.runner"]
        runner = runner_on(db, monkeypatch)
        self._quiet(module, monkeypatch, runner)

        project = repo.projects.add_project(db, "Something", "")
        repo.tasks.add_task(db, "a step", status="planned", project_id=project["id"])
        session = conversations.start(db, "working")["id"]
        repo.conversations.set_project(db, session, project["id"])
        working(db, session)

        seen: list[str] = []

        def spy(*_a, **_k):
            seen.append(session_context.current())
            return iter(())

        monkeypatch.setattr(module, "stream_agent", spy)
        runner._tick()

        assert seen == [session]

    def test_an_unbound_session_leaves_a_claimed_project_alone(self, db, monkeypatch):
        module = sys.modules["kith.autonomy.runner"]
        runner = runner_on(db, monkeypatch)
        self._quiet(module, monkeypatch, runner)

        claimed = repo.projects.add_project(db, "Claimed", "")
        repo.tasks.add_task(db, "someone else's step", status="planned", project_id=claimed["id"])
        repo.tasks.add_task(db, "an errand", status="planned")  # no project

        driver = conversations.start(db, "driver")["id"]
        general = conversations.start(db, "general")["id"]
        repo.conversations.set_project(db, driver, claimed["id"])
        # `general` stays unbound, and is touched last, so it ticks second.
        working(db, general)
        working(db, driver)

        focused: list[str] = []
        monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(()))
        monkeypatch.setattr(
            module, "_focus_prompt", lambda focus, active: focused.append(focus["goal"]) or ""
        )
        runner._tick()

        assert focused == ["an errand"]


class TestTheTickSeesTheProjectMemory:
    """A conversation has been shown `.kith/memory.md` since it existed. A tick never was.

    Which is exactly backwards: the unattended step is the one with nobody around to remind
    him how the thing is built.
    """

    def test_it_reads_the_memory_of_the_session_project(self, db, monkeypatch, tmp_path):
        from kith.services import project_memory

        runner = runner_on(db, monkeypatch)
        folder = tmp_path / "repo"
        folder.mkdir()
        project_memory.ensure(folder)
        project_memory.path_for(folder).write_text(
            "# Memory\n\n## Gotchas\n- the dev server needs PORT=4000\n", encoding="utf-8"
        )
        project = repo.projects.add_project(db, "Has a folder", "", str(folder))

        block = runner._project_memory(project["id"])
        assert "PORT=4000" in block

    def test_a_session_on_nothing_reads_nothing(self, db, monkeypatch):
        assert runner_on(db, monkeypatch)._project_memory(None) == ""

    def test_a_project_with_no_folder_reads_nothing(self, db, monkeypatch):
        runner = runner_on(db, monkeypatch)
        project = repo.projects.add_project(db, "Just rows", "")
        assert runner._project_memory(project["id"]) == ""

    def test_a_folder_with_no_memory_yet_is_asked_for_one(self, db, monkeypatch, tmp_path):
        """Not an empty block: a project with a folder and no memory gets told to start one."""
        runner = runner_on(db, monkeypatch)
        folder = tmp_path / "fresh"
        folder.mkdir()
        project = repo.projects.add_project(db, "Fresh", "", str(folder))
        assert "no `.kith/memory.md` yet" in runner._project_memory(project["id"])

    def test_a_folder_moved_out_from_under_him_says_so_instead(self, db, monkeypatch, tmp_path):
        """This case used to give the same answer as the one above, and the docstring here
        used to call that "nudged rather than crashed".

        The nudge was "write a memory file", which is the wrong move: the folder is gone, so
        writing lands nowhere useful and whatever the project already knew stays unread. It
        happened for real — a project pointed at a deleted folder while three thousand
        characters of its memory sat on disk being reported as absent.
        """
        runner = runner_on(db, monkeypatch)
        project = repo.projects.add_project(db, "Moved", "", str(tmp_path / "gone"))

        block = runner._project_memory(project["id"])

        assert "not there" in block
        assert "no `.kith/memory.md` yet" not in block


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
