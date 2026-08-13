"""Where the workspace is, and turning anything said about it into a real path.

Every path in the application goes through :func:`resolve`. There is no second way in: a
relative path anchors in the workspace, an absolute one is honoured but gated, because
"read ~/Documents/thing.pdf" is a reasonable request and should cost a click rather than be
impossible.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from kith import settings
from kith.services import permissions

from .base import WorkspaceError

#: Where his work lives. A short path with no space in it, because he writes shell
#: commands about it all day and every space is a quoting bug waiting to happen.
DEFAULT_ROOT = Path.home() / "Kith"

#: Bookkeeping that is his, not his work: conversation transcripts and the like. Dotted so
#: it stays out of the way in Finder and out of the file browser's default view.
INTERNAL_DIR = ".kith"


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
        from kith.infra.db import config_store
        from kith.settings import CONFIG_DB_PATH

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
        from kith.infra.db import repositories as repo
        from kith.kernel import session_context
        from kith.settings import AGENT_DB_PATH

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

    from kith.infra.db import config_store
    from kith.settings import CONFIG_DB_PATH

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


def display(path: Path | str) -> str:
    """How a path is named when it is shown to him: short where that is unambiguous, full where
    it is not.

    This exists because `glob` formatted its results with ``relative_to(root())`` and failed 14
    times out of 23 over 2026-08-10 to 2026-08-12, handing the model a raw
    ``ValueError: '…/ai-play/…' is not in the subpath of '/Users/…/Kith'``. Permission had
    already said yes — a folder linked to an active project is his (see
    ``permissions.linked_project_roots``) — so the search ran, found the files, and then could
    not name them. Permission knew about linked folders and presentation did not.

    A helper rather than a mended line, because the assumption is available to make at every
    call site that formats a path for him, and `glob` only happened to make it first.

    Relative to the **innermost** root that contains the path, so a project linked inside his own
    folder is named relative to the project — both answers are true and the specific one is the
    useful one. A path under no known root is returned whole: there is nothing for it to be
    relative to, and inventing something would name a file he cannot open.
    """
    target = Path(path)
    resolved = _resolved(target)

    innermost: Path | None = None
    for base in (root(), *permissions.linked_project_roots()):
        candidate = _resolved(base)
        if resolved != candidate and not resolved.is_relative_to(candidate):
            continue
        if innermost is None or len(candidate.parts) > len(innermost.parts):
            innermost = candidate
    if innermost is None:
        return str(target)
    relative = resolved.relative_to(innermost)
    # The root itself. "" is not a path and reads as a bug in whatever printed it.
    return str(relative) if relative.parts else "."


def _resolved(path: Path) -> Path:
    """``resolve`` that cannot raise. A path that will not resolve — a broken link, a folder gone
    since it was listed — still has to be namable, and its literal form is the honest answer."""
    try:
        return path.resolve()
    except OSError:
        return path


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
