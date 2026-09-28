"""A builder's copy has what the project needs to run, and its commands cannot reach past it.

Three things a copy was missing, each of which made a builder's own check worthless: the files
the main agent had written but not committed, the dependency folders (`tsc` in a copy resolved
no import), and any way to run a command at all. And one thing it must never gain with them: a
command from inside a copy writing outside it, or reaching the network.

Confinement is tested against a real folder under the real home directory, because a temporary
directory lives under `/private/var`, outside the deny, and would prove nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from kith.engine.run import testing
from kith.infra import confinement
from kith.infra.workspace import paths, worktrees
from kith.kernel import session_context

needs_confinement = pytest.mark.skipif(not confinement.available(), reason="sandbox-exec is macOS-only")


def _repo(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=where, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=where, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=where, check=True)
    (where / ".gitignore").write_text("node_modules/\n.venv/\n*.log\n")
    (where / "kept.py").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=where, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=where, check=True)
    return where


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    made = _repo(tmp_path / "project")
    monkeypatch.setattr(paths, "root", lambda: made)
    return made


class TestWhatACopyIsHanded:
    def test_untracked_source_files_are_carried_and_are_not_the_workers_work(self, repo: Path):
        (repo / "written_today.py").write_text("fresh\n")
        copy = Path(str(worktrees.open_for("u1")))
        assert (copy / "written_today.py").read_text() == "fresh\n"
        assert worktrees.diff_in(copy) == ""

    def test_ignored_files_are_not_carried(self, repo: Path):
        (repo / "debug.log").write_text("noise")
        copy = Path(str(worktrees.open_for("u2")))
        assert not (copy / "debug.log").exists()

    @pytest.mark.skipif(sys.platform != "darwin", reason="clonefile is APFS")
    def test_dependency_folders_are_cloned_and_stay_out_of_the_diff(self, repo: Path):
        package = repo / "web" / "node_modules" / "pkg"
        package.mkdir(parents=True)
        (package / "index.js").write_text("module.exports = 1\n")
        copy = Path(str(worktrees.open_for("d1")))
        cloned = copy / "web" / "node_modules" / "pkg" / "index.js"
        assert cloned.read_text() == "module.exports = 1\n"
        assert "node_modules" not in worktrees.diff_in(copy)
        cloned.write_text("changed in the copy\n")
        assert (package / "index.js").read_text() == "module.exports = 1\n"


class TestPuttingPatchesTogether:
    def test_each_patch_applies_on_its_own_and_a_misfit_is_named(self, repo: Path):
        one = Path(str(worktrees.open_for("p1")))
        (one / "kept.py").write_text("from builder one\n")
        two = Path(str(worktrees.open_for("p2")))
        (two / "added.py").write_text("new file\n")
        three = Path(str(worktrees.open_for("p3")))
        (three / "kept.py").write_text("from builder three\n")
        patches = [worktrees.diff_in(one), worktrees.diff_in(two), worktrees.diff_in(three)]

        merged = Path(str(worktrees.open_for("m1")))
        outcome = worktrees.merge_in(merged, patches)
        assert outcome[0] == "" and outcome[1] == ""
        assert outcome[2], "the third patch changes a line the first already changed"
        diff = worktrees.diff_in(merged)
        assert "from builder one" in diff and "added.py" in diff
        assert "from builder three" not in diff


@needs_confinement
class TestCommandsFromACopyAreConfined:
    def test_nothing_is_wrapped_outside_a_copy(self):
        assert confinement.in_copy(["true"]) is None

    def test_it_writes_in_its_copy_and_nowhere_else(self, tmp_path: Path, monkeypatch):
        # The project under the real home folder, where people keep projects: a temporary
        # directory sits under /private/var, which the boundary leaves writable for temp files.
        home_side = Path(tempfile.mkdtemp(dir=Path.home(), prefix=".kith-test-"))
        try:
            real = _repo(home_side / "project")
            monkeypatch.setattr(paths, "root", lambda: real)
            copy = Path(str(worktrees.open_for("c1")))
            elsewhere = home_side / "elsewhere"
            elsewhere.mkdir()
            with session_context.working_from(str(copy), mirror_of=str(real)):
                boxed = confinement.in_copy(
                    [
                        "bash",
                        "-c",
                        f"echo in > inside.txt; echo out > {elsewhere}/x.txt; echo real > {real}/kept.py",
                    ]
                )
            assert boxed is not None
            argv, env = boxed
            subprocess.run(argv, cwd=copy, env=env, capture_output=True, timeout=30)
            assert (copy / "inside.txt").exists()
            assert not (elsewhere / "x.txt").exists()
            assert (real / "kept.py").read_text() == "original\n"
            worktrees.close(copy)
        finally:
            shutil.rmtree(home_side, ignore_errors=True)

    def test_it_reaches_localhost_and_nothing_else(self, repo: Path):
        copy = Path(str(worktrees.open_for("c2")))
        probe = (
            "import socket, threading\n"
            "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(1)\n"
            "threading.Thread(target=lambda: s.accept()[0].send(b'hi'), daemon=True).start()\n"
            "c = socket.create_connection(s.getsockname(), timeout=3); print('local', c.recv(2))\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 443), timeout=3); print('remote open')\n"
            "except OSError:\n"
            "    print('remote refused')\n"
        )
        with session_context.working_from(str(copy), mirror_of=str(repo)):
            boxed = confinement.in_copy([sys.executable, "-c", probe])
        assert boxed is not None
        argv, env = boxed
        done = subprocess.run(argv, cwd=copy, env=env, capture_output=True, text=True, timeout=30)
        assert "local b'hi'" in done.stdout
        assert "remote refused" in done.stdout

    def test_the_tests_run_inside_the_copy_and_come_back_with_counts(self, repo: Path):
        (repo / "pytest.ini").write_text("[pytest]\n")
        (repo / "test_it.py").write_text("def test_one():\n    assert True\n")
        # The project's interpreter, as a venv would provide one: this suite's own Python.
        venv = repo / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        (venv / "python").chmod(0o755)
        copy = Path(str(worktrees.open_for("t1")))
        with session_context.working_from(str(copy), mirror_of=str(repo)):
            result = testing.run(".", "")
        assert result["ok"] is True, result
        assert result.get("passed") == 1
