"""An agent's own checkout of the repository: a git worktree on a branch of its own.

An agent that can edit files is only safe — and only *parallel* — if it is not editing the same
files as everything else. `git worktree` is the mechanism: a second checkout of the same
repository, with its own working tree and its own index, sharing one object store and one set of
refs. Two agents in two worktrees share nothing but the objects they both read.

**On a branch, because agents nest and work flows back up by merging.** An agent commits on its
branch; whoever started it merges that branch into its own checkout with ordinary git. An agent
that starts agents of its own opens their worktrees from *its* worktree, so their branches start
from its branch — the tree of agents is a tree of branches, and the conductor's checkout is where
it all finally lands. (The workers this replaced used detached copies and handed back patches,
and nothing about a patch survived the copy being deleted.)

**Started from the last commit, not from the working tree.** A branch is a line of commits, and
uncommitted changes in the handler's checkout are not on it. Carrying them in by hand would put
them on the agent's branch as the agent's own work, and merging back would then collide with the
very same uncommitted changes still sitting in the handler's checkout. So a handler commits what
its agents need first — the strategy says so, and `start_agents` says so when there is anything
uncommitted.

What git does not track and an agent still needs is carried: `.kith` (the notes Kith writes for
itself, gitignored by convention), environment files, and the dependency folders the project's
checks need — all ignored, so none of them can end up on the branch.
"""

from __future__ import annotations

import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .base import WorkspaceError
from .git import _git, _repo_root, has_git
from .paths import internal

#: Where the worktrees live: beside the databases in `paths.internal`, never inside the
#: repository being copied.
#:
#: The first version of the workers' copies put them under the workspace root and a test caught
#: it, which is worth recording because the reasoning looked sound. The workspace is not the
#: project, so a copy of ~/Kith/myapp under ~/Kith/.kith is outside it — *unless the workspace
#: root is itself the repository*, which `git.ensure_repo` will happily make it. Then every copy
#: is a full checkout nested inside the tree it copied, walked by `glob`, matched by `grep`, and
#: picked up by the project's own build.
WORKTREE_DIR = "agents"

#: Worktree operations are bookkeeping in front of an agent's whole run, so a slow one should
#: fail fast rather than stall it. Longer than the checkpoint's 20s because `worktree add`
#: writes a full checkout and a large repository legitimately takes a while.
_TIMEOUT = 60


#: Held while a copy is being made, and only then.
#:
#: Making one is not the read-only operation it looks like: `worktree add` writes the shared
#: `.git/worktrees` registry and creates a ref. Several agents started together — which is the
#: whole point of `start_agents` — hit both at once, and one of them dies with "could not write
#: index" before it has read a single file.
#:
#: Found in use on 2026-09-15: builder 2 won the race, builder 1 died at startup, and the agent
#: that sent them diagnosed it from the lock files and retried. It should not have had to.
#:
#: Serialising creation costs nothing worth measuring. A copy takes well under a second; the
#: work afterwards takes minutes, and that is the part that stays parallel.
_making = threading.Lock()


def _home() -> Path:
    return internal() / WORKTREE_DIR


@dataclass(frozen=True)
class Opened:
    """A worktree made for one agent."""

    #: The worktree's root.
    copy: Path
    #: Where in it the work is: the project's folder when the project is a folder of a larger
    #: repository, the root otherwise. What the agent's relative paths resolve against.
    folder: Path
    #: The branch it is on, which nobody else has checked out.
    branch: str
    #: The commit the branch started from — what "ahead" is measured against.
    base: str
    #: The checkout it was made from: the person's, or the handler's own worktree.
    source: Path


def open_branch(agent_id: str, folder: Path | str, name: str = "") -> Opened | None:
    """A worktree on a new branch for one agent, started from ``folder``'s last commit.

    ``folder`` is where the session starting the agent is working — the conductor's project, or an
    agent's own worktree — and the new branch starts at that checkout's HEAD, so an agent's agents
    branch from the agent's branch. ``name`` only flavours the branch name; ``agent_id`` keeps it
    unique.

    None when there cannot be one — git is not installed, or the folder is not in a repository,
    or the repository has no commit yet — because those are ordinary situations the caller has to
    handle anyway (the agent shares the handler's folder instead). A failure of git itself is an
    error: "it could not be isolated, so it edited your files instead" must never happen quietly.
    """
    if not has_git():
        return None
    work = _settled(Path(folder))
    source = _repo_root(work)
    if source is None:
        return None
    head = _git("rev-parse", "--verify", "-q", "HEAD", cwd=source, timeout=_TIMEOUT)
    base = head.output.strip().splitlines()[0].strip() if head.exit_code == 0 and head.output.strip() else ""
    if not base:
        return None

    where = _home() / str(agent_id)
    branch = branch_for(name, agent_id)
    with _making:
        where.parent.mkdir(parents=True, exist_ok=True)
        made = _git("worktree", "add", "-b", branch, str(where), base, cwd=source, timeout=_TIMEOUT)
        if made.exit_code != 0:
            raise WorkspaceError(f"Could not open a worktree for this agent: {made.output.strip()[:300]}")
        _carry_notes(source, where, work)
        _carry_environment(source, where)
        _carry_dependencies(source, where)
    return Opened(copy=where, folder=base_in(where, work, source), branch=branch, base=base, source=source)


def branch_for(name: str, agent_id: str) -> str:
    """``kith/<name>-<6 hex>`` — readable in `git branch`, and unique whatever the name is."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "agent").lower()).strip("-")[:40] or "agent"
    suffix = re.sub(r"[^a-z0-9]", "", str(agent_id).lower())[-6:] or uuid.uuid4().hex[:6]
    return f"kith/{slug}-{suffix}"


def uncommitted(folder: Path | str) -> list[str]:
    """The paths with uncommitted changes in ``folder``'s checkout — what an agent started from
    here would not see. Empty when clean or not a repository."""
    root = _repo_root(_settled(Path(folder)))
    if root is None:
        return []
    listed = _git("status", "--porcelain", "-uall", cwd=root, timeout=_TIMEOUT)
    if listed.exit_code != 0:
        return []
    return [line[3:].strip() for line in listed.output.splitlines() if line.strip()]


def status(copy: Path, base: str) -> dict:
    """Where an agent's branch stands: commits since it started, and what is uncommitted."""
    ahead = 0
    if base:
        counted = _git("rev-list", "--count", f"{base}..HEAD", cwd=copy, timeout=_TIMEOUT)
        if counted.exit_code == 0 and counted.output.strip().isdigit():
            ahead = int(counted.output.strip())
    return {"ahead": ahead, "uncommitted": uncommitted(copy)}


def commit_leftovers(copy: Path, message: str) -> list[str]:
    """Commit whatever the agent left uncommitted in its worktree. Returns the paths committed.

    Run when an agent reports. Its branch is what its handler merges, and a change sitting
    uncommitted in a worktree is a change the merge cannot see — the same failure as the workers'
    "edited files, empty patch", one level up. Committed under a message that says it was left
    behind, so the history does not pretend the agent meant it.
    """
    left = uncommitted(copy)
    if not left:
        return []
    _git("add", "-A", cwd=copy, timeout=_TIMEOUT)
    done = _git("commit", "-q", "--no-verify", "-m", message, cwd=copy, timeout=_TIMEOUT)
    return left if done.exit_code == 0 else []


def merged(branch: str, into: Path | str) -> bool:
    """Whether every commit on ``branch`` is already in ``into``'s checked-out HEAD."""
    root = _repo_root(_settled(Path(into)))
    if root is None or not branch:
        return False
    exists = _git("rev-parse", "--verify", "-q", branch, cwd=root, timeout=_TIMEOUT)
    if exists.exit_code != 0:
        return True  # nothing left to merge from a branch that is gone
    return _git("merge-base", "--is-ancestor", branch, "HEAD", cwd=root, timeout=_TIMEOUT).exit_code == 0


#: What is under `.kith` but is not a note. Skipped when carrying it into a worker's copy.
#:
#: Build output — he writes little web apps into `.kith/work/`, and they bring what a web app
#: brings. See :func:`_carry_notes` for the 150 MB this was measured at before the list existed.
#:
#: `scratch` used to be here too, as throwaway by definition, and that turned out to be wrong for
#: the files that matter most: it is where he keeps the check scripts and reference files he
#: points a worker at. A workflow told a builder to "follow the structure of
#: .kith/scratch/check_surface.py" and the builder spent its whole budget searching for a file
#: its copy did not have. So scratch is carried now, minus what is big or is itself a run.
_NOT_NOTES = (
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".next",
    ".cache",
)


def _settled(folder: Path) -> Path:
    """``folder`` absolute and with its symlinks resolved, so it and the repository found by
    walking up from it are spelled the same way and one is a prefix of the other."""
    try:
        return folder.expanduser().resolve()
    except OSError:
        return folder.expanduser().absolute()


def _within(source: Path, work: Path) -> Path | None:
    """Where ``work`` sits inside ``source``, as a relative path; None when it is not inside it."""
    try:
        return work.relative_to(source)
    except ValueError:
        return None


def base_in(copy: Path, folder: Path | str, source: Path | None = None) -> Path:
    """Where ``folder`` is inside ``copy``: the folder a worker's relative paths should land in.

    The copy's root when the project is its own repository. A project that is a folder of a
    larger one — ``~/Kith/ai-personal-finance`` inside ``~/Kith`` — is that folder of the copy,
    which is what makes a builder told to read ``.kith/design/brief.md`` read the project's
    brief and not the workspace's. Falls back to the root when the folder is not in the copy.
    """
    work = _settled(Path(folder))
    source = source or _repo_root(work)
    inside = _within(source, work) if source is not None else None
    if not inside or inside == Path("."):
        return copy
    there = copy / inside
    return there if there.is_dir() else copy


def source_of(copy: Path) -> Path | None:
    """The repository a copy was made from, asked of git rather than remembered.

    Read back from the copy itself (``--git-common-dir`` names the original's ``.git``), so a
    copy made of a repository other than the working base's — a folder named for the run — is
    tidied up in the repository that actually holds it.
    """
    try:
        found = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=copy, timeout=20)
    except Exception:
        return None
    if found.exit_code != 0 or not found.output.strip():
        return None
    common = Path(found.output.strip().splitlines()[0].strip())
    return common.parent if common.name == ".git" else None


def _carry_notes(source: Path, copy: Path, work: Path | None = None) -> None:
    """Copy `.kith` into the copy, because git will not — the repository's, and the project's.

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

    **And the project's own, when the project is a folder of the repository.** Every project
    made under the workspace is one, and the workspace ignores `.kith/` at every depth — so the
    root's `.kith` was carried and the project's was not. A design workflow's builders were
    pointed at `.kith/design/brief.md` and found nothing to build from (2026-09-30).

    Never raises. A worker without its notes is a worker that reports it could not find them,
    which is recoverable; a worker that could not start at all is not.
    """
    places = [Path(".")]
    inside = _within(source, work) if work is not None else None
    if inside and inside != Path("."):
        places.append(inside)
    for place in places:
        notes = source / place / ".kith"
        if not notes.is_dir():
            continue
        try:
            shutil.copytree(notes, copy / place / ".kith", dirs_exist_ok=True, ignore=_not_a_note)
        except Exception:
            pass


#: The largest single file carried from `.kith`. A note, a brief or a check script is a few
#: kilobytes; a file past this is data somebody generated, and copying it per builder is the
#: 150 MB problem again one file at a time.
_LARGEST_NOTE = 1_000_000


def _not_a_note(directory: str, names: list[str]) -> set[str]:
    """What `copytree` should skip in one directory of `.kith`.

    Build output anywhere, and any file too big to be a note.
    """
    skipped = {name for name in names if name in _NOT_NOTES}
    here = Path(directory)
    for name in names:
        try:
            if (here / name).is_file() and (here / name).stat().st_size > _LARGEST_NOTE:
                skipped.add(name)
        except OSError:
            skipped.add(name)
    return skipped


#: The largest environment file carried into a copy. A `.env` is a few dozen lines.
_LARGEST_ENV = 100_000


def _is_environment(name: str) -> bool:
    """`.env`, `.env.local`, `.env.test` — not the `.env.example` a project commits for people."""
    base = Path(name).name
    if base != ".env" and not base.startswith(".env."):
        return False
    return not base.endswith((".example", ".sample", ".template", ".dist"))


def _carry_environment(source: Path, copy: Path) -> None:
    """Copy in the project's environment files, which every project ignores and its code needs.

    A Django backend reads `SECRET_KEY` from `backend/.env`; without it the suite stops before
    collecting a test. Two verifiers on 2026-10-01 reported every backend claim "unverified"
    for exactly that reason, and the builders' own test runs failed the same way. Ignored files
    are never staged in a copy, so these are not in anyone's patch, and a copy's commands cannot
    reach the network to send them anywhere — the boundary is what makes carrying them safe.

    Found with ``--directory``, so an ignored `node_modules` is named once rather than listed
    file by file. Never raises, for the reason `_carry_notes` gives.
    """
    try:
        listed = _git(
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "-z",
            cwd=source,
            timeout=_TIMEOUT,
        )
        if listed.exit_code != 0:
            return
        for name in listed.output.split("\0"):
            name = name.strip()
            if not name or name.endswith("/") or ".kith" in Path(name).parts or not _is_environment(name):
                continue
            origin = source / name
            try:
                if not origin.is_file() or origin.is_symlink() or origin.stat().st_size > _LARGEST_ENV:
                    continue
                target = copy / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target)
            except OSError:
                continue
    except Exception:
        pass


#: The folders a project's own checks and tests run on, carried into every copy.
#:
#: A copy is a bare `git worktree`, so it has none of them: `tsc` in a copy resolves no import,
#: `pytest` finds no package, and `npx` goes looking for the network. A builder given
#: `check_code` was being given a checker that could not check.
DEPENDENCY_DIRS = ("node_modules", ".venv", "venv")


def _carry_dependencies(source: Path, copy: Path) -> None:
    """Clone the source's dependency folders into the copy, copy-on-write.

    Cloned with APFS `clonefile`, which copies a whole tree in one call without copying its
    data — measured on this repository, 483 MB of `node_modules` in 0.46 s and no disk until a
    file is changed. A clone rather than a symlink because a symlink named `node_modules` is not
    matched by the `node_modules/` ignore line, so it would arrive in the worker's diff as a new
    file; and because a builder's test run may write into its own dependencies without touching
    yours.

    Only folders git itself calls ignored, found by asking git rather than by walking the tree,
    so a project that tracks a `venv/` of its own is not duplicated and nothing under `.kith` is
    ever matched. Where cloning is not possible — another filesystem, another OS — the copy
    simply goes without, which is what it had before. Never raises.
    """
    try:
        listed = _git(
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "-z",
            cwd=source,
            timeout=_TIMEOUT,
        )
        if listed.exit_code != 0:
            return
        for name in listed.output.split("\0"):
            name = name.strip().rstrip("/")
            if not name or ".kith" in Path(name).parts or Path(name).name not in DEPENDENCY_DIRS:
                continue
            origin = source / name
            target = copy / name
            if not origin.is_dir() or target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            clone(origin, target)
    except Exception:
        pass


def clone(origin: Path, target: Path) -> bool:
    """Copy-on-write clone of a file or a whole tree. False where the filesystem cannot."""
    import ctypes
    import sys

    if sys.platform != "darwin":
        return False
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        return libc.clonefile(str(origin).encode(), str(target).encode(), ctypes.c_int(0)) == 0
    except Exception:
        return False


def close(worktree: Path, branch: str = "") -> None:
    """Remove one agent's worktree, its branch, and the bookkeeping git keeps for it.

    ``--force`` because a worktree may still have changes in it — closing is the decision to let
    them go, taken by `close_agents` only after checking the branch was merged or being told to
    force it. The branch is deleted with ``-D`` for the same reason.

    Never raises: tidying up must not turn finished work into an error.

    **Refuses anything that is not one of the worktrees.** It ends in `rmtree`, and on 2026-09-28
    a caller handed the workers' version of this ``Path("")`` — which is the current directory: it
    deleted the whole server folder, source, virtualenv and data, from under a test run. So the
    path must be absolute and must sit directly inside the worktrees' own folder (:func:`_home`),
    or nothing happens at all.
    """
    if not _is_a_copy(worktree):
        return
    source = source_of(worktree)
    try:
        if source is not None:
            _git("worktree", "remove", "--force", str(worktree), cwd=source, timeout=_TIMEOUT)
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        if source is not None:
            # Without this git keeps `.git/worktrees/<name>` and still believes the checkout
            # exists, and a branch it believes is checked out cannot be deleted.
            _git("worktree", "prune", cwd=source, timeout=_TIMEOUT)
            if branch:
                _git("branch", "-D", branch, cwd=source, timeout=_TIMEOUT)
    except Exception:
        pass


def _is_a_copy(worktree: Path) -> bool:
    """Whether ``worktree`` is a path this module made: absolute, and directly inside :func:`_home`.

    Checked on the path as given and as resolved, so neither ``Path("")``, a relative path, nor a
    symlink pointing out of the copies' folder can reach `rmtree`.
    """
    try:
        text = str(worktree)
        if not text or not worktree.is_absolute():
            return False
        home = _home().resolve()
        return worktree.resolve().parent == home and worktree.parent.resolve() == home
    except (OSError, ValueError):
        return False
