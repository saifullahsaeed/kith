"""The net under everything he changes.

A commit is deliberate; this is not. It takes a silent snapshot before anything destructive
so there is a way back that does not depend on him having thought to make one.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import WorkspaceError
from .git import _git, _repo_root, ensure_repo, has_git
from .paths import base_dir

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
        from kith.infra.db.repositories import checkpoints as checkpoint_repo
        from kith.settings import AGENT_DB_PATH

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
