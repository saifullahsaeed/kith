"""His history: whether the workspace is a repo, and what has happened in it.

Split from the checkpoints beside it because the two answer different questions. This one is
about the record he keeps deliberately — a commit is a claim that something is a coherent
change — and the other is about the net underneath him.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kith.infra import executables, permissions

from .base import ExecResult, WorkspaceError, _clip
from .paths import base_dir, resolve, root

#
# None of his four projects had any. He would say "I redesigned the UI" and there was no way
# for him to check what he had actually changed, and no way for anyone else to review a night
# of edits nobody watched land. For an agent that rewrites files a page at a time, that is the gap worth
# closing before any of the others.
#
# One repository at the workspace root rather than one per project. His projects are database
# rows, not directories — he makes folders as he goes — so per-project would need a mapping
# that does not exist, and would miss everything he writes outside one. A single repo covers
# all of it and `git diff` still works per directory.

#: Never versioned: build output, dependencies, and his own bookkeeping. Without this the
#: first commit is 60MB of node_modules and every diff afterwards is unreadable.
#: Written only into *his own* folder, never into a project — see `ensure_repo`. A project has
#: its own opinion about what to ignore and did not ask for ours.
#:
#: `.kith/scratch/` and not `.kith/`. The whole folder used to be ignored, which was right when
#: it held nothing but his private bookkeeping and is wrong now that it holds the project's
#: memory and its task briefs: those are the things that are *supposed* to travel with the
#: repository so somebody else's Kith can pick the work up from a clone. Only the scratch
#: subfolder is excluded, because it is screenshots and PNGs do not belong in history.
_GITIGNORE = """\
node_modules/
dist/
build/
.venv/
venv/
__pycache__/
*.pyc
.DS_Store
.kith/scratch/
"""

#: Committed as, so a commit works on a machine where git has no global identity. Without
#: these git refuses with "please tell me who you are" and the history silently never starts.
_GIT_AUTHOR = ("Kith", "kith@localhost")


def _git(
    *args: str,
    check: bool = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> ExecResult:
    """One git command where the work is happening, with an identity of its own.

    Deliberately not through :func:`run_command`: that asks the permission layer, and these
    are the app's own bookkeeping rather than something he decided to run. The identity is
    passed per-invocation so nothing depends on, or alters, the machine's git config.

    ``base_dir()`` and not ``root()``, and the difference cost five hours of unsaved work.
    Every git tool ran in *his own folder* whatever project he was on — so working in a
    codebase on the Desktop, `changes` reported an empty diff, `commit` committed nothing and
    `history` showed nothing. He used `commit` once in five hours and `history` never, which
    was the correct response to three tools that did nothing. The repository ended with no
    commits at all against 52 changed files.

    The same mistake as `base_dir` itself, one layer along: that fix taught *paths* to follow
    the project and nobody checked git.

    ``cwd`` defaults to ``base_dir()`` for every existing caller, but a checkpoint restore
    has to target the repo a checkpoint was actually *taken* in, which may not be
    ``base_dir()`` any more by the time someone restores it — so it takes an explicit
    override rather than trusting the ambient session.

    ``env`` is for the checkpoint machinery's shadow index (``GIT_INDEX_FILE``); it must be
    built as ``{**os.environ, ...}`` by the caller, never a bare dict, or git won't resolve
    on ``PATH``. ``None`` (the default) means "inherit the real environment," identical to
    every call site before this parameter existed.
    """
    here = cwd or base_dir()
    proc = subprocess.run(
        [
            # Resolved rather than named. `/usr/bin/git` happens to be on the minimal `PATH` a
            # GUI-launched app inherits, so this one was never actually broken — but relying on
            # that is relying on Apple shipping a shim, and someone with a newer git in
            # Homebrew should get theirs rather than the one that came with Xcode.
            executables.which("git") or "git",
            "-c",
            f"user.name={_GIT_AUTHOR[0]}",
            "-c",
            f"user.email={_GIT_AUTHOR[1]}",
            *args,
        ],
        capture_output=True,
        cwd=str(here),
        timeout=timeout,
        env=env,
    )
    out = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
    if check and proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {out.strip()[:300]}")
    return ExecResult(exit_code=proc.returncode, output=out)


def has_git() -> bool:
    return executables.which("git") is not None


def _repo_root(here: Path) -> Path | None:
    """The repository this folder is inside, if any — walking up, not just at `here`.

    Checking only `here/.git` is what would make `ensure_repo` initialise a *second*
    repository inside an existing checkout the moment he worked in a subfolder. Nothing would
    fail; the commits would simply go somewhere nobody looks.
    """
    for folder in [here, *here.parents]:
        if (folder / ".git").exists():
            return folder
    return None


def ensure_repo() -> bool:
    """Make where he is working a repository if it is not one. True when history is available.

    Walks up before initialising. A project usually *is* already a repository, often with its
    root a level or two above the folder in hand — and `git init` inside an existing checkout
    creates a nested repository that swallows the work silently, which is a far worse outcome
    than not having history.
    """
    if not has_git():
        return False
    here = base_dir()
    if not _repo_root(here):
        if _git("init", "-q").exit_code != 0:
            return False
    # Only ever in his own folder. Dropping an opinionated `.gitignore` into somebody's Django
    # project because it happened not to have one is not tidying up, it is deciding something
    # that was not ours to decide — and it would be the first thing they saw in the diff.
    if here == root():
        ignore = here / ".gitignore"
        if not ignore.exists():
            ignore.write_text(_GITIGNORE)
    return True


def commit_all(message: str) -> str:
    """Commit whatever changed, and say what. Empty string when there was nothing.

    Called by him, deliberately, and never on a timer. The first version committed at the end
    of every turn, which was wrong twice over: a turn is not a unit of work — a task takes many,
    so most of those commits would be mid-edit states that do not build — and a commit is a
    claim that something is a coherent step, which is a judgement, not something a clock can
    make. A history committed on a schedule is a keystroke log, and the point of having one is
    to be able to read it.

    Best-effort about *failing*: not being able to record history must never take down the work
    that was just done, so this returns a description rather than raising.
    """
    if not ensure_repo():
        return ""
    _git("add", "-A")
    staged = _git("diff", "--cached", "--stat")
    if not staged.output.strip():
        return ""
    subject = " ".join(str(message or "work").split())[:72] or "work"
    result = _git("commit", "-q", "-m", subject)
    if result.exit_code != 0:
        return ""
    return staged.output.strip().splitlines()[-1].strip()


def diff(path: str | None = None, staged: bool = False) -> str:
    """What has changed and is not yet committed."""
    if not ensure_repo():
        return "git is not available on this machine, so there is no history to compare against."
    args = ["diff", "--cached"] if staged else ["diff"]
    if path:
        target = Path(resolve(path))
        permissions.require_path("read", target, root())
        args += ["--", str(target)]
    out = _git(*args).output.strip()
    if not out:
        # An untracked file shows in neither diff, and "no changes" would be a lie.
        fresh = _git("ls-files", "--others", "--exclude-standard").output.strip()
        if fresh:
            return "Nothing changed in tracked files. Not yet tracked:\n" + _clip(fresh)
        return "Nothing has changed since the last commit."
    return _clip(out)


def log(limit: int = 20) -> str:
    """Recent history, one line each."""
    if not ensure_repo():
        return "git is not available on this machine."
    out = _git("log", f"-{max(1, min(limit, 200))}", "--format=%h %ad %s", "--date=format:%d %b %H:%M").output
    return _clip(out.strip()) or "No history yet."
