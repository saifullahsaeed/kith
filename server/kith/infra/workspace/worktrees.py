"""A private copy of the repository for one worker to change things in.

A worker that can edit files is only safe — and only *parallel* — if it is not editing the
same files as everything else. `git worktree` is the mechanism: a second checkout of the same
repository, with its own working tree and its own index, sharing one object store. Two
workers in two worktrees share nothing but the objects they both read.

**Its own index is the part that is easy to miss and load-bearing.** A linked worktree keeps
its index at ``.git/worktrees/<name>/index``, not in the main one — so `git add -A` in here,
which is how untracked files get into a diff at all, cannot disturb a commit someone is
deliberately building toward in the real tree. Without that this whole approach would be
unusable: the only way to see a new file in a diff would be to stage it in the repository the
worker is supposed to be isolated from.

**Seeded from the working tree, not from HEAD.** ``git stash create`` writes a commit object
for whatever is currently uncommitted and returns its sha *without touching the stash ref, the
index, or the working tree* — so it is a read as far as the real repository is concerned. The
worktree is checked out at that commit, which means a worker sees the code as it is right now
rather than as it was at the last commit. The alternative was a worker reporting a diff
against a tree nobody has, that will not apply.

Untracked files are not carried by ``stash create``, and one directory has to be exempted from
that: ``.kith``. It is where Kith keeps the notes it writes for itself — a convention file, a
task's state, the plan a worker is supposed to follow — and it is gitignored by convention in
every project. So git would never carry it, and the result is a main agent writing a brief its
own worker cannot open.

That is not hypothetical. On the first real use of this, the agent wrote `CONVENTION.md` to
`.kith/work/`, told the builder to read it, and the builder spent its entire round budget
hunting for a file that was not there and produced no patch at all. It diagnosed the cause
itself from `.gitignore`.

So :func:`_carry_notes` copies it in. Everything *else* untracked stays out, and deliberately:
``--include-untracked`` writes an object for every untracked file in the tree, which in a
repository with a stale ignore file means whatever large thing is sitting in it.

**Detached, always.** A worktree on a branch takes that branch — git refuses to check the
same branch out twice, so a worker opened on `master` would make the real checkout fail. A
detached head belongs to nobody.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from .base import WorkspaceError
from .git import _git, _repo_root, has_git
from .paths import base_dir, internal

#: Where the copies live: beside the databases in `paths.internal`, never inside the repository
#: being copied.
#:
#: The first version of this put them under the workspace root and a test caught it, which is
#: worth recording because the reasoning looked sound. The workspace is not the project, so a
#: copy of ~/Kith/myapp under ~/Kith/.kith is outside it — *unless the workspace root is itself
#: the repository*, which `git.ensure_repo` will happily make it. Then every worker's copy is a
#: full checkout nested inside the tree it copied, walked by `glob`, matched by `grep`, picked
#: up by the project's own build, and listed by `git status` (only `.kith/scratch/` is in
#: `_GITIGNORE`, not `.kith/`).
#:
#: `internal()` has no such edge: it is beside the databases and has nothing to do with where
#: the work happens, which is the same argument its own docstring makes about transcripts.
WORKTREE_DIR = "workers"

#: How long an abandoned copy is kept before `prune` may remove it. Generous, because the
#: thing it is protecting against is deleting work: a worker whose turn died still has its
#: edits in here, and its worker row still points at them. A day means a person who comes
#: back the next morning still has yesterday's, and a machine that has been running for a
#: month is not accumulating a hundred checkouts of the same repository.
KEEP_SECONDS = 24 * 60 * 60

#: Worktree operations are bookkeeping in front of a worker's whole run, so a slow one should
#: fail fast rather than stall it. Longer than the checkpoint's 20s because `worktree add`
#: writes a full checkout and a large repository legitimately takes a while.
_TIMEOUT = 60


#: Held while a copy is being made, and only then.
#:
#: Making one is not the read-only operation it looks like. ``git stash create`` runs against
#: the *source* repository and refreshes its index, so it takes `.git/index.lock`; `worktree
#: add` then writes the shared `.git/worktrees` registry. Two builders sent in the same round —
#: which is the whole point of `send_builder` being parallel-safe — hit both at once, and one of
#: them dies with "could not write index" before it has read a single file.
#:
#: Found in use on 2026-09-15: builder 2 won the race, builder 1 died at startup, and the agent
#: that sent them diagnosed it from the lock files and retried. It should not have had to.
#:
#: Serialising creation costs nothing worth measuring. A copy takes well under a second; the
#: work afterwards takes minutes, and that is the part that stays parallel. The lock is released
#: before `_run_worker` streams a single token.
_making = threading.Lock()


def _home() -> Path:
    return internal() / WORKTREE_DIR


def open_for(worker_id: str) -> Path | None:
    """A private checkout for ``worker_id``, or None when there cannot be one.

    None rather than an exception for the two ordinary reasons — git is not installed, or the
    work is not happening in a repository at all — because both are conditions a caller has to
    handle anyway and neither is a fault. The caller decides what a worker without isolation
    is allowed to do; this only reports that it could not be given one.
    """
    if not has_git():
        return None
    source = _repo_root(base_dir())
    if source is None:
        return None

    where = _home() / str(worker_id)
    if where.exists():
        # A worker being resumed already has its copy, and its edits are in it. Handing back
        # the existing one is the whole point of keeping worktrees as long as workers.
        return where

    with _making:
        # Re-checked inside the lock. Two threads can both find it missing above, and the
        # second would otherwise ask git to register a worktree that now exists.
        if where.exists():
            return where
        where.parent.mkdir(parents=True, exist_ok=True)

        # The uncommitted state as a commit, with no side effect on the repository it came from.
        # Empty output means there was nothing uncommitted, and HEAD is then the same tree.
        seeded = _git("stash", "create", cwd=source, timeout=_TIMEOUT).output.strip()
        start = seeded.splitlines()[0].strip() if seeded else "HEAD"

        made = _git("worktree", "add", "--detach", str(where), start, cwd=source, timeout=_TIMEOUT)
        if made.exit_code == 0:
            _carry_notes(source, where)

    if made.exit_code != 0:
        # Left as a failure rather than a silent fall back to the real tree. "He could not be
        # isolated, so he edited your files instead" is the one outcome this module exists to
        # make impossible.
        raise WorkspaceError(f"Could not open a private copy to work in: {made.output.strip()[:300]}")
    return where


#: What is under `.kith` but is not a note. Skipped when carrying it into a worker's copy.
#:
#: `scratch` is throwaway by definition. The rest is build output — he writes little web apps
#: into `.kith/work/`, and they bring what a web app brings. See :func:`_carry_notes` for the
#: 150 MB this was measured at before the list existed.
_NOT_NOTES = (
    "scratch",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".next",
    ".cache",
)


def _carry_notes(source: Path, copy: Path) -> None:
    """Copy `.kith` into the copy, because git will not.

    The one directory a worker always needs and never gets. See the module docstring for the
    round budget this cost before it existed.

    Copied rather than symlinked: a symlink would let a worker write into the real `.kith`,
    which is the main agent's own working memory and the one place it trusts completely.

    **Only the notes, and that qualifier is load-bearing.** `.kith` is not a folder of text: he
    builds things in `.kith/work/`, and a thing he built has a `node_modules`. Measured on one
    real project the day this shipped — 150 MB, of which 146 MB was two `node_modules` — and it
    was being copied per builder, so a round of three cost 440 MB of disk and the seconds to
    write it. Nothing in there is a brief.

    So :data:`_NOT_NOTES` is skipped, and it is the same set `paths._LOOKUP_SKIP` prunes when
    looking for a file someone clicked on, for the same reason: these are things a tool produced,
    not things anyone wrote.

    Never raises. A worker without its notes is a worker that reports it could not find them,
    which is recoverable; a worker that could not start at all is not.
    """
    notes = source / ".kith"
    if not notes.is_dir():
        return
    try:
        shutil.copytree(
            notes,
            copy / ".kith",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*_NOT_NOTES),
        )
    except Exception:
        pass


def diff_in(worktree: Path) -> str:
    """Everything the worker changed in its copy, as one patch that will apply to the real tree.

    Staged first, into the worktree's own index, because that is the only way an untracked
    file — a file the worker *created*, which is most of what a builder does — appears in a
    diff at all. See the module docstring on why that staging is safe.

    ``--binary`` so an image or a compiled fixture survives the round trip, and no ``--stat``
    or colour: the reader is `git apply`, and after it a language model. Both want the patch.

    ``.kith`` is excluded from the diff. It was copied in by :func:`_carry_notes` rather than
    checked out, so it is not the worker's work and must not come back as a patch. Usually it is
    gitignored and would never appear — but a project that tracks it would otherwise hand the
    main agent a diff that writes its own notes back over itself, so the answer does not depend
    on someone's ignore file.

    **The add takes no pathspec, and that is the fix for a bug that made every builder look
    broken.** It was ``add -A -- . ':(exclude).kith'``, and naming `.` explicitly makes git
    refuse an ignored path that matches it: with `.kith` copied in *and* gitignored — which is
    the normal case — git printed "the following paths are ignored", exited 1, and this returned
    "" on the exit code. Three builders in a row did the work correctly, had it staged, and were
    reported as having changed nothing. Bare ``add -A`` skips ignored paths silently and stages
    exactly the same bytes; measured on those three worktrees, 5,843 either way.

    The exit code is no longer fatal either. `add` warns about all sorts of things, and turning
    an advisory into "this worker produced nothing" is the worst available reading — an empty
    diff below is the honest answer, and it is empty only when nothing is really there.
    """
    _git("add", "-A", cwd=worktree, timeout=_TIMEOUT)
    return _git(
        "diff", "--cached", "--binary", "--", ".", ":(exclude).kith", cwd=worktree, timeout=_TIMEOUT
    ).output


def close(worktree: Path) -> None:
    """Remove one copy and the administrative files git keeps for it.

    ``--force`` because the copy is always dirty by the time this runs — that is what it was
    for — and git otherwise refuses to remove a worktree with changes in it. The refusal is a
    good default for a person at a terminal and wrong here: the diff has already been taken,
    and the alternative to removing it is leaving a full checkout on disk forever.

    Never raises. This runs in the teardown of a worker that has already produced its report,
    and a failure to tidy up must not turn a finished piece of work into an error.
    """
    source = _repo_root(base_dir())
    try:
        if source is not None:
            _git("worktree", "remove", "--force", str(worktree), cwd=source, timeout=_TIMEOUT)
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        if source is not None:
            # Without this git keeps `.git/worktrees/<name>` and still believes the checkout
            # exists, so re-using the same worker id later fails with "already registered".
            _git("worktree", "prune", cwd=source, timeout=_TIMEOUT)
    except Exception:
        pass


def prune(keep_seconds: int = KEEP_SECONDS) -> list[str]:
    """Remove copies nothing is coming back for. Returns the worker ids that lost theirs.

    The counterpart to workers outliving their turn: a worktree is kept as long as the worker
    that owns it can still be followed up, and a worker whose conversation was abandoned never
    says it is finished. Age is the only honest signal left, so age is what this uses.

    Ids rather than a count, because the directory name *is* the worker id and the caller has a
    row to mark. A worktree removed while its row still says `reported` is a `follow_up` that
    looks available, is offered, and then edits a copy of the repository that no longer exists.
    """
    home = _home()
    if not home.is_dir():
        return []
    cutoff = time.time() - max(0, int(keep_seconds))
    gone: list[str] = []
    for candidate in home.iterdir():
        try:
            if candidate.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        close(candidate)
        gone.append(candidate.name)
    return gone
