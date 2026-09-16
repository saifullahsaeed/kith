"""Planning has to be as cheap to do as to skip, or it gets skipped.

Two measured facts sit behind this file, and they are the same fact twice. Told to delegate,
Kith delegates well; left alone it never reaches for `delegate_subtask` at all. Told to plan, it
plans well; left alone it does the work inline and files nothing — and when it does file a task
into a project with a roadmap, the task lands beside the roadmap rather than under it.

Neither is a judgement failure. Both are economics. `shell` costs one call and can do anything,
so it wins every round against a tool that costs a dozen — and a milestone was a dozen: one
`add_milestone`, then an `add_task` each, then every `add_checklist_item`. And `milestone_id` is
a bare integer in `add_task`'s schema, so placing a task correctly meant having already called
`list_tasks`, held the ids, and recalled the right one at that moment, with skipping it always
valid because the field is optional.

So: one call for a plan, and the candidates offered at the moment the choice is made.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools import tasks as task_tools


def _project(db: Path, name: str = "Sadeef AI") -> int:
    return repo.projects.add_project(db, name)["id"]


class TestAMilestoneIsOfferedWhereTheChoiceIsMade:
    def test_a_task_into_a_project_with_a_roadmap_must_say_which_step(self, db: Path):
        here = _project(db)
        repo.projects.add_milestone(db, here, "Retrieval is trustworthy")

        answer = task_tools.add_task(
            db,
            {
                "goal": "Chunker keeps page numbers",
                "description": "tests/test_chunk.py::test_pages passes",
                "project_id": here,
            },
        )
        assert answer["ok"] is False
        assert "milestone" in answer["error"]

    def test_the_refusal_names_the_candidates(self, db: Path):
        """The whole point. A refusal that says 'pass milestone_id' and nothing else sends it
        back to the board to look them up, which is the round this is trying to save."""
        here = _project(db)
        first = repo.projects.add_milestone(db, here, "Retrieval is trustworthy")
        second = repo.projects.add_milestone(db, here, "Connections survive a token refresh")

        answer = task_tools.add_task(
            db,
            {"goal": "x", "description": "a real done-condition here", "project_id": here},
        )
        offered = {one["id"]: one["title"] for one in answer["milestones"]}
        assert offered == {
            first["id"]: "Retrieval is trustworthy",
            second["id"]: "Connections survive a token refresh",
        }

    def test_a_finished_milestone_is_not_offered(self, db: Path):
        here = _project(db)
        done = repo.projects.add_milestone(db, here, "Already shipped")
        repo.projects.update_milestone(db, done["id"], status="done")

        answer = task_tools.add_task(
            db,
            {"goal": "x", "description": "a real done-condition here", "project_id": here},
        )
        # No open milestone to choose from, so nothing to refuse over.
        assert answer.get("ok", True) is not False

    def test_a_project_with_no_roadmap_is_left_alone(self, db: Path):
        """Refusing here would make the first task of a new project impossible to file."""
        here = _project(db)
        answer = task_tools.add_task(
            db,
            {"goal": "First thing", "description": "a real done-condition here", "project_id": here},
        )
        assert answer.get("ok", True) is not False

    def test_a_task_with_no_project_is_left_alone(self, db: Path):
        answer = task_tools.add_task(db, {"goal": "Buy milk"})
        assert answer.get("ok", True) is not False


class TestOneCallLaysOutAMilestone:
    def test_it_writes_the_step_its_tasks_and_their_checklists(self, db: Path):
        here = _project(db)
        answer = task_tools.plan_work(
            db,
            {
                "project_id": here,
                "milestone": "Retrieval is trustworthy",
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
        assert len(answer["tasks"]) == 2
        filed = repo.tasks.list_tasks(db)
        assert {one["goal"] for one in filed} == {"Chunker keeps page numbers", "Reranker is measured"}
        assert all(one["milestone_id"] == answer["milestone_id"] for one in filed)
        assert len(repo.tasks.list_checklist(db, answer["tasks"][0]["id"])) == 3

    def test_everything_lands_in_planning(self, db: Path):
        """Nothing is born pickable. `plan_work` is a shortcut through the writers, not around
        the gate they enforce."""
        here = _project(db)
        task_tools.plan_work(
            db,
            {
                "project_id": here,
                "milestone": "A step",
                "tasks": [{"goal": "A thing", "description": "a real done-condition here"}],
            },
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
                "milestone": "A step",
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
                "milestone": "A step",
                "tasks": [
                    {"goal": "One", "description": "a real done-condition here"},
                    {"goal": "Two", "description": "x"},
                ],
            },
        )
        assert len(repo.tasks.list_tasks(db)) == 1

    def test_it_can_hang_tasks_on_a_milestone_that_exists(self, db: Path):
        here = _project(db)
        step = repo.projects.add_milestone(db, here, "Already planned")
        answer = task_tools.plan_work(
            db,
            {
                "project_id": here,
                "milestone_id": step["id"],
                "tasks": [{"goal": "A thing", "description": "a real done-condition here"}],
            },
        )
        assert answer["milestone_id"] == step["id"]
        assert len(repo.projects.list_milestones(db, here)) == 1

    def test_it_needs_a_step_to_hang_them_on(self, db: Path):
        here = _project(db)
        answer = task_tools.plan_work(db, {"project_id": here, "tasks": [{"goal": "x", "description": "y"}]})
        assert answer["ok"] is False

    def test_it_needs_tasks(self, db: Path):
        here = _project(db)
        answer = task_tools.plan_work(db, {"project_id": here, "milestone": "A step", "tasks": []})
        assert answer["ok"] is False


class TestAWorkerCannotPlan:
    def test_plan_work_is_withheld(self):
        """A sub-agent that lays out a roadmap is a sub-agent making plans, and the plan is the
        one thing that must stay in the conversation a person can see."""
        from kith.tools import delegation

        for role in delegation.ROLES:
            assert "plan_work" not in delegation.ROLES[role]["given"]()
