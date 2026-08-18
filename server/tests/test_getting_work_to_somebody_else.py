"""Committing makes work durable here. Pushing is what makes it exist for anybody else.

`.kith/` reaching a second person depends entirely on this, and it has been a raw `git push` in a
shell command — 15 such commits in one project here — so every way it can fail arrived as shell
output for a model to read. One of those ways is not a failure at all: a rejected push is the
normal condition of two people working, and its answer (pull, look, push again) is different from
every other one's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kith.infra.workspace import git, paths


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    """A repository with a remote it can actually reach, so the failures are real ones."""
    origin = tmp_path / "origin.git"
    _run("init", "--bare", "-q", str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    _run("init", "-q", cwd=work)
    _run("config", "user.email", "t@example.com", cwd=work)
    _run("config", "user.name", "T", cwd=work)
    _run("config", "commit.gpgsign", "false", cwd=work)
    (work / "a.txt").write_text("one\n")
    _run("add", "-A", cwd=work)
    _run("commit", "-q", "-m", "first", cwd=work)
    monkeypatch.setattr(paths, "configured_root", lambda: work)
    return work, origin


class TestSendingIt:
    def test_the_first_push_sets_the_branch_up_to_track(self, repo):
        """Otherwise the next one needs an argument, and "nothing to push" has no answer."""
        work, _ = repo
        _run("remote", "add", "origin", str(repo[1]), cwd=work)
        said = git.push()
        assert "Pushed" in said and "track" in said

    def test_it_says_how_much_went(self, repo):
        work, origin = repo
        _run("remote", "add", "origin", str(origin), cwd=work)
        git.push()
        (work / "b.txt").write_text("two\n")
        _run("add", "-A", cwd=work)
        _run("commit", "-q", "-m", "second", cwd=work)
        assert "Pushed 1 commit on" in git.push()

    def test_nothing_to_send_is_said_plainly(self, repo):
        work, origin = repo
        _run("remote", "add", "origin", str(origin), cwd=work)
        git.push()
        assert "Nothing to push" in git.push()


class TestTheWaysItDoesNotWork:
    def test_no_remote_at_all(self, repo):
        """A local-only repository is a normal thing to have, not a broken one."""
        assert "no remote" in git.push()

    def test_somebody_else_pushed_first_is_not_reported_as_an_error(self, repo):
        """The normal condition of two people working, and the one with a different answer.
        Left as git's own `! [rejected] main -> main (fetch first)` it reads as a breakage."""
        work, origin = repo
        _run("remote", "add", "origin", str(origin), cwd=work)
        git.push()
        # Somebody else's commit lands on the remote first.
        other = work.parent / "other"
        subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
        _run("config", "user.email", "u@example.com", cwd=other)
        _run("config", "user.name", "U", cwd=other)
        (other / "theirs.txt").write_text("theirs\n")
        _run("add", "-A", cwd=other)
        _run("commit", "-q", "-m", "theirs", cwd=other)
        _run("push", "-q", cwd=other)
        # And now ours, from behind.
        (work / "mine.txt").write_text("mine\n")
        _run("add", "-A", cwd=work)
        _run("commit", "-q", "-m", "mine", cwd=work)

        said = git.push()
        assert "somebody else pushed first" in said
        assert "Pull" in said

    def test_it_never_raises(self, tmp_path: Path, monkeypatch):
        """Failing to publish must not take down the work that was published."""
        monkeypatch.setattr(paths, "configured_root", lambda: tmp_path / "not-a-repo")
        assert isinstance(git.push(), str)
