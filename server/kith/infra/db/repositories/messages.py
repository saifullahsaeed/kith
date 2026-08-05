"""The notification channel, and the per-tick flight recorder."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, func, select, update

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Message, TickLog
from kith.infra.db.support import utc_now_iso

# How many ticks the summary looks back over. Enough to see a trend, bounded so the
# aggregate stays cheap on a database that has been running for months.
_SUMMARY_WINDOW = 500
_TOP_TOOLS = 10


# --------------------------------------------------------------------------- #
# Messages (a two-way channel between Kith and his person)
# --------------------------------------------------------------------------- #


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


def unread_message_count(path: Path) -> int:
    with session(path) as db:
        return int(db.scalar(select(func.count()).select_from(Message).where(Message.read == 0)) or 0)


def mark_message_read(path: Path, message_id: int) -> dict | None:
    with session(path) as db:
        row = db.get(Message, message_id)
        if row is None:
            return None
        row.read = 1
        db.flush()
        return as_dict(row)


def mark_all_messages_read(path: Path) -> int:
    with session(path) as db:
        return db.execute(update(Message).where(Message.read == 0).values(read=1)).rowcount


def delete_message(path: Path, message_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Message).where(Message.id == message_id)).rowcount > 0


# --------------------------------------------------------------------------- #
# Tick log (the durable flight recorder — one row per autonomy tick)
# --------------------------------------------------------------------------- #


def add_tick_log(
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
    """Record one autonomy tick to the durable flight recorder.

    ``tokens_uncached`` comes last with a default deliberately: both callers pass every
    other argument positionally, so a parameter inserted mid-signature would slide
    ``seconds`` into ``outcome`` — and the call sites sit inside a bare ``except: pass``,
    so nothing would have told us.
    """
    with session(path) as db:
        db.add(
            TickLog(
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


def list_tick_log(path: Path, limit: int = 100) -> list[dict]:
    """Recent ticks, newest first, with `tools` parsed back to a list."""
    with session(path) as db:
        rows = db.scalars(select(TickLog).order_by(TickLog.id.desc()).limit(limit)).all()
        out = []
        for row in rows:
            record = as_dict(row)
            record["tools"] = _tools(record.get("tools"))
            out.append(record)
        return out


def times_worked(path: Path, goal: str) -> int:
    """How many unattended ticks have actually *worked* this task.

    Read from the recorder rather than counted in memory, so restarting the process cannot hand a
    task a fresh budget — which would quietly make "restart the app" the way to keep grinding.

    Matched on the focus line the runner writes, ``working on: <goal>``, and only for ``start``
    mode, so planning ticks and replies do not spend a task's allowance. Matching on the goal text
    rather than an id is what the recorder makes possible: it stores the focus line, not a foreign
    key, and a log that survives the task being edited is worth more here than a tidy join.
    """
    wanted = f"working on: {str(goal).strip()}"
    with session(path) as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(TickLog)
                .where(TickLog.mode == "start", TickLog.focus == wanted)
            )
            or 0
        )


def _tools(raw: object) -> list[str]:
    """Tool names are stored as a JSON string; a malformed row is not worth a crash."""
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def tick_log_summary(path: Path, limit: int = _SUMMARY_WINDOW) -> dict:
    """Aggregate the recent flight recorder — a quick read on how he's doing.

    Aggregated in Python rather than SQL on purpose: `tools` is a JSON array, and
    counting across it in SQLite would mean json_each and a far less readable query
    for a few hundred rows.
    """
    rows = list_tick_log(path, limit)
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
        # Cache hits removed — what these ticks actually made a provider read. Rows from
        # before v20 contribute nothing, so a window spanning the upgrade reads low.
        "tokensUncached": tokens_uncached,
        "errors": errors,
    }
