"""A safety net under every file change he makes, automatically, before it happens.

`commit_all` is deliberate on purpose — a commit is a claim that something is a coherent
step, and that has to stay a judgement. But deliberate also means there is no net between
commits, and he edits files unattended. This is that net: a snapshot taken before every
mutating call, that never touches the real index, HEAD, or a branch, chained through a
hidden ref (`refs/kith/checkpoint`) so `git gc` can never collect one.

Restoring is a person's decision. There is no tool here an agent calls.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace
from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import checkpoints, conversations


@pytest.fixture
def home_and_project(tmp_path, monkeypatch, db):
    """His own folder, and a separate project with a git repo of its own — same shape as
    test_git_follows_the_work.py's fixture, since checkpointing lives on the same `_git`
    plumbing and the same `base_dir()` resolution."""
    home = tmp_path / "kith-home"
    home.mkdir()
    project = tmp_path / "Desktop" / "the-app"
    project.mkdir(parents=True)
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: home)
    monkeypatch.setattr("kith.settings.AGENT_DB_PATH", db)

    row = repo.projects.add_project(db, "The App", "an app", directory=str(project))
    return {"home": home, "project": project, "id": int(row["id"]), "db": db}


@pytest.fixture
def on_project(home_and_project):
    """Working on the project, inside a real turn — the state every mutating call
    actually runs under. Ensures the repo exists first, same as a real first edit would."""
    with session_context.working_on(home_and_project["id"]), session_context.a_turn():
        workspace.ensure_repo()
        yield home_and_project


def _checkpoint_ref_log(project) -> str:
    return workspace.git._git(
        "log", "--format=%H %P", workspace.checkpoints._CHECKPOINT_REF, cwd=project
    ).output


class TestACheckpointIsTakenAutomatically:
    def test_a_write_inside_a_turn_makes_one(self, on_project):
        (on_project["project"] / "a.py").write_text("x = 1\n")
        workspace.write_file("a.py", "x = 2\n")

        assert _checkpoint_ref_log(on_project["project"]).strip() != ""

    def test_the_real_index_head_and_branch_are_untouched(self, on_project):
        project = on_project["project"]
        (project / "a.py").write_text("x = 1\n")
        before_status = workspace.git._git("status", "--porcelain", cwd=project).output
        before_head = workspace.git._git("symbolic-ref", "-q", "HEAD", cwd=project).output

        workspace.write_file("a.py", "x = 2\n")

        after_status = workspace.git._git("status", "--porcelain", cwd=project).output
        after_head = workspace.git._git("symbolic-ref", "-q", "HEAD", cwd=project).output
        assert after_status == before_status
        assert after_head == before_head
        # And no real commit exists — HEAD has nothing to point at yet.
        assert workspace.git._git("log", cwd=project).exit_code != 0

    def test_several_mutations_in_one_turn_still_make_only_one(self, on_project):
        project = on_project["project"]
        workspace.write_file("a.py", "x = 1\n")
        workspace.write_file("b.py", "y = 2\n")
        workspace.edit_file("a.py", "x = 1", "x = 11")

        log = _checkpoint_ref_log(project).strip().splitlines()
        assert len(log) == 1

    def test_edit_files_batches_to_one_checkpoint_for_the_whole_batch(self, on_project):
        project = on_project["project"]
        (project / "a.py").write_text("x = 1\n")
        (project / "b.py").write_text("y = 2\n")
        workspace.ensure_repo()

        workspace.edit_files(
            [
                {"path": "a.py", "old": "x = 1", "new": "x = 11"},
                {"path": "b.py", "old": "y = 2", "new": "y = 22"},
            ]
        )

        log = _checkpoint_ref_log(project).strip().splitlines()
        assert len(log) == 1

    def test_a_shell_command_inside_a_turn_also_triggers_one(self, on_project):
        project = on_project["project"]
        workspace.run_command(f"echo hi > {project / 'c.txt'}")

        assert _checkpoint_ref_log(project).strip() != ""

    def test_two_turns_chain_through_the_parent_link(self, home_and_project):
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            with session_context.a_turn():
                workspace.write_file("a.py", "x = 1\n")
            with session_context.a_turn():
                workspace.write_file("a.py", "x = 2\n")

        log = _checkpoint_ref_log(project).strip().splitlines()
        assert len(log) == 2
        # The oldest is a root commit — no parent, so its line has only one token.
        _newest, parent_of_newest = log[0].split()
        oldest = log[1].split()[0]
        assert parent_of_newest == oldest

    def test_a_turn_with_no_real_change_makes_no_new_checkpoint(self, home_and_project):
        """Not two identical writes — a checkpoint always precedes its own write, so two
        writes of the same content in separate turns legitimately produce two different
        "before" states (empty, then "x = 1"). The no-op case is a turn whose mutating
        call touches nothing: `ls` twice in a row leaves the tree exactly as the first
        `ls`'s checkpoint already recorded it."""
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            with session_context.a_turn():
                workspace.write_file("a.py", "x = 1\n")
            with session_context.a_turn():
                workspace.run_command("ls")
            with session_context.a_turn():
                workspace.run_command("ls")

        log = _checkpoint_ref_log(project).strip().splitlines()
        assert len(log) == 2

    def test_outside_a_turn_nothing_is_checkpointed_at_all(self, home_and_project):
        """The regression guard for `in_turn()` — every test file that calls these
        functions with no turn wrapper depends on this staying true."""
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
            workspace.write_file("a.py", "x = 1\n")

        assert (
            workspace.git._git(
                "rev-parse",
                "--verify",
                "-q",
                workspace.checkpoints._CHECKPOINT_REF,
                cwd=home_and_project["project"],
            ).exit_code
            != 0
        )
        assert repo.checkpoints.list_for_conversation(home_and_project["db"], "") == []

    def test_a_machine_with_no_git_is_a_clean_no_op(self, on_project, monkeypatch):
        monkeypatch.setattr(workspace.checkpoints, "has_git", lambda: False)
        (on_project["project"] / "a.py").write_text("x = 1\n")
        workspace.write_file("a.py", "x = 2\n")  # must not raise

    def test_it_is_recorded_against_the_conversation_bound_to_the_session(self, home_and_project, db):
        conversation = conversations.start(db, "hi")
        with (
            session_context.working_in(conversation["id"]),
            session_context.working_on(home_and_project["id"]),
            session_context.a_turn(),
        ):
            workspace.write_file("a.py", "x = 1\n")

        rows = repo.checkpoints.list_for_conversation(db, conversation["id"])
        assert len(rows) == 1
        assert rows[0]["repo_root"] == str(home_and_project["project"])


class TestRestoring:
    def test_it_reverts_an_edited_file_and_deletes_one_created_after(self, on_project):
        project = on_project["project"]
        (project / "a.py").write_text("original\n")
        workspace.write_file("a.py", "changed\n")
        rows = _all_checkpoints(on_project["db"])
        checkpoint = rows[0]

        workspace.write_file("b.py", "new file\n")

        checkpoints.restore(on_project["db"], checkpoint["id"])

        assert (project / "a.py").read_text() == "original\n"
        assert not (project / "b.py").exists()

    def test_restoring_takes_a_safety_checkpoint_that_itself_restores_cleanly(self, home_and_project):
        project = home_and_project["project"]
        db = home_and_project["db"]
        (project / "a.py").write_text("zero\n")
        with session_context.working_on(home_and_project["id"]):
            with session_context.a_turn():
                workspace.write_file("a.py", "first\n")
            with session_context.a_turn():
                # This turn's checkpoint captures "first" — the previous turn's own dedup
                # guard only covers its own turn, so a fresh turn always gets its own.
                workspace.write_file("a.py", "second\n")
        checkpoint_for_first = _all_checkpoints(db)[-1]

        result = checkpoints.restore(db, checkpoint_for_first["id"])
        assert result["safetyCheckpointId"] is not None
        assert (project / "a.py").read_text() == "first\n"

        checkpoints.restore(db, result["safetyCheckpointId"])
        assert (project / "a.py").read_text() == "second\n"

    def test_it_targets_the_checkpoints_own_repo_root_not_the_current_base_dir(self, home_and_project, db):
        project_a = home_and_project["project"]
        (project_a / "a.py").write_text("zero\n")
        with session_context.working_on(home_and_project["id"]), session_context.a_turn():
            # The checkpoint taken right before this — it captures "zero".
            workspace.write_file("a.py", "first\n")
        checkpoint = _all_checkpoints(db)[-1]

        with session_context.working_on(home_and_project["id"]), session_context.a_turn():
            workspace.write_file("a.py", "second\n")

        # Switch away entirely — base_dir() now resolves to his own folder, not project_a.
        other = repo.projects.add_project(db, "Elsewhere")
        with session_context.working_on(int(other["id"])):
            checkpoints.restore(db, checkpoint["id"])

        assert (project_a / "a.py").read_text() == "zero\n"

    def test_it_refuses_cleanly_mid_merge(self, on_project):
        project = on_project["project"]
        workspace.write_file("a.py", "first\n")
        checkpoint = _all_checkpoints(on_project["db"])[0]
        (workspace.git._repo_root(project) / ".git" / "MERGE_HEAD").write_text("deadbeef\n")

        with pytest.raises(workspace.WorkspaceError, match="merge"):
            checkpoints.restore(on_project["db"], checkpoint["id"])

    def test_a_machine_with_no_git_raises_rather_than_pretending_to_restore(self, on_project, monkeypatch):
        workspace.write_file("a.py", "first\n")
        checkpoint = _all_checkpoints(on_project["db"])[0]
        monkeypatch.setattr(workspace.checkpoints, "has_git", lambda: False)

        with pytest.raises(workspace.WorkspaceError):
            checkpoints.restore(on_project["db"], checkpoint["id"])


class TestTurnIndexCorrelation:
    def test_a_checkpoints_turn_index_matches_the_turn_it_happened_during(self, home_and_project, db):
        conversation = conversations.start(db, "first")
        conversations.record(db, conversation["id"], "user", "make a file")

        with (
            session_context.working_in(conversation["id"]),
            session_context.working_on(home_and_project["id"]),
            session_context.a_turn(),
        ):
            workspace.write_file("a.py", "x = 1\n")

        conversations.record(db, conversation["id"], "user", "make another")

        with (
            session_context.working_in(conversation["id"]),
            session_context.working_on(home_and_project["id"]),
            session_context.a_turn(),
        ):
            workspace.write_file("b.py", "y = 2\n")

        listed = checkpoints.for_conversation(db, conversation["id"])
        assert [row["turnIndex"] for row in listed] == [0, 1]

    def test_the_pure_correlation_function_picks_the_latest_turn_at_or_before(self):
        turn_ats = ["2030-01-01T00:00:00", "2030-01-01T00:00:05", "2030-01-01T00:00:10"]
        assert checkpoints._turn_index_at_or_before(turn_ats, "2030-01-01T00:00:00") == 0
        assert checkpoints._turn_index_at_or_before(turn_ats, "2030-01-01T00:00:07") == 1
        assert checkpoints._turn_index_at_or_before(turn_ats, "2030-01-01T00:00:20") == 2
        assert checkpoints._turn_index_at_or_before(turn_ats, "2029-12-31T00:00:00") is None


def _all_checkpoints(db) -> list[dict]:
    """Every checkpoint row, across conversations — for tests that don't bind a
    conversation and only care that exactly one/the-first checkpoint exists."""
    with_conv = repo.checkpoints.list_for_conversation(db, "")
    if with_conv:
        return with_conv
    # `_checkpoint_before_change` records `conversation_id=None` when nothing is bound
    # (the ordinary case in most of this file) — read those rows directly.
    from sqlalchemy import select

    from kith.infra.db.engine import as_dict, session
    from kith.infra.db.models import Checkpoint

    with session(db) as conn:
        rows = conn.scalars(select(Checkpoint).order_by(Checkpoint.id.asc())).all()
        return [as_dict(row) for row in rows]
