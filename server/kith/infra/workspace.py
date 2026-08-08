"""His computer — which is now your computer.

This replaces the Docker sandbox. The container was a real machine of his own with root
inside it and no way out, and the trade it made was total: nothing of yours was reachable,
and neither was anything of yours he might have been useful with. He could not open a file
you pointed at, could not use the tools you already have installed, and everything he made
had to be copied out through ``docker cp`` before you could see it. Dropping it also drops
a dependency nobody should have to install to run a desktop app.

So he works here instead, in one folder you choose (``~/Kith`` by default), with your
shell, your PATH, and your installed programs. What used to be enforced by a container
boundary is now enforced by :mod:`kith.services.permissions`: inside the workspace he is
unrestricted, and outside it — or anything destructive anywhere — needs your yes.

Two things worth knowing:

**Every path goes through :func:`resolve`, and every command through the permission
check.** There is no second way in. A relative path anchors in the workspace; an absolute
one is honoured but gated, because "read /Users/you/Documents/thing.pdf" is a reasonable
request and should be answerable with a click rather than impossible.

**Commands run under a login shell** (``bash -lc``) with the workspace as the working
directory. That is what makes "use the tools already on this machine" true — his ``python3``
is your python3, his ``git`` is your git — and it is the whole point of moving him here.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from kith import settings
from kith.infra import renderer
from kith.services import permissions

SEARCH_URL = settings.SEARCH_URL

#: Where his work lives. A short path with no space in it, because he writes shell
#: commands about it all day and every space is a quoting bug waiting to happen.
DEFAULT_ROOT = Path.home() / "Kith"

#: Bookkeeping that is his, not his work: conversation transcripts and the like. Dotted so
#: it stays out of the way in Finder and out of the file browser's default view.
INTERNAL_DIR = ".kith"

#: How long one shell command may take before it is stopped.
#:
#: This was 900 — fifteen minutes — from when the shell was the only way to run anything, so
#: it had to cover a full build. The effect was that a command which stuck waiting for
#: something took the turn with it: nothing came back, nothing could be read, and by the time
#: it gave up the conversation had been abandoned. A person watching that does not see a
#: timeout, they see him frozen.
#:
#: Three minutes covers an npm install on a cold cache, which is the honest upper bound for
#: something you wait for. Genuinely long-lived work has `start_process` now, and genuinely
#: long test suites have `run_tests` with its own limit — so the shell no longer has to be
#: the tool that can do everything, and can be the tool that comes back.
_EXEC_TIMEOUT = 180
_OUTPUT_LIMIT = 8_000

#: What one `read_file` may return, separately from what a *command* may print.
#:
#: They shared the 8,000 and should not: clipping arbitrary command output there is sensible,
#: because the interesting part of a build log is at the end and the rest is noise. A source
#: file is not noise, and 8,000 characters is about 160 lines — under a typical React
#: component. Measured on a real project: a 271-line page returned 160 lines, and a 172-line
#: page returned 158, so reading it whole cost *two* calls where the second fetched fourteen
#: lines. A second call is a second round, and a round re-sends the ~20,000-token prompt
#: floor — twenty thousand tokens to collect fourteen lines of TSX.
_READ_LIMIT = 16_000

#: How far past the limit to go rather than force another call.
#:
#: The cost of stopping is not the characters saved, it is the round the caller must spend to
#: ask again. Overshooting by half a limit is always cheaper than that, so a file that is
#: nearly finished gets finished.
_READ_OVERSHOOT = 8_000
_MAX_WRITE = 5_000_000
_READ_DEFAULT_LINES = 400
_MAX_UI_READ = 2_000_000


class WorkspaceError(RuntimeError):
    """Anything that went wrong doing work on the machine."""


#: The old name. Kept because a dozen call sites catch it by name and because a tool
#: raising an error the loop does not recognise ends a turn instead of informing him.
SandboxError = WorkspaceError


@dataclass
class ExecResult:
    exit_code: int
    output: str


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
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
            "git",
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
    return shutil.which("git") is not None


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


# --------------------------------------------------------------------------- #
# Checkpoints
# --------------------------------------------------------------------------- #
#
# `commit_all` is deliberate, by design — a commit is a claim that something is a
# coherent step, and that has to stay a judgement, not a reflex. But "deliberate" also
# means there is no safety net between commits, and he edits files in bulk. This is
# that net: a snapshot taken automatically before every change, that never touches the
# real index, HEAD, or a branch, so it can run on every mutating call without any risk
# of disturbing a commit someone is deliberately building toward.
#
# The mechanism is entirely git's own: `write-tree`/`commit-tree` against a *shadow*
# index write commit objects with zero side effects on the real one, and a single
# chained ref (`refs/kith/checkpoint`) keeps every checkpoint reachable — and therefore
# safe from `git gc` — for as long as the ref points at the tip. The database row is
# bookkeeping for the UI only; lose it and the chain in git is still intact.
#
# Restoring is a person's decision, never his — there is no tool here an agent calls.

#: Under `refs/kith/`, not `refs/heads/*` or `refs/tags/*` — the same idea as
#: `refs/stash`, so it stays out of `git branch`/`git tag`/most GUIs by default.
_CHECKPOINT_REF = "refs/kith/checkpoint"

#: Shorter than the general 120s: a checkpoint attempt sits in front of every mutating
#: call in a turn, including a shell command in an otherwise read-only turn, so a slow
#: one should fail fast rather than stall the whole turn.
_CHECKPOINT_TIMEOUT = 20

#: git's own spelling for "this ref must not exist yet" in `update-ref`'s compare-and-
#: swap. An empty string is not the same thing and must not be used here.
_NO_REF_YET = "0" * 40


def _shadow_index(repo_root: Path) -> Path:
    """A persistent, alternate index for checkpoint snapshots — never the repo's real
    ``.git/index``.

    Reused across checkpoints rather than a fresh temp file each time: git's stat-cache
    carries over between calls, so a file that has not changed since the last checkpoint
    costs a stat, not a re-hash.

    Resolved via ``rev-parse --git-dir`` rather than a hardcoded ``repo_root / ".git"``,
    because a ``git worktree`` checkout has a *file* there, not a directory — joining a
    path onto that would raise.
    """
    git_dir = _git("rev-parse", "--git-dir", cwd=repo_root, timeout=_CHECKPOINT_TIMEOUT).output.strip()
    resolved = Path(git_dir)
    if not resolved.is_absolute():
        resolved = repo_root / resolved
    return resolved / "kith-checkpoint-index"


def _take_checkpoint(here: Path, trigger: str) -> dict | None:
    """Snapshot the repo `here` is in, before he changes anything in it. `None` when
    there is nothing to checkpoint — no git, no repo that could be made one, or nothing
    has changed since the last checkpoint.

    Best-effort, like `commit_all`: a checkpoint that fails must never take down the
    real change it was guarding, so every failure returns `None` rather than raising.
    """
    if not has_git():
        return None
    root_here = _repo_root(here)
    if root_here is None:
        if not ensure_repo():
            return None
        root_here = _repo_root(here)
        if root_here is None:
            return None

    env = {**os.environ, "GIT_INDEX_FILE": str(_shadow_index(root_here))}
    added = _git("add", "-A", cwd=root_here, env=env, timeout=_CHECKPOINT_TIMEOUT)
    if added.exit_code != 0:
        return None
    tree = _git("write-tree", cwd=root_here, env=env, timeout=_CHECKPOINT_TIMEOUT)
    if tree.exit_code != 0:
        return None
    tree_sha = tree.output.strip()

    parent = _git("rev-parse", "--verify", "-q", _CHECKPOINT_REF, cwd=root_here, timeout=_CHECKPOINT_TIMEOUT)
    parent_sha = parent.output.strip() if parent.exit_code == 0 else None
    if parent_sha:
        parent_tree = _git(
            "rev-parse",
            "--verify",
            "-q",
            f"{parent_sha}^{{tree}}",
            cwd=root_here,
            timeout=_CHECKPOINT_TIMEOUT,
        ).output.strip()
        if parent_tree == tree_sha:
            return None  # nothing has changed since the last checkpoint

    commit_args = ["commit-tree", tree_sha]
    if parent_sha:
        commit_args += ["-p", parent_sha]
    commit_args += ["-m", f"checkpoint: before {trigger}"]
    commit = _git(*commit_args, cwd=root_here, timeout=_CHECKPOINT_TIMEOUT)
    if commit.exit_code != 0:
        return None
    new_sha = commit.output.strip()

    updated = _git(
        "update-ref",
        _CHECKPOINT_REF,
        new_sha,
        parent_sha or _NO_REF_YET,
        cwd=root_here,
        timeout=_CHECKPOINT_TIMEOUT,
    )
    if updated.exit_code != 0:
        return None  # lost a race with a concurrent checkpoint on the same repo

    return {"sha": new_sha, "tree_sha": tree_sha, "parent_sha": parent_sha, "repo_root": str(root_here)}


def _checkpoint_before_change(trigger: str) -> None:
    """The hook: called right after a mutating call's permission check passes, before
    the change itself happens. A no-op outside a real turn, and at most once per repo
    per turn — see `session_context.in_turn`/`turn_notes` for why both of those matter.
    """
    from kith.services import session_context

    if not session_context.in_turn():
        return
    here = base_dir()
    root_here = _repo_root(here) or here
    notes = session_context.turn_notes()
    key = f"checkpointed:{root_here}"
    if notes.get(key):
        return
    notes[key] = True  # set before attempting — a failure here should not retry all turn
    try:
        result = _take_checkpoint(root_here, trigger)
        if result is None:
            return
        from kith.config import AGENT_DB_PATH
        from kith.infra.db.repositories import checkpoints as checkpoint_repo

        checkpoint_repo.add_checkpoint(
            AGENT_DB_PATH,
            repo_root=result["repo_root"],
            sha=result["sha"],
            tree_sha=result["tree_sha"],
            parent_sha=result["parent_sha"],
            conversation_id=session_context.current() or None,
            trigger=trigger,
        )
    except Exception:
        pass  # a checkpoint must never take down the real change it was guarding


def _mid_git_operation(repo_root: Path) -> str | None:
    """The name of whatever git operation is half-finished here, or `None`. `read-tree
    --reset` bypasses the safety check that would otherwise refuse to run mid-merge —
    which means running it anyway would silently discard someone's unresolved conflict
    resolution."""
    git_dir = _git("rev-parse", "--git-dir", cwd=repo_root, timeout=_CHECKPOINT_TIMEOUT).output.strip()
    resolved = Path(git_dir)
    if not resolved.is_absolute():
        resolved = repo_root / resolved
    for name, marker in (
        ("merge", "MERGE_HEAD"),
        ("rebase", "rebase-merge"),
        ("rebase", "rebase-apply"),
        ("cherry-pick", "CHERRY_PICK_HEAD"),
    ):
        if (resolved / marker).exists():
            return name
    return None


def restore_to_sha(repo_root: Path, sha: str) -> dict:
    """Make the repo's real working tree and index exactly match a checkpoint. Takes one
    more checkpoint of the current state first, unconditionally, so this is itself
    undoable by restoring forward again.

    ``git read-tree --reset -u`` alone is not enough: it only removes a working-tree file
    if the file's entry is in the *real* index and absent from the target tree, and
    nothing here has ever put anything into the real index — every checkpoint is taken
    through the shadow index precisely so it never touches the real one. So the real
    index has to be brought up to the *current* full state first (`add -A`), giving
    `read-tree` an accurate "before" to diff the target tree against; only then does it
    correctly delete a file that did not exist at checkpoint time. Verified against a
    real repo before writing this, not assumed from documentation.
    """
    if not has_git():
        raise WorkspaceError("git is not available on this machine.")
    mid = _mid_git_operation(repo_root)
    if mid:
        raise WorkspaceError(
            f"{repo_root} has a {mid} in progress — finish or abort it in git before restoring."
        )

    dirty_before = _git("status", "--porcelain", cwd=repo_root, timeout=_CHECKPOINT_TIMEOUT).output.strip()
    safety = _take_checkpoint(repo_root, "restore")

    synced = _git("add", "-A", cwd=repo_root, timeout=_CHECKPOINT_TIMEOUT)
    if synced.exit_code != 0:
        raise WorkspaceError(f"restore failed to read the current state: {synced.output.strip()[:300]}")
    result = _git("read-tree", "--reset", "-u", sha, cwd=repo_root, timeout=_CHECKPOINT_TIMEOUT)
    if result.exit_code != 0:
        raise WorkspaceError(f"restore failed: {result.output.strip()[:300]}")
    return {"dirty_before": dirty_before, "safety": safety}


# --------------------------------------------------------------------------- #
# Where we are
# --------------------------------------------------------------------------- #


#: Where a folder picked in the app is remembered. ``KITH_WORKSPACE`` still wins when it is
#: set, because someone who started the process pointing at a folder meant it.
ROOT_KEY = "workspace_dir"


#: Folders that must never be the workspace. Inside the workspace he needs no permission —
#: that is the whole design — so the choice of folder *is* the boundary. Picking your home
#: folder would not give him a big workspace, it would silently switch the permission
#: system off for every file you own. These are refused rather than warned about, because a
#: warning you can click past is not a boundary.
def _forbidden_roots() -> set[Path]:
    home = Path.home()
    return {
        Path("/"),
        home,
        home.parent,
        *(home / name for name in ("Desktop", "Documents", "Downloads", "Library")),
    }


def configured_root() -> Path:
    """The folder he is set to work in, without creating anything.

    Environment first, then whatever was picked in the app, then the default.
    """
    stored: object = None
    try:
        from kith.config import CONFIG_DB_PATH
        from kith.infra.db import config_store

        stored = config_store.load_settings(CONFIG_DB_PATH).get(ROOT_KEY)
    except Exception:
        # Before the config database exists — first run, a migration in flight — the
        # default is the right answer, and failing here would take the whole app down.
        stored = None
    for candidate in (settings.WORKSPACE_DIR, stored):
        text = str(candidate or "").strip()
        if text:
            return Path(text).expanduser()
    return DEFAULT_ROOT


def root() -> Path:
    """The workspace folder, created if it is not there yet.

    Read fresh rather than captured at import: it is a setting someone can change, and a
    module-level constant would mean a restart to take effect.
    """
    chosen = configured_root()
    try:
        chosen.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"Can't use {chosen} as his folder: {exc}") from None
    return chosen


def base_dir() -> Path:
    """Where the current work should happen: the active session's linked project folder, or root().

    Every turn runs inside ``session_context.working_in(...)``, so a tool called deep in
    the loop can find out which project it is on without every caller threading a directory down. A
    project with a linked folder makes that folder the working base — his relative paths and a
    command's cwd land in your project rather than in his own scratch space, which is what "work in
    ~/Desktop/my-app" is supposed to mean. Everything else — a project with no folder, a chat with
    no project, a test, a script — falls back to ``root()``.

    The permission model is unchanged by this: a linked folder was already a free zone
    (``permissions._inside_linked_project``), so this only decides *where paths land*, never *what
    he may touch*. Defensive to a fault — any failure in the lookup returns ``root()`` rather than
    breaking the file operation that asked.
    """
    try:
        from kith.config import AGENT_DB_PATH
        from kith.infra.db import repositories as repo
        from kith.services import session_context

        # The task in hand first, the conversation second. Only the conversation was
        # consulted for a long time, and that made linking a folder work in chat and do
        # nothing at all: an unbound session picks up a task in a folder-linked
        # project, the *session* is bound to no project, so this returned `root()` and every
        # relative path he wrote landed in ~/Kith instead of the project he was working on.
        # The unbound case — the common one — was the one that did not work.
        project_id = session_context.current_project()
        if not project_id:
            conversation = session_context.current()
            if conversation:
                project_id = repo.conversations.project_of(AGENT_DB_PATH, conversation)
        if project_id:
            row = repo.projects.get_project(AGENT_DB_PATH, project_id)
            directory = str((row or {}).get("directory") or "").strip()
            if directory and Path(directory).is_dir():
                return Path(directory)
    except Exception:
        pass
    return root()


def set_root(raw: str) -> Path:
    """Move him to a different folder, and say what that does and does not do.

    Nothing is copied. His existing work stays where it is — which is the honest
    behaviour: silently moving a folder that may hold gigabytes, or that the person has
    open in an editor, is not something a settings row should do behind a click. The
    interface says so; this only changes where he works next.
    """
    text = str(raw or "").strip()
    if not text:
        raise WorkspaceError("Pick a folder for him to work in.")
    chosen = Path(text).expanduser()
    if not chosen.is_absolute():
        raise WorkspaceError("That needs to be a full path.")
    chosen = Path(os.path.normpath(chosen))
    if chosen in _forbidden_roots():
        raise WorkspaceError(
            f"{chosen} is too broad to be his folder — he works without asking inside it, "
            "so this would hand him everything under it. Give him a folder of his own."
        )
    if chosen.exists() and not chosen.is_dir():
        raise WorkspaceError(f"{chosen} is a file, not a folder.")
    try:
        chosen.mkdir(parents=True, exist_ok=True)
        probe = chosen / ".kith-write-test"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise WorkspaceError(f"Can't write to {chosen}: {exc}") from None

    previous = configured_root()
    if chosen != previous:
        _carry_records(previous, chosen)

    from kith.config import CONFIG_DB_PATH
    from kith.infra.db import config_store

    config_store.update_settings(CONFIG_DB_PATH, {ROOT_KEY: str(chosen)})
    return chosen


def _carry_records(previous: Path, chosen: Path) -> None:
    """Bring his own records to the new folder, before anything is switched.

    His *work* stays behind deliberately. His records cannot, and the difference is not a
    preference: conversation transcripts live in ``.kith/`` inside the workspace, but the
    index that lists them lives in the databases, which do not move. Change the folder
    without them and every past conversation is still listed and every one of them opens
    empty — the index points at a file that is no longer under the current root. Nothing
    was deleted, which is exactly what makes it so hard to understand.

    Copied rather than moved, so the old folder remains a complete thing on disk. And done
    before the setting changes, so a failure here leaves him where he was rather than
    pointed at a folder his own history cannot be reached from.
    """
    source = previous / INTERNAL_DIR
    if not source.is_dir():
        return
    destination = chosen / INTERNAL_DIR
    try:
        shutil.copytree(source, destination, dirs_exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(
            f"Couldn't copy his conversations to {destination}: {exc}. Leaving him in "
            f"{previous} — moving him without them would leave his history unreadable."
        ) from None


def internal() -> Path:
    """Where Kith keeps its own records. Beside the databases, not inside his work.

    This used to be ``<workspace>/.kith``, and that overloaded one name with two meanings.
    ``.kith`` inside a folder now means *that project's* memory — the same idea as a
    ``CLAUDE.md`` living with the code it describes — so it cannot also mean "the transcript
    of every conversation he has ever had". A project folder that happened to be the
    workspace root would have had both, and a project copied elsewhere would have carried
    his whole history with it.

    Moving them also deletes a failure mode rather than relocating it. History living inside
    the workspace meant changing the workspace risked stranding it, which is why there was a
    whole copy-and-verify step for pointing him at a different folder; with records beside
    the databases, where the work happens is simply irrelevant to what he remembers.
    """
    directory = Path(settings.DATA_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    _migrate_internal(directory)
    return directory


_migrated = False


def _migrate_internal(destination: Path) -> None:
    """Bring records forward from the old in-workspace location, once.

    Copied rather than moved, and only when the destination has nothing of that name: a
    half-finished migration that has eaten the original is far worse than one that leaves a
    duplicate behind for someone to delete.
    """
    global _migrated
    if _migrated:
        return
    _migrated = True
    try:
        old = Path(root()) / INTERNAL_DIR
        if not old.is_dir():
            return
        for item in old.iterdir():
            target = destination / item.name
            if target.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    except OSError:
        # Never fatal. Failing to carry history forward must not stop him working.
        pass


#: The container's home. It appears in fourteen rows of his own memory, in notes he wrote,
#: in messages he sent, and in every task working-file path he was ever given — so it
#: cannot simply stop meaning anything. Paths under it are mapped onto the real workspace.
LEGACY_HOME = "/home/kith"

HOME = str(DEFAULT_ROOT)


def resolve(path: str) -> str:
    """Anchor a relative path in the working base; keep an absolute one as given.

    The base is the active project's linked folder when there is one (see :func:`base_dir`), so his
    relative paths land in your project rather than in his own folder — otherwise the workspace
    root. Absolute paths are deliberately not rejected here. Refusing them would make "look at
    ~/Downloads/report.pdf" impossible rather than merely gated, and the gate is the permission
    check — which can be answered — not this function.
    """
    text = (path or "").strip()
    if not text:
        return str(base_dir())

    # The container's home, rewritten. On macOS /home is an autofs mount, so creating
    # /home/kith fails with "Operation not supported" — which is exactly how this showed
    # up: he was handed /home/kith/work/task-41.md, could not create it, and reported
    # himself blocked on a task he was perfectly able to do. Anything that still says
    # /home/kith means "his folder", because for two years that is what it meant.
    if text == LEGACY_HOME or text.startswith(LEGACY_HOME + "/"):
        text = text[len(LEGACY_HOME) :].lstrip("/")
        return str(root() / text) if text else str(root())

    expanded = Path(text).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str(base_dir() / expanded)


def status() -> dict:
    """What the interface shows about where he works."""
    here = root()
    return {
        "root": str(here),
        "exists": here.exists(),
        "mode": str(permissions.mode()),
        "bytes": _tree_size(here),
        "entries": sum(1 for _ in here.iterdir()) if here.exists() else 0,
    }


# `ensure_ready()` used to sit here — a no-op kept, its docstring said, because "the name is
# load-bearing at a dozen call sites". There were none. The sites went with the sandbox
# container and the justification outlived the fact by long enough that nobody rechecked it.


# --------------------------------------------------------------------------- #
# Doing things
# --------------------------------------------------------------------------- #


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


#: Image types worth handing to a vision model. Anything else is bytes as far as this is
#: concerned and gets the ordinary "not text" refusal.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

#: Cap on an image handed to the model. A screenshot at 1440 is well under this; a 20MB
#: capture is a payload nobody meant to send and the message says how to shrink it.
_MAX_IMAGE_BYTES = 3_000_000


def read_image(path: str) -> dict:
    """An image, as a data URI the model can actually look at.

    This exists because of something the record made obvious. He spent hours redesigning a UI,
    took Playwright screenshots at 1440 and 390 on every pass, attached them as deliverables —
    and could not see a single one of them, because ``read_file`` decodes as text. His model
    takes images. He was working blind on the one kind of task where looking is the whole job.

    Returned as data rather than text because a tool result is a JSON string and cannot carry
    an image part; the agent loop turns this into a message the model can see. See
    :mod:`kith.services.agent_loop`.
    """
    import base64
    import mimetypes

    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_file():
        raise WorkspaceError(f"there's no {path}")
    size = target.stat().st_size
    if size > _MAX_IMAGE_BYTES:
        raise WorkspaceError(
            f"{path} is {size:,} bytes, over the {_MAX_IMAGE_BYTES:,} limit for looking at an "
            "image. Shrink it first — a screenshot does not need to be full resolution to be "
            "judged."
        )
    kind = mimetypes.guess_type(target.name)[0] or "image/png"
    encoded = base64.b64encode(target.read_bytes()).decode()
    return {
        "path": str(target),
        "bytes": size,
        # The loop looks for this key. Named plainly so a reader of a transcript can see why a
        # picture appeared in the conversation.
        "image": f"data:{kind};base64,{encoded}",
        "note": "Look at the image below and describe or judge what you actually see.",
    }


def read_file(path: str, offset: int | None = None, limit: int | None = None) -> str:
    """Read a file, line-numbered and windowed, so it composes with grep and cannot
    dump a huge file into context."""
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.exists():
        raise WorkspaceError(f"there's no {path}")
    if target.is_dir():
        raise WorkspaceError(f"{path} is a folder, not a file")
    start = max(1, offset or 1)
    count = limit if (limit and limit > 0) else _READ_DEFAULT_LINES
    end = start + count - 1
    try:
        lines = target.read_text(errors="replace").splitlines()
    except OSError as exc:
        raise WorkspaceError(f"cannot read {path}: {exc}") from None
    window = lines[start - 1 : end]

    # Fill up to the output budget a whole line at a time, and remember which line we stopped
    # on. Two things were wrong with slicing the finished string instead.
    #
    # It cut mid-line — a CSS rule or a JSX attribute sheared in half, which is not something
    # anyone can reason about. And worse, the "read with offset=" hint below only fired when
    # the *line* window ran out, so a short file over the byte budget produced a dead end:
    # "[truncated, 16078 chars total]" with no offset and no next step. Watched him hit
    # exactly that — a 79-line stylesheet, asked for whole, half returned, no way to ask for
    # the rest — so he read the same two files five times in one step and got the same first
    # half every time.
    # How much this read may return. The whole rest of the file, when finishing it costs less
    # than the round the caller would otherwise spend coming back for the remainder.
    numbered_window = [f"{start + i:6d}\t{line}" for i, line in enumerate(window)]
    whole = sum(len(one) + 1 for one in numbered_window)
    budget = _READ_LIMIT + _READ_OVERSHOOT if whole <= _READ_LIMIT + _READ_OVERSHOOT else _READ_LIMIT

    rendered: list[str] = []
    used = 0
    for numbered in numbered_window:
        if rendered and used + len(numbered) + 1 > budget:
            break
        rendered.append(numbered)
        used += len(numbered) + 1
    shown = len(rendered)
    body = "\n".join(rendered)

    last = start + shown - 1
    if shown < len(window) or len(lines) > end:
        # Whichever limit bit, the sentence is the same and it always carries the offset.
        reason = "output limit" if shown < len(window) else f"{count}-line window"
        body += (
            f"\n… [showing lines {start}-{last} of {len(lines)} — stopped at the {reason}; "
            f"read with offset={last + 1} for the rest]"
        )
    return body


#: Bytes the viewer will stream for one picture or document. Larger than the text limit
#: on purpose: a full-page screenshot at 2x is routinely over a megabyte, and the whole
#: point is to see it. Still a limit, because the browser holds all of it in memory.
_MAX_MEDIA_BYTES = 40_000_000


def media_file(path: str) -> tuple[Path, str]:
    """A file to be served as-is, and the type to send it as.

    For the things the viewer can show without decoding them as text — a screenshot, a
    PDF. Returns the resolved path rather than the bytes so the response can stream it
    and answer range requests, which is how a PDF viewer reads a document: it wants the
    trailer first, not the whole file.

    Same gate as every other read. Serving raw bytes over HTTP is exactly the shape of
    bug that turns a file viewer into "read any file on this machine", so the permission
    check is the first thing that happens and the path is resolved before it.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_file():
        raise WorkspaceError(f"there's no {path}")
    size = target.stat().st_size
    if size > _MAX_MEDIA_BYTES:
        raise WorkspaceError(
            f"{path} is {size:,} bytes, too big to show here (max {_MAX_MEDIA_BYTES:,}) — "
            "open it in another application instead"
        )
    kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return target, kind


def read_raw(path: str, max_bytes: int = _MAX_UI_READ) -> str:
    """The file exactly as it is, for the viewer — no line numbers, no window."""
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.exists():
        raise WorkspaceError(f"there's no {path}")
    size = target.stat().st_size
    if size > max_bytes:
        raise WorkspaceError(f"that file is too big to open here ({size} bytes; max {max_bytes})")
    try:
        return target.read_text()
    except UnicodeDecodeError as exc:
        raise WorkspaceError("that looks like a binary file, not text") from exc
    except OSError as exc:
        raise WorkspaceError(f"cannot read {path}: {exc}") from None


def grep(pattern: str, path: str = ".", glob: str | None = None, max_matches: int = 60) -> str:
    """Search with ripgrep if it is installed, grep if it is not.

    Falling back matters more here than it did in the container: there we shipped the
    image and knew rg was in it. On your machine it is whatever you happen to have.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if shutil.which("rg"):
        args = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-columns", "300"]
        if glob:
            args += ["--glob", glob]
        args += ["-e", pattern, str(target)]
    else:
        args = ["grep", "-rIn", "--color=never"]
        if glob:
            args += [f"--include={glob}"]
        args += ["-e", pattern, str(target)]
    result = run_command(f"{shlex.join(args)} 2>/dev/null | head -n {int(max_matches)}", timeout=60)
    out = result.output.strip()
    if not out:
        return f"No matches for {pattern!r} under {path}."
    lines = out.splitlines()
    tail = (
        f"\n… [showing first {max_matches} matches; narrow the pattern or set a path for the rest]"
        if len(lines) >= max_matches
        else ""
    )
    return _clip(out + tail)


def write_file(path: str, content: str) -> str:
    data = content.encode()
    if len(data) > _MAX_WRITE:
        raise WorkspaceError(f"content too large ({len(data)} bytes; max {_MAX_WRITE})")
    target = Path(resolve(path))
    permissions.require_path("write", target, root())
    _checkpoint_before_change("write_file")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        raise WorkspaceError(f"cannot write {path}: {exc}") from None
    return f"wrote {len(data)} bytes to {target}"


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(text: str, base: str, strip: str) -> str:
    """Re-base every line of ``text`` from the copy's indentation onto the file's.

    Used only by the whitespace-tolerant path. When the model's copy of a block was indented
    differently from the file, its replacement is indented to match its copy rather than the
    file, so pasting it verbatim would land at the wrong depth.

    Only the *base* indentation is exchanged. Indentation relative to that base is structure —
    the body of the function inside the block being replaced — and flattening it produces
    syntactically broken Python, which an earlier version of this did by reaching for
    ``lstrip``.
    """
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
            continue
        if not strip:
            body = line
        elif line.startswith(strip):
            body = line[len(strip) :]
        else:
            # Shallower than the copy's own base — nothing sensible to subtract.
            body = line.lstrip()
        out.append(base + body)
    return "\n".join(out)


def _tolerant_span(before: str, old: str) -> tuple[int, int, str] | None:
    """Find ``old`` in ``before`` ignoring each line's leading and trailing whitespace.

    The exact matcher refuses rather than guesses, and that is right — but it also refuses on
    a class of near-miss that is never actually ambiguous: the model copied the block
    correctly and got the indentation wrong, or the file uses tabs where the copy used
    spaces. Every one of those costs a whole round to rediscover, and the model's usual
    recovery is to re-read the file and try again with the same mistake.

    So: match line by line on stripped content, and accept **only** when exactly one window
    matches. Two candidates is genuine ambiguity and still refuses. The caller is told the
    match was tolerant rather than exact, because an edit that landed somewhere slightly
    different from where it was aimed is something a person reviewing the diff should see.

    Returns the character span to replace and the file's own indentation at that point.
    """
    old_lines = old.split("\n")
    # A single-line `old` with no exact match is not worth guessing at: one stripped line
    # matches far too easily, and the failure mode is an edit landing on the wrong line.
    if len(old_lines) < 2:
        return None
    wanted = [line.strip() for line in old_lines]

    lines = before.split("\n")
    # Offsets of each line's start, so a line window converts back to a character span.
    starts, at = [], 0
    for line in lines:
        starts.append(at)
        at += len(line) + 1

    hits = []
    for i in range(len(lines) - len(wanted) + 1):
        if all(lines[i + j].strip() == wanted[j] for j in range(len(wanted))):
            hits.append(i)
            if len(hits) > 1:
                return None  # ambiguous — fall through to the honest refusal
    if len(hits) != 1:
        return None

    i = hits[0]
    start = starts[i]
    end = starts[i + len(wanted) - 1] + len(lines[i + len(wanted) - 1])
    return start, end, _indent_of(lines[i])


def _apply_edit(before: str, old: str, new: str, path: str, replace_all: bool) -> tuple[str, int, bool]:
    """One edit against text already in hand. Returns the result, how many, and whether
    the match had to fall back to whitespace-tolerant matching.

    Split out of ``edit_file`` so a batch can apply several edits to one file in memory
    before anything is written — which is what makes the batch atomic.
    """
    if not old:
        raise WorkspaceError("old must be the exact text to replace — an empty string matches nothing")
    if old == new:
        raise WorkspaceError("old and new are identical, so there is nothing to change")

    found = before.count(old)
    if found > 1 and not replace_all:
        raise WorkspaceError(
            f"that text appears {found} times in {path}, so which one is ambiguous. Include "
            "more surrounding lines to pin down the one you mean, or pass replace_all to "
            "change every occurrence."
        )
    if found:
        after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
        return after, (found if replace_all else 1), False

    span = _tolerant_span(before, old)
    if span is None:
        raise WorkspaceError(
            f"that exact text is not in {path}. Whitespace and indentation count — read the "
            "part you mean to change and copy it verbatim."
        )
    start, end, indent = span
    shifted = _reindent(new, indent, _indent_of(old.split("\n")[0]))
    return before[:start] + shifted + before[end:], 1, True


def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """Replace an exact string in a file. Returns a diff of what changed.

    This exists because ``write_file`` was the only way to change anything, and rewriting a
    whole file to alter one line has three costs that all showed up in his work.
    It is expensive — a 12KB component is ~3,300 output tokens per edit. It is lossy: he
    regenerates from what he remembers reading, so anything he did not re-emit is gone, and
    while ``read_file`` was truncating without a way to continue, "anything he did not read"
    was in that category too. And it degrades formatting, because a model paying by the token
    to re-emit a file compresses it: his App.jsx ended up 55 lines averaging 220 characters,
    with one JSX line of 3,262.

    Exact string matching, no regex and no fuzzy fallback, and it refuses rather than guesses:

    * **Not found** is an error, not a no-op. A silent no-op reads as success and he moves on
      believing the change landed.
    * **Ambiguous** is an error too. If the string appears four times, replacing the first is
      a coin flip on which one he meant; the message says how many and what to do about it.

    Both refusals name the fix, because the caller is a model that will otherwise retry the
    identical call.

    One concession to reality, added later and deliberately narrow: if the text is not there
    verbatim but exactly one block matches it line-for-line ignoring indentation, that block
    is edited and the result says the match was tolerant. See ``_tolerant_span`` — two
    candidates still refuse, and a one-line ``old`` never takes this path.
    """
    target = Path(resolve(path))
    permissions.require_path("write", target, root())
    _checkpoint_before_change("edit_file")
    if not target.is_file():
        raise WorkspaceError(f"there's no {path} to edit")
    try:
        before = target.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkspaceError(f"cannot read {path} to edit it: {exc}") from None

    after, replacements, tolerant = _apply_edit(before, old, new, path, replace_all)
    data = after.encode()
    if len(data) > _MAX_WRITE:
        raise WorkspaceError(f"the result would be too large ({len(data)} bytes; max {_MAX_WRITE})")
    try:
        target.write_text(after)
    except OSError as exc:
        raise WorkspaceError(f"cannot write {path}: {exc}") from None

    report = _diff(path, before, after, old, replacements)
    return _NOTE_TOLERANT + report if tolerant else report


#: Prefixed to a diff whose match was whitespace-tolerant rather than exact. Worth saying out
#: loud: the edit landed where it was aimed, but not at the indentation it was aimed with.
_NOTE_TOLERANT = (
    "(matched ignoring indentation — your copy's whitespace did not match the file's, so the "
    "replacement was re-indented to fit. Check the diff.)\n"
)


def edit_files(edits: list[dict]) -> dict:
    """Apply several edits as one all-or-nothing change. Returns a combined diff.

    One edit per model round is the wrong unit for the work that actually happens. Renaming a
    helper used in eight places is eight rounds, and a round is not cheap: the whole prompt
    goes back over the wire each time, against a turn that gets forty of them. Batching a
    refactor into one call is the difference between finishing it and running out of room
    halfway through, which is a failure mode this project has watched happen.

    **Atomic, and that is the point.** Every edit is resolved, permission-checked and applied
    in memory first; nothing touches the disk until all of them have succeeded. A batch that
    fails on its sixth edit leaves the first five unwritten, because the alternative — a
    half-applied refactor across five files, reported as an error — is a worse place to be
    than not having started. It is also the state a model is least able to reason its way out
    of, since the error says what went wrong with edit six and nothing about the five that
    landed.

    **Edits to the same file compose in order.** They are applied to the running text, so an
    edit may legitimately depend on one before it, and an edit whose target a previous edit
    destroyed fails at that point rather than silently matching something else.
    """
    if not edits:
        raise WorkspaceError("no edits given — pass at least one {path, old, new}")

    # Resolve and permission-check everything before reading anything, so a batch that is
    # going to be refused is refused before it has half-read the disk.
    prepared = []
    for i, edit in enumerate(edits, start=1):
        raw = str(edit.get("path") or "").strip()
        if not raw:
            raise WorkspaceError(f"edit {i} has no path")
        target = Path(resolve(raw))
        permissions.require_path("write", target, root())
        prepared.append((i, raw, target, edit))

    # Once for the whole batch, not once per edit — the snapshot is "before any of the
    # batch," matching the batch's own atomicity: nothing here is written until every edit
    # has succeeded, so there is no in-between state worth a checkpoint of its own.
    _checkpoint_before_change("edit_files")

    texts: dict[Path, str] = {}
    originals: dict[Path, str] = {}
    counts: dict[Path, int] = {}
    tolerant_at: list[int] = []

    for i, raw, target, edit in prepared:
        if target not in texts:
            if not target.is_file():
                raise WorkspaceError(f"edit {i}: there's no {raw} to edit")
            try:
                texts[target] = target.read_text()
            except (OSError, UnicodeDecodeError) as exc:
                raise WorkspaceError(f"edit {i}: cannot read {raw} to edit it: {exc}") from None
            originals[target] = texts[target]
            counts[target] = 0
        try:
            after, made, tolerant = _apply_edit(
                texts[target],
                str(edit.get("old") or ""),
                str(edit.get("new") or ""),
                raw,
                bool(edit.get("replace_all")),
            )
        except WorkspaceError as exc:
            # Which edit, out of how many — a bare message about text not being found is
            # unactionable when six edits went out together.
            raise WorkspaceError(f"edit {i} of {len(prepared)} failed, so none were applied: {exc}") from None
        texts[target] = after
        counts[target] += made
        if tolerant:
            tolerant_at.append(i)

    for target, after in texts.items():
        data = after.encode()
        if len(data) > _MAX_WRITE:
            raise WorkspaceError(f"{target} would be too large ({len(data)} bytes; max {_MAX_WRITE})")

    written = []
    for target, after in texts.items():
        try:
            target.write_text(after)
        except OSError as exc:
            # Half-written is the one state the all-or-nothing promise cannot cover: the
            # earlier files are already on disk. Say so plainly rather than reporting a
            # clean failure the caller would take to mean nothing changed.
            done = ", ".join(str(p) for p in written) or "none"
            raise WorkspaceError(
                f"cannot write {target}: {exc}. Already written: {done}. The change is "
                "partly applied — check `changes` before doing anything else."
            ) from None
        written.append(target)

    diffs = [_diff(str(target), originals[target], texts[target], "", counts[target]) for target in texts]
    result = {
        "files": len(texts),
        "replacements": sum(counts.values()),
        "diff": _clip("\n".join(diffs)),
    }
    if tolerant_at:
        result["note"] = (
            f"edit{'' if len(tolerant_at) == 1 else 's'} {', '.join(map(str, tolerant_at))} "
            "matched ignoring indentation and were re-indented to fit — check the diff."
        )
    return result


#: A diff line longer than this is not readable as a line, so the change is shown as a
#: character window instead. His App.jsx has a 3,262-character line of JSX; a one-word edit to
#: it produced a 10,931-character unified diff in which the changed line was truncated *before*
#: the change — a page of context that showed nothing.
_DIFF_LINE = 220


def _window(before: str, after: str, old: str, path: str, replacements: int) -> str:
    """The change as a character window, for a file whose lines are too long to diff.

    Exact rather than guessed: the offset of the replaced text is known, so the window is
    centred on it and the line number is reported so the position is not lost.
    """
    at = before.find(old)
    line_no = before.count("\n", 0, at) + 1
    pad = 90
    lo = max(0, at - pad)
    was = before[lo : at + len(old) + pad].replace("\n", "⏎")
    # The same span in the new text: everything before the change is identical, so the offset
    # holds and only the replaced length differs.
    now = after[lo : at + len(old) + pad + 200].replace("\n", "⏎")
    lead = "…" if lo > 0 else ""
    return (
        f"{replacements} replacement{'' if replacements == 1 else 's'} in {path}, line {line_no}"
        f" (lines here are too long to diff, so this is the changed region)\n"
        f"- {lead}{was}…\n"
        f"+ {lead}{now}…"
    )


def _diff(path: str, before: str, after: str, old: str, replacements: int) -> str:
    """A unified diff of one edit, clipped.

    Returned rather than "ok" on purpose: the diff is the only way he can see that what he
    changed is what he meant to change, and it is the thing worth putting in front of a person
    reviewing an edit you did not watch. Three lines of context — enough to place the change, not
    enough to re-send the file he already has.
    """
    import difflib

    return_window = max((len(line) for line in before.splitlines()), default=0) > _DIFF_LINE
    if return_window and replacements == 1:
        return _window(before, after, old, path, replacements)

    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
            n=3,
        )
    )
    made = f"{replacements} replacement{'' if replacements == 1 else 's'} in {path}"
    if not lines:
        # replace() found the text and the result is identical — a no-change edit that is not
        # worth reporting as a success without saying so.
        return f"{made}, but the file is unchanged"
    return made + "\n" + _clip("".join(lines))


#: What a project is checked with, in the order the presence of a file decides it. Detection
#: rather than configuration: he should not have to be told what a project is, and the answer
#: is sitting in the directory.
_CHECKERS = (
    ("tsconfig.json", "npx tsc --noEmit", "TypeScript"),
    ("pyproject.toml", "ruff check .", "Ruff"),
    ("ruff.toml", "ruff check .", "Ruff"),
    ("package.json", "npm run --silent build", "the project's build"),
)


def check_code(path: str = ".") -> dict:
    """Run whatever this project is checked with, and report only what is wrong.

    He *could* shell out for this, and mostly did — he ran `npm run build` before claiming
    things, which is better discipline than most. But "mostly" is the problem: a check he has
    to remember is a check that is skipped on the round where it mattered. Detected from the
    directory so there is nothing to configure and nothing to get wrong.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_dir():
        target = target.parent
    for marker, command, label in _CHECKERS:
        if not (target / marker).is_file():
            continue
        proc = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            cwd=str(target),
            timeout=_EXEC_TIMEOUT,
        )
        out = (proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")).strip()
        return {
            "ran": command,
            "checker": label,
            "clean": proc.returncode == 0,
            # On success the output is noise — a build log nobody reads. On failure it is the
            # entire point, so it is kept.
            "problems": "" if proc.returncode == 0 else _clip(out),
        }
    return {
        "ran": "",
        "clean": True,
        "problems": "",
        "note": f"Nothing in {path} says how it is checked — no tsconfig.json, pyproject.toml or package.json.",
    }


def glob(pattern: str, path: str = ".") -> str:
    """Files matching a name pattern, newest first.

    ``grep`` finds text and ``list_files`` shows one directory; neither answers "where are the
    test files" or "which components exist". Newest first because the file he wants is usually
    the one most recently touched.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_dir():
        raise WorkspaceError(f"{path} is not a folder to search in")
    wanted = str(pattern or "").strip() or "*"
    skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build"}
    found = [
        item
        for item in target.rglob(wanted)
        if item.is_file() and not (skip & set(item.relative_to(target).parts))
    ]
    if not found:
        return f"Nothing under {path} matches {wanted}"
    found.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    shown = found[:200]
    lines = [str(item.relative_to(root())) for item in shown]
    body = "\n".join(lines)
    if len(found) > len(shown):
        body += f"\n… [{len(found) - len(shown)} more; narrow the pattern]"
    return _clip(body)


def list_files(path: str = ".") -> str:
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.exists():
        raise WorkspaceError(f"there's no {path}")
    return _clip(run_command(f"ls -la {shlex.quote(str(target))}").output)


def list_dir(path: str = ".") -> list[dict]:
    """One level, structured, for the file browser — with modification times, because
    "what did he touch last" is how anyone finds work in progress."""
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_dir():
        raise WorkspaceError(f"cannot list {path}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: item.name):
        # His own bookkeeping is not his work; it would only be clutter in the browser.
        if child.name == INTERNAL_DIR:
            continue
        try:
            info = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": info.st_size if child.is_file() else 0,
                "modified": int(info.st_mtime),
            }
        )
    return entries


def make_dir(path: str) -> None:
    target = Path(resolve(path))
    permissions.require_path("write", target, root())
    _checkpoint_before_change("make_dir")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"cannot create {path}: {exc}") from None


def move(source: str, destination: str) -> None:
    src, dst = Path(resolve(source)), Path(resolve(destination))
    permissions.require_path("write", src, root())
    permissions.require_path("write", dst, root())
    _checkpoint_before_change("move")
    if dst.exists():
        raise WorkspaceError(f"{dst.name} already exists here.")
    try:
        src.rename(dst)
    except OSError as exc:
        raise WorkspaceError(f"cannot rename {source}: {exc}") from None


def remove(path: str) -> str:
    """Put a file or folder in the Trash. Refuses the workspace root itself.

    The Trash rather than ``unlink``, and this was ``unlink`` until it mattered. Asked to
    delete a file from the Desktop, he did it with ``rm`` and the file was simply gone —
    no prompt, and nothing to undo. The prompt is fixed separately, in
    :mod:`kith.services.permissions`; this fixes the other half, which is that a delete
    anyone can get wrong should not be the one operation on the machine with no way back.
    macOS has a recoverable delete and every other app on the machine uses it.

    Returns where it went, so the answer can say "in the Trash" and mean it.
    """
    target = Path(resolve(path))
    if target.resolve() == root().resolve():
        raise WorkspaceError("that's his whole folder — not that.")
    permissions.require_path("delete", target, root())
    _checkpoint_before_change("remove")
    if not target.exists() and not target.is_symlink():
        raise WorkspaceError(f"there is nothing at {path}.")
    return trash_path(target)


def trash_path(target: Path) -> str:
    """Move an absolute path to the Trash, and say where it went.

    Separate from :func:`remove` because two different callers need the Trash and only one of
    them is him. His file operations go through ``remove``, which resolves the path against
    his workspace and asks permission first. This one is for things the app itself owns and
    is putting away — an uninstalled skill folder — where there is no path to resolve and
    nobody to ask. Both end up recoverable, which is the part that matters.
    """
    bin_ = Path.home() / ".Trash"
    if not bin_.is_dir():
        # Not macOS, or a home directory without one. Say what happened rather than
        # reporting "moved to the Trash" about a file that is gone for good.
        try:
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        except OSError as exc:
            raise WorkspaceError(f"cannot delete {target.name}: {exc}") from None
        return "deleted permanently (this machine has no Trash)"

    destination = _free_name(bin_, target.name)
    try:
        # move, not rename: the Trash can be on a different volume from the file.
        shutil.move(str(target), str(destination))
    except OSError as exc:
        raise WorkspaceError(f"cannot move {target.name} to the Trash: {exc}") from None
    return f"in the Trash as {destination.name}"


def _free_name(folder: Path, name: str) -> Path:
    """``report.md``, then ``report 2.md`` — Finder's own convention, so a Trash full of
    same-named files reads the way people expect it to."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, dot, suffix = name.partition(".")
    for index in range(2, 1000):
        candidate = folder / f"{stem} {index}{dot}{suffix}"
        if not candidate.exists():
            return candidate
    raise WorkspaceError(f"the Trash already has a thousand things called {name}.")


def kind_of(path: str) -> str:
    """``"file"``, ``"dir"``, or ``""`` when there is nothing there."""
    target = Path(resolve(path))
    if target.is_dir():
        return "dir"
    return "file" if target.exists() else ""


# --------------------------------------------------------------------------- #
# The web
# --------------------------------------------------------------------------- #


#: How much of a page to download before converting it to prose. A bound on bandwidth and
#: parsing time, *not* on what reaches the model — the conversion throws away the
#: overwhelming majority, and the text it produces is clipped separately.
_MAX_FETCH_BYTES = 5_000_000


def fetch_url(url: str) -> str:
    """A page, as the prose a reader would see.

    The download is bounded and the conversion is bounded, and getting those the wrong way
    round made this useless on anything real. It used to go through ``run_command``, which
    clips output to 8,000 characters — so the *HTML* was cut at 8,000 bytes and only then
    turned into text. On a modern page that is the middle of ``<head>``.

    Measured before the fix: the Wikipedia article on prompt engineering, 470,144 characters
    of markup, came back as 114 characters — its title and half a stray ``<link>`` tag.
    Hacker News gave 805. Only example.com worked, because example.com is smaller than the
    limit. The `<head>` strip below cannot help either, since with the closing tag cut off
    the pattern never matches.

    Downloading the whole thing costs nothing that matters: curl's output is not context.
    What reaches him is the same 8,000 characters it always was — of prose now instead of
    markup.
    """
    target = url.strip()
    if not re.match(r"^https?://", target):
        raise WorkspaceError("url must start with http:// or https://")
    command = (
        f"curl -sL --max-time 25 --max-filesize {_MAX_FETCH_BYTES} "
        f"-A 'Mozilla/5.0 (Kith)' {shlex.quote(target)}"
    )
    code, markup = _capture(command, timeout=30)
    if code != 0 and not markup:
        raise WorkspaceError("fetch failed (is this machine online?)")
    return _html_to_text(markup)


def browse_page(url: str) -> str:
    """Render a page in a real browser and return its visible text.

    Uses the desktop app's Chromium when it is running — already installed, already
    updated with Electron. Without it there is no fallback any more: the container carried
    a Playwright install and this machine may not have one, so the honest answer is to say
    so and let him use fetch_url.
    """
    target = url.strip()
    if not re.match(r"^https?://", target):
        raise WorkspaceError("url must start with http:// or https://")
    rendered = renderer.render(target)
    if rendered is not None:
        return _clip(rendered)
    raise WorkspaceError(
        "No browser renderer available — the desktop app provides it, so this needs Kith "
        "running in the app rather than a bare server. Try fetch_url for a static page."
    )


def searx_search(query: str, limit: int = 5) -> list[dict]:
    """Search via a SearXNG instance (JSON API), if one is reachable."""
    encoded = urllib.parse.quote(query)
    result = run_command(f"curl -sL --max-time 10 '{SEARCH_URL}/search?q={encoded}&format=json'", timeout=15)
    try:
        data = json.loads(result.output)
    except (ValueError, TypeError):
        raise WorkspaceError(
            f"SearXNG at {SEARCH_URL} did not return JSON (is it up, with the JSON format enabled?)"
        ) from None
    hits = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:300],
        }
        for item in (data.get("results") or [])[:limit]
    ]
    if hits:
        return hits
    blocked = data.get("unresponsive_engines") or []
    if blocked:
        detail = ", ".join(
            f"{item[0]}: {item[1]}" for item in blocked if isinstance(item, list) and len(item) > 1
        )
        raise WorkspaceError(f"every SearXNG engine was blocked ({detail})")
    return []


# --------------------------------------------------------------------------- #


def _tree_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _html_to_text(markup: str) -> str:
    if "<" not in markup:
        return _clip(markup)
    text = re.sub(r"(?is)<(script|style|head|noscript|svg)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return _clip(text.strip())


def _clip(text: str) -> str:
    if len(text) > _OUTPUT_LIMIT:
        return text[:_OUTPUT_LIMIT] + f"\n… [truncated, {len(text)} chars total]"
    return text
