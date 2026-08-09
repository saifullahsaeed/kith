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

import re
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
    # Its own word, not "wrote". A delete leaves nothing to stat, so it lands in the same
    # no-version state as a read of a file that was never there — and told "not there when you
    # looked" about a file he deliberately removed, he may reasonably go looking for it again.
    "delete_file": "deleted",
}

#: How many files the manifest names before it starts dropping the oldest. This is the one
#: block that grows for the whole life of a conversation, and it is carried on every turn of
#: it, so it needs a ceiling. Forty is roughly a page — enough to cover a real working session
#: without the list becoming the thing he reads instead of the request.
MANIFEST_LIMIT = 40


def record(path: Path, tool_name: str, arguments: dict, result: object = None) -> None:
    """Note that this conversation touched whatever files this call touched.

    ``result`` is what the tool returned, and it is needed for one reason: only the result
    knows how much of the file he saw. ``read_file`` windows to 400 lines by default and says
    so in its own output; the arguments cannot distinguish a default read that finished from
    one that stopped a quarter of the way in.

    Silent about everything it cannot answer: a tool that touches no file, work belonging to
    no conversation (a tick, a script, a test), an argument shaped in a way this does not
    recognise. None of those are errors, and none of them should be able to fail a tool call
    that has already run — this is bookkeeping about the call, not part of it.
    """
    action = _ACTIONS.get(tool_name)
    conversation_id = session_context.current()
    if not action or not conversation_id:
        return
    extent = _extent(result) if action == "read" else ""
    for wanted in _paths(tool_name, arguments or {}):
        try:
            resolved = workspace.resolve(wanted)
            repo.touches.touch(path, conversation_id, resolved, action, _version(resolved), extent)
        except Exception:
            # Bookkeeping must never fail the call it describes. The tool has already run and
            # its result is already the answer; a database that would not take a row about it
            # is not a reason to hand the model an error for work that succeeded.
            continue


def seen(path: Path, conversation_id: str, limit: int | None = None) -> list[dict]:
    """Files this conversation has touched, each with whether it has moved since.

    ``stale`` is the whole point: it is False when what he last saw is what is there now, and
    True when the file has changed under him — including when it changed with no tool call
    involved.

    ``limit`` keeps the newest that many. It exists because every row costs a `stat`, and this
    runs while the prompt is being assembled: the manifest shows forty, so loading four hundred
    would be four hundred syscalls a turn spent on rows nobody will read.
    """
    if not conversation_id:
        return []
    rows = repo.touches.touched_files(path, conversation_id, limit)
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
    rows = seen(path, conversation_id, MANIFEST_LIMIT)
    if not rows:
        return ""

    dropped = max(0, repo.touches.count_touched(path, conversation_id) - len(rows))
    lines = ["[Files you have already opened in this conversation]"]
    if dropped:
        lines.append(f"({dropped} older file(s) not listed — you have touched more than this.)")
    lines += [f"- {_short(row['path'])} — {_state(row)}" for row in rows]
    lines.append(
        "A file marked whole and unchanged is still exactly what you saw — do not open it "
        "again. Where a line names only part of a file, that is all you have seen of it, so "
        "read the rest before answering about the rest. Anything changed or not there, look "
        "again before you rely on it."
    )
    return "\n".join(lines)


def _state(row: dict) -> str:
    """The one useful sentence about a file: what he did, how much of it, and whether it holds.

    The extent is not decoration. ``read_file`` returns 400 lines by default, so without it
    this line said "read, unchanged" of a 3,000-line file he had seen an eighth of — and the
    closing instruction then told him not to open it again. That converts a wasted round into
    a confident answer drawn from a fragment, which is worse than the repeat reads the whole
    manifest exists to prevent.
    """
    if row["action"] == "deleted":
        return "deleted by you"
    if not row["version"]:
        return "not there when you looked"
    extent = row.get("extent") or ""
    what = f"{row['action']} only part of it (lines {extent})" if extent else f"{row['action']} whole"
    if row["stale"]:
        return f"{what}, but CHANGED SINCE you looked"
    return f"{what}, unchanged since"


def _short(resolved: str) -> str:
    """The path as he would type it — relative to whichever of his roots contains it.

    An absolute path is most of a line and none of the meaning, and every line here is carried
    on every turn for the rest of the conversation.

    Two roots, both his, and they are not the same one: ``resolve`` anchors relative paths in
    the *base*, which is a linked project folder whenever a session has one, so a file under
    his own workspace root arrives absolute and would have stayed that way. Tried in that
    order because the base is the narrower answer when both apply.
    """
    for root in (workspace.paths.base_dir(), workspace.root()):
        try:
            return str(Path(resolved).relative_to(root))
        except ValueError:
            continue
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


#: How ``workspace.read_file`` announces that it stopped early. Matched rather than
#: reconstructed, because the two limits it can stop at — the line window and the output
#: budget — produce different reasons and the same sentence, and only that sentence knows
#: which lines actually came back.
_WINDOW = re.compile(r"showing lines (\d+)-(\d+) of (\d+)")


def _extent(result: object) -> str:
    """The window a read returned, or ``''`` when it returned the whole file.

    Parsed out of the result because nothing else knows. A read with no ``offset`` or ``limit``
    is a complete read of a short file and a first-quarter read of a long one, and the arguments
    are identical in both cases.

    Anything unparseable is treated as whole, which is the dangerous direction — so the tests
    for this drive the real ``read_file`` against a real long file rather than a hand-written
    string, and reworded output fails them instead of quietly degrading to the old behaviour.
    """
    if not isinstance(result, str):
        return ""
    found = _WINDOW.search(result)
    return f"{found[1]}-{found[2]} of {found[3]}" if found else ""


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
