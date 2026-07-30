"""Every conversation, kept.

Chat used to be a thing that happened and then was gone. The interface held the messages
in React state, the server held none of them, and closing the window or reloading the page
ended the conversation permanently — along with everything he had worked out in it. For an
agent whose whole premise is continuity, that was the largest hole in the project.

**Two stores, on purpose, and the split is the point.**

*The transcript is a file.* One JSONL per conversation in ``~/Kith/.kith/conversations``,
appended to and never rewritten. Append-only is what makes "I don't want to lose any info"
a property of the format rather than a promise: a crash mid-write costs the last line, not
the file, and there is no schema migration that can ever eat one. It is plain text, so it
survives this program — grep it, read it in a text editor, copy it to another machine, open
it in ten years. A database that only Kith can read is a database that dies with Kith.

*The index is a table.* Titles, times, message counts and the OpenRouter session id live in
SQLite, because "list my conversations, newest first" over hundreds of files is a query, not
a directory scan. The index can always be rebuilt from the files; the files can never be
rebuilt from the index. That asymmetry is which one is the source of truth.

**The session id is per conversation, not per install.** OpenRouter uses it for provider
stickiness, and a conversation is exactly the unit that wants to stay on one warm cache: its
prefix is shared across every turn in it and shared with nothing else. One id for the whole
install kept unrelated chats fighting over the same upstream.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kith.infra.db import repositories as repo

#: Cap on a title. Long enough to recognise a conversation, short enough for a sidebar.
TITLE_CHARS = 60

#: How much of the first message becomes the fallback title.
_TITLE_SOURCE_CHARS = 200


def directory() -> Path:
    """Where transcripts live. Inside the workspace, so a backup of his folder is a
    backup of his conversations too."""
    from kith.infra import workspace

    place = workspace.internal() / "conversations"
    place.mkdir(parents=True, exist_ok=True)
    return place


def transcript_path(conversation_id: str) -> Path:
    return directory() / f"{conversation_id}.jsonl"


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    session_id: str
    created_at: str
    updated_at: str
    messages: int

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "sessionId": self.session_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "messages": self.messages,
            "transcript": str(transcript_path(self.id)),
        }


def start(agent_db: Path, first_message: str = "") -> dict:
    """Open a conversation and return it.

    The id is a timestamp plus a short random suffix — sortable by name in a file listing,
    which is what you want the moment you are looking at the folder in Finder rather than
    through the app.

    Milliseconds are in it because seconds are not enough: two conversations opened in the
    same second sorted by their random suffix instead, which quietly made the sentence
    above false exactly when someone was clicking quickly.
    """
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"{int(now % 1 * 1000):03d}"
    conversation_id = f"{stamp}-{uuid.uuid4().hex[:6]}"
    session_id = f"kith-{uuid.uuid4().hex[:16]}"
    title = title_from(first_message) if first_message else "New conversation"
    repo.conversations.create(agent_db, conversation_id, title, session_id)
    transcript_path(conversation_id).touch()
    _append(conversation_id, {"type": "start", "at": _now(), "title": title, "sessionId": session_id})
    return get(agent_db, conversation_id)


def get(agent_db: Path, conversation_id: str) -> dict:
    row = repo.conversations.get(agent_db, conversation_id)
    if not row:
        raise KeyError(conversation_id)
    return _to_public(row)


def recent(agent_db: Path, limit: int = 50) -> list[dict]:
    return [_to_public(row) for row in repo.conversations.recent(agent_db, limit)]


def session_id(agent_db: Path, conversation_id: str) -> str:
    """The OpenRouter stickiness id for this conversation, if it has one."""
    row = repo.conversations.get(agent_db, conversation_id)
    return str(row.get("session_id") or "") if row else ""


def record(agent_db: Path, conversation_id: str, role: str, content: str, extra: dict | None = None) -> None:
    """Append one message to the transcript and touch the index.

    Written before anything else happens with it: a message recorded after a reply is a
    message lost when the reply fails.
    """
    if not conversation_id:
        return
    entry: dict[str, Any] = {"type": "message", "at": _now(), "role": role, "content": content}
    if extra:
        entry.update(extra)
    _append(conversation_id, entry)
    repo.conversations.touch(agent_db, conversation_id, delta=1)
    # First real words become the title, replacing the placeholder.
    row = repo.conversations.get(agent_db, conversation_id)
    if row and role == "user" and str(row.get("title") or "") in ("", "New conversation"):
        repo.conversations.rename(agent_db, conversation_id, title_from(content))


def record_event(conversation_id: str, kind: str, payload: dict) -> None:
    """Append something that is not a message — a tool call, a token count, an error.

    In the transcript but not counted as a message, because "12 messages" should mean
    what a person would count, and because losing the shape of a turn is losing most of
    what made it interesting.
    """
    if not conversation_id:
        return
    _append(conversation_id, {"type": kind, "at": _now(), **payload})


def messages(conversation_id: str) -> list[dict]:
    """The conversation, in the shape /api/chat wants back.

    Only user and assistant text: replaying a stored tool call would mean replaying its
    id and its result, and a tool result from an hour ago is not a fact about now.
    """
    out = []
    for entry in read(conversation_id):
        if entry.get("type") == "message" and entry.get("role") in ("user", "assistant"):
            out.append({"role": entry["role"], "content": entry.get("content") or ""})
    return out


def timeline(conversation_id: str) -> list[dict]:
    """The conversation as parts, ready to be rendered back exactly as it happened.

    ``messages()`` above is the prompt view — plain text, because that is what a model needs
    handed back. This is the *interface* view: reasoning blocks, prose between tool rounds,
    each call with the result it got, in arrival order.

    Two views over one append-only file rather than two stores. The alternative was
    reconstructing a turn's shape from a paragraph, which cannot be done: once you have
    thrown away which sentence went with which tool call, no amount of cleverness gets it
    back, and a resumed conversation becomes a summary of itself.
    """
    out: list[dict] = []
    current: dict | None = None

    def assistant() -> dict:
        nonlocal current
        if current is None or current["role"] != "assistant":
            current = {"role": "assistant", "parts": []}
            out.append(current)
        return current

    calls: dict[str, dict] = {}
    for entry in read(conversation_id):
        kind = entry.get("type")
        if kind == "message" and entry.get("role") == "user":
            current = {"role": "user", "parts": [{"kind": "text", "text": entry.get("content") or ""}]}
            out.append(current)
        elif kind == "reasoning":
            assistant()["parts"].append({"kind": "reasoning", "text": entry.get("text") or ""})
        elif kind == "said":
            assistant()["parts"].append({"kind": "text", "text": entry.get("text") or ""})
        elif kind == "tool_call":
            part = {
                "kind": "tool",
                "id": str(entry.get("id") or ""),
                "name": entry.get("name") or "",
                "arguments": entry.get("arguments") or {},
            }
            calls[part["id"]] = part
            assistant()["parts"].append(part)
        elif kind == "tool_result":
            # Attached to its call rather than appended, so a result never shows up as a
            # part of its own — which is how the live view does it too.
            call = calls.get(str(entry.get("id") or ""))
            if call is not None:
                call["result"] = entry.get("result")
        elif kind == "stats":
            stats = entry.get("stats") or {}
            assistant()["parts"].append(
                {
                    "kind": "usage",
                    "uncached": int(stats.get("uncachedTokens") or 0),
                    "cached": int(stats.get("cachedTokens") or 0),
                    "out": int(stats.get("responseTokens") or 0),
                }
            )
    # A turn with nothing in it is a turn that failed before it said anything.
    return [message for message in out if message["parts"]]


def read(conversation_id: str) -> list[dict]:
    """Every line of the transcript, skipping any that got mangled.

    A truncated final line is what a crash mid-append looks like, and it must cost that
    line rather than the conversation — which is the whole reason this is JSONL and not
    one big JSON document.
    """
    path = transcript_path(conversation_id)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def rename(agent_db: Path, conversation_id: str, title: str) -> dict:
    repo.conversations.rename(agent_db, conversation_id, title.strip()[:TITLE_CHARS] or "Untitled")
    _append(conversation_id, {"type": "rename", "at": _now(), "title": title})
    return get(agent_db, conversation_id)


def delete(agent_db: Path, conversation_id: str, keep_file: bool = True) -> None:
    """Drop it from the list.

    The transcript file stays by default. Removing a conversation from a sidebar is a
    tidying gesture; deleting the only record of an afternoon's work is not, and the two
    should not be the same click.
    """
    repo.conversations.delete(agent_db, conversation_id)
    if not keep_file:
        transcript_path(conversation_id).unlink(missing_ok=True)


def title_from(text: str) -> str:
    """A title out of the first thing said. Not a model call — a title is not worth a
    round trip, and the first line of what someone asked is usually the best one anyway."""
    cleaned = " ".join((text or "").strip().split())[:_TITLE_SOURCE_CHARS]
    if not cleaned:
        return "New conversation"
    if len(cleaned) <= TITLE_CHARS:
        return cleaned
    cut = cleaned[:TITLE_CHARS]
    # Break on a word so it reads as a phrase rather than a truncation.
    if " " in cut[40:]:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


def storage() -> dict:
    """How much room the transcripts take, for the settings page."""
    place = directory()
    files = list(place.glob("*.jsonl"))
    return {
        "folder": str(place),
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files if f.is_file()),
    }


# --------------------------------------------------------------------------- #


def _append(conversation_id: str, entry: dict) -> None:
    """One line, opened and closed per write.

    Deliberately not a held-open handle: the process can be killed at any moment and an
    unflushed buffer is exactly the information this module exists to not lose.
    """
    try:
        with transcript_path(conversation_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except OSError:
        # A transcript that cannot be written must not take the turn down with it.
        pass


def _to_public(row: dict) -> dict:
    return Conversation(
        id=str(row["id"]),
        title=str(row.get("title") or "Untitled"),
        session_id=str(row.get("session_id") or ""),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        messages=int(row.get("messages") or 0),
    ).public()


def _now() -> str:
    from kith.infra.db.support import utc_now_iso

    return utc_now_iso()
