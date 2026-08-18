"""Who did this — which stops being a rhetorical question the moment two people share a project.

Every task on this machine says `created_by = 'kith'`. All 87 of them. Every comment says the
same, 625 of 628. That is not a bug while there is one person: "him or me" is the whole of the
question a single-user board has to answer. It stops being the whole of it as soon as a second
person clones `.kith/` and files something, and it is the one fact that cannot be recovered
afterwards — a task with no author never had one, and no later migration can invent it.

**Identity is whatever git will put on the commit.** Not a setting, not a profile, not a row.
The work syncs by being committed, so the name attached to the work should be the name attached
to the commit that carries it; any second answer is a second source of truth for the same fact,
and it would be the one that disagrees.

Read from the project's own repository when there is one — a work address and a personal one are
a real thing people have, and `git config` is already where they keep that distinction.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

#: How long an answer is trusted. `git config` is a subprocess and this is read on every task
#: written; a minute is far longer than a burst of work and far shorter than someone changing
#: their git identity and wondering why nothing noticed.
_TTL = 60.0

_cached: dict[str, tuple[float, str]] = {}
#: Read on the way to writing a task, and turns run on their own threads. `permissions._linked`
#: guards its cache the same way — a dict is safe enough under the GIL that nothing corrupts,
#: but the read-then-write is not atomic, and two turns starting together would both shell out.
#: The lock costs nothing here and makes the invariant a fact rather than an accident.
_state = threading.Lock()


def whoami(directory: str | Path | None = None) -> str:
    """The identity git would attribute a commit to here. Empty when there is none.

    Empty rather than a guess. A machine with no git identity configured is a machine where
    nobody has said who they are, and `unknown` written into 87 rows is worse than a blank —
    it looks like an answer.
    """
    where = str(directory or "")
    now = time.monotonic()
    with _state:
        hit = _cached.get(where)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    # Outside the lock: this is a subprocess, and holding a lock across one would make every
    # other turn wait on a `git config` that is not theirs. Two racing readers both shell out
    # and write the same answer, which is wasteful once and never wrong.
    found = _ask(where, "user.email") or _ask(where, "user.name")
    with _state:
        _cached[where] = (now, found)
    return found


def _ask(directory: str, key: str) -> str:
    """One `git config` read, or "" for every way it can fail.

    Never raises. This is called on the way to writing a task, and a task that could not be
    written because nobody had configured git would be an absurd failure.
    """
    try:
        done = subprocess.run(
            ["git", "config", "--get", key],
            cwd=directory or None,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def forget() -> None:
    """Drop the cache. For tests, and for the moment after somebody changes their git identity."""
    with _state:
        _cached.clear()
