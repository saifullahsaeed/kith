"""Kith's sense of time, in the part of it that needs nothing to answer.

Times are stored in UTC (ISO 8601). Everything here works in UTC or in whatever zone it is
handed, and so needs no setting, no database and no caller — which is what puts it in the
kernel, alongside the other things every layer asks for and nothing can supply.

**The zone lives one layer up**, in `services/local_time.py`. Showing a time in his person's
zone means reading a stored setting, and `local_tz()` reaching for it is the whole reason
this file used to be `domain/clock.py` with a function-body import of `kith.services.tuning`
in it. Splitting on that line is what let the rest of it stop dodging.

`parse` takes its zone explicitly. It was `_parse(iso, assume_local=False)`, and the two
public callers that make this module pure — `humanize_since` and `humanize_until` — were
pure only because they let that default ride. A boolean that silently decides whether a
function touches storage is a boolean worth replacing with the zone itself: passing one now
means having already resolved it, one layer up, where resolving is allowed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now_utc().isoformat()


def time_of_day(hour: int) -> str:
    if hour < 5:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


def humanize_since(iso: str | None) -> str:
    """'3 minutes ago', '2 hours ago', 'just now' — or '' if unknown."""
    dt = parse(iso)
    if dt is None:
        return ""
    return humanize_delta(now_utc() - dt) + " ago"


def humanize_until(iso: str | None) -> str:
    """'in 12 minutes', 'now', or '' if unknown."""
    dt = parse(iso)
    if dt is None:
        return ""
    delta = dt - now_utc()
    if delta.total_seconds() <= 0:
        return "now"
    return "in " + humanize_delta(delta)


def humanize_delta(delta: timedelta) -> str:
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


def parse(iso: str | None, tz: tzinfo = UTC) -> datetime | None:
    """An ISO string as an aware datetime, reading a naive one in `tz`.

    `tz` defaults to UTC, which is what a stored time means here. The one caller that wants
    a naive time read as his person's wall clock — `local_time.resolve_fire_at`, where "at
    9pm" means 9pm where he is — passes the zone in, because it is at a layer that may look
    one up.
    """
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt
