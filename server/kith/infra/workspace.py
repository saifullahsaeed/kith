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

_EXEC_TIMEOUT = 900
_OUTPUT_LIMIT = 8_000
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
# of unattended edits. For an agent that rewrites files while you sleep, that is the gap worth
# closing before any of the others.
#
# One repository at the workspace root rather than one per project. His projects are database
# rows, not directories — he makes folders as he goes — so per-project would need a mapping
# that does not exist, and would miss everything he writes outside one. A single repo covers
# all of it and `git diff` still works per directory.

#: Never versioned: build output, dependencies, and his own bookkeeping. Without this the
#: first commit is 60MB of node_modules and every diff afterwards is unreadable.
_GITIGNORE = """\
node_modules/
dist/
build/
.venv/
venv/
__pycache__/
*.pyc
.DS_Store
.kith/
"""

#: Committed as, so a commit works on a machine where git has no global identity. Without
#: these git refuses with "please tell me who you are" and the history silently never starts.
_GIT_AUTHOR = ("Kith", "kith@localhost")


def _git(*args: str, check: bool = False) -> ExecResult:
    """One git command in the workspace, with an identity of its own.

    Deliberately not through :func:`run_command`: that asks the permission layer, and these
    are the app's own bookkeeping rather than something he decided to run. The identity is
    passed per-invocation so nothing depends on, or alters, the machine's git config.
    """
    here = root()
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
        timeout=120,
    )
    out = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
    if check and proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {out.strip()[:300]}")
    return ExecResult(exit_code=proc.returncode, output=out)


def has_git() -> bool:
    return shutil.which("git") is not None


def ensure_repo() -> bool:
    """Make the workspace a repository if it is not one. True when history is available."""
    if not has_git():
        return False
    here = root()
    if not (here / ".git").is_dir():
        if _git("init", "-q").exit_code != 0:
            return False
    ignore = here / ".gitignore"
    if not ignore.exists():
        ignore.write_text(_GITIGNORE)
    return True


def commit_all(message: str) -> str:
    """Commit whatever changed, and say what. Empty string when there was nothing.

    Called by him, deliberately, and never on a timer. The first version committed at the end
    of every tick, which was wrong twice over: a tick is not a unit of work — a task takes many,
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
    """Where Kith keeps its own records inside the workspace."""
    directory = root() / INTERNAL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


#: The container's home. It appears in fourteen rows of his own memory, in notes he wrote,
#: in messages he sent, and in every task working-file path he was ever given — so it
#: cannot simply stop meaning anything. Paths under it are mapped onto the real workspace.
LEGACY_HOME = "/home/kith"

HOME = str(DEFAULT_ROOT)


def resolve(path: str) -> str:
    """Anchor a relative path in the workspace; keep an absolute one as given.

    Absolute paths are deliberately not rejected here. Refusing them would make "look at
    ~/Downloads/report.pdf" impossible rather than merely gated, and the gate is the
    permission check — which can be answered — not this function.
    """
    text = (path or "").strip()
    if not text:
        return str(root())

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
    return str(root() / expanded)


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


def ensure_ready() -> None:
    """Kept for the call sites that used to guarantee a container was up.

    Now it only guarantees the folder exists, which :func:`root` already does — but the
    name is load-bearing at a dozen call sites and a no-op is cheaper than a rename that
    touches all of them.
    """
    root()


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
    permissions.require_command(command, root())
    here = root()
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            timeout=timeout,
            cwd=str(here),
            env={**os.environ, "KITH_WORKSPACE": str(here)},
        )
    except FileNotFoundError:
        raise WorkspaceError("No bash on this machine — can't run commands.") from None
    except subprocess.TimeoutExpired:
        raise WorkspaceError(
            f"That took longer than {timeout}s and was stopped. Anything long-running "
            "(a server, a watcher, a big build) should be started in the background with "
            "`nohup … &`, which returns straight away."
        ) from None
    combined = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
    return ExecResult(exit_code=proc.returncode, output=_clip(combined))


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
    rendered: list[str] = []
    used = 0
    for index, line in enumerate(window):
        numbered = f"{start + index:6d}\t{line}"
        if rendered and used + len(numbered) + 1 > _OUTPUT_LIMIT:
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
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        raise WorkspaceError(f"cannot write {path}: {exc}") from None
    return f"wrote {len(data)} bytes to {target}"


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
    """
    if not old:
        raise WorkspaceError("old must be the exact text to replace — an empty string matches nothing")
    if old == new:
        raise WorkspaceError("old and new are identical, so there is nothing to change")

    target = Path(resolve(path))
    permissions.require_path("write", target, root())
    if not target.is_file():
        raise WorkspaceError(f"there's no {path} to edit")
    try:
        before = target.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkspaceError(f"cannot read {path} to edit it: {exc}") from None

    found = before.count(old)
    if found == 0:
        raise WorkspaceError(
            f"that exact text is not in {path}. Whitespace and indentation count — read the "
            "part you mean to change and copy it verbatim."
        )
    if found > 1 and not replace_all:
        raise WorkspaceError(
            f"that text appears {found} times in {path}, so which one is ambiguous. Include "
            "more surrounding lines to pin down the one you mean, or pass replace_all to "
            "change every occurrence."
        )

    after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
    data = after.encode()
    if len(data) > _MAX_WRITE:
        raise WorkspaceError(f"the result would be too large ({len(data)} bytes; max {_MAX_WRITE})")
    try:
        target.write_text(after)
    except OSError as exc:
        raise WorkspaceError(f"cannot write {path}: {exc}") from None

    return _diff(path, before, after, old, found if replace_all else 1)


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
    reviewing an unattended edit. Three lines of context — enough to place the change, not
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
    to remember is a check that is skipped on the tick where it mattered. Detected from the
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
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"cannot create {path}: {exc}") from None


def move(source: str, destination: str) -> None:
    src, dst = Path(resolve(source)), Path(resolve(destination))
    permissions.require_path("write", src, root())
    permissions.require_path("write", dst, root())
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


def copy_out(source_path: str, destination: Path) -> None:
    """Copy something to somewhere else on the machine.

    A plain copy now that both ends are the same filesystem. It exists at all because the
    file browser still offers "put a copy somewhere I choose", which is a reasonable thing
    to want even when the original is already reachable in Finder.
    """
    src = Path(resolve(source_path))
    permissions.require_path("read", src, root())
    permissions.require_path("write", Path(destination), root())
    try:
        if src.is_dir():
            shutil.copytree(src, Path(destination) / src.name, dirs_exist_ok=True)
        else:
            Path(destination).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, destination)
    except OSError as exc:
        raise WorkspaceError(f"cannot copy {source_path}: {exc}") from None


# --------------------------------------------------------------------------- #
# The web
# --------------------------------------------------------------------------- #


def fetch_url(url: str) -> str:
    target = url.strip()
    if not re.match(r"^https?://", target):
        raise WorkspaceError("url must start with http:// or https://")
    result = run_command(f"curl -sL --max-time 25 -A 'Mozilla/5.0 (Kith)' {shlex.quote(target)}", timeout=30)
    if result.exit_code != 0 and not result.output:
        raise WorkspaceError("fetch failed (is this machine online?)")
    return _html_to_text(result.output)


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
