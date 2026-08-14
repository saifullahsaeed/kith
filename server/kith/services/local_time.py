"""Time in his person's zone, which is a setting and therefore not a kernel concern.

Stored times are UTC; shown times are local (``KITH_TZ``, default UTC) so "evening" means
his person's evening. Reading that setting is the only thing separating this file from
`kernel/clock.py`, and it is enough: the kernel's rule is that a module needing a setting
belongs at the layer that owns one.

Everything here either resolves the zone or needs it to answer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kith.kernel import clock
from kith.services import tuning


def local_tz() -> ZoneInfo | type[UTC]:
    """His zone, read per call because it is editable in settings.

    Falls back to UTC on an unknown name rather than raising: a mistyped zone should
    make his timestamps unsurprising, not stop him working.

    Read per call rather than resolved once and held. A snapshot would be wrong from the
    moment someone changed the setting until the process restarted, and this is a desktop
    app where the settings page is two clicks away.
    """
    try:
        return ZoneInfo(str(tuning.value("timezone")))
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def local(dt: datetime) -> datetime:
    return dt.astimezone(local_tz())


def clock_line() -> str:
    """A human sentence for the current moment, e.g.
    'It's Monday, 27 Jul 2026, 9:52 PM (evening).'"""
    here = local(clock.now_utc())
    return f"It's {here.strftime('%A, %-d %b %Y, %-I:%M %p')} ({clock.time_of_day(here.hour)})."


def resolve_fire_at(in_minutes: float | None = None, at: str | None = None) -> str:
    """When a reminder should fire, as a UTC ISO string.

    Accepts a relative offset (``in_minutes``) or an absolute time (``at``, ISO;
    a naive time is read in the local zone). ``in_minutes`` wins if both given.
    """
    if in_minutes is not None:
        return (clock.now_utc() + timedelta(minutes=float(in_minutes))).isoformat()
    if at:
        # The one place a naive time means his wall clock rather than UTC: "remind me at 9pm"
        # is 9pm where he is. The zone is passed rather than assumed, which is why
        # `clock.parse` can stay in the kernel.
        dt = clock.parse(at, tz=local_tz())
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
    now = after or clock.now_utc()
    if every_minutes:
        return (now + timedelta(minutes=float(every_minutes))).isoformat()
    if daily_at:
        try:
            hh, mm = (int(part) for part in daily_at.split(":", 1))
        except (ValueError, TypeError):
            raise ValueError("daily_at must look like 'HH:MM'") from None
        here = local(now)
        target = here.replace(hour=hh % 24, minute=mm % 60, second=0, microsecond=0)
        if target <= here:
            target += timedelta(days=1)
        return target.astimezone(UTC).isoformat()
    raise ValueError("give either every_minutes or daily_at")
