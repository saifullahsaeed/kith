"""Planning has to be as cheap to do as to skip, or it gets skipped.

Two measured facts sit behind this file, and they are the same fact twice. Told to delegate,
Kith delegates well; left alone it never reaches for `delegate_subtask` at all. Told to plan, it
plans well; left alone it does the work inline and files nothing.

Neither is a judgement failure. Both are economics. `shell` costs one call and can do anything,
so it wins every round against a tool that costs a dozen — and a plan was a dozen: an `add_task`
each, then every checklist item. So: one call for a plan.

This file also used to hold the milestone refusal — a task filed into a project with a roadmap had
to name the step it delivered. Milestones are gone, and with them the refusal: a task filed into a
project needs a done-condition and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools import tasks as task_tools


def _project(db: Path, name: str = "Sadeef AI") -> int:
    return repo.projects.add_project(db, name)["id"]


class TestFilingIntoAProject:
    def test_a_task_with_a_done_condition_is_filed(self, db: Path):
        """Refusing here would make the first task of a new project impossible to file."""
        here = _project(db)
        answer = task_tools.add_task(
            db,
            {"goal": "First thing", "description": "a real done-condition here", "project_id": here},
        )
        assert answer.get("ok", True) is not False
        assert answer["project_id"] == here

    def test_a_task_with_no_project_is_left_alone(self, db: Path):
        answer = task_tools.add_task(db, {"goal": "Buy milk"})
        assert answer.get("ok", True) is not False

    def test_a_stale_milestone_argument_is_ignored_rather_than_fatal(self, db: Path):
        """His transcripts and his habits both still say `milestone_id`. The schema no longer
        offers it; a call that sends it anyway files the task and drops the number."""
        here = _project(db)
        answer = task_tools.add_task(
            db,
            {
                "goal": "A thing",
                "description": "a real done-condition here",
                "project_id": here,
                "milestone_id": 7,
            },
        )
        assert answer.get("ok", True) is not False
        assert "milestone_id" not in answer


class TestOneCallLaysOutTheNextTasks:
    def test_it_writes_the_tasks_and_their_checklists(self, db: Path):
        here = _project(db)
        answer = task_tools.plan_work(
            db,
            {
                "project_id": here,
                "tasks": [
                    {
                        "goal": "Chunker keeps page numbers",
                        "description": "tests/test_chunk.py::test_pages exits 0",
                        "checklist": ["read chunk_document", "carry page through", "add the test"],
                    },
                    {
                        "goal": "Reranker is measured",
                        "description": "scripts/eval.py prints ndcg above 0.7",
                        "checklist": ["wire ragas"],
                    },
                ],
            },
        )

        assert answer["ok"] is True
        assert answer["project_id"] == here
        assert len(answer["tasks"]) == 2
        filed = repo.tasks.list_tasks(db)
        assert {one["goal"] for one in filed} == {"Chunker keeps page numbers", "Reranker is measured"}
        assert all(one["project_id"] == here for one in filed)
        assert len(repo.tasks.list_checklist(db, answer["tasks"][0]["id"])) == 3

    def test_everything_lands_in_planning(self, db: Path):
        """Nothing is born pickable. `plan_work` is a shortcut through the writers, not around
        the gate they enforce."""
        here = _project(db)
        task_tools.plan_work(
            db,
            {"project_id": here, "tasks": [{"goal": "A thing", "description": "a real done-condition here"}]},
        )
        assert {one["status"] for one in repo.tasks.list_tasks(db)} == {"planning"}

    def test_a_task_with_no_done_condition_is_still_refused(self, db: Path):
        """The rules live in `add_task` and `plan_work` goes through it. A second write path
        would be a second place for them to be wrong."""
        here = _project(db)
        answer = task_tools.plan_work(
            db,
            {
                "project_id": here,
                "tasks": [
                    {"goal": "Fine", "description": "a real done-condition here"},
                    {"goal": "Vague", "description": "verify"},
                ],
            },
        )
        assert [one["goal"] for one in answer["tasks"]] == ["Fine"]
        assert answer["refused"][0]["goal"] == "Vague"
        assert (
            "done" in answer["refused"][0]["why"].lower() or "finish" in answer["refused"][0]["why"].lower()
        )

    def test_the_good_ones_are_kept_when_one_is_refused(self, db: Path):
        """Partial rather than rolled back: four written and one refused is four tasks of real
        work plus one thing to fix, and a rollback throws the four away."""
        here = _project(db)
        task_tools.plan_work(
            db,
            {
                "project_id": here,
                "tasks": [
                    {"goal": "One", "description": "a real done-condition here"},
                    {"goal": "Two", "description": "x"},
                ],
            },
        )
        assert len(repo.tasks.list_tasks(db)) == 1

    def test_an_old_milestone_argument_does_not_stop_it(self, db: Path):
        """The shape it was called with for weeks. The step's name has nowhere to go now; the
        tasks still do."""
        here = _project(db)
        answer = task_tools.plan_work(
            db,
            {
                "project_id": here,
                "milestone": "Retrieval is trustworthy",
                "tasks": [{"goal": "A thing", "description": "a real done-condition here"}],
            },
        )
        assert answer["ok"] is True
        assert len(repo.tasks.list_tasks(db)) == 1

    def test_it_needs_a_project(self, db: Path):
        answer = task_tools.plan_work(db, {"tasks": [{"goal": "x", "description": "a real done-condition"}]})
        assert answer["ok"] is False

    def test_it_needs_tasks(self, db: Path):
        here = _project(db)
        answer = task_tools.plan_work(db, {"project_id": here, "tasks": []})
        assert answer["ok"] is False


class TestAWorkerCannotPlan:
    def test_plan_work_is_withheld(self):
        """A sub-agent that lays out work is a sub-agent making plans, and the plan is the
        one thing that must stay in the conversation a person can see."""
        from kith.tools import workers

        for role in workers.ROLES:
            assert "plan_work" not in workers.ROLES[role]["given"]()
