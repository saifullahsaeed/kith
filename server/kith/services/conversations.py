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
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kith.infra.db import repositories as repo
from kith.kernel import live_turns

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
    #: Where the conversation was left — the opening line of the last thing he said in it.
    last_said: str = ""
    #: What this session is working on, and whether it keeps going without being asked.
    #: Both are properties of the conversation rather than of the app, which is what makes
    #: two projects at once possible: you switch sessions and the work switches with you.
    project_id: int | None = None
    working: bool = False

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "sessionId": self.session_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "messages": self.messages,
            "lastSaid": self.last_said,
            "transcript": str(transcript_path(self.id)),
            "projectId": self.project_id,
            "working": self.working,
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
    """Newest first, each row carrying where its conversation was left.

    A row written before there was a column for that has it read out of its transcript here,
    once, and written back. Doing it on the way past rather than in the migration keeps the
    upgrade from opening a hundred and fifty files, and keeps the cost on the list that
    actually asked — and it is the last line of each file, not the whole of it.
    """
    rows = repo.conversations.recent(agent_db, limit)
    # Which of these are working right now.
    #
    # `working` has been on the public shape all along and nothing ever set it: it was read from
    # `row.get("working")`, and there is no such column — so the history panel's "still working"
    # dot, the only sign the app had that a conversation you were not looking at was alive, could
    # never light up. It is not a column because it is not a fact about a row; it is what is
    # happening in memory this second, which only `live_turns` knows.
    live = set(live_turns.live())
    for row in rows:
        row["working"] = str(row.get("id") or "") in live
        if row.get("last_said") or not int(row.get("messages") or 0):
            continue
        said = _last_said(transcript_path(str(row.get("id") or "")))
        if said:
            row["last_said"] = said
            repo.conversations.set_last_said(agent_db, str(row["id"]), said)
    return [_to_public(row) for row in rows]


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
    # His word, kept on the index row as well as in the file: the sidebar reads a hundred and
    # fifty of these at a time and cannot open a hundred and fifty transcripts to do it.
    repo.conversations.touch(
        agent_db,
        conversation_id,
        delta=1,
        # `or None` so a reply that is nothing but a code block leaves the last readable
        # line standing rather than blanking the row.
        last_said=(outcome_from(content) or None) if role == "assistant" else None,
    )
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


def record_summary(conversation_id: str, through: int, text: str) -> None:
    """Persist the running brief that folds the conversation's older turns.

    An ordinary transcript event, so it rides the same append-only file everything else does
    — a brief written mid-turn survives a crash the way a message does. ``through`` is how many
    of the conversation's turns it covers, so the next turn knows how much is already folded.
    """
    record_event(conversation_id, "summary", {"through": int(through), "text": text})


def latest_summary(conversation_id: str) -> dict:
    """The most recent folded brief, or ``{}`` if the conversation has never been folded.

    Last-write-wins by walking the append-only file: each fold appends a new summary event
    covering more of the conversation, and only the last one is current.
    """
    found: dict = {}
    for entry in read(conversation_id):
        if entry.get("type") == "summary":
            found = {"through": int(entry.get("through") or 0), "text": str(entry.get("text") or "")}
    return found


def latest_reading(conversation_id: str) -> dict:
    """The most recent context reading, or ``{}`` on a conversation that has never had a turn.

    Same last-write-wins walk as :func:`latest_summary`, and for the same reason: one of these
    is written per turn, each describes the window as that turn left it, and only the last one
    is still true.

    Exists because a reading is only ever taken *by a turn*, and `/fold` is not a turn. Asking a
    fold to say how full the window is afterwards means starting from the last real measurement
    rather than inventing a fresh one.
    """
    found: dict = {}
    for entry in read(conversation_id):
        if entry.get("type") == "context":
            found = entry
    return found


def messages(conversation_id: str) -> list[dict]:
    """The conversation, prose only — what the client shows and starts a fresh chat from.

    Only user and assistant text. See :func:`full_messages` for the version a real turn
    actually replays, which carries tool history forward too.
    """
    out = []
    for entry in read(conversation_id):
        if entry.get("type") == "message" and entry.get("role") in ("user", "assistant"):
            out.append({"role": entry["role"], "content": entry.get("content") or ""})
    return out


def full_messages(conversation_id: str) -> list[dict]:
    """The conversation a real turn replays — prose, and every tool call with its result.

    Where :func:`messages` drops all of that, this puts it back: a new turn should
    remember what the last one actually read and ran, not just what it said. That is a
    deliberate tradeoff, not a free lunch — a tool result from hours ago is replayed as
    though it were still true, which it might not be. Matching how the field generally
    treats this axis (Claude Code keeps tool history until it compacts, rather than
    dropping it every turn) rather than trying to time-box around the staleness risk.

    Reconstructed from the transcript's own ``tool_call``/``tool_result`` events rather
    than the rounds they were originally batched into — nothing in the transcript records
    which calls shared a round, and nothing needs to: ``openai_compat._to_openai`` pairs a
    ``tool`` message to the assistant message immediately before it *positionally*, not by
    id, so one call to its own result, in the order they actually resolved, is exactly the
    shape it wants. ``pending`` is keyed by the transcript's own per-turn call id (unique
    only within the turn that produced it — the agent loop resets its counter at the start
    of every real turn) and is cleared at every user message rather than trusted to stay
    unique for the whole file, so a crashed turn's orphaned call can never be mistaken for
    a later turn's reuse of the same id. A call with no matching result — the turn crashed
    mid-call — is left out entirely: a provider refuses a ``tool_calls`` message it cannot
    pair to one, which would fail the whole request that tried to replay it.
    """
    out: list[dict] = []
    pending: dict[str, dict] = {}
    turn = 0
    #: Which turn each `tool` message in `out` came from, so the old ones can be found once the
    #: newest turn is known — which it is not until the file has been read to the end.
    turn_of: dict[int, int] = {}
    for entry in read(conversation_id):
        kind = entry.get("type")
        if kind == "message" and entry.get("role") in ("user", "assistant"):
            if entry["role"] == "user":
                pending = {}  # a turn boundary — nothing from before it can still be open
                turn += 1
            out.append({"role": entry["role"], "content": entry.get("content") or ""})
        elif kind == "tool_call":
            call_id = str(entry.get("id") or "")
            if call_id:
                pending[call_id] = entry
        elif kind == "tool_result":
            call = pending.pop(str(entry.get("id") or ""), None)
            if call is None:
                continue  # no matching call this turn — nothing safe to pair it to
            out.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": call.get("name") or "",
                                "arguments": call.get("arguments") or {},
                            }
                        }
                    ],
                }
            )
            turn_of[len(out)] = turn
            out.append(
                {
                    "role": "tool",
                    "tool_name": entry.get("name") or call.get("name") or "",
                    "content": json.dumps(entry.get("result")),
                    "_call": call.get("arguments") or {},
                }
            )
    _let_go_of_old_results(out, turn_of, newest=turn)
    for message in out:
        message.pop("_call", None)
    return out


def _let_go_of_old_results(out: list[dict], turn_of: dict[int, int], newest: int) -> None:
    """Trim tool output from turns older than the last few, in place.

    Nothing used to. There are two things that shrink a conversation and neither is about age: the
    fold, at 80% of the window, which summarises what was *said*; and a character budget on tool
    results that `agent_loop` only reaches *after* the fold has given up (`if not folded:`). So
    below 80% every tool result ever produced was replayed in full, every turn, for the life of the
    conversation — measured at 4.42M characters across 124 turns on one real board, 39% of a
    million-token window, with the 80,000-character budget never once applying.

    **Why by turn and not by size.** Size is the smaller half of the argument. `full_messages` says
    the rest of it in its own docstring: "a tool result from hours ago is replayed as though it were
    still true, which it might not be." A file read forty turns ago and edited since is not stale
    context, it is wrong context, and a stub that sends him back to the file is better than a
    confident copy of what it used to say.

    **Why here and not in the loop.** A turn boundary is fixed, so this is deterministic: a result
    stubbed on the last turn is stubbed byte-identically on this one, and the cached prefix still
    matches. Trimming under pressure inside the loop rewrites the middle of the array and moves the
    eviction point every round — the mistake `_compact_tool_history` made, which cost 93% of a
    turn's prompt re-billed uncached.

    The head of the result survives, and the call's own arguments are named beside it, because the
    point is for him to know he already looked at something. A hole where a result was is how a
    conversation ends up reading the same file twice.
    """
    from kith.services import tuning

    keep = int(tuning.value("keep_tool_turns") or 0)
    if keep <= 0:
        return
    stub_chars = int(tuning.value("tool_stub_chars") or 1_200)
    oldest_kept = newest - keep + 1
    for index, from_turn in turn_of.items():
        if from_turn >= oldest_kept:
            continue
        message = out[index]
        content = message.get("content") or ""
        if len(content) <= stub_chars:
            continue  # trimming a couple of hundred characters buys nothing and loses something
        name = message.get("tool_name") or "tool"
        what = message.get("_call") or {}
        about = what.get("path") or what.get("pattern") or what.get("command") or what.get("query")
        # Still JSON, because the content of a `tool` message is a JSON-encoded result and
        # `_to_openai` hands it straight to the provider.
        message["content"] = json.dumps(
            content[1 : stub_chars + 1].rstrip("\\")
            + f"…[trimmed: {len(content):,} characters from {name}"
            + (f" on {about}" if isinstance(about, str) and about else "")
            # Deliberately *not* "N turns ago". That was the first wording and it is a cache bug:
            # the number changes every turn, so every stub in the conversation is rewritten on every
            # message and the prefix stops matching — the exact failure this function is placed here
            # to avoid. A relative age is worth nothing anyway; that it is old is the whole point.
            + " on an earlier turn. Run it again if you still need it.]"
        )


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
            current = {"role": "assistant", "parts": [], "at": entry.get("at") or ""}
            out.append(current)
        return current

    calls: dict[str, dict] = {}
    for entry in read(conversation_id):
        kind = entry.get("type")
        if kind == "message" and entry.get("role") == "user":
            current = {
                "role": "user",
                "parts": [{"kind": "text", "text": entry.get("content") or ""}],
                "at": entry.get("at") or "",
            }
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
        elif kind == "context":
            # One per turn, written when it ends — a reading of how full the window was, not a
            # thing that happened, so only the final one is still true. Recording all forty
            # would put a categorised breakdown in the transcript on every round to say what the
            # last one already says.
            assistant()["parts"].append(
                {
                    "kind": "context",
                    "context": entry.get("context") or {},
                    # What was actually persisted going into this turn — before its own tool
                    # calls added anything. Absent on a turn recorded before this field existed;
                    # the UI falls back to `context` for those.
                    "baseline": entry.get("baseline") or {},
                    "folded": bool(entry.get("folded")),
                    # Zero on every turn recorded before this existed, which reads correctly:
                    # nothing was sent again.
                    "retried": int(entry.get("retried") or 0),
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


def interrupted_asks() -> list[str]:
    """Conversations whose transcript ends on an `ask` that never got a result.

    That shape has exactly one cause: the process died while a turn was parked on a question.
    Everything else writes a result — an answer, a stop, or the deadline — because the tool
    returns down all three paths and the loop records what it returned.

    Read from the tail rather than the whole file. A transcript runs to megabytes and there are
    hundreds of them; the question, if there is one, is in the last handful of lines by
    definition, because nothing can have been appended after the turn that stopped writing.
    Cheap enough to do on every start, which is where it is called from.
    """
    out = []
    for path in sorted(directory().glob("*.jsonl")):
        try:
            tail = path.read_text(errors="replace").splitlines()[-_TAIL_LINES:]
        except OSError:
            continue
        pending: set[str] = set()
        for line in tail:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            kind = entry.get("type")
            if kind == "tool_call" and entry.get("name") == "ask":
                pending.add(str(entry.get("id") or ""))
            elif kind == "tool_result":
                pending.discard(str(entry.get("id") or ""))
            elif kind == "message" and entry.get("role") == "user":
                # They carried on talking, so whatever was open is not what the conversation is
                # waiting on any more.
                pending.clear()
        if pending:
            out.append(path.stem)
    return out


#: How much of a transcript's end is read looking for an unanswered question. Generous next to
#: the few lines a parked turn can have left — a `stats`, a `tool_call`, and nothing else — and
#: small enough that scanning every conversation on start costs nothing worth measuring.
_TAIL_LINES = 40


def search(agent_db: Path, query: str, limit: int = 40) -> list[dict]:
    """Find where something was said, across every conversation.

    Nothing could search these. The control panel's box filters whatever tab you are
    looking at, and the tab that held messages was removed — so the one thing kept most
    carefully, a complete transcript of everything either of you said, was the one thing
    with no way in. Titles are generated from a first message, which means a conversation
    is findable by how it opened and by nothing else that happened in it.

    Read from the files rather than an index. They are the record — the database only
    points at them — so a search over the files cannot disagree with what you would see on
    opening one. At the scale this runs at (tens of conversations, tens of thousands of
    lines) reading them is a few milliseconds, and an index would be a second copy of the
    truth to keep in step for no gain anyone would feel.

    Newest first, and it stops once it has enough: what you are looking for is nearly
    always something recent, and scanning years of history to fill a list nobody scrolls
    is work done for its own sake.
    """
    needle = str(query or "").strip().lower()
    if not needle:
        return []

    hits: list[dict] = []
    for row in repo.conversations.recent(agent_db, 500):
        conversation_id = str(row.get("id") or "")
        path = transcript_path(conversation_id)
        if not path.is_file():
            continue
        for entry in _spoken(path):
            text = entry["text"]
            at = text.lower().find(needle)
            if at < 0:
                continue
            hits.append(
                {
                    "conversationId": conversation_id,
                    "title": str(row.get("title") or "Untitled"),
                    "role": entry["role"],
                    "at": entry["at"],
                    "snippet": _around(text, at, len(needle)),
                }
            )
            # One hit per conversation. Ten matches from one long conversation would bury
            # the other nine conversations that also have one, and the point of the list is
            # to get you to the right conversation.
            break
        if len(hits) >= limit:
            break
    return hits


def _spoken(path: Path) -> list[dict]:
    """Only the things that were actually said. Reasoning, tool calls and token counts are
    in the file too, and matching them would answer "where did we talk about X" with a
    stack trace."""
    said: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = entry.get("type")
        if kind == "message":
            text, role = entry.get("content"), entry.get("role") or "user"
        elif kind == "said":
            text, role = entry.get("text"), "assistant"
        else:
            continue
        if isinstance(text, str) and text.strip():
            said.append({"text": text, "role": role, "at": str(entry.get("at") or "")})
    return said


#: How much of the line to show around a match. Enough to recognise it, short enough that
#: a result list stays a list.
_SNIPPET_SIDE = 90


def _around(text: str, at: int, length: int) -> str:
    """The match with its surroundings, on one line."""
    flat = " ".join(text.split())
    # Re-find in the flattened text: collapsing whitespace moves the offset, and using the
    # original index here put the window in the wrong place on anything with a newline.
    at = flat.lower().find(text[at : at + length].lower())
    if at < 0:
        return flat[: _SNIPPET_SIDE * 2] + ("…" if len(flat) > _SNIPPET_SIDE * 2 else "")
    start = max(0, at - _SNIPPET_SIDE)
    end = min(len(flat), at + length + _SNIPPET_SIDE)
    return ("…" if start > 0 else "") + flat[start:end] + ("…" if end < len(flat) else "")


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
    # Anything keyed to the conversation goes with it. These rows are only ever read by way of
    # the conversation, so left behind they are unreachable rather than merely stale — a table
    # that grows for the life of the install and that nothing will ever look at again.
    repo.touches.forget_conversation(agent_db, conversation_id)
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


#: Markdown that opens a line and says nothing about what the line is: heading rules,
#: bullets, quote marks, list numbers.
_OPENER = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|>\s+|\d+[.)]\s+)")

#: How much of his last word a row carries. A line — the row says where you left off, and
#: reading the rest of it is what opening the conversation is for.
OUTCOME_CHARS = 130


def outcome_from(text: str) -> str:
    """Where the conversation stands, in a line: the opening of the last thing he said.

    The first *readable* line, not the first line. His replies routinely open with a fenced
    diff or a heading, and a sidebar row reading "```python" is worse than the message count
    it replaced. Fences are skipped along with what is inside them, and the markdown that
    only decorates a line is taken off the front of it.
    """
    fenced = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line:
            continue
        line = " ".join(_OPENER.sub("", line).replace("*", "").replace("`", "").split())
        if len(line) < 3:
            continue
        if len(line) <= OUTCOME_CHARS:
            return line
        cut = line[:OUTCOME_CHARS]
        if " " in cut[60:]:
            cut = cut[: cut.rfind(" ")]
        return cut + "…"
    return ""


def _last_said(path: Path) -> str:
    """His last word in a transcript, read from the end.

    Backwards because the answer is at the bottom and some of these files are megabytes —
    `_spoken` parses every line of every one, which is right for a search and wasteful for a
    single lookup repeated across a whole listing.
    """
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind, role = entry.get("type"), entry.get("role")
        if kind == "message" and role == "assistant":
            said = outcome_from(str(entry.get("content") or ""))
        elif kind == "said":
            said = outcome_from(str(entry.get("text") or ""))
        else:
            continue
        if said:
            return said
    return ""


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
        last_said=str(row.get("last_said") or ""),
        project_id=int(row["project_id"]) if row.get("project_id") else None,
        working=bool(row.get("working")),
    ).public()


class ProjectLocked(Exception):
    """Raised when a conversation already bound to a project is asked to move or unbind."""


def set_project(agent_db: Path, conversation_id: str, project_id: int | None) -> dict:
    """Point this session at a project, or at nothing — the first time only.

    He binds a session himself by working on something — starting a project, filing a task
    under one — and this is the same decision made by hand, for the times that guess is
    wrong or you want to say it up front. Once bound, though, it holds: a conversation is
    stuck with the project it picked for the rest of its life, the same way it is stuck with
    whatever it has already said. Wanting a different project is what a new conversation is
    for.
    """
    if repo.conversations.get(agent_db, conversation_id) is None:
        raise KeyError(conversation_id)
    if not repo.conversations.set_project(agent_db, conversation_id, project_id):
        raise ProjectLocked(conversation_id)
    return get(agent_db, conversation_id)


def _now() -> str:
    from kith.infra.db.support import utc_now_iso

    return utc_now_iso()
