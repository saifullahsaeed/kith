"""A provider error (a 502, a rate-limit) is not a real attempt.

The 57M night had a 502 mid-loop. An errored tick must not count toward the grind limit (it
proves nothing about whether the work is stuck), and it should briefly back the loop off so a
flapping upstream isn't hammered tick after tick.
"""

import sys

from kith.infra.db import repositories as repo
from kith.services import tuning


def _runner_on(db, monkeypatch, events):
    module = sys.modules["kith.autonomy.runner"]
    prompts = sys.modules["kith.autonomy.prompts"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    monkeypatch.setattr(prompts, "AGENT_DB_PATH", db)
    monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(list(events)))
    return module.AutonomyRunner()


def test_error_ticks_do_not_count_toward_the_grind_limit(db, monkeypatch):
    tuning.apply({"focus_grind_limit": 3, "stall_break": 50, "stall_giveup": 50})
    task = repo.tasks.add_task(
        db, "Make db:generate pass", "high", None, "done when it exits 0", "todo", "kith", None, None
    )
    repo.tasks.add_checklist_item(db, task["id"], "run db:generate")

    r = _runner_on(db, monkeypatch, [{"type": "error", "message": "502 overloaded"}])
    for _ in range(6):  # would set aside a real task; these are all errors
        r._step(None, "")

    assert repo.tasks.task_detail(db, task["id"])["status"] != "waiting"
    assert r._focus_grind == 0
    assert r._error_backoff_until > 0  # backoff armed after an error
