"""A worker that can change files must not be able to change *yours*.

The whole case for letting a sub-agent write is that it writes somewhere else. Take that away
and a builder is a second agent editing the codebase in the dark, which is the thing
`tools/workers` has refused from the beginning — the main agent holds the plan,
and it cannot hold a plan for a tree that changed underneath it.

The isolation is one context variable, which makes it cheap and makes it fragile in a specific
way: nothing at the call site *looks* like isolation. A future edit that resolves a path before
entering `working_from`, or that reads `root()` instead of `base_dir()`, silently puts a
builder back in the real tree and no test of the builder's behaviour would notice. So these
test the seam itself, at the two places that read it.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from kith.infra import permissions
from kith.infra.workspace import paths, worktrees
from kith.kernel import session_context


class TestThePinnedContext:
    def test_nothing_is_pinned_by_default(self):
        """The honest answer everywhere that is not a worker."""
        assert session_context.isolated_base() == ""

    def test_a_pinned_context_decides_where_paths_land(self, tmp_path: Path):
        copy = tmp_path / "copy"
        copy.mkdir()
        with session_context.working_from(str(copy)):
            assert paths.base_dir() == copy
            # The point of pinning `base_dir` rather than patching a tool: every relative path
            # in the application goes through `resolve`, so all of them move at once.
            assert paths.resolve("src/thing.py") == str(copy / "src" / "thing.py")

    def test_the_pin_is_lifted_afterwards(self, tmp_path: Path):
        """A worker that leaked its folder would redirect the turn that sent it."""
        before = paths.base_dir()
        with session_context.working_from(str(tmp_path)):
            pass
        assert paths.base_dir() == before
        assert session_context.isolated_base() == ""

    def test_pinning_to_nothing_is_a_no_op(self):
        """A scout has no copy, and the caller should not have to branch on that."""
        before = paths.base_dir()
        with session_context.working_from(""):
            assert paths.base_dir() == before
        with session_context.working_from(None):
            assert paths.base_dir() == before

    def test_the_pin_beats_a_linked_project(self, tmp_path: Path, monkeypatch):
        """The branch order matters, and this is the test that pins it down.

        A builder is on the same project as the turn that sent it, so every lookup in
        `base_dir` resolves to the project's real folder — the one folder it must not write in.
        Checking the pin first is what makes isolation work at all.
        """
        real = tmp_path / "project"
        real.mkdir()
        copy = tmp_path / "copy"
        copy.mkdir()
        monkeypatch.setattr(paths, "root", lambda: real)

        assert paths.base_dir() == real
        with session_context.working_from(str(copy)):
            assert paths.base_dir() == copy


class TestTheCopyIsAFreeZone:
    def test_a_write_inside_the_pinned_copy_needs_no_permission(self, tmp_path: Path, monkeypatch):
        """Otherwise the prompt is put to an empty room.

        A worker has no `ask` and no `reach_out` by construction, and nobody is watching a
        scratchpad. In ask-mode, a permission request raised inside a worker would block the
        turn that sent it on a tool call that never returns.
        """
        monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)
        elsewhere = tmp_path / "workspace"
        elsewhere.mkdir()
        copy = tmp_path / "copy"
        copy.mkdir()

        with session_context.working_from(str(copy)):
            verdict = permissions.check_path("write", copy / "src" / "new.py", elsewhere)
        assert verdict.allowed

    def test_the_grant_does_not_reach_outside_the_copy(self, tmp_path: Path, monkeypatch):
        """One folder, not a switch that turns the gate off while a worker is running."""
        monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)
        elsewhere = tmp_path / "workspace"
        elsewhere.mkdir()
        copy = tmp_path / "copy"
        copy.mkdir()
        outside = tmp_path / "somebody-elses"
        outside.mkdir()

        with session_context.working_from(str(copy)):
            verdict = permissions.check_path("write", outside / "theirs.py", elsewhere)
        assert not verdict.allowed

    def test_the_grant_ends_with_the_pin(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)
        elsewhere = tmp_path / "workspace"
        elsewhere.mkdir()
        copy = tmp_path / "copy"
        copy.mkdir()

        verdict = permissions.check_path("write", copy / "new.py", elsewhere)
        assert not verdict.allowed


def _repo(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=where, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=where, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=where, check=True)
    (where / "kept.py").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=where, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=where, check=True)
    return where


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    made = _repo(tmp_path / "project")
    monkeypatch.setattr(paths, "root", lambda: made)
    return made


class TestTheCopyItself:
    def test_a_copy_has_the_files_the_original_has(self, repo: Path):
        copy = worktrees.open_for("w1")
        assert copy is not None
        assert (copy / "kept.py").read_text() == "original\n"

    def test_a_copy_carries_uncommitted_work(self, repo: Path):
        """Seeded from the working tree, not from HEAD.

        A builder that saw the last commit would be reading code nobody has, and its patch
        would be against a tree that no longer exists — so it would not apply.
        """
        (repo / "kept.py").write_text("edited but not committed\n")
        copy = worktrees.open_for("w2")
        assert copy is not None
        assert (copy / "kept.py").read_text() == "edited but not committed\n"

    def test_changing_the_copy_does_not_change_the_original(self, repo: Path):
        copy = worktrees.open_for("w3")
        assert copy is not None
        (copy / "kept.py").write_text("the builder's version\n")
        (copy / "added.py").write_text("brand new\n")

        assert (repo / "kept.py").read_text() == "original\n"
        assert not (repo / "added.py").exists()

    def test_the_patch_includes_files_the_builder_created(self, repo: Path):
        """The one that needs staging, and the reason `diff_in` stages at all.

        Most of what a builder does is write new files, and an untracked file appears in no
        diff. Staging in the worktree's own index is what makes them visible — and is safe
        precisely because that index is not the repository's.
        """
        copy = worktrees.open_for("w4")
        assert copy is not None
        (copy / "added.py").write_text("brand new\n")

        patch = worktrees.diff_in(copy)
        assert "added.py" in patch
        assert "brand new" in patch

    def test_staging_in_the_copy_leaves_the_real_index_alone(self, repo: Path):
        """The load-bearing git detail, asserted rather than trusted.

        `diff_in` runs `git add -A`. If a linked worktree shared the main index, that would
        stage the person's uncommitted work into a commit they were deliberately building.
        """
        (repo / "kept.py").write_text("their uncommitted edit\n")
        copy = worktrees.open_for("w5")
        assert copy is not None
        worktrees.diff_in(copy)

        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert staged.stdout.strip() == ""

    def test_a_copy_that_changed_nothing_has_an_empty_patch(self, repo: Path):
        copy = worktrees.open_for("w6")
        assert copy is not None
        assert worktrees.diff_in(copy).strip() == ""

    def test_resuming_hands_back_the_same_copy_with_its_edits(self, repo: Path):
        """What makes a builder worth following up: its real state is on disk, not in a transcript."""
        first = worktrees.open_for("w7")
        assert first is not None
        (first / "half-done.py").write_text("part one\n")

        again = worktrees.open_for("w7")
        assert again == first
        assert (again / "half-done.py").read_text() == "part one\n"

    def test_closing_takes_the_copy_away(self, repo: Path):
        copy = worktrees.open_for("w8")
        assert copy is not None
        (copy / "scratch.py").write_text("x\n")
        worktrees.close(copy)
        assert not copy.exists()

    def test_the_same_id_can_be_used_again_after_closing(self, repo: Path):
        """Without `worktree prune`, git still believes the old checkout exists and refuses."""
        first = worktrees.open_for("w9")
        assert first is not None
        worktrees.close(first)
        again = worktrees.open_for("w9")
        assert again is not None
        assert again.exists()

    def test_copies_live_outside_the_repository_they_copy(self, repo: Path):
        """The one that caught the original placement.

        Copies used to live under the workspace root, which is outside the project — right up
        until the workspace root *is* the repository, which `ensure_repo` will make it. Then
        every copy is a full checkout nested in the tree it copied: walked by `glob`, matched
        by `grep`, and listed by `git status`, since only `.kith/scratch/` is ignored.
        """
        copy = worktrees.open_for("w10")
        assert copy is not None
        assert repo not in copy.parents
        assert repo != copy.parent

    def test_there_is_no_copy_without_a_repository(self, tmp_path: Path, monkeypatch):
        """None rather than an exception: it is a condition, not a fault."""
        bare = tmp_path / "not-a-repo"
        bare.mkdir()
        monkeypatch.setattr(paths, "root", lambda: bare)
        assert worktrees.open_for("w11") is None


def _build(db: Path, objective: str) -> dict:
    """One builder, the way a workflow sends one: its own copy, then the agent run in it."""
    from kith.tools import workers

    copy = workers.open_copy(f"test-{uuid.uuid4().hex[:6]}")
    answer, _report = workers.run_agent(db, "builder", objective, "", worktree=copy)
    return answer


class TestABuilderEndToEnd:
    """The whole stack at once, against a real repository and a real `git worktree`.

    Every test above checks one seam. This one exists because the seams are the kind that can
    each be right while the thing they add up to is wrong: the pin is set, the permission is
    granted, the copy is made — and a builder still edits the real tree because something
    resolved a path one frame too early. The only way to know is to let one run and then look
    at the folder it was supposed not to touch.
    """

    def test_it_edits_its_copy_and_hands_back_a_patch(self, repo: Path, db: Path, monkeypatch):
        from kith.services import agent_loop

        rounds: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            rounds.append(1)
            if len(rounds) == 1:
                call = {
                    "function": {
                        "name": "write_file",
                        "arguments": '{"path": "kept.py", "content": "the builder version\\n"}',
                    }
                }
                yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}
                return
            yield {"type": "delta", "role": "text", "text": "Rewrote kept.py."}
            yield {"type": "turn", "content": "Rewrote kept.py.", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = _build(db, "rewrite kept.py")

        # It did the work, and the work is in the patch.
        assert "kept.py" in answer["patch"]
        assert "the builder version" in answer["patch"]
        assert answer["findings"] == "Rewrote kept.py."

        # And the real tree is exactly as it was. This is the assertion the whole design is for.
        assert (repo / "kept.py").read_text() == "original\n"

    def test_a_builder_that_changed_nothing_says_so_rather_than_failing(
        self, repo: Path, db: Path, monkeypatch
    ):
        """An empty patch is a real answer — and is also what a builder that only *described*
        its edits produces, so the note points at the one thing that tells them apart."""
        from kith.services import agent_loop

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            yield {"type": "delta", "role": "text", "text": "Nothing needed changing."}
            yield {"type": "turn", "content": "Nothing needed changing.", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = _build(db, "fix the thing")
        assert answer["patch"] == ""
        assert "changed nothing" in answer["note"]

    def test_there_is_no_builder_without_a_repository(self, tmp_path: Path, db: Path, monkeypatch):
        """Refused, not run unisolated. "He could not be isolated, so he edited your files
        instead" is the one outcome this design exists to prevent."""

        bare = tmp_path / "not-a-repo"
        bare.mkdir()
        monkeypatch.setattr(paths, "root", lambda: bare)

        from kith.tools import workers

        with pytest.raises(workers.WorkerError, match="nowhere safe to work"):
            workers.open_copy("no-repo")


class TestAnAbsolutePathCannotEscapeTheCopy:
    """The hole that shipped, and the one that matters most.

    Every test above pins a context and checks a *relative* path lands in the copy. That was
    the whole of the isolation, and it was not enough for a reason nothing in the design
    predicted: the agent that sends a builder writes the objective, the brief tells it to
    include the paths it has, and the paths it has are absolute. The worker did as it was told,
    `resolve` honoured the absolute path as written, and two builders put 224 lines into the
    real repository while their copies sat empty. In bypass mode the permission layer never
    looked either.
    """

    def test_an_absolute_path_into_the_original_lands_in_the_copy(self, tmp_path: Path):
        real = tmp_path / "project"
        (real / "app").mkdir(parents=True)
        copy = tmp_path / "copy"
        (copy / "app").mkdir(parents=True)

        with session_context.working_from(str(copy), mirror_of=str(real)):
            landed = paths.resolve(str(real / "app" / "store.py"))
        assert landed == str(copy / "app" / "store.py")

    def test_the_original_itself_resolves_to_the_copy(self, tmp_path: Path):
        real = tmp_path / "project"
        real.mkdir()
        copy = tmp_path / "copy"
        copy.mkdir()
        with session_context.working_from(str(copy), mirror_of=str(real)):
            assert paths.resolve(str(real)) == str(copy)

    def test_case_does_not_decide_it(self, tmp_path: Path):
        """macOS is case-insensitive, and the real objective said `Desktop/Personal` while the
        project is reached as `desktop/personal` all day. A case-sensitive prefix test passes
        every suite and fails on the only machine this runs on."""
        real = tmp_path / "Project"
        (real / "app").mkdir(parents=True)
        copy = tmp_path / "copy"
        copy.mkdir()

        said = str(tmp_path / "project" / "app" / "store.py")
        with session_context.working_from(str(copy), mirror_of=str(real)):
            landed = paths.resolve(said)
        assert landed == str(copy / "app" / "store.py")

    def test_a_path_outside_the_original_is_left_alone(self, tmp_path: Path):
        """A worker reading ~/Downloads/spec.pdf is doing something legitimate that has
        nothing to do with its copy."""
        real = tmp_path / "project"
        real.mkdir()
        copy = tmp_path / "copy"
        copy.mkdir()
        elsewhere = tmp_path / "downloads" / "spec.pdf"
        elsewhere.parent.mkdir()

        with session_context.working_from(str(copy), mirror_of=str(real)):
            assert paths.resolve(str(elsewhere)) == str(elsewhere)

    def test_nothing_is_bent_when_no_copy_is_pinned(self, tmp_path: Path):
        somewhere = tmp_path / "a" / "b.py"
        somewhere.parent.mkdir()
        assert paths.resolve(str(somewhere)) == str(somewhere)

    def test_an_absolute_write_is_refused_outside_the_copy_and_allowed_inside(
        self, tmp_path: Path, monkeypatch
    ):
        """Both halves in one place: the rewrite lands it in the copy, and the copy is granted."""
        monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)
        real = tmp_path / "project"
        (real / "app").mkdir(parents=True)
        copy = tmp_path / "copy"
        (copy / "app").mkdir(parents=True)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with session_context.working_from(str(copy), mirror_of=str(real)):
            landed = Path(paths.resolve(str(real / "app" / "store.py")))
            assert permissions.check_path("write", landed, workspace).allowed
        assert not permissions.check_path("write", real / "app" / "store.py", workspace).allowed


class TestABuilderEndToEndWithAnAbsoluteObjective:
    def test_it_still_only_touches_its_copy(self, repo: Path, db: Path, monkeypatch):
        """The original failure, reproduced: the worker is handed the project's absolute path
        exactly as the real objective handed it one."""
        from kith.services import agent_loop

        rounds: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            rounds.append(1)
            if len(rounds) == 1:
                call = {
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": str(repo / "kept.py"), "content": "builder wrote this\n"}
                        ),
                    }
                }
                yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}
                return
            yield {"type": "delta", "role": "text", "text": "Rewrote kept.py."}
            yield {"type": "turn", "content": "Rewrote kept.py.", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = _build(db, f"rewrite kept.py in the repo at {repo}")

        assert "builder wrote this" in answer["patch"]
        assert (repo / "kept.py").read_text() == "original\n"


class TestTwoBuildersInOneRound:
    """Making a copy is not the read-only operation it looks like.

    A workflow runs builders in parallel precisely so several can work at once — and the
    setup for that was racing against itself. `git stash create` refreshes the *source*
    repository's index and takes `.git/index.lock`; `worktree add` then writes the shared
    `.git/worktrees` registry. Two at once, and one dies with "could not write index" before it
    reads a single file.

    Observed in use on 2026-09-15 rather than reasoned about: builder 2 won, builder 1 died at
    startup, and the agent that sent them had to diagnose git lock files to work out why.
    """

    def test_several_copies_opened_at_once_all_succeed(self, repo: Path):
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            made = list(pool.map(worktrees.open_for, [f"race{n}" for n in range(6)]))

        assert all(one is not None for one in made)
        assert len({str(one) for one in made}) == 6
        for one in made:
            assert (one / "kept.py").read_text() == "original\n"

    def test_they_do_not_share_a_working_tree(self, repo: Path):
        """The claim parallel builders rest on: two builders share an object store and nothing
        that either can change."""
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(worktrees.open_for, ["a", "b"]))

        assert first is not None and second is not None
        (first / "kept.py").write_text("first builder\n")
        (second / "kept.py").write_text("second builder\n")

        assert (first / "kept.py").read_text() == "first builder\n"
        assert (second / "kept.py").read_text() == "second builder\n"
        assert (repo / "kept.py").read_text() == "original\n"

    def test_asking_twice_for_one_id_concurrently_gives_one_copy(self, repo: Path):
        """The check-then-create above the lock is not enough on its own: two threads can both
        find it missing, and the second would ask git to register a worktree that now exists."""
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            both = list(pool.map(worktrees.open_for, ["same", "same"]))

        assert both[0] == both[1]
        assert both[0] is not None and both[0].exists()


class TestTheWorkerCanReadTheNotesItWasBriefedOn:
    """`.kith` is the one directory a worker always needs and git will never carry.

    Found on the first real use. The agent wrote a convention file to `.kith/work/`, told the
    builder to read it, and the builder spent its entire round budget hunting for a file that
    was not in its copy — because `.kith/` is gitignored by convention, so neither `stash
    create` nor `worktree add` carries it. It produced no patch at all and diagnosed the cause
    itself from `.gitignore`.

    This is not the general untracked-files gap. It is the specific case where the *main agent's
    own working memory* — the brief, the plan, the task state it just wrote — is invisible to
    the worker it wrote them for.
    """

    def test_the_notes_are_in_the_copy(self, repo: Path):
        (repo / ".kith" / "work").mkdir(parents=True)
        (repo / ".kith" / "work" / "CONVENTION.md").write_text("write it like build_embed_text\n")

        copy = worktrees.open_for("notes")
        assert copy is not None
        assert (copy / ".kith" / "work" / "CONVENTION.md").read_text() == "write it like build_embed_text\n"

    def test_they_are_carried_even_when_git_ignores_them(self, repo: Path):
        """The case that actually happened — and the reason this cannot be left to git."""
        (repo / ".gitignore").write_text(".kith/\n")
        (repo / ".kith").mkdir()
        (repo / ".kith" / "plan.md").write_text("the plan\n")

        copy = worktrees.open_for("ignored")
        assert copy is not None
        assert (copy / ".kith" / "plan.md").read_text() == "the plan\n"

    def test_scratch_is_carried_but_not_its_runs_or_its_big_files(self, repo: Path):
        """Scratch is where the check scripts a worker is told to copy live — a workflow sent a
        builder to "follow .kith/scratch/check_surface.py" and it searched its whole budget for a
        file its copy did not have. A workflow's own run folder and generated data stay out."""
        scratch = repo / ".kith" / "scratch"
        (scratch / "workflows" / "wf-1").mkdir(parents=True)
        (scratch / "workflows" / "wf-1" / "a.patch").write_text("x")
        (scratch / "check_surface.py").write_text("print('check')\n")
        (scratch / "huge.json").write_text("x" * (worktrees._LARGEST_NOTE + 1))

        copy = worktrees.open_for("scratch")
        assert copy is not None
        assert (copy / ".kith" / "scratch" / "check_surface.py").read_text() == "print('check')\n"
        assert not (copy / ".kith" / "scratch" / "workflows").exists()
        assert not (copy / ".kith" / "scratch" / "huge.json").exists()

    def test_writing_to_the_notes_does_not_reach_the_real_ones(self, repo: Path):
        """Copied, not symlinked: `.kith` is the main agent's own memory and a worker must not
        be able to edit it."""
        (repo / ".kith").mkdir()
        (repo / ".kith" / "plan.md").write_text("the real plan\n")

        copy = worktrees.open_for("nowrite")
        assert copy is not None
        (copy / ".kith" / "plan.md").write_text("the worker's idea\n")

        assert (repo / ".kith" / "plan.md").read_text() == "the real plan\n"

    def test_the_notes_never_come_back_as_a_patch(self, repo: Path):
        """They were copied in, not checked out, so they are not the worker's work. True even
        where a project tracks `.kith` instead of ignoring it."""
        (repo / ".kith").mkdir()
        (repo / ".kith" / "plan.md").write_text("the plan\n")

        copy = worktrees.open_for("nopatch")
        assert copy is not None
        (copy / "kept.py").write_text("real work\n")

        patch = worktrees.diff_in(copy)
        assert "real work" in patch
        assert ".kith" not in patch

    def test_what_the_worker_writes_under_the_notes_does_come_back(self, repo: Path):
        """The other half of the rule above, and the half that was missing. Carried notes stay
        out of the patch; a design mockup or a check script the worker was asked to write there
        comes back — tracked `.kith/screens/` and gitignored `.kith/scratch/` alike."""
        (repo / ".kith" / "screens").mkdir(parents=True)
        (repo / ".kith" / "screens" / "old.html").write_text("<p>old</p>\n")
        (repo / ".kith" / ".gitignore").write_text("scratch/\n")
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "screens"], cwd=repo, check=True)
        (repo / ".kith" / "plan.md").write_text("an uncommitted note\n")

        copy = worktrees.open_for("writes-notes")
        assert copy is not None
        (copy / ".kith" / "screens" / "devices.html").write_text("<p>new screen</p>\n")
        (copy / ".kith" / "scratch").mkdir(exist_ok=True)
        (copy / ".kith" / "scratch" / "check_devices.py").write_text("print('ok')\n")

        patch = worktrees.diff_in(copy)
        assert ".kith/screens/devices.html" in patch
        assert ".kith/scratch/check_devices.py" in patch
        assert "plan.md" not in patch, "a note carried in is not the worker's work"
        assert "old.html" not in patch

    def test_earlier_patches_can_be_applied_into_a_copy_and_stay_separate(self, repo: Path):
        """A review or an integration step has to see what earlier builders made. Applied into
        its copy and re-baselined, so its own patch is only its own work and the two stack."""
        first = worktrees.open_for("first")
        assert first is not None
        (first / "added.py").write_text("from the first builder\n")
        earlier = worktrees.diff_in(first)

        second = worktrees.open_for("second")
        assert second is not None
        assert worktrees.apply_in(second, [earlier]) == ""
        assert (second / "added.py").read_text() == "from the first builder\n"
        (second / "kept.py").write_text("from the second builder\n")
        mine = worktrees.diff_in(second)
        assert "from the second builder" in mine
        assert "added.py" not in mine

        # And they stack on the real tree, in order.
        for patch in (earlier, mine):
            holder = repo.parent / "p.patch"
            holder.write_text(patch)
            subprocess.run(["git", "apply", str(holder)], cwd=repo, check=True)
        assert (repo / "added.py").read_text() == "from the first builder\n"
        assert (repo / "kept.py").read_text() == "from the second builder\n"

    def test_a_patch_that_does_not_apply_says_so(self, repo: Path):
        copy = worktrees.open_for("bad-patch")
        assert copy is not None
        broken = "--- a/nothing.py\n+++ b/nothing.py\n@@ -1 +1 @@\n-not there\n+x\n"
        assert "did not apply" in worktrees.apply_in(copy, [broken])

    def test_a_repo_with_no_notes_is_fine(self, repo: Path):
        copy = worktrees.open_for("nonotes")
        assert copy is not None
        assert (copy / "kept.py").exists()

    def test_build_output_under_the_notes_is_left_behind(self, repo: Path):
        """`.kith` is not a folder of text.

        He builds things in `.kith/work/`, and a thing he built has a `node_modules`. Measured
        on one real project the day carrying notes shipped: 150 MB under `.kith`, of which
        146 MB was two `node_modules` — copied per builder, so a round of three cost 440 MB and
        the seconds to write it. Nothing in there is a brief.
        """
        notes = repo / ".kith" / "work" / "a-little-web-app"
        (notes / "node_modules" / "left-pad").mkdir(parents=True)
        (notes / "node_modules" / "left-pad" / "index.js").write_text("x" * 5000)
        (notes / "dist").mkdir()
        (notes / "dist" / "bundle.js").write_text("y" * 5000)
        (notes / "PLAN.md").write_text("the actual note\n")

        copy = worktrees.open_for("heavy")
        assert copy is not None
        carried = copy / ".kith" / "work" / "a-little-web-app"

        assert (carried / "PLAN.md").read_text() == "the actual note\n"
        assert not (carried / "node_modules").exists()
        assert not (carried / "dist").exists()

    def test_a_patch_survives_the_notes_being_gitignored(self, repo: Path):
        """The bug that made three builders in a row look like they had done nothing.

        `_carry_notes` copies `.kith` in, and `.kith` is gitignored in most projects. The staging
        step named `.` explicitly, so git refused the ignored path that matched it — printing
        "the following paths are ignored", exiting 1 — and `diff_in` returned "" on that exit
        code. Every builder reported "changed nothing in its copy" while its edits sat staged.

        Two fixes in one test, because either alone would leave it broken: the add takes no
        pathspec (ignored paths are skipped silently), and a non-zero exit is no longer read as
        "there is nothing here".
        """
        (repo / ".gitignore").write_text(".kith/\n")
        (repo / ".kith").mkdir()
        (repo / ".kith" / "CONVENTION.md").write_text("hold to this\n")
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "ignore the notes"], cwd=repo, check=True)

        copy = worktrees.open_for("ignored-notes")
        assert copy is not None
        # The worker can read its brief...
        assert (copy / ".kith" / "CONVENTION.md").read_text() == "hold to this\n"
        # ...and its work still comes back.
        (copy / "kept.py").write_text("the builder did this\n")

        patch = worktrees.diff_in(copy)
        assert "the builder did this" in patch
        assert ".kith" not in patch
