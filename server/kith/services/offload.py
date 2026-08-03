"""Spilling an aged-out tool result to a file instead of throwing its tail away.

A long research turn evicts old tool results to stay inside the context window. Truncating
each to a stub is cheap but lossy: the model, needing the part that was cut, re-runs the
tool and pays for the same output a second time. Writing the whole result to a file it can
``read_file`` keeps it one round away instead of gone — the pattern the field converged on
(Anthropic's context-editing clears tool results to disk; Cursor writes long tool output to
files the agent re-reads).

**Where.** Inside the workspace, under a hidden ``.kith/offload/<conversation>``. Inside the
root a read is never gated (see ``permissions.check_path``), so the model can fetch a spilled
result without a prompt in any mode; ``.kith`` keeps it out of the file browser and out of
git. Outside the root — the databases' ``DATA_DIR`` — a read would prompt, which defeats the
point.

**Lifecycle.** A conversation's spill is cleared at the start of each of its turns. A past
turn's tool results never re-enter the prompt (``conversations.messages`` replays only prose),
so nothing beyond the turn that wrote a spill can ever refer to it — there is nothing to keep.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from kith.infra import workspace


def _dir(conversation_id: str) -> Path:
    """Where one conversation's spilled results live. Hidden, inside the workspace.

    ``configured_root`` rather than ``root`` so merely locating the folder — which ``clear``
    does every turn — never creates the workspace as a side effect. Only ``save`` makes
    directories, and only when there is actually something to spill.
    """
    name = conversation_id or "loose"
    return workspace.configured_root() / workspace.INTERNAL_DIR / "offload" / name


def save(conversation_id: str, name: str, content: str) -> str:
    """Write one aged-out result and return the absolute path to read it back.

    Named by a hash of its content, so re-offloading the same result is idempotent and the
    stub that points at it stays byte-stable across rounds — which matters, because a moving
    path would invalidate the prompt cache the compaction is protecting.

    Returns "" if the write fails: the caller falls back to a plain trim, which is a worse
    stub but never a broken turn.
    """
    place = _dir(conversation_id)
    digest = hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()[:8]
    safe = "".join(c for c in (name or "tool") if c.isalnum() or c in "-_") or "tool"
    path = place / f"{safe}-{digest}.txt"
    try:
        place.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError:
        return ""
    return str(path)


def clear(conversation_id: str) -> None:
    """Remove a conversation's spilled results. Best-effort: never fatal to a turn."""
    try:
        shutil.rmtree(_dir(conversation_id))
    except (OSError, FileNotFoundError):
        pass
