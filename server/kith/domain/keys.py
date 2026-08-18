"""A name a task keeps when it leaves this machine.

`tasks.id` is `INTEGER PRIMARY KEY AUTOINCREMENT`, which is a perfectly good handle for one
database and cannot be an identity for two. Two people both file something on Monday and both get
108. Worse — and this is the part that kills the integer outright — when the second person pulls
the first one's brief, their Kith has to make a local row for it, and that row gets *their* next
number. The same task ends up with a different id on each machine by construction. No amount of
care about allocation fixes that; the local number is a local fact.

So the identity travels in the file and the number stays home. `tasks.key` is what "the same
task" means once `.kith/` is shared, and the integer goes on being what every existing tool
argument, URL and filename already uses.

**Time-ordered on purpose.** A random key would do for uniqueness and would make a directory
listing meaningless — and these end up in filenames, where sorting by name is how a person finds
the recent one. The first eleven characters are milliseconds since the epoch in hex, which stays
eleven characters until the year 10889; the last five are random, which is 1,048,576 keys per
millisecond before two can collide.

Hex rather than base32: it is unambiguous to read aloud, safe in a filename on every filesystem,
and case-insensitively distinct, which matters because macOS will not distinguish `a1b` from
`A1B` and would happily merge two tasks into one file.
"""

from __future__ import annotations

import secrets
import time

#: Characters of hex timestamp. Eleven holds milliseconds past the year 10000.
_WHEN = 11
#: Characters of randomness. Five hex digits is ~1M per millisecond.
_LUCK = 5

KEY_LENGTH = _WHEN + _LUCK


def new_key(now_ms: int | None = None) -> str:
    """A fresh key. Sortable by when it was made, unique without asking anybody."""
    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return f"{stamp:0{_WHEN}x}{secrets.randbelow(16**_LUCK):0{_LUCK}x}"


def looks_like_a_key(text: str) -> bool:
    """Is this one of ours, rather than an integer id or a slug?

    Length and alphabet only. It is asked of filenames, where the alternative is a number that
    has been there since long before keys existed, and the two cannot be confused: a key is 16
    hex characters and an id is at most a few digits.
    """
    candidate = str(text or "").strip().lower()
    if len(candidate) != KEY_LENGTH:
        return False
    return all(c in "0123456789abcdef" for c in candidate)
