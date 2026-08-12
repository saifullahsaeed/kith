"""The notification channel, and the per-turn flight recorder."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, func, select, update

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Message, TurnLog
from kith.infra.db.support import utc_now_iso


def _notifies_message(write):
    from functools import wraps

    @wraps(write)
    def inner(*args, **kwargs):
        out = write(*args, **kwargs)
        _changed()
        return out

    return inner


def _changed() -> None:
    """Tell the interface a message arrived or was read — the alerts list and the badge both read it.
    Local import and swallowed, the same as the task one: a note about a save, never a reason one
    fails."""
    try:
        from kith.services import changes

        changes.publish("message")
    except Exception:
        pass


# How many turns the summary looks back over. Enough to see a trend, bounded so the
# aggregate stays cheap on a database that has been running for months.
_SUMMARY_WINDOW = 500
_TOP_TOOLS = 10


# --------------------------------------------------------------------------- #
# Messages (a two-way channel between Kith and his person)
# --------------------------------------------------------------------------- #


@_notifies_message
def add_message(
    path: Path,
    body: str,
    sender: str = "kith",
    link: str | None = None,
    kind: str = "note",
) -> dict:
    """Record a message, and light the badge only if this kind is allowed to interrupt.

    Everything is recorded regardless — the channel is the history and nothing is dropped.
    What the threshold changes is whether it counts as unread and whether it posts a desktop
    notification, so "quieter" never means "you did not find out". `link` (e.g. "/tasks/12")
    makes the notification clickable straight to what it is about.
    """
    from kith.services import notify

    loud = sender != "user" and notify.interrupts(kind)
    with session(path) as db:
        row = Message(
            body=body,
            # Unread is what lights the badge, so it follows the threshold rather than the
            # sender: a note he made while working is still in the channel, just not a
            # tap on the shoulder.
            read=0 if loud else 1,
            sender=sender,
            kind=kind if sender != "user" else "user",
            link=link,
            created_at=utc_now_iso(),
        )
        db.add(row)
        db.flush()
        saved = as_dict(row)
    if loud:
        notify.announce(kind, body, link)
    return saved


def list_messages(path: Path, limit: int = 100, unread_only: bool = False) -> list[dict]:
    query = select(Message).order_by(Message.id.desc()).limit(limit)
    if unread_only:
        query = query.where(Message.read == 0)
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def pending_user_messages(path: Path, limit: int = 5) -> list[dict]:
    """Replies from his person that arrived after his last word — i.e. things he
    hasn't responded to yet."""
    with session(path) as db:
        last_kith = db.scalar(select(func.coalesce(func.max(Message.id), 0)).where(Message.sender == "kith"))
        rows = db.scalars(
            select(Message)
            .where(Message.sender == "user", Message.id > last_kith)
            .order_by(Message.id.asc())
            .limit(limit)
        ).all()
        return [as_dict(row) for row in rows]


def count_messages_by_kind(path: Path) -> dict[str, int]:
    """How many of each kind are in the channel — over all of it, not the page.

    The alerts panel offers to clear one sort of thing and says how many that is. It was
    counting the hundred rows it had been handed, so "Clear notes · 100" on a channel of six
    hundred was a number invented by the limit. Counted here so the figure on the button is
    the figure that goes.
    """
    with session(path) as db:
        rows = db.execute(
            select(Message.kind, func.count()).where(Message.sender != "user").group_by(Message.kind)
        ).all()
    return {str(kind or "note"): int(count) for kind, count in rows}


def delete_messages(path: Path, kinds: list[str] | None = None) -> int:
    """Clear the channel, or one sort of thing in it. Returns how many went.

    ``kinds`` of None means everything he has said; a list narrows it. His person's own
    replies are never the target — but the ones sitting below the newest thing being cleared
    go with it, and that is load-bearing rather than tidy-mindedness. `pending_user_messages`
    works out what he still owes an answer to by comparing ids against his last word. Delete
    his last word and every reply he answered months ago sits above the new high-water mark
    and becomes pending again — he would re-read them as if they had just arrived, in the
    context of every turn. Dropping the replies beneath the cleared tail keeps that
    comparison answering exactly what it answered before the clear.
    """
    criteria = [Message.sender != "user"]
    if kinds is not None:
        criteria.append(Message.kind.in_(kinds))
    with session(path) as db:
        newest = db.scalar(select(func.max(Message.id)).where(*criteria))
        if newest is None:
            return 0
        removed = db.execute(delete(Message).where(*criteria)).rowcount
        db.execute(delete(Message).where(Message.sender == "user", Message.id < newest))
        return int(removed)


def unread_message_count(path: Path) -> int:
    with session(path) as db:
        return int(db.scalar(select(func.count()).select_from(Message).where(Message.read == 0)) or 0)


@_notifies_message
def mark_message_read(path: Path, message_id: int) -> dict | None:
    with session(path) as db:
        row = db.get(Message, message_id)
        if row is None:
            return None
        row.read = 1
        db.flush()
        return as_dict(row)


@_notifies_message
def mark_all_messages_read(path: Path) -> int:
    with session(path) as db:
        return db.execute(update(Message).where(Message.read == 0).values(read=1)).rowcount


@_notifies_message
def delete_message(path: Path, message_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Message).where(Message.id == message_id)).rowcount > 0


# --------------------------------------------------------------------------- #
# Turn log (the durable flight recorder — one row per turn)
# --------------------------------------------------------------------------- #


def add_turn_log(
    path: Path,
    at: str,
    mode: str,
    focus: str | None,
    tools: list[str],
    tokens_in: int,
    tokens_out: int,
    seconds: float,
    outcome: str | None,
    tokens_uncached: int | None = None,
) -> None:
    """Record one turn to the durable flight recorder.

    ``tokens_uncached`` comes last with a default deliberately: both callers pass every
    other argument positionally, so a parameter inserted mid-signature would slide
    ``seconds`` into ``outcome`` — and the call sites sit inside a bare ``except: pass``,
    so nothing would have told us.
    """
    with session(path) as db:
        db.add(
            TurnLog(
                at=at,
                mode=mode,
                focus=focus,
                tools=json.dumps(tools or []),
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                tokens_uncached=None if tokens_uncached is None else int(tokens_uncached),
                seconds=float(seconds),
                outcome=outcome,
            )
        )


def list_turn_log(path: Path, limit: int = 100) -> list[dict]:
    """Recent turns, newest first, with `tools` parsed back to a list."""
    with session(path) as db:
        rows = db.scalars(select(TurnLog).order_by(TurnLog.id.desc()).limit(limit)).all()
        out = []
        for row in rows:
            record = as_dict(row)
            record["tools"] = _tools(record.get("tools"))
            out.append(record)
        return out


def _tools(raw: object) -> list[str]:
    """Tool names are stored as a JSON string; a malformed row is not worth a crash."""
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def turn_log_summary(path: Path, limit: int = _SUMMARY_WINDOW) -> dict:
    """Aggregate the recent flight recorder — a quick read on how he's doing.

    Aggregated in Python rather than SQL on purpose: `tools` is a JSON array, and
    counting across it in SQLite would mean json_each and a far less readable query
    for a few hundred rows.
    """
    rows = list_turn_log(path, limit)
    if not rows:
        return {"ticks": 0}

    by_mode: dict[str, int] = {}
    tools: dict[str, int] = {}
    tokens_in = tokens_out = tokens_uncached = errors = 0
    for record in rows:
        by_mode[record["mode"]] = by_mode.get(record["mode"], 0) + 1
        tokens_in += record.get("tokens_in") or 0
        tokens_out += record.get("tokens_out") or 0
        tokens_uncached += record.get("tokens_uncached") or 0
        if (record.get("outcome") or "").startswith("error:"):
            errors += 1
        for name in record.get("tools") or []:
            tools[name] = tools.get(name, 0) + 1

    return {
        "ticks": len(rows),
        "since": rows[-1]["at"],
        "byMode": by_mode,
        "topTools": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:_TOP_TOOLS]),
        "tokensIn": tokens_in,
        "tokensOut": tokens_out,
        # Cache hits removed — what these turns actually made a provider read. Rows from
        # before v20 contribute nothing, so a window spanning the upgrade reads low.
        "tokensUncached": tokens_uncached,
        "errors": errors,
    }
