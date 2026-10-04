"""A workflow agent's copy is of the folder the work is in, keeps what it writes, and can run it.

Every agent that worked in a copy on 2026-09-30 was handed the wrong tree, and each way it was
wrong is a test here:

* **A project that is a folder of a bigger repository.** Every project made under the workspace
  is one, and the workspace ignores `.kith/` at every depth. Only the root's `.kith` was carried
  in and forced into the patch, so four design builders started without their brief, then — once
  it was committed for them — wrote 54 screens into `<project>/.kith/screens/` and came back with
  empty patches. The run failed on the empty merge and deleted the copies holding the screens.
* **A repository nested inside the workspace.** A verifier sent to check one got a copy of the
  workspace instead, with no trace of the code it was sent for.
* **No way to run anything.** The builders could not run the generator they wrote or the skill's
  own design check, and five audit agents told to demonstrate their findings could not.

Confinement is tested against folders under the real home directory, because a temporary
directory lives under `/private/var`, which the boundary leaves writable for temp files.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from kith import settings
from kith.infra import confinement, permissions
from kith.infra.workspace import paths, worktrees
from kith.infra.workspace.shell import run_command
from kith.kernel import session_context
from kith.services import workflows
from kith.tools import workers, workflow

needs_confinement = pytest.mark.skipif(not confinement.available(), reason="sandbox-exec is macOS-only")


def _id(name: str) -> str:
    """A worker id no other test has used. The suite shares one data folder, so a copy named
    `p1` by an earlier test is still there, and `open_for` hands back an existing copy as is."""
    return f"{name}-{uuid.uuid4().hex[:6]}"


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=where, check=True, capture_output=True)


def _repo(where: Path, ignore: str = "") -> Path:
    where.mkdir(parents=True, exist_ok=True)
    _git(where, "init", "-q")
    _git(where, "config", "user.email", "t@t")
    _git(where, "config", "user.name", "t")
    if ignore:
        (where / ".gitignore").write_text(ignore)
    (where / "kept.py").write_text("original\n")
    _git(where, "add", "-A")
    _git(where, "commit", "-qm", "first")
    return where


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    """The workspace as it is on the machine this broke on: one repository, `.kith/` ignored at
    every depth, a project as a folder of it with its own notes."""
    root = _repo(tmp_path / "Kith", ignore=".kith/\nnode_modules/\n")
    project = root / "finance"
    (project / ".kith" / "design").mkdir(parents=True)
    (project / ".kith" / "design" / "brief.md").write_text("54 screens\n")
    (project / "app.py").write_text("print('hi')\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "project")
    monkeypatch.setattr(paths, "root", lambda: root)
    return root


class TestAProjectInsideTheWorkspaceRepository:
    def test_its_notes_are_carried_into_the_copy(self, workspace: Path):
        copy = worktrees.open_for(_id("p1"), workspace / "finance")
        assert copy is not None
        assert (copy / "finance" / ".kith" / "design" / "brief.md").read_text() == "54 screens\n"

    def test_relative_paths_land_in_the_project_not_the_workspace(self, workspace: Path):
        copy = worktrees.open_for(_id("p2"), workspace / "finance")
        assert copy is not None
        base = worktrees.base_in(copy, workspace / "finance")
        assert base == copy / "finance"
        with session_context.working_from(str(copy), mirror_of=str(workspace), base=str(base)):
            assert Path(paths.resolve(".kith/design/brief.md")).read_text() == "54 screens\n"
            # An absolute path anywhere in the workspace is still bent into the copy's root.
            assert Path(paths.resolve(str(workspace / "kept.py"))) == copy / "kept.py"

    def test_a_whole_repository_project_is_the_copys_root(self, tmp_path: Path, monkeypatch):
        own = _repo(tmp_path / "own")
        monkeypatch.setattr(paths, "root", lambda: own)
        copy = worktrees.open_for(_id("p3"), own)
        assert copy is not None
        assert worktrees.base_in(copy, own) == copy

    def test_what_a_builder_writes_in_the_projects_notes_comes_back(self, workspace: Path):
        """The 54 screens: written under the project's ignored `.kith`, and the patch was empty."""
        copy = worktrees.open_for(_id("p4"), workspace / "finance")
        assert copy is not None
        screens = copy / "finance" / ".kith" / "screens"
        screens.mkdir(parents=True)
        (screens / "01-welcome.html").write_text("<main>welcome</main>\n")

        diff = worktrees.diff_in(copy)
        assert "finance/.kith/screens/01-welcome.html" in diff
        assert "54 screens" not in diff, "the notes it was handed are not its work"

    def test_build_output_under_the_notes_stays_out_of_the_patch(self, workspace: Path):
        copy = worktrees.open_for(_id("p5"), workspace / "finance")
        assert copy is not None
        app = copy / "finance" / ".kith" / "work" / "app"
        (app / "node_modules" / "left-pad").mkdir(parents=True)
        (app / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1\n")
        (app / "index.html").write_text("<p>app</p>\n")

        diff = worktrees.diff_in(copy)
        assert "finance/.kith/work/app/index.html" in diff
        assert "node_modules" not in diff

    def test_a_patch_from_the_copy_applies_to_the_real_project(self, workspace: Path):
        copy = worktrees.open_for(_id("p6"), workspace / "finance")
        assert copy is not None
        (copy / "finance" / ".kith" / "screens").mkdir(parents=True)
        (copy / "finance" / ".kith" / "screens" / "a.html").write_text("<p>a</p>\n")
        (copy / "finance" / "app.py").write_text("print('changed')\n")
        patch = workspace / "change.patch"
        patch.write_text(worktrees.diff_in(copy))

        done = subprocess.run(
            ["git", "apply", str(patch)], cwd=workspace / "finance", capture_output=True, text=True
        )
        assert done.returncode == 0, done.stderr
        assert (workspace / "finance" / ".kith" / "screens" / "a.html").read_text() == "<p>a</p>\n"
        assert (workspace / "finance" / "app.py").read_text() == "print('changed')\n"


class TestWhatTheProjectNeedsToRun:
    def test_its_environment_file_is_carried_but_never_patched(self, workspace: Path):
        """Without `backend/.env` a Django suite stops before collecting a test."""
        (workspace / "finance" / ".env").write_text("SECRET_KEY=s3cret\n")
        (workspace / "finance" / ".env.example").write_text("SECRET_KEY=\n")
        (workspace / ".gitignore").write_text(".kith/\nnode_modules/\n.env\n")
        _git(workspace, "add", ".gitignore", "finance/.env.example")
        _git(workspace, "commit", "-qm", "env")

        copy = worktrees.open_for(_id("e1"), workspace / "finance")
        assert copy is not None
        assert (copy / "finance" / ".env").read_text() == "SECRET_KEY=s3cret\n"
        (copy / "finance" / "app.py").write_text("print('changed')\n")
        diff = worktrees.diff_in(copy)
        assert "app.py" in diff
        assert "s3cret" not in diff

    def test_the_check_uses_the_projects_own_tools(self, tmp_path: Path, monkeypatch):
        """`ruff` lived in `backend/venv` and the copy's check said "command not found"."""
        from kith.infra.workspace import files

        backend = tmp_path / "backend"
        (backend / "venv" / "bin").mkdir(parents=True)
        (backend / "ruff.toml").write_text("")
        ruff = backend / "venv" / "bin" / "ruff"
        ruff.write_text("#!/bin/sh\necho checked-with-project-ruff >&2\nexit 3\n")
        ruff.chmod(0o755)
        monkeypatch.setattr(paths, "root", lambda: tmp_path)
        monkeypatch.setattr(files, "root", lambda: tmp_path)
        monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)

        found = files.check_code(str(backend))
        assert found["clean"] is False
        assert "checked-with-project-ruff" in found["problems"]


class TestARepositoryNestedInTheWorkspace:
    def test_the_copy_is_of_the_nested_repository(self, workspace: Path):
        nested = _repo(workspace / "reviewer")
        (nested / "action.yml").write_text("name: review\n")
        _git(nested, "add", "-A")
        _git(nested, "commit", "-qm", "action")

        copy = worktrees.open_for(_id("n1"), nested)
        assert copy is not None
        assert (copy / "action.yml").read_text() == "name: review\n"
        assert not (copy / "finance").exists(), "not a copy of the workspace around it"
        assert worktrees.source_of(copy) == nested.resolve()

    def test_closing_it_tidies_the_repository_it_came_from(self, workspace: Path):
        nested = _repo(workspace / "reviewer")
        copy = worktrees.open_for(_id("n2"), nested)
        assert copy is not None
        worktrees.close(copy)
        assert not copy.exists()
        listed = subprocess.run(
            ["git", "worktree", "list"], cwd=nested, capture_output=True, text=True
        ).stdout
        assert str(copy) not in listed


@pytest.fixture
def home_side() -> Iterator[Path]:
    """A folder under the real home directory, where people keep projects."""
    made = Path(tempfile.mkdtemp(dir=Path.home(), prefix=".kith-test-"))
    try:
        yield made
    finally:
        shutil.rmtree(made, ignore_errors=True)


@needs_confinement
class TestAShellFromACopy:
    def test_it_runs_from_the_project_writes_in_the_copy_and_nowhere_else(self, home_side: Path, monkeypatch):
        root = _repo(home_side / "Kith", ignore=".kith/\n")
        (root / "finance").mkdir()
        (root / "finance" / "app.py").write_text("print('hi')\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "project")
        monkeypatch.setattr(paths, "root", lambda: root)
        elsewhere = home_side / "elsewhere"
        elsewhere.mkdir()

        copy = worktrees.open_for(_id("s1"), root / "finance")
        assert copy is not None
        base = worktrees.base_in(copy, root / "finance")
        try:
            with session_context.working_from(str(copy), mirror_of=str(root), base=str(base)):
                done = run_command(
                    f"pwd; echo in > made.txt; rm -f app.py; "
                    f"echo out > {elsewhere}/x.txt; echo real > {root}/kept.py; true"
                )
            assert done.output.splitlines()[0] == str(base.resolve())
            assert (base / "made.txt").exists()
            assert not (base / "app.py").exists(), "a delete inside the copy needs nobody's yes"
            assert (root / "finance" / "app.py").exists()
            assert not (elsewhere / "x.txt").exists()
            assert (root / "kept.py").read_text() == "original\n"
        finally:
            worktrees.close(copy)

    def test_it_can_read_the_skills_but_not_the_rest_of_kiths_data(self, home_side: Path, monkeypatch):
        repo = _repo(home_side / "project")
        monkeypatch.setattr(paths, "root", lambda: repo)
        data = Path(settings.DATA_DIR)
        (data / "skills" / "designing").mkdir(parents=True, exist_ok=True)
        (data / "skills" / "designing" / "check.py").write_text("print('checked')\n")
        (data / "secret.db").write_text("keys\n")
        monkeypatch.setattr(settings, "skills_dir", lambda: data / "skills")

        copy = worktrees.open_for(_id("s2"), repo)
        assert copy is not None
        try:
            with session_context.working_from(str(copy), mirror_of=str(repo)):
                done = run_command(
                    f'cat "{data}/skills/designing/check.py"; echo "--"; cat "{data}/secret.db"; '
                    'echo "skills=$KITH_SKILLS_DIR"'
                )
            assert "print('checked')" in done.output
            assert "keys" not in done.output
            assert f"skills={data / 'skills'}" in done.output
        finally:
            worktrees.close(copy)

    def test_a_shot_it_leaves_in_its_scratch_is_its_to_read(self, home_side: Path, monkeypatch):
        repo = _repo(home_side / "project")
        monkeypatch.setattr(paths, "root", lambda: repo)
        copy = worktrees.open_for(_id("s3"), repo)
        assert copy is not None
        try:
            shot = confinement.scratch_for(copy) / "shots" / "home.png"
            with session_context.working_from(str(copy), mirror_of=str(repo)):
                assert permissions.check_path("read", shot, home_side / "elsewhere").allowed
        finally:
            worktrees.close(copy)


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    here = tmp_path / "project"
    here.mkdir()
    monkeypatch.setattr(paths, "base_dir", lambda: here)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    workflows.forget_all()
    yield here
    for run in list(workflows.listing()):
        workflows.stop(run["run"])
    workflows.forget_all()


class TestWhatARunCopies:
    def test_a_chat_in_a_workspace_of_projects_must_name_the_folder(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "Kith"
        (root / "finance" / ".kith").mkdir(parents=True)
        monkeypatch.setattr(paths, "root", lambda: root)
        monkeypatch.setattr(paths, "base_dir", lambda: root)

        assert workflow._workdir({}, None) == ("", "")
        run = workflows.Run(id="wf-000001", conversation="c", name="n", description="", phases=[], folder="")
        with pytest.raises(workflow._Failed, match="folder="):
            workflow._open_copy(run, "a1")

        named, refused = workflow._workdir({"folder": str(root / "finance")}, None)
        assert (named, refused) == (str(root / "finance"), "")

    def test_a_project_chat_copies_its_project(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "Kith"
        (root / "finance" / ".kith").mkdir(parents=True)
        monkeypatch.setattr(paths, "root", lambda: root)
        monkeypatch.setattr(paths, "base_dir", lambda: root / "finance")
        assert workflow._workdir({}, None) == (str(root / "finance"), "")

    def test_a_resumed_run_keeps_its_folder(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
        assert workflow._workdir({}, {"workdir": "/somewhere/else"}) == ("/somewhere/else", "")

    def test_a_folder_that_is_not_there_is_refused(self, tmp_path: Path):
        _, refused = workflow._workdir({"folder": str(tmp_path / "missing")}, None)
        assert "no folder" in refused


class _Report:
    def __init__(self, calls: list[str]) -> None:
        self.cost = 0.01
        self.cut_short = False
        self.error = ""
        self.text = "said"
        self.said_before = ""
        self.calls = calls


def _wait(run_id: str, seconds: float = 30) -> workflows.Run:
    run = workflows.get(run_id)
    assert run is not None
    deadline = time.monotonic() + seconds
    while run.state == workflows.RUNNING and time.monotonic() < deadline:
        time.sleep(0.05)
    assert run.state != workflows.RUNNING, "the run never ended"
    return run


@pytest.mark.skipif(
    not confinement.available(), reason="a workflow script needs sandbox-exec, which is macOS-only"
)
class TestABuildersEditsAreNotDeletedWithTheRun:
    def test_a_builder_that_edited_but_has_no_patch_keeps_its_copy(
        self, db: Path, project: Path, tmp_path: Path, monkeypatch
    ):
        made: dict[str, Path] = {}
        closed: list[str] = []
        lock = threading.Lock()

        def open_copy(ident, folder=None):
            where = tmp_path / "copies" / ident
            where.mkdir(parents=True)
            with lock:
                made[ident] = where
            return str(where)

        worked_in: dict[str, str] = {}

        def run_agent(path, kind, objective, conversation, **kw):
            with lock:
                worked_in[objective] = kw["worktree"]
            if "screens" in objective:
                return {"findings": "wrote 12 screens", "patch": ""}, _Report(["write_file", "shell"])
            return {"findings": "changed it", "patch": "--- a/x\n+++ b/x\n@@\n-a\n+b\n"}, _Report(
                ["edit_files"]
            )

        monkeypatch.setattr(workers, "open_copy", open_copy)
        monkeypatch.setattr(workers, "run_agent", run_agent)
        monkeypatch.setattr(worktrees, "close", lambda where: closed.append(str(where)))

        script = (
            'META = {"name": "keep"}\n'
            'a = agent("draw the screens", kind="builder", label="screens")\n'
            'b = agent("change x", kind="builder", label="x")\n'
            "finish([a['patch_file'], b['patch_file']])\n"
        )
        with session_context.working_in("conv-keep"), session_context.may_run_workflows(True):
            started = workflow.run_workflow(db, {"script": script})
        run = _wait(started["run"])

        screens, changed = worked_in["draw the screens"], worked_in["change x"]
        assert screens not in closed, "its edits are only in its copy"
        assert changed in closed, "a builder whose patch was saved needs no copy"
        assert f"its copy is kept at {screens}" in run.note
