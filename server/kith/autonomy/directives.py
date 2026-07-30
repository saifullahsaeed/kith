"""Loading the instructions a tick runs under.

The eight directives used to be Python string constants in the middle of the loop —
roughly a hundred lines of prose wrapped in parentheses, sitting between a thread
and a lock. They are not code. They are the thing you edit most often when tuning
how Kith behaves, and editing them meant opening the module that runs him.

They are markdown now, one file per mode, beside the loop that uses them — the same
convention ``persona/`` already follows. So changing what a reflection tick asks for
is a text edit, and a diff of it reads as English.

Read once at import: a tick must not depend on the filesystem mid-run, and a typo in
a directive should surface at startup rather than three hours into a session.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent / "directives"


def _load(name: str) -> str:
    path = _DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"missing directive: {path}")
    return path.read_text().strip()


#: Running on his own with nothing pressing — take one real step.
AUTONOMY = _load("autonomy")

#: Step back and look honestly at whether the work is actually moving.
REFLECTION = _load("reflection")

#: His own time to get sharper, anchored to the real work.
CURIOSITY = _load("curiosity")

#: Distil the journal into fewer, truer memories — the equivalent of sleep.
CONSOLIDATION = _load("consolidation")

#: Working a task through plan → act → verify → deliver.
WORK = _load("work")

#: His person replied on a task he was waiting on.
RESUME = _load("resume")

#: His person wrote to him and is waiting to hear back.
REPLY = _load("reply")

#: He has been going in circles and is being made to change course.
BREAKOUT = _load("breakout")

ALL = {
    "autonomy": AUTONOMY,
    "reflection": REFLECTION,
    "curiosity": CURIOSITY,
    "consolidation": CONSOLIDATION,
    "work": WORK,
    "resume": RESUME,
    "reply": REPLY,
    "breakout": BREAKOUT,
}
