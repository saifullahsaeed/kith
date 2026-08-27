"""A shell command finishes, or says why it did not. It does not just stop.

Watching him work, a step would reach a command and freeze — no output, no error, nothing to
read — and by the time anything happened the conversation had been abandoned. From outside
that does not look like a timeout, it looks like him being stuck, and the only move left is
to interrupt and type "continue".

Three causes, all of them the shell being asked to do something it cannot:

* **Backgrounding.** The tool's own description recommended `nohup … &`, written when nothing
  better existed, and still being followed long after `start_process` arrived. It hands back
  a pid and nothing else — no output, no exit code, no way to tell a dev server that is
  serving from one that died on a port collision.
* **Waiting for a person.** Anything that asks a question — a pager, a credential prompt, a
  bare interpreter — waits for input that is never coming.
* **The timeout.** Fifteen minutes, from when the shell had to be able to do everything. Long
  enough that nobody ever saw the error.
"""

from __future__ import annotations

import time

import pytest

from kith.infra import workspace
from kith.infra.workspace import WorkspaceError


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


class TestBackgroundingIsRefused:
    def test_nohup_is_sent_to_start_process(self, workspace_root):
        """The exact command he was writing, because the description told him to."""
        with pytest.raises(WorkspaceError) as caught:
            workspace.run_command("nohup .venv/bin/python manage.py runserver 8012 > /tmp/be.log 2>&1 &")
        assert "start_process" in str(caught.value)

    def test_a_trailing_ampersand_is_too(self, workspace_root):
        with pytest.raises(WorkspaceError) as caught:
            workspace.run_command("sleep 30 &")
        assert "start_process" in str(caught.value)

    def test_it_is_refused_before_anything_runs(self, workspace_root):
        """A warning would arrive with the result, by which point the thing is already
        running unsupervised and the round is spent."""
        marker = workspace_root / "should-not-exist"
        with pytest.raises(WorkspaceError):
            workspace.run_command(f"touch {marker} &")
        assert not marker.exists()

    def test_and_is_not_backgrounding(self, workspace_root):
        """`&&` ends in an ampersand too. Catching it would break most real commands."""
        assert workspace.run_command("echo one && echo two").exit_code == 0

    def test_a_background_job_mid_command_is_still_caught(self, workspace_root):
        with pytest.raises(WorkspaceError):
            workspace.run_command("nohup python -m http.server 8000 >/dev/null 2>&1 & echo ok")

    def test_an_ordinary_command_is_untouched(self, workspace_root):
        result = workspace.run_command("echo hello")
        assert result.exit_code == 0 and "hello" in result.output


class TestNothingWaitsForAPersonWhoIsNotThere:
    def test_a_command_reading_stdin_returns_immediately(self, workspace_root):
        """`cat` with no file, a bare interpreter, `manage.py shell` — all of them block on
        input that is never coming."""
        started = time.monotonic()
        result = workspace.run_command("cat", timeout=8)
        assert time.monotonic() - started < 5, "it waited for input"
        assert result.exit_code == 0

    def test_a_bare_interpreter_returns_immediately(self, workspace_root):
        started = time.monotonic()
        workspace.run_command("python3", timeout=8)
        assert time.monotonic() - started < 5

    def test_a_pager_does_not_swallow_the_turn(self, workspace_root):
        """`git log` with no PAGER pipes into `less`, which waits for a keypress forever."""
        workspace.run_command("git init -q .", timeout=20)
        workspace.run_command(
            "git -c user.email=a@b -c user.name=a commit -q --allow-empty -m first", timeout=20
        )

        started = time.monotonic()
        result = workspace.run_command("git log", timeout=8)

        assert time.monotonic() - started < 5, "the pager blocked"
        assert "first" in result.output

    def test_the_environment_says_nobody_is_watching(self):
        """The settings that turn a question into a failure. A failure he can read and work
        around; a block just eats the turn."""
        assert workspace.shell._NON_INTERACTIVE["GIT_TERMINAL_PROMPT"] == "0"
        assert workspace.shell._NON_INTERACTIVE["PAGER"] == "cat"
        assert workspace.shell._NON_INTERACTIVE["GIT_PAGER"] == "cat"

    def test_it_reaches_the_command(self, workspace_root):
        result = workspace.run_command("echo $PAGER-$GIT_TERMINAL_PROMPT-$NO_COLOR")
        assert "cat-0-1" in result.output


class TestWhenItGenuinelyTakesTooLong:
    def test_the_message_names_the_two_possibilities(self, workspace_root):
        """Either it should have been a background process, or it is stuck. Those call for
        different next moves, so the message says both rather than guessing."""
        with pytest.raises(WorkspaceError) as caught:
            workspace.run_command("sleep 20", timeout=2)
        message = str(caught.value)
        assert "start_process" in message
        assert "stuck" in message

    def test_the_default_is_minutes_not_a_quarter_of_an_hour(self):
        """Long enough for an npm install on a cold cache; short enough that a stuck command
        is something you see rather than something you abandon the conversation over."""
        assert workspace.base._EXEC_TIMEOUT <= 300


class TestTheAdviceMatchesTheTools:
    def test_the_shell_no_longer_recommends_what_it_refuses(self):
        """The description told him to use `nohup … &` for years after `start_process`
        existed. He was following instructions."""
        from kith.tools import registry

        described = registry.require("shell").description
        assert "start_process" in described
        assert "nohup … &`, which returns" not in described

    def test_the_timeout_message_points_at_a_tool_that_exists(self, workspace_root):
        from kith.tools import registry

        assert registry.get("start_process") is not None
        with pytest.raises(WorkspaceError) as caught:
            workspace.run_command("sleep 20", timeout=2)
        assert "check_process" in str(caught.value)
