"""Breaking a loop must not manufacture the blocker it then reports.

Measured on the ai-play project, 2026-08-03. The loop-breaker fired at 12:17 and he wrote:

    "this session has no filesystem, shell, editor, test, or browser access, so I cannot
     inspect or implement the frontend chooser or verify its tests/build"

— on a day he had made 554 `read_file` calls and 148 `shell` calls. It was not a hallucination.
`_ALLOW["breakout"]` handed him six tools (journal, list_tasks, read_skill, recall,
update_project, update_task), so he looked at what he had, correctly saw no way to touch a file,
and reported it. Then he used the one tool he did have to park the task on his person.

Three faults compounding, one per test class here:

* the loadout removed exactly the capabilities the directive asks him to assess;
* the directive named `ask_on_task`, which was not in that loadout — so he could not follow the
  instruction as written even if he wanted to, and improvised with `update_task`;
* `update_task(status='waiting')` notified nobody, so the park was invisible. Four tasks sat in
  "Waiting on you" that nobody knew were waiting, and this one sat for three hours until
  someone read the tick log.
"""

from __future__ import annotations

from pathlib import Path

from kith.autonomy import directives
from kith.autonomy.toolsets import _ALLOW, _WORK
from kith.infra.db import repositories as repo
from kith.tools import run_tool


#: Long enough to clear `_VERIFY_MIN_BRIEF` (80), so closing one of these goes through the
#: verification gate rather than past it — which is the path the duplicate-notification risk
#: lives on. The first version of this file used a 69-character brief and skipped the gate
#: entirely, so the test that was meant to catch a double notification saw none at all.
_BRIEF = (
    "Done when the chooser lists every model from the catalog, remembers the one picked, "
    "and the frontend tests pass."
)


def _a_task(db: Path, status: str = "working") -> dict:
    return repo.tasks.add_task(
        db, "Implement the frontend chooser", "high", None, _BRIEF, status, "kith", None, None
    )


class TestBreakoutCanActNotJustDescribe:
    def test_it_has_the_tools_it_is_told_to_use(self):
        """Option (a) is "a genuinely different approach". With bookkeeping tools only, that is
        something he can write down and not something he can do."""
        assert _ALLOW["breakout"] == _WORK

    def test_the_capabilities_he_reported_missing_are_there(self):
        for name in ("shell", "read_file", "edit_file", "run_tests", "browse_page"):
            assert name in _ALLOW["breakout"], f"breakout still cannot {name}"

    def test_every_tool_the_directive_names_is_one_he_has(self):
        """The general form of the `ask_on_task` bug: a directive that names a tool the mode
        does not offer is an instruction that cannot be followed, in the one place where that
        is invisible — he simply does something else."""
        for mode, text in directives.ALL.items():
            offered = _ALLOW.get(mode)
            if offered is None:
                continue
            for name in sorted(_WORK):
                if f"{name}:" in text or f"`{name}`" in text or f"{name} status=" in text:
                    assert name in offered, f"{mode} directive names {name!r}, which it lacks"


class TestTheDirectiveWantsARefusalNotAHunch:
    def test_it_says_the_tools_are_there(self):
        """He reasoned from the loadout in front of him. The fix is the loadout, but the
        directive should not leave the question open either."""
        text = directives.BREAKOUT.lower()
        assert "full working tools" in text
        assert "nothing has been taken away" in text

    def test_a_block_has_to_be_one_he_hit(self):
        text = directives.BREAKOUT.lower()
        assert "refused you" in text
        assert "try it and read the error" in text

    def test_it_still_refuses_to_let_him_drop_a_blocked_task(self):
        """The part that was already right, and worth keeping through a rewrite."""
        text = directives.BREAKOUT.lower()
        assert "do not drop it" in text
        assert "never pause or abandon a whole project" in text


class TestParkingWorkIsNeverSilent:
    def test_leaving_a_task_with_them_tells_them(self, db: Path):
        task = _a_task(db)
        run_tool("update_task", {"id": task["id"], "status": "waiting"}, db)
        stuck = [m for m in repo.messages.list_messages(db, 20) if m.get("kind") == "stuck"]
        assert len(stuck) == 1
        assert f"#{task['id']}" in stuck[0]["body"]
        assert stuck[0]["link"] == f"/tasks/{task['id']}"

    def test_restating_it_does_not_tell_them_again(self, db: Path):
        """A tick that re-parks a task it already parked would otherwise ping on every pass,
        and a notification that arrives for no change is one they learn to ignore."""
        task = _a_task(db)
        for _ in range(3):
            run_tool("update_task", {"id": task["id"], "status": "waiting"}, db)
        assert len([m for m in repo.messages.list_messages(db, 20) if m.get("kind") == "stuck"]) == 1

    def test_the_unmet_brief_hand_back_still_tells_them_exactly_once(self, db: Path):
        """`_verify_done` refuses a close it does not believe, sets 'waiting' itself, and
        notifies. It then falls through the same update path — so without a marker this is
        where the duplicate would appear."""
        task = _a_task(db, status="working")
        run_tool(
            "update_task",
            {
                "id": task["id"],
                "status": "done",
                "verification": [
                    {"requirement": "the chooser lists models", "met": True, "evidence": "did it"},
                    {"requirement": "the frontend tests pass", "met": False, "evidence": ""},
                ],
            },
            db,
        )
        stuck = [m for m in repo.messages.list_messages(db, 20) if m.get("kind") == "stuck"]
        assert len(stuck) == 1, f"expected one notification, got {len(stuck)}"
        detail = repo.tasks.task_detail(db, task["id"])
        assert detail["status"] == "waiting"

    def test_an_ordinary_move_says_nothing(self, db: Path):
        """Only the column that means "your turn" is worth interrupting them for."""
        task = _a_task(db)
        for status in ("working", "planned", "backlog"):
            run_tool("update_task", {"id": task["id"], "status": status}, db)
        assert [m for m in repo.messages.list_messages(db, 20) if m.get("kind") == "stuck"] == []

    def test_no_argument_he_can_pass_makes_it_quiet(self, db: Path):
        """The first version of this deduped the two paths with an "already told them" marker set
        in the argument dict — which the model fills in, so anything that suppresses the
        notification is something he could suppress it with. The dedupe reads the status he
        *asked* for instead. These are the shapes that marker would have taken."""
        for extra in ({"_told_them": True}, {"notify": False}, {"silent": True}):
            task = _a_task(db)
            run_tool("update_task", {"id": task["id"], "status": "waiting", **extra}, db)
            stuck = [
                m
                for m in repo.messages.list_messages(db, 50)
                if m.get("kind") == "stuck" and f"#{task['id']}" in m["body"]
            ]
            assert len(stuck) == 1, f"{extra} silenced the park"
