"""Kith's sense of time.

Gives him a wall clock, a feel for how long it's been since he last did
something, and the plumbing to schedule reminders for his future self. Times are
stored in UTC (ISO 8601) but *shown* in a configurable local zone (``KITH_TZ``,
default UTC) so "evening" means his person's evening.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kith.infra.db import repositories as repo


def local_tz() -> ZoneInfo | type[UTC]:
    """His zone, read per call because it is editable in settings.

    Falls back to UTC on an unknown name rather than raising: a mistyped zone should
    make his timestamps unsurprising, not stop him working.
    """
    from kith.services import tuning

    try:
        return ZoneInfo(str(tuning.value("timezone")))
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now_utc().isoformat()


def _local(dt: datetime) -> datetime:
    return dt.astimezone(local_tz())


def _time_of_day(hour: int) -> str:
    if hour < 5:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


def clock_line() -> str:
    """A human sentence for the current moment, e.g.
    'It's Monday, 27 Jul 2026, 9:52 PM (evening).'"""
    local = _local(now_utc())
    return f"It's {local.strftime('%A, %-d %b %Y, %-I:%M %p')} ({_time_of_day(local.hour)})."


def humanize_since(iso: str | None) -> str:
    """'3 minutes ago', '2 hours ago', 'just now' — or '' if unknown."""
    dt = _parse(iso)
    if dt is None:
        return ""
    return _humanize_delta(now_utc() - dt) + " ago"


def humanize_until(iso: str | None) -> str:
    """'in 12 minutes', 'now', or '' if unknown."""
    dt = _parse(iso)
    if dt is None:
        return ""
    delta = dt - now_utc()
    if delta.total_seconds() <= 0:
        return "now"
    return "in " + _humanize_delta(delta)


def resolve_fire_at(in_minutes: float | None = None, at: str | None = None) -> str:
    """When a reminder should fire, as a UTC ISO string.

    Accepts a relative offset (``in_minutes``) or an absolute time (``at``, ISO;
    a naive time is read in the local zone). ``in_minutes`` wins if both given.
    """
    if in_minutes is not None:
        return (now_utc() + timedelta(minutes=float(in_minutes))).isoformat()
    if at:
        dt = _parse(at, assume_local=True)
        if dt is not None:
            return dt.astimezone(UTC).isoformat()
    raise ValueError("give either in_minutes or a valid ISO 'at' time")


def next_fire_after(
    every_minutes: float | None = None, daily_at: str | None = None, after: datetime | None = None
) -> str:
    """When a recurring schedule should next fire, as a UTC ISO string.

    ``every_minutes`` gives a simple interval; ``daily_at`` is a local 'HH:MM'
    that fires the next time that clock time comes around.
    """
    now = after or now_utc()
    if every_minutes:
        return (now + timedelta(minutes=float(every_minutes))).isoformat()
    if daily_at:
        try:
            hh, mm = (int(part) for part in daily_at.split(":", 1))
        except (ValueError, TypeError):
            raise ValueError("daily_at must look like 'HH:MM'") from None
        local = _local(now)
        target = local.replace(hour=hh % 24, minute=mm % 60, second=0, microsecond=0)
        if target <= local:
            target += timedelta(days=1)
        return target.astimezone(UTC).isoformat()
    raise ValueError("give either every_minutes or daily_at")


def presence_block(path: Path) -> str:
    """The '[Right now]' block injected each turn: the time, how long since he
    last acted, and any reminders waiting or newly due."""
    lines = ["[Right now]", clock_line()]

    mood = repo.self_model.get_mood(path)
    if mood.get("label"):
        felt = f"You feel {mood['label']} (energy {mood['energy']}/100)"
        felt += f" — {mood['note']}." if mood.get("note") else "."
        lines.append(felt)

    last = repo.activity.last_activity_at(path)
    since = humanize_since(last)
    if since:
        lines.append(f"You last acted {since}.")

    pending = repo.reminders.list_reminders(path, status="pending")
    now = now_iso()
    due = [r for r in pending if r["fire_at"] <= now]
    waiting = [r for r in pending if r["fire_at"] > now]
    if due:
        lines.append("Reminders that have come due — deal with them:")
        lines += [f"- (#{r['id']}) {r['note']}" for r in due]
    if waiting:
        nxt = waiting[0]
        extra = f" (+{len(waiting) - 1} more)" if len(waiting) > 1 else ""
        lines.append(f"Next reminder {humanize_until(nxt['fire_at'])}: {nxt['note']}{extra}.")
    return "\n".join(lines)


def _humanize_delta(delta: timedelta) -> str:
    seconds = int(abs(delta.total_seconds()))
    if seconds < 45:
        return "moments"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = round(hours / 24)
    return f"{days} day{'s' if days != 1 else ''}"


def _parse(iso: str | None, assume_local: bool = False) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz() if assume_local else UTC)
    return dt
