"""The work prompt surfaces enough recent attempts, framed as a loop warning.

The 57M-token night was one task re-attempted 23 times because each tick could see only its
last three steps, neutrally worded, and never registered that it was repeating itself. This
deepens that window to a tunable count and frames it as "if these look the same, change or
escalate" — so a tick can tell it is looping and break out instead of grinding on.
"""

from kith.autonomy import prompts
from kith.infra.db import repositories as repo
from kith.services import tuning


def test_focus_prompt_shows_the_configured_number_of_recent_steps(db, monkeypatch):
    monkeypatch.setattr(prompts, "AGENT_DB_PATH", db)
    tuning.apply({"handoff_steps": 6})
    for i in range(8):
        repo.journal.add_journal(db, f"(start) attempt {i}: db:generate still failing [used: shell]")

    detail = {"id": 96, "goal": "Make db:generate pass", "priority": "high", "checklist": []}
    text = prompts._focus_prompt(detail, [detail])

    # the 6 most-recent attempts are shown; the older two (0, 1) are not
    assert "attempt 7" in text
    assert "attempt 2" in text
    assert "attempt 1" not in text


def test_focus_prompt_frames_recent_steps_as_a_loop_warning(db, monkeypatch):
    monkeypatch.setattr(prompts, "AGENT_DB_PATH", db)
    tuning.apply({"handoff_steps": 8})
    repo.journal.add_journal(db, "(start) inspected the scaffold; still missing a Prisma schema")

    detail = {"id": 96, "goal": "Make db:generate pass", "priority": "high", "checklist": []}
    text = prompts._focus_prompt(detail, [detail]).lower()

    # not neutral "your last steps"; it must prompt a change of course
    assert "change approach" in text or "going in circles" in text or "same attempt" in text
