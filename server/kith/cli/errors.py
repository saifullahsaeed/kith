"""How this exits, and why the numbers are the ones they are.

A CLI another agent calls is read by its exit code long before anyone reads its output, so the
codes have to distinguish the two failures that need different responses: *Kith is not running*
(start him, try again) from *Kith ran and it went wrong* (don't retry, read the message).

``69`` is ``EX_UNAVAILABLE`` from ``sysexits.h``. Using the conventional number rather than a
private one matters here for a specific reason: a shell wrapper or a supervisor that already
knows sysexits will treat "service is down" correctly without being taught, and 69 is the one
value in that table that means exactly this.
"""

from __future__ import annotations

import sys

#: Everything worked.
OK = 0

#: The turn itself failed — the model errored, a tool blew up, the server said no. Kith was
#: reachable and answered; the answer was a failure.
FAILED = 1

#: Bad arguments. argparse's own convention, kept so a typo does not look like a server fault.
USAGE = 2

#: Nothing is listening. Distinct from every other failure because it has exactly one fix, and
#: because a caller retrying this one is sensible where retrying the others is not.
UNAVAILABLE = 69

#: Something we did not anticipate. `EX_SOFTWARE`. If this is ever seen, the message alongside
#: it is the bug report.
INTERNAL = 70


class Failure(Exception):
    """A failure with an exit code attached, raised anywhere and handled once in `main`.

    The alternative — every command returning a code, every caller remembering to propagate it
    — is the shape where one forgotten `return` turns a failure into a silent success. Here the
    only way to fail is to raise, and the only place that decides what to print is `main`.
    """

    def __init__(self, message: str, code: int = FAILED, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        #: What to do about it, on its own line. Separate from the message because a hint is
        #: advice and a message is fact, and a caller parsing stderr should be able to tell.
        self.hint = hint


def die(message: str, code: int = FAILED, hint: str = "") -> None:
    raise Failure(message, code, hint)


def report(failure: Failure) -> int:
    """Print it the way a command line should: to stderr, prefixed, hint on its own line."""
    print(f"kith: {failure}", file=sys.stderr)
    if failure.hint:
        print(f"      {failure.hint}", file=sys.stderr)
    return failure.code
