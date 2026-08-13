"""One project per conversation, enforced where the writes happen.

The rule is old and was never enforced. `running-a-project` has said for a long time that "a
conversation locks to the first project it works on and stays there for the rest of its life", and
`session_context.adopt` goes to real trouble to protect the binding from moving — its docstring
explains that a session which wandered onto another project's task was "silently reassigned there",
and that `_in_scope` is what "confines what a bound session may pick up".

**`_in_scope` does not exist.** It was a method on the autonomy runner and was deleted with the
self-directed loop on 2026-08-08. So what is left is a binding kept carefully immovable, and nothing
that reads it — chat was never confined at all. The board shows what an unenforced rule gets you:
nine projects, six of them on `…/Desktop/Personal/ai-play`, two of those active, one named
`placeholder`, one named "duplicate board entry".

And it is what happened when it was tested: asked to create a task in Sadeef AI #6 from a
conversation locked to Project Management Test #15, he asked which outcome it should have rather
than refusing, and had to be told "you were suppose to push back that this is not project of this
session". His reasoning was right once challenged. The guard was missing.

Reads stay open throughout. Looking at another project is normal and often necessary; it is writing
that changes what a conversation is about.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.tools import run_tool

BRIEF = "Done when the thing exists and the check passes."


def _two_projects(db: Path, tmp_path: Path) -> tuple[int, int]:
    here = repo.projects.add_project(db, "Project Management Test", "", str(tmp_path / "here"))
    there = repo.projects.add_project(db, "Sadeef AI", "", str(tmp_path / "there"))
    repo.conversations.create(db, "c-1", "working on the test project", "kith-1")
    repo.conversations.set_project(db, "c-1", int(here["id"]))
    return int(here["id"]), int(there["id"])


class TestWritingIntoAnotherProject:
    def test_filing_a_task_there_is_refused(self, db: Path, tmp_path: Path):
        _, there = _two_projects(db, tmp_path)

        with session_context.working_in("c-1"):
            out = run_tool("add_task", {"goal": "Do it", "description": BRIEF, "project_id": there}, db)

        assert "blocked" in (out.get("result") or {}), out
        assert repo.tasks.list_tasks(db) == []

    def test_the_refusal_names_both_projects(self, db: Path, tmp_path: Path):
        _, there = _two_projects(db, tmp_path)

        with session_context.working_in("c-1"):
            out = run_tool("add_task", {"goal": "Do it", "description": BRIEF, "project_id": there}, db)

        said = str(out.get("result") or {})
        # Which conversation you are in, and which project you reached for. "Not allowed" on its
        # own is unactionable — the answer is always "start a new conversation", and it should say so.
        assert "Project Management Test" in said
        assert "Sadeef AI" in said
        assert "conversation" in said.lower()

    def test_moving_another_projects_task_is_refused(self, db: Path, tmp_path: Path):
        _, there = _two_projects(db, tmp_path)
        stray = repo.tasks.add_task(db, "Theirs", "normal", BRIEF, "working", "kith", there)

        with session_context.working_in("c-1"):
            out = run_tool("update_task", {"id": int(stray["id"]), "priority": "high"}, db)

        assert "blocked" in (out.get("result") or {}), out
        assert repo.tasks.task_detail(db, int(stray["id"]))["priority"] == "normal"

    def test_adding_a_milestone_there_is_refused(self, db: Path, tmp_path: Path):
        _, there = _two_projects(db, tmp_path)

        with session_context.working_in("c-1"):
            out = run_tool("add_milestone", {"project_id": there, "title": "Theirs"}, db)

        assert "blocked" in (out.get("result") or {}), out


class TestItsOwnProjectIsFine:
    def test_filing_a_task_here_works(self, db: Path, tmp_path: Path):
        here, _ = _two_projects(db, tmp_path)

        with session_context.working_in("c-1"):
            run_tool("add_task", {"goal": "Do it", "description": BRIEF, "project_id": here}, db)

        assert [t["goal"] for t in repo.tasks.list_tasks(db)] == ["Do it"]

    def test_a_task_with_no_project_is_never_confined(self, db: Path, tmp_path: Path):
        """A one-off errand belongs to nobody's project and must not be caught by this."""
        _two_projects(db, tmp_path)

        with session_context.working_in("c-1"):
            run_tool("add_task", {"goal": "Buy milk"}, db)

        assert [t["goal"] for t in repo.tasks.list_tasks(db)] == ["Buy milk"]

    def test_an_unbound_conversation_may_work_anywhere(self, db: Path, tmp_path: Path):
        """Nothing to be foreign to yet. Filing the task is also what binds it."""
        _, there = _two_projects(db, tmp_path)
        repo.conversations.create(db, "c-2", "a fresh session", "kith-2")

        with session_context.working_in("c-2"):
            run_tool("add_task", {"goal": "Do it", "description": BRIEF, "project_id": there}, db)

        assert repo.conversations.project_of(db, "c-2") == there

    def test_no_conversation_at_all_is_not_refused(self, db: Path, tmp_path: Path):
        """A script, a test, a reminder with no chat — there is no binding to violate."""
        _, there = _two_projects(db, tmp_path)

        run_tool("add_task", {"goal": "Do it", "description": BRIEF, "project_id": there}, db)

        assert len(repo.tasks.list_tasks(db)) == 1


class TestLookingIsAlwaysAllowed:
    def test_another_projects_task_can_be_read(self, db: Path, tmp_path: Path):
        _, there = _two_projects(db, tmp_path)
        stray = repo.tasks.add_task(db, "Theirs", "normal", BRIEF, "working", "kith", there)

        with session_context.working_in("c-1"):
            out = run_tool("view_task", {"id": int(stray["id"])}, db)

        assert (out.get("result") or {}).get("goal") == "Theirs"

    def test_projects_can_be_listed(self, db: Path, tmp_path: Path):
        _two_projects(db, tmp_path)

        with session_context.working_in("c-1"):
            out = run_tool("list_projects", {}, db)

        assert out.get("ok") is True


class TestStartingASecondProject:
    def test_a_bound_conversation_cannot_start_one(self, db: Path, tmp_path: Path):
        _two_projects(db, tmp_path)

        with session_context.working_in("c-1"):
            out = run_tool("create_project", {"name": "Something else"}, db)

        assert "blocked" in (out.get("result") or {}), out
        # A set, not a list: `list_projects` orders by recency, which is not what this is about.
        assert {p["name"] for p in repo.projects.list_projects(db)} == {
            "Project Management Test",
            "Sadeef AI",
        }

    def test_a_folder_that_already_has_a_project_returns_that_one(self, db: Path, tmp_path: Path):
        """Six of the nine projects on the board point at one folder. A directory has one project."""
        shared = tmp_path / "ai-play"
        shared.mkdir()
        first = repo.projects.add_project(db, "Sadeef AI", "", str(shared))

        out = run_tool("create_project", {"name": "Odoo Accounting Agent", "directory": str(shared)}, db)

        assert (out.get("result") or {}).get("id") == first["id"]
        assert len(repo.projects.list_projects(db)) == 1

    def test_and_says_it_reused_one(self, db: Path, tmp_path: Path):
        shared = tmp_path / "ai-play"
        shared.mkdir()
        repo.projects.add_project(db, "Sadeef AI", "", str(shared))

        out = run_tool("create_project", {"name": "Odoo Accounting Agent", "directory": str(shared)}, db)

        # Silently handing back a different project than the one asked for would read as success
        # at creating "Odoo Accounting Agent".
        assert "Sadeef AI" in str(out.get("result") or {})

    def test_a_fresh_conversation_still_starts_one(self, db: Path, tmp_path: Path):
        repo.conversations.create(db, "c-3", "a fresh session", "kith-3")

        with session_context.working_in("c-3"):
            out = run_tool("create_project", {"name": "A new thing"}, db)

        assert (out.get("result") or {}).get("name") == "A new thing"
