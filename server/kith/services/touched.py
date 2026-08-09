"""What this conversation has opened or changed, and whether it is still true.

Two measured problems share one missing fact. His own compaction code records the first:
in one session he read ``ModelsSettings.tsx`` fifteen times and ``.kith/memory.md`` thirteen,
and 54% of every read he made was of a file he had already read. `conversations.full_messages`
records the second, in its own docstring — a tool result from hours ago is replayed as though
it were still true, "which it might not be", accepted as a deliberate risk.

Neither is answerable from the transcript. The transcript has the bytes a read returned; it
does not have what those bytes were a version *of*, so nothing downstream can tell a read that
still holds from one the file has moved past. That fact only exists at the moment of the touch,
which is why this records there.

**The stamp is ``size:mtime_ns``, not a content hash.** A read returns a window
(``offset``/``limit``), so hashing what came back would answer "is this window the same" rather
than "is the file the same". Hashing the file instead means a full re-read of every file he
touches, on his most-called tool. A stat is O(1) and answers the question actually being asked.
It can be wrong in exactly one direction — a rewrite to identical content with a preserved
mtime reads as unchanged — and every editor, his own tools included, moves mtime.

**Staleness is computed, never stored.** The row holds the version he saw; whether that is
current is the comparison against the file now. So a file you change in your own editor, with
no tool call anywhere, is caught on the next turn without anything having written a row for it.

Called from ``run_tool``, which is the one place every tool call passes through and already
knows the conversation. The alternative — recording inside each file tool — is five call sites
today and forgotten by the sixth.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra import workspace
from kith.infra.db import repositories as repo
from kith.services import session_context

#: Which tools touch a file, and what to call what they did. `read` and `wrote` rather than
#: the tool names: the manifest is read by a model deciding whether to open something again,
#: and "wrote" is the useful distinction — it means he already knows what is in there.
_ACTIONS = {
    "read_file": "read",
    "write_file": "wrote",
    "edit_file": "wrote",
    "edit_files": "wrote",
    "delete_file": "wrote",
}

#: How many files the manifest names before it starts dropping the oldest. This is the one
#: block that grows for the whole life of a conversation, and it is carried on every turn of
#: it, so it needs a ceiling. Forty is roughly a page — enough to cover a real working session
#: without the list becoming the thing he reads instead of the request.
MANIFEST_LIMIT = 40


def record(path: Path, tool_name: str, arguments: dict) -> None:
    """Note that this conversation touched whatever files this call touched.

    Silent about everything it cannot answer: a tool that touches no file, work belonging to
    no conversation (a tick, a script, a test), an argument shaped in a way this does not
    recognise. None of those are errors, and none of them should be able to fail a tool call
    that has already run — this is bookkeeping about the call, not part of it.
    """
    action = _ACTIONS.get(tool_name)
    conversation_id = session_context.current()
    if not action or not conversation_id:
        return
    for wanted in _paths(tool_name, arguments or {}):
        try:
            resolved = workspace.resolve(wanted)
            repo.touches.touch(path, conversation_id, resolved, action, _version(resolved))
        except Exception:
            # Bookkeeping must never fail the call it describes. The tool has already run and
            # its result is already the answer; a database that would not take a row about it
            # is not a reason to hand the model an error for work that succeeded.
            continue


def seen(path: Path, conversation_id: str) -> list[dict]:
    """Every file this conversation has touched, each with whether it has moved since.

    ``stale`` is the whole point: it is False when what he last saw is what is there now, and
    True when the file has changed under him — including when it changed with no tool call
    involved.
    """
    rows = repo.touches.touched_files(path, conversation_id) if conversation_id else []
    return [{**row, "stale": _version(row["path"]) != row["version"]} for row in rows]


def manifest(path: Path, conversation_id: str) -> str:
    """The block for the prompt: what he has open, and what he can still trust.

    Empty when there is nothing to say. A header over an empty list is tokens spent to tell
    him nothing, on every turn until he opens something.

    Ordered oldest first, so it reads as the session in sequence, and capped from the newest
    end — what he touched recently is what he is working on. The drop is stated rather than
    silent: a truncated list that looks complete is how he concludes he has never opened a
    file he opened forty reads ago.
    """
    rows = seen(path, conversation_id)
    if not rows:
        return ""

    dropped = max(0, len(rows) - MANIFEST_LIMIT)
    lines = ["[Files you have already opened in this conversation]"]
    if dropped:
        lines.append(f"({dropped} older file(s) not listed — you have touched more than this.)")
    lines += [f"- {_short(row['path'])} — {_state(row)}" for row in rows[dropped:]]
    lines.append(
        "Anything unchanged is still exactly what you saw — do not open it again. Anything "
        "changed or not there, look before you rely on it."
    )
    return "\n".join(lines)


def _state(row: dict) -> str:
    """The one useful sentence about a file: what he did, and whether it still holds."""
    if not row["version"]:
        return "not there when you looked"
    if row["stale"]:
        return f"{row['action']}, but CHANGED SINCE you looked"
    return f"{row['action']}, unchanged since"


def _short(resolved: str) -> str:
    """The path as he would type it — relative to where he is working, when it is under it.

    An absolute path is most of a line and none of the meaning, and every line here is carried
    on every turn for the rest of the conversation.
    """
    try:
        return str(Path(resolved).relative_to(workspace.paths.base_dir()))
    except ValueError:
        return resolved


def _paths(tool_name: str, arguments: dict) -> list[str]:
    """The file paths one call names.

    ``edit_files`` is the reason this is a list: it carries ``edits``, a list of
    ``{path, old, new}``, and is the tool he is told to reach for the moment a change touches
    more than one place — so the multi-path case is the common one, not the exotic one.
    """
    if tool_name == "edit_files":
        edits = arguments.get("edits")
        if not isinstance(edits, list):
            return []
        return [str(edit["path"]) for edit in edits if isinstance(edit, dict) and edit.get("path")]
    wanted = arguments.get("path")
    return [str(wanted)] if wanted else []


def _version(resolved: str) -> str:
    """``size:mtime_ns`` for a file, or ``''`` when it is not there.

    A missing file gets a real, comparable value rather than a failure: he tried to read
    something that does not exist, that is worth recording, and if it later appears the
    empty string differs from whatever it becomes — so it goes stale exactly as it should.
    """
    try:
        stat = Path(resolved).stat()
    except OSError:
        return ""
    return f"{stat.st_size}:{stat.st_mtime_ns}"
