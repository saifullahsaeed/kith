"""Running a command on the machine.

Under a login shell (``bash -lc``) with the workspace as the working directory, which is what
makes "use the tools already installed" true — his ``python3`` is your python3.
"""

from __future__ import annotations

import os
import subprocess

from kith.infra import permissions

from .base import _EXEC_TIMEOUT, ExecResult, WorkspaceError, _clip
from .checkpoints import _checkpoint_before_change
from .paths import base_dir, root


def run_command(command: str, timeout: int = _EXEC_TIMEOUT) -> ExecResult:
    """Run a shell command in the workspace.

    A *login* shell, so he inherits the PATH you actually use — homebrew, pyenv, node,
    whatever you have — rather than the stunted environment a GUI app starts with. This is
    the difference between "he can use the tools on this machine" being true and being a
    claim in a docstring.
    """
    code, combined = _capture(command, timeout)
    return ExecResult(exit_code=code, output=_clip(combined))


def _capture(command: str, timeout: int) -> tuple[int, str]:
    """Run a command and return its exit code and output, *unclipped*.

    Split out from `run_command` because the two callers want the clip in different places.
    A command's output goes to the model as it is, so it is clipped on the way out. A fetched
    web page is HTML that gets converted to prose first, and clipping the HTML instead is what
    broke `fetch_url` for every real page — see there.
    """
    permissions.require_command(command, root())
    backgrounding = _looks_backgrounded(command)
    if backgrounding:
        raise WorkspaceError(backgrounding)
    _checkpoint_before_change("shell")
    # Where the command runs. The permission check above still measures against root(), so his own
    # folder AND the linked project both count as inside; but the command's cwd is the working base,
    # so relative paths in a shell line land in your project, not his scratch space.
    here = base_dir()
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            timeout=timeout,
            cwd=str(here),
            # Nothing is going to type an answer. Without this the command inherits whatever
            # stdin the server was started with, and anything that asks a question — `python`
            # with no script, `manage.py shell`, `git commit` opening an editor, an npm
            # prompt — waits for a person who is not there. `./run` already redirects the
            # server's stdin, which makes this belt-and-braces; it is worth having anyway,
            # because it makes the behaviour a property of *this call* rather than of how
            # somebody happened to launch the app.
            stdin=subprocess.DEVNULL,
            env={**os.environ, "KITH_WORKSPACE": str(here), **_NON_INTERACTIVE},
        )
    except FileNotFoundError:
        raise WorkspaceError("No bash on this machine — can't run commands.") from None
    except subprocess.TimeoutExpired:
        raise WorkspaceError(
            f"That took longer than {timeout}s and was stopped. If it was meant to keep "
            "running — a server, a watcher — start it with `start_process` instead, which "
            "returns straight away and lets you read its output with `check_process`. If it "
            "was meant to finish, it is stuck: something is waiting for an answer, or it is "
            "genuinely slower than that."
        ) from None
    return proc.returncode, proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")


#: Told to every command, so nothing stops to ask a question nobody is there to answer.
#:
#: A pager is the classic: `git log` with no `PAGER` set pipes into `less`, which waits for a
#: keypress forever. `GIT_TERMINAL_PROMPT=0` is the other one that matters — without it a git
#: operation needing credentials blocks instead of failing, and blocking is much worse: a
#: failure he can read and work around, a block just eats the turn.
_NON_INTERACTIVE = {
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "TERM": "dumb",
    "DEBIAN_FRONTEND": "noninteractive",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PYTHONUNBUFFERED": "1",
    # Colour is escape codes he pays for and cannot see. Most tools already detect a pipe;
    # the ones that do not respect this.
    "NO_COLOR": "1",
}


def _looks_backgrounded(command: str) -> str:
    """Is this an attempt to start something long-lived from the shell? Then say so.

    `start_process` exists now, and this tool's description used to recommend `nohup … &`
    because for a long time nothing better existed. That advice outlived its reason and kept
    being taken: a dev server started this way returns a pid and nothing else — no output, no
    exit code, no way to tell serving from crashed-on-a-port-collision, and no way to stop it.

    Refused rather than warned. A warning arrives with the result, by which point the thing is
    already running unsupervised and the round is spent; a refusal costs one round and he
    reaches for the tool that works.
    """
    text = (command or "").strip()
    if not text:
        return ""
    trailing_amp = text.endswith("&") and not text.endswith("&&")
    if "nohup " not in text and not trailing_amp:
        return ""
    return (
        "That starts something in the background, and `shell` cannot watch it — you would "
        "get a pid and nothing else. Use `start_process` with a name instead: it returns "
        "straight away, `check_process` reads what it has printed since you last looked, and "
        "`stop_process` stops it and everything it started."
    )
