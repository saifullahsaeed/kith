"""Which conversation, and which project — answered from where you are standing.

This is the part that decides whether the CLI is usable by another agent at all. A command
that needs a conversation id before it can say anything is a command Claude has to think about
every single time; one that continues *this repository's* thread is a command it can fire
without deciding anything. So the id is resolved rather than demanded, and every layer of the
resolution exists for a case the layer above it cannot cover:

1. ``--conversation`` — you said so explicitly. Accepts a prefix (see `resolve_id`).
2. ``$KITH_CONVERSATION`` — the composability hook. Export it once and every later ``kith
   send`` in that shell lands in the same chat, which is how a wrapper script or an agent
   pins a session without threading an argument through everything it runs.
3. The sticky file — this directory's last conversation. The common case, and the one nobody
   has to know about.
4. Nothing: start one, bind it to the project this directory belongs to, remember it.

**The binding happens once and only on the first message.** ``conversations.set_project``
refuses to move a conversation that already has a project — deliberately; a session is stuck
with what it picked, and wanting a different project is what a new conversation is for. The
web client learned this the expensive way: it used to write the binding on a *second* request
after the stream reported the id, which meant the first turn of every chat — the turn where
somebody says what they want — was assembled with no project at all, and 54 of 66
conversations on this machine never acquired a binding. So `send` passes ``projectId`` in the
body of the first message and never tries to rebind afterwards.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from kith.cli.errors import FAILED, Failure

#: Per-person CLI state — which conversation each directory is in.
#:
#: Fixed at ``~/.kith`` rather than following ``settings.DATA_DIR``, and that is a correction
#: rather than a shortcut. ``DATA_DIR`` is ``server/data`` from a checkout and ``~/.kith``
#: frozen, so following it would give the same person two separate sets of sessions depending
#: on which build of the CLI they happened to run — and both builds talk to the same server.
#: "Which conversation is this directory in" is a fact about the person and the machine, not
#: about how the binary was produced.
#:
#: The token is the opposite case and is resolved the opposite way (see ``client.find_token``):
#: it must match whichever server is answering, and each server writes it into its own data
#: directory.
STATE_PATH = Path.home() / ".kith" / "cli" / "sessions.json"

#: Markers of a project root, checked walking up from the working directory. ``.kith`` first:
#: a repository Kith has worked in has one, and it is a stronger signal than ``.git`` for a
#: checkout that contains several projects as subdirectories.
ROOTS = (".kith", ".git")


#: macOS and Windows match paths without regard to case; Linux does not. This is not a
#: hypothetical: on the machine this was written on, one project is recorded at
#: ``…/Desktop/Personal/ai-play`` while the shell sits in ``…/Desktop/personal/…`` — the same
#: directory on disk and two different strings. ``Path.resolve()`` does not fix it, because it
#: resolves symlinks and leaves case alone. Comparing raw strings would silently fail to find
#: the project for a directory that is plainly inside it, and the only symptom would be a
#: conversation quietly starting unbound.
_CASE_BLIND = sys.platform in ("darwin", "win32")


def _comparable(path: Path) -> str:
    text = os.path.normcase(str(path))
    return text.lower() if _CASE_BLIND else text


def _at_or_under(root: Path, here: Path) -> bool:
    """Is `here` the same directory as `root`, or inside it?

    ``samefile`` first, because it asks the filesystem — it is right about case, symlinks and
    every mount quirk at once. It only works when both paths exist, which is why the string
    comparison is still here underneath rather than replaced by it.
    """
    try:
        if root.samefile(here):
            return True
    except OSError:
        pass
    root_key = _comparable(root).rstrip(os.sep)
    return _comparable(here).startswith(root_key + os.sep)


def project_root(start: Path | None = None) -> Path:
    """The directory this work belongs to — the nearest ancestor holding a root marker.

    Falls back to the starting directory rather than to the home directory. A sticky session
    keyed on ``$HOME`` would be shared by every unrelated command run outside a repository,
    which is the one way this could silently put a message in the wrong conversation.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if any((directory / marker).exists() for marker in ROOTS):
            return directory
    return here


# --------------------------------------------------------------------------- #
# The sticky file
# --------------------------------------------------------------------------- #


def _read_state() -> dict[str, Any]:
    try:
        loaded = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {"sessions": {}, "pinned": {}}
    if not isinstance(loaded, dict):
        return {"sessions": {}, "pinned": {}}
    loaded.setdefault("sessions", {})
    loaded.setdefault("pinned", {})
    return loaded


def _write_state(state: dict[str, Any]) -> None:
    """Replace the file atomically, and never fail the command over it.

    Written through a temporary file and a rename because two shells running ``kith send`` at
    once is the expected case, not the exotic one — that is the entire scenario this CLI was
    built for. A half-written JSON file would lose every directory's session, not just the one
    being written.

    Swallowing the error is deliberate. This is bookkeeping: the message was already sent and
    the answer is already on screen, and failing the command now would report a failure for
    something that succeeded.
    """
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        scratch = STATE_PATH.with_suffix(f".{os.getpid()}.tmp")
        scratch.write_text(json.dumps(state, indent=2, sort_keys=True))
        scratch.replace(STATE_PATH)
    except OSError:
        pass


def remembered(directory: Path) -> dict[str, Any]:
    return _read_state()["sessions"].get(str(directory), {}) or {}


def remember(directory: Path, conversation_id: str, project_id: int | None) -> None:
    state = _read_state()
    state["sessions"][str(directory)] = {
        "conversationId": conversation_id,
        "projectId": project_id,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_state(state)


def forget(directory: Path) -> None:
    state = _read_state()
    state["sessions"].pop(str(directory), None)
    _write_state(state)


def pin(directory: Path, project_id: int) -> None:
    state = _read_state()
    state["pinned"][str(directory)] = project_id
    _write_state(state)


def pinned(directory: Path) -> int | None:
    value = _read_state()["pinned"].get(str(directory))
    return int(value) if isinstance(value, int) else None


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def resolve_project(projects: list[dict], directory: Path) -> dict | None:
    """Which project owns this directory: a pin, else the deepest matching folder.

    Deepest rather than first, and that is the whole subtlety. Projects nest — a repository
    with a project for the whole thing and another for one service inside it — and the first
    match in an arbitrary order would put a message about the service into the umbrella
    project roughly half the time. Longest path wins, which is the only answer that is stable
    regardless of what order the server lists them in.

    ``directory`` is None for a project that is not code — research, a shortlist — and those
    are skipped rather than matched against an empty string, which would match everything.
    """
    chosen = pinned(directory)
    if chosen is not None:
        for project in projects:
            if int(project.get("id") or 0) == chosen:
                return project

    best: dict | None = None
    best_depth = -1
    here = directory.resolve()
    for project in projects:
        folder = str(project.get("directory") or "").strip()
        if not folder:
            continue
        try:
            root = Path(folder).expanduser().resolve()
        except OSError:
            continue
        if _at_or_under(root, here):
            depth = len(root.parts)
            if depth > best_depth:
                best, best_depth = project, depth
    return best


def resolve_id(wanted: str, known: list[str]) -> str:
    """Expand a conversation id prefix, or refuse to guess between two.

    Ids are a sortable timestamp plus six random hex — ``20260916-143022891-a3f9c1`` — chosen
    so a folder of transcripts sorts usefully in Finder. That shape pays off somewhere it was
    not designed for: a prefix of a timestamped id is meaningful to a human in a way a prefix
    of a UUID is not, so ``-c 20260916-1430`` is a thing somebody can type from what they saw
    on screen.

    An ambiguous prefix lists the candidates rather than picking the newest. Picking would be
    right most of the time, and the times it was wrong it would put a message in a stranger's
    conversation with nothing on screen to say so.
    """
    wanted = wanted.strip()
    if not wanted:
        return ""
    if wanted in known:
        return wanted
    matches = [candidate for candidate in known if candidate.startswith(wanted)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise Failure(f"no conversation starting {wanted!r}", FAILED, "list them with: kith conversations")
    raise Failure(
        f"{wanted!r} matches {len(matches)} conversations",
        FAILED,
        "did you mean " + ", ".join(matches[:4]) + ("…" if len(matches) > 4 else ""),
    )


def from_environment() -> str:
    return (os.environ.get("KITH_CONVERSATION") or "").strip()
