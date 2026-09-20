"""What he is putting in front of you.

He could make designs and never show one. Asked to, he went looking for the word "board" —
`find -iname "*board*"`, `git log | grep board`, two plugin reads, `ls boards/` — because the
surface that draws them belongs to the interface and nothing told him it could be opened. Three
commands to not find a thing that was already on screen one click away.

So: one small piece of state saying what he last asked you to look at, and a `changed` ping. The
interface fetches it and opens the board. Deliberately *not* a payload on the event itself —
`kernel/changes` carries no values on purpose, and a second kind of event that did would be a
second protocol.

**In memory, and that is the design.** Showing somebody something is an act inside a
conversation, not a fact about the world: a board he opened last Tuesday is not something you
want reopened when you relaunch the app. It dies with the process, which is exactly as long as
it means anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from kith.infra.db.support import utc_now_iso
from kith.kernel import changes, session_context


@dataclass(frozen=True)
class Showing:
    """One thing he asked you to look at."""

    kind: str
    task_id: int
    #: A sentence he wants read next to it, when he has one.
    note: str
    at: str
    #: Which conversation asked, so a board does not open over an unrelated chat.
    conversation: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "taskId": self.task_id,
            "note": self.note,
            "at": self.at,
            "conversation": self.conversation,
        }


_lock = Lock()
_current: Showing | None = None


def show(kind: str, task_id: int, note: str = "") -> Showing:
    """Put something in front of them, and say so."""
    global _current
    made = Showing(
        kind=kind,
        task_id=int(task_id),
        note=note.strip()[:400],
        at=utc_now_iso(),
        conversation=session_context.current(),
    )
    with _lock:
        _current = made
    changes.publish("showing", made.conversation)
    return made


def current() -> dict | None:
    with _lock:
        return _current.as_dict() if _current else None


def clear() -> None:
    """Forget it — used when the interface has opened it, and by tests."""
    global _current
    with _lock:
        _current = None
