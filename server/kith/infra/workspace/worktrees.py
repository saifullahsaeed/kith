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

So :func:`_carry_notes` copies it in, and :func:`_carry_untracked` copies the untracked source
files git would list — the ones written this session and not committed yet, which are exactly
the ones a builder is told to extend. Copied by hand rather than with ``--include-untracked``,
which writes an object for every untracked file in the tree, including whatever large thing a
stale ignore file lets through. The dependency folders the project's checks need are cloned in
last (:func:`_carry_dependencies`).

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
#: add` then writes the shared `.git/worktrees` registry. Two builders started together — which
#: is the whole point of a workflow's `parallel()` — hit both at once, and one of them dies with
#: "could not write index" before it has read a single file.
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
            _carry_untracked(source, where)
            _baseline(where)
            # After the baseline, and it would not matter if it were before: these are ignored
            # in the source, so `add -A` never stages them and they can never reach a diff.
            _carry_dependencies(source, where)

    if made.exit_code != 0:
        # Left as a failure rather than a silent fall back to the real tree. "He could not be
        # isolated, so he edited your files instead" is the one outcome this module exists to
        # make impossible.
        raise WorkspaceError(f"Could not open a private copy to work in: {made.output.strip()[:300]}")
    return where


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
        shutil.copytree(notes, copy / ".kith", dirs_exist_ok=True, ignore=_not_a_note)
    except Exception:
        pass


#: The largest single file carried from `.kith`. A note, a brief or a check script is a few
#: kilobytes; a file past this is data somebody generated, and copying it per builder is the
#: 150 MB problem again one file at a time.
_LARGEST_NOTE = 1_000_000


def _not_a_note(directory: str, names: list[str]) -> set[str]:
    """What `copytree` should skip in one directory of `.kith`.

    Build output anywhere; a workflow's own run folder (``scratch/workflows``), which holds the
    patches of the very run that is opening this copy; and any file too big to be a note.
    """
    skipped = {name for name in names if name in _NOT_NOTES}
    here = Path(directory)
    if here.name == "scratch":
        skipped.add("workflows")
    for name in names:
        try:
            if (here / name).is_file() and (here / name).stat().st_size > _LARGEST_NOTE:
                skipped.add(name)
        except OSError:
            skipped.add(name)
    return skipped


def _carry_untracked(source: Path, copy: Path) -> None:
    """Copy in the source files git is not tracking yet, because `stash create` will not.

    The files a main agent wrote an hour ago and has not committed are the ones a builder is
    most often told to extend, and a copy seeded from `stash create` alone does not have them —
    the builder is sent to edit a file that is not there. Only what git would *show* as
    untracked (`--exclude-standard`), so nothing the project ignores is carried, and nothing
    under `.kith`, which :func:`_carry_notes` has already handled with its own rules.

    Carried before the baseline, so they are part of what the worker was handed and never come
    back in its diff unless it changes them. Never raises, for the reason `_carry_notes` gives.
    """
    try:
        listed = _git("ls-files", "--others", "--exclude-standard", "-z", cwd=source, timeout=_TIMEOUT)
        if listed.exit_code != 0:
            return
        for name in listed.output.split("\0"):
            name = name.strip()
            if not name or name == ".kith" or name.startswith(".kith/"):
                continue
            origin = source / name
            try:
                if not origin.is_file() or origin.is_symlink() or origin.stat().st_size > _LARGEST_UNTRACKED:
                    continue
                target = copy / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target)
            except OSError:
                continue
    except Exception:
        pass


#: The largest untracked file carried into a copy. A source file somebody is writing is well
#: under this; past it, it is generated output that happens not to be ignored.
_LARGEST_UNTRACKED = 2_000_000

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
            if not name or name.startswith(".kith") or Path(name).name not in DEPENDENCY_DIRS:
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


def _stage_everything(worktree: Path) -> None:
    """Stage the copy as it stands, `.kith` included even where the project ignores it.

    Bare ``add -A`` first — no pathspec, so an ignored path is skipped silently instead of
    failing the whole add (see :func:`diff_in` for the builders that cost). Then ``.kith`` again
    with ``-f``, because a note the worker writes into an ignored `.kith/scratch/` is still the
    worker's work, and a check script it was told to write there came back as nothing.
    """
    _git("add", "-A", cwd=worktree, timeout=_TIMEOUT)
    if (worktree / ".kith").is_dir():
        _git("add", "-A", "-f", "--", ".kith", cwd=worktree, timeout=_TIMEOUT)


def _baseline(worktree: Path) -> None:
    """Record the copy as it was handed over, so its diff is only what the worker did.

    The copy is the seeded tree plus notes carried in by hand, and those notes are not the
    worker's work. The diff used to keep them out by excluding `.kith` wholesale — which also
    threw away every file a worker *wrote* there. A builder asked for a design mockup in
    `.kith/screens/` wrote it, edited it four times, and came back "changed nothing"; so did one
    asked for a check script in `.kith/scratch/`.

    So the handed-over state is committed inside the copy — a detached commit in the copy's own
    history, invisible to every branch of the real repository — and :func:`diff_in` diffs against
    it. Carried notes are in the baseline and never come back; whatever the worker adds or
    changes, anywhere, does. Also called again after earlier patches are applied into a copy
    (:func:`apply_in`), for the same reason.

    Never raises: a copy without a baseline diffs against its seed, which is the old behaviour.
    """
    try:
        _stage_everything(worktree)
        tree = _git("write-tree", cwd=worktree, timeout=_TIMEOUT).output.strip()
        if not tree:
            return
        made = _git(
            "commit-tree",
            tree,
            "-p",
            "HEAD",
            "-m",
            "what the worker was handed",
            cwd=worktree,
            timeout=_TIMEOUT,
        )
        commit = made.output.strip().splitlines()[-1].strip() if made.exit_code == 0 else ""
        if commit:
            _git("reset", "-q", "--soft", commit, cwd=worktree, timeout=_TIMEOUT)
    except Exception:
        pass


def apply_in(worktree: Path, patches: list[str]) -> str:
    """Apply earlier workers' patches inside a copy, then re-baseline. Returns "" or what failed.

    For a workflow step that has to see what earlier builders made — an integration step, or a
    review. Without this every agent starts from the original tree, and a reviewer sent to read
    `discover.py` finds a file that exists only as a patch in a folder it was never told about.

    Re-baselined afterwards, so the new worker's own diff is only its own work and the patches
    stay independent: applied in order, they stack.
    """
    for number, patch in enumerate(patches, start=1):
        if not patch.strip():
            continue
        holder = worktree.parent / f".{worktree.name}.incoming-{number}.patch"
        try:
            holder.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8")
            done = _git("apply", "--whitespace=nowarn", str(holder), cwd=worktree, timeout=_TIMEOUT)
        finally:
            holder.unlink(missing_ok=True)
        if done.exit_code != 0:
            return f"patch {number} of {len(patches)} did not apply: {done.output.strip()[:300]}"
    _baseline(worktree)
    return ""


def merge_in(worktree: Path, patches: list[str]) -> list[str]:
    """Apply several patches into one copy, each on its own. One entry per patch: "" or why not.

    Unlike :func:`apply_in` this does not stop at the first refusal and does not re-baseline:
    it is how a workflow puts its builders' work together, so the answer it owes is *which*
    patches fit, and the diff taken afterwards is the whole of what did. A patch that does not
    apply is left out entirely — `git apply` is atomic per patch — rather than half applied.
    """
    outcome: list[str] = []
    for number, patch in enumerate(patches, start=1):
        if not patch.strip():
            outcome.append("")
            continue
        holder = worktree.parent / f".{worktree.name}.merging-{number}.patch"
        try:
            holder.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8")
            done = _git("apply", "--whitespace=nowarn", str(holder), cwd=worktree, timeout=_TIMEOUT)
        except OSError as exc:
            outcome.append(f"could not be written out to apply: {exc}")
            continue
        finally:
            holder.unlink(missing_ok=True)
        outcome.append("" if done.exit_code == 0 else (done.output.strip()[:300] or "git apply refused it"))
    return outcome


def diff_in(worktree: Path) -> str:
    """Everything the worker changed in its copy, as one patch that will apply to the real tree.

    Staged first, into the worktree's own index, because that is the only way an untracked
    file — a file the worker *created*, which is most of what a builder does — appears in a
    diff at all. See the module docstring on why that staging is safe.

    ``--binary`` so an image or a compiled fixture survives the round trip, and no ``--stat``
    or colour: the reader is `git apply`, and after it a language model. Both want the patch.

    Diffed against the copy's baseline (:func:`_baseline`), not its seed. The notes carried into
    `.kith` are in that baseline, so they never come back as a patch — a project that tracks
    `.kith` would otherwise get a diff writing its own notes back over itself — while a file the
    worker writes under `.kith` does come back. That used to be excluded wholesale, which lost a
    design mockup and a check script in the first real workflow.

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
    _stage_everything(worktree)
    return _git("diff", "--cached", "--binary", cwd=worktree, timeout=_TIMEOUT).output


def close(worktree: Path) -> None:
    """Remove one copy and the administrative files git keeps for it.

    ``--force`` because the copy is always dirty by the time this runs — that is what it was
    for — and git otherwise refuses to remove a worktree with changes in it. The refusal is a
    good default for a person at a terminal and wrong here: the diff has already been taken,
    and the alternative to removing it is leaving a full checkout on disk forever.

    Never raises. This runs in the teardown of a worker that has already produced its report,
    and a failure to tidy up must not turn a finished piece of work into an error.

    **Refuses anything that is not one of the copies.** It ends in `rmtree`, and on 2026-09-28 a
    caller handed it ``Path("")`` — a builder whose copy had been stubbed to "" — which is the
    current directory: it deleted the whole server folder, source, virtualenv and data, from
    under a test run. So the path must be absolute and must sit directly inside the copies'
    own folder (:func:`_home`), or nothing happens at all.
    """
    if not _is_a_copy(worktree):
        return
    source = _repo_root(base_dir())
    try:
        if source is not None:
            _git("worktree", "remove", "--force", str(worktree), cwd=source, timeout=_TIMEOUT)
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        # What its commands were given to write in, and the boundary they ran under. Both named
        # from the copy, which has just been proven to be one.
        from kith import settings
        from kith.infra import confinement

        shutil.rmtree(confinement.scratch_for(worktree), ignore_errors=True)
        (Path(settings.DATA_DIR) / confinement.PROFILES / f"copy-{worktree.name}.sb").unlink(missing_ok=True)
        if source is not None:
            # Without this git keeps `.git/worktrees/<name>` and still believes the checkout
            # exists, so re-using the same worker id later fails with "already registered".
            _git("worktree", "prune", cwd=source, timeout=_TIMEOUT)
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
