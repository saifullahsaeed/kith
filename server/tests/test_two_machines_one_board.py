"""Two people, one project, one folder — the whole loop, driven end to end.

Every part of this was tested on its own: push and pull against a real bare remote, the import
against real briefs, the migrations against a real database. None of it had ever been run
*together*, and the parts are not the feature. This is the test a reviewer looks for first, and
it is where the assumptions about keys, identity and naming meet for the first time.

Nothing is stubbed but the clock. Two workspaces, two databases, one bare remote, and the real
functions the tools call.

    A files a task -> brief written -> commit -> push
                                                  |
    B pulls -> B's board says work is waiting -> B takes it in
                                                  |
                    the same key, and so the same task, on both machines
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kith.infra import identity, project_files
from kith.infra.db import repositories as repo
from kith.infra.db.connection import apply_migrations, connect
from kith.infra.db.migrations import _migrations
from kith.services import board_sync


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout


def _machine(root: Path, name: str, email: str, origin: Path, first: bool = False) -> tuple[Path, Path]:
    """One person's world: a checkout of the shared project, and a database of their own."""
    work = root / name
    if first:
        work.mkdir(parents=True)
        _git("init", "-q", cwd=work)
        _git("remote", "add", "origin", str(origin), cwd=work)
    else:
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    _git("config", "user.email", email, cwd=work)
    _git("config", "user.name", name, cwd=work)
    _git("config", "commit.gpgsign", "false", cwd=work)

    db = root / f"{name}.db"
    conn = connect(db)
    apply_migrations(conn, _migrations())
    conn.close()
    return work, db


@pytest.fixture
def two_people(tmp_path: Path):
    identity.forget()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True, capture_output=True)
    a_work, a_db = _machine(tmp_path, "ayesha", "ayesha@example.com", origin, first=True)
    (a_work / "README.md").write_text("# the project\n")
    _git("add", "-A", cwd=a_work)
    _git("commit", "-q", "-m", "start", cwd=a_work)
    # `-u` with the branch's real name: pushing to `refs/heads/main` from a branch git called
    # something else leaves an upstream whose name does not match, and every later bare `push`
    # refuses. The default branch name is a local git setting and not ours to assume.
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=a_work).strip()
    _git("push", "-q", "-u", "origin", branch, cwd=a_work)
    b_work, b_db = _machine(tmp_path, "bilal", "bilal@example.com", origin)
    yield (a_work, a_db), (b_work, b_db)
    identity.forget()


def _files_a_task(db: Path, work: Path, goal: str) -> dict:
    """What `add_task` and its mirror do, which is what a tool call amounts to."""
    project = repo.projects.add_project(db, "Shared", "", str(work))
    task = repo.tasks.add_task(db, goal, project_id=int(project["id"]))
    project_files.write_brief(work, repo.tasks.task_detail(db, int(task["id"])))
    return {"project": int(project["id"]), "task": task}


class TestTheWholeLoop:
    def test_work_filed_on_one_machine_reaches_the_other(self, two_people):
        (a_work, a_db), (b_work, b_db) = two_people
        mine = _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file a task", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        # B has a project over the same folder and knows nothing of it yet.
        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        assert repo.tasks.list_tasks(b_db) == []

        _git("pull", "-q", "--ff-only", cwd=b_work)
        out = board_sync.pull(b_db, int(theirs["id"]), str(b_work))
        assert out["added"] == ["Wire the routes"]

        landed = repo.tasks.list_tasks(b_db)
        assert [t["goal"] for t in landed] == ["Wire the routes"]
        # The thing that makes it the *same* task rather than one that reads alike.
        assert landed[0]["key"] == mine["task"]["key"]

    def test_it_arrives_attributed_to_whoever_filed_it(self, two_people):
        """Not to whoever pulled it. `account` is a claim about who did the work."""
        (a_work, a_db), (b_work, b_db) = two_people
        _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        _git("pull", "-q", "--ff-only", cwd=b_work)
        board_sync.pull(b_db, int(theirs["id"]), str(b_work))
        assert repo.tasks.list_tasks(b_db)[0]["account"] == "ayesha@example.com"

    def test_b_is_told_before_being_asked(self, two_people, monkeypatch):
        """`list_tasks` is where this surfaces. B should not have to know to go looking."""
        (a_work, a_db), (b_work, b_db) = two_people
        _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: int(theirs["id"]))
        _git("pull", "-q", "--ff-only", cwd=b_work)

        said = board_sync.waiting_here(b_db)
        assert "Wire the routes" in said and "not on this board" in said

    def test_nothing_arrives_until_somebody_fetches(self, two_people, monkeypatch):
        """The honest limit, asserted so nobody later mistakes silence for agreement."""
        (a_work, a_db), (b_work, b_db) = two_people
        _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: int(theirs["id"]))
        assert board_sync.waiting_here(b_db) == "", "B has not pulled, so B sees nothing"


class TestBothOfThemChangeIt:
    def test_the_newer_side_wins_across_the_wire(self, two_people):
        (a_work, a_db), (b_work, b_db) = two_people
        mine = _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        _git("pull", "-q", "--ff-only", cwd=b_work)
        board_sync.pull(b_db, int(theirs["id"]), str(b_work))

        # A finishes it and pushes; B still thinks it is open.
        repo.tasks.update_task(a_db, int(mine["task"]["id"]), status="done")
        project_files.write_brief(a_work, repo.tasks.task_detail(a_db, int(mine["task"]["id"])))
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "done", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        _git("pull", "-q", "--ff-only", cwd=b_work)
        out = board_sync.pull(b_db, int(theirs["id"]), str(b_work))
        assert len(out["updated"]) == 1
        assert repo.tasks.list_tasks(b_db)[0]["status"] == "done"

    def test_it_does_not_go_backwards(self, two_people):
        """B moved on locally and A's brief is older. Nothing of B's is lost."""
        (a_work, a_db), (b_work, b_db) = two_people
        _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        _git("pull", "-q", "--ff-only", cwd=b_work)
        board_sync.pull(b_db, int(theirs["id"]), str(b_work))
        theirs_task = repo.tasks.list_tasks(b_db)[0]
        repo.tasks.update_task(b_db, int(theirs_task["id"]), status="done")

        out = board_sync.pull(b_db, int(theirs["id"]), str(b_work))
        assert len(out["kept"]) == 1, "reported as a difference, not applied"
        assert repo.tasks.list_tasks(b_db)[0]["status"] == "done"


class TestWhatGitLeavesBehind:
    def test_a_real_conflict_stops_the_import(self, two_people):
        """Both edit the same brief and push. Git leaves markers in the file, and a forgiving
        reader would import one side and destroy the evidence there was ever a disagreement."""
        (a_work, a_db), (b_work, b_db) = two_people
        mine = _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file", cwd=a_work)
        _git("push", "-q", cwd=a_work)
        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        _git("pull", "-q", "--ff-only", cwd=b_work)
        board_sync.pull(b_db, int(theirs["id"]), str(b_work))

        # Each side moves the task and rewrites its own brief, which is what really happens —
        # not a string edit that might match nothing.
        name = next((a_work / ".kith" / "tasks").glob("*.md")).name
        repo.tasks.update_task(a_db, int(mine["task"]["id"]), status="done")
        project_files.write_brief(a_work, repo.tasks.task_detail(a_db, int(mine["task"]["id"])))
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "a says done", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        theirs_task = repo.tasks.list_tasks(b_db)[0]
        repo.tasks.update_task(b_db, int(theirs_task["id"]), status="dropped")
        project_files.write_brief(b_work, repo.tasks.task_detail(b_db, int(theirs_task["id"])))
        theirs_brief = b_work / ".kith" / "tasks" / name
        _git("add", "-A", cwd=b_work)
        _git("commit", "-q", "-m", "b says dropped", cwd=b_work)
        subprocess.run(["git", "pull", "--no-rebase", "-q"], cwd=b_work, capture_output=True)

        assert "<<<<<<<" in theirs_brief.read_text(), "git really did leave a conflict"

        out = board_sync.pull(b_db, int(theirs["id"]), str(b_work))
        # Blocked on the repository being mid-merge, which is checked before any file is opened
        # — so the markers never even had to be found. Both guards are right and the earlier one
        # is stronger: it does not depend on recognising what a conflict looks like inside a
        # brief. Asserting the *reason* here would pin the weaker of the two.
        assert out["blocked"], "an unmerged folder must not be read"
        assert out["added"] == [] and out["updated"] == []
        # Nothing was taken in, so B's board still says what B last said — not A's `done`,
        # and not a silent pick of whichever side the reader happened to parse first.
        assert repo.tasks.list_tasks(b_db)[0]["status"] == "dropped"

    def test_resolving_it_unblocks_reading_and_does_not_decide_the_board(self, two_people):
        """The guard holds until a person decides, then gets out of the way — a refusal that
        never lifts is a broken feature wearing a safety feature's clothes.

        **And resolving the git conflict is not the same as deciding the task.** Found by
        writing this test and expecting the opposite. The person took A's file, so the brief now
        says `done`; B's row still says `dropped` and was changed *later*. `pull` reports the
        difference and keeps B's — which looks wrong for a second and is right: the resolution
        chose which *text* survives in the repository, and somebody choosing a side to stop git
        complaining is not the same as somebody saying A's status is the true one.

        Nothing is lost either way. It comes back as a difference with both values named, and
        the person decides again with everything in front of them. The alternative — silently
        applying an older status because a merge picked that file — is a decision made by a
        heuristic on someone's behalf, which is the thing this whole design refuses to do."""
        (a_work, a_db), (b_work, b_db) = two_people
        mine = _files_a_task(a_db, a_work, "Wire the routes")
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "file", cwd=a_work)
        _git("push", "-q", cwd=a_work)
        theirs = repo.projects.add_project(b_db, "Shared", "", str(b_work))
        _git("pull", "-q", "--ff-only", cwd=b_work)
        board_sync.pull(b_db, int(theirs["id"]), str(b_work))

        name = next((a_work / ".kith" / "tasks").glob("*.md")).name
        repo.tasks.update_task(a_db, int(mine["task"]["id"]), status="done")
        project_files.write_brief(a_work, repo.tasks.task_detail(a_db, int(mine["task"]["id"])))
        _git("add", "-A", cwd=a_work)
        _git("commit", "-q", "-m", "a says done", cwd=a_work)
        _git("push", "-q", cwd=a_work)

        theirs_task = repo.tasks.list_tasks(b_db)[0]
        repo.tasks.update_task(b_db, int(theirs_task["id"]), status="dropped")
        project_files.write_brief(b_work, repo.tasks.task_detail(b_db, int(theirs_task["id"])))
        _git("add", "-A", cwd=b_work)
        _git("commit", "-q", "-m", "b says dropped", cwd=b_work)
        subprocess.run(["git", "pull", "--no-rebase", "-q"], cwd=b_work, capture_output=True)
        assert board_sync.pull(b_db, int(theirs["id"]), str(b_work))["blocked"]

        # The person takes A's side and finishes the merge, as a person has to.
        _git("checkout", "--theirs", f".kith/tasks/{name}", cwd=b_work)
        _git("add", "-A", cwd=b_work)
        _git("commit", "-q", "-m", "took a's", cwd=b_work)

        out = board_sync.pull(b_db, int(theirs["id"]), str(b_work))
        assert out["blocked"] == "", "the guard lifts once the folder is settled"
        assert len(out["kept"]) == 1, "the difference is reported, with both sides named"
        assert "status" in out["kept"][0]
        assert repo.tasks.list_tasks(b_db)[0]["status"] == "dropped", "B's later change stands"
