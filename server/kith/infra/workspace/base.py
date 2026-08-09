"""What everything in here shares: the error, the shape of a result, and how much of it.

Nothing in this module knows where the workspace is or how to reach it — that is the point.
It sits at the bottom so the six modules above it can depend on it and not on each other.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How long one shell command may take before it is stopped.
#:
#: This was 900 — fifteen minutes — from when the shell was the only way to run anything, so
#: it had to cover a full build. The effect was that a command which stuck waiting for
#: something took the turn with it: nothing came back, nothing could be read, and by the time
#: it gave up the conversation had been abandoned. A person watching that does not see a
#: timeout, they see him frozen.
#:
#: Three minutes covers an npm install on a cold cache, which is the honest upper bound for
#: something you wait for. Genuinely long-lived work has `start_process` now, and genuinely
#: long test suites have `run_tests` with its own limit — so the shell no longer has to be
#: the tool that can do everything, and can be the tool that comes back.
_EXEC_TIMEOUT = 180
_OUTPUT_LIMIT = 8_000


class WorkspaceError(RuntimeError):
    """Anything that went wrong doing work on the machine."""


#: The old name. Kept because a dozen call sites catch it by name and because a tool
#: raising an error the loop does not recognise ends a turn instead of informing him.
SandboxError = WorkspaceError


@dataclass
class ExecResult:
    exit_code: int
    output: str


def _clip(text: str) -> str:
    if len(text) > _OUTPUT_LIMIT:
        return text[:_OUTPUT_LIMIT] + f"\n… [truncated, {len(text)} chars total]"
    return text
