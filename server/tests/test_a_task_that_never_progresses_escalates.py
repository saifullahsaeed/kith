"""A task worked for too many ticks with no NET progress is set aside — the third stall signal.

The 23x Prisma loop slipped past the prose detector (it reworded itself) and the shape detector
(it 'used update_task' each tick). This signal ignores both and watches the task's own state:
checklist items ticked + deliverables filed. No motion for focus_grind_limit ticks -> hand it
back via the same _give_up path stall-giveup uses. The existing detectors are set high here so
this one is what fires.
"""

import sys

from kith.infra.db import repositories as repo
from kith.services import tuning


def _runner_on(db, monkeypatch):
    module = sys.modules["kith.autonomy.runner"]
    prompts = sys.modules["kith.autonomy.prompts"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    # _give_up + the prompt builders read prompts.AGENT_DB_PATH, so point it at the same db
    # or escalation writes "waiting" to a different temp database than the test reads.
    monkeypatch.setattr(prompts, "AGENT_DB_PATH", db)
    monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(()))  # each tick does nothing
    return module.AutonomyRunner()


def test_a_task_worked_with_no_progress_is_set_aside(db, monkeypatch):
    tuning.apply({"focus_grind_limit": 3, "stall_break": 50, "stall_giveup": 50})
    task = repo.tasks.add_task(
        db,
        "Make db:generate pass",
        "high",
        None,
        "Done when npm run db:generate exits 0",
        "todo",
        "kith",
        None,
        None,
    )
    repo.tasks.add_checklist_item(db, task["id"], "run db:generate")

    r = _runner_on(db, monkeypatch)
    for _ in range(4):  # first sight + 3 no-progress ticks -> escalation
        r._step(None, "")

    assert repo.tasks.task_detail(db, task["id"])["status"] == "waiting"
    assert any(m.get("kind") == "stuck" for m in repo.messages.list_messages(db, limit=10))


def test_real_progress_resets_the_grind_counter(db, monkeypatch):
    tuning.apply({"focus_grind_limit": 3, "stall_break": 50, "stall_giveup": 50})
    task = repo.tasks.add_task(
        db,
        "Build the thing",
        "high",
        None,
        "done when built",
        "todo",
        "kith",
        None,
        None,
    )
    item = repo.tasks.add_checklist_item(db, task["id"], "step one")

    r = _runner_on(db, monkeypatch)
    r._step(None, "")  # first sight
    r._step(None, "")  # grind 1
    r._step(None, "")  # grind 2
    repo.tasks.set_checklist_item(db, item["id"], done=True)  # real progress
    r._step(None, "")  # progress seen -> grind resets
    r._step(None, "")  # grind 1 again, nowhere near the limit

    assert repo.tasks.task_detail(db, task["id"])["status"] != "waiting"
