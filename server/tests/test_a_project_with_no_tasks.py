"""A laid-out project that nothing can act on.

Asked for a workout tracker he did it mostly right: a project, five milestones phrased as
states, ordered correctly. And no tasks. Everything that decides what to do next reads
``active_tasks``, so the board was empty as far as the machinery was concerned — he went idle
and reflected, while a project sat at 0% looking planned and unable to move.

Told twice in prose to file tasks and twice he did not, so this is machinery: a milestone whose
turn it is with nothing under it counts as work, and the work is breaking it down.

One milestone at a time, deliberately. He does not know enough to break down the third before
the first is done, and tasks written that early are fiction he then feels obliged to follow.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo


@pytest.fixture
def project(db):
    made = repo.projects.add_project(db, "Gym Workout Tracker", "for me")
    return made["id"]


class TestFindingWorkNobodyCanStart:
    def test_a_milestone_with_no_tasks_is_work(self, db, project):
        first = repo.projects.add_milestone(db, project, "The data model is defined")
        found = repo.projects.milestones_needing_tasks(db)
        assert [m["id"] for m in found] == [first["id"]]
        assert found[0]["project"] == "Gym Workout Tracker"

    def test_a_milestone_with_open_tasks_is_not(self, db, project):
        first = repo.projects.add_milestone(db, project, "The data model is defined")
        repo.tasks.add_task(
            db, "Define the exercise record", status="planned", milestone_id=first["id"], project_id=project
        )
        assert repo.projects.milestones_needing_tasks(db) == []

    def test_only_the_milestone_whose_turn_it_is(self, db, project):
        first = repo.projects.add_milestone(db, project, "The data model is defined")
        second = repo.projects.add_milestone(db, project, "Logging works end to end")
        repo.projects.add_dependency(db, second["id"], first["id"])

        found = [m["id"] for m in repo.projects.milestones_needing_tasks(db)]
        # Not the second. Planning ahead of what he knows is how a plan becomes fiction, and
        # the point of the roadmap is that a waiting milestone stays out of his way — that has
        # to hold for planning it too, not only for working it.
        assert found == [first["id"]]

    def test_a_finished_milestone_is_not_work(self, db, project):
        first = repo.projects.add_milestone(db, project, "The data model is defined")
        repo.projects.update_milestone(db, first["id"], status="done")
        assert repo.projects.milestones_needing_tasks(db) == []

    def test_a_parked_project_is_left_alone(self, db, project):
        repo.projects.add_milestone(db, project, "The data model is defined")
        repo.projects.update_project(db, project, status="done")
        # Closing a project has to actually close it. Otherwise every finished project he has
        # ever had comes back as something to plan.
        assert repo.projects.milestones_needing_tasks(db) == []

    def test_two_independent_milestones_both_count(self, db, project):
        a = repo.projects.add_milestone(db, project, "The data model is defined")
        b = repo.projects.add_milestone(db, project, "The config format is decided")
        # Nothing waits on anything, so both are his to plan — the graph is a graph.
        assert {m["id"] for m in repo.projects.milestones_needing_tasks(db)} == {a["id"], b["id"]}

    def test_finishing_the_last_task_closes_the_milestone_rather_than_emptying_it(self, db, project):
        """The case that makes this safe to run on every tick.

        Finishing a milestone's last task completes the milestone, so it does not come back as
        "a milestone with nothing under it". Without that it would: every milestone he ever
        finished would look unplanned again the moment its tasks closed, and he would spend the
        rest of the project re-planning work he had already done.
        """
        first = repo.projects.add_milestone(db, project, "The data model is defined")
        made = repo.tasks.add_task(db, "Define it", milestone_id=first["id"], project_id=project)
        repo.tasks.update_task(db, made["id"], status="done")

        assert repo.projects.milestones_needing_tasks(db) == []
        closed = {m["id"]: m["status"] for m in repo.projects.list_milestones(db)}
        assert closed[first["id"]] == "done"


class TestTheStepItBecomes:
    def test_the_prompt_names_the_milestone_and_the_project(self):
        from kith.autonomy.prompts import _breakdown_prompt

        text = _breakdown_prompt(
            {
                "id": 26,
                "title": "The data model is defined",
                "project": "Gym Workout Tracker",
                "project_id": 15,
            }
        )
        assert "Gym Workout Tracker" in text
        assert "The data model is defined" in text
        # The id, because `add_task` needs it and making him look it up wastes a round.
        assert "26" in text

    def test_it_says_only_this_milestone(self):
        from kith.autonomy.prompts import _breakdown_prompt

        text = _breakdown_prompt({"id": 1, "title": "t", "project": "p", "project_id": 2})
        assert "Only this milestone" in text

    def test_it_says_to_stop_after_filing(self):
        from kith.autonomy.prompts import _breakdown_prompt

        text = _breakdown_prompt({"id": 1, "title": "t", "project": "p", "project_id": 2})
        # Filing and then starting in the same step is the failure the persona already names;
        # a planning step is exactly where it would happen.
        assert "stop" in text.lower()


class TestTheDuplicateMilestone:
    def test_the_same_title_twice_returns_the_first(self, db, project):
        from kith.tools import projects as tool

        one = tool.add_milestone(db, {"project_id": project, "title": "The data model is defined"})
        two = tool.add_milestone(db, {"project_id": project, "title": "The data model is defined"})
        assert one["id"] == two["id"], "a second row appeared"
        assert "already existed" in (two.get("note") or "")

    def test_it_does_not_leave_a_dangling_unordered_copy(self, db, project):
        from kith.tools import projects as tool

        tool.add_milestone(db, {"project_id": project, "title": "The data model is defined"})
        tool.add_milestone(db, {"project_id": project, "title": "  The data model is defined  "})
        # He called add_milestone five times for four milestones and the duplicate had no
        # order on it, so it was permanently available and looked like real work.
        assert len(repo.projects.list_milestones(db)) == 1

    def test_re_adding_with_after_wires_the_order(self, db, project):
        """The regression the guard itself caused, and the reason it needed a second pass.

        Laying out a fresh roadmap he has no ids, so he adds the milestones bare and comes
        back to wire the order — which is the same title again, this time with `after`. The
        first version of the guard returned the existing milestone and dropped the `after`, so
        he asked for three edges, got three milestones with no order, asked for the identical
        three again, and gave up: ten calls, zero edges, every milestone available at once.
        """
        from kith.tools import projects as tool

        first = tool.add_milestone(db, {"project_id": project, "title": "The contract is defined"})
        second = tool.add_milestone(db, {"project_id": project, "title": "Logging works"})
        # Now the order, on milestones that already exist.
        again = tool.add_milestone(
            db, {"project_id": project, "title": "Logging works", "after": [first["id"]]}
        )

        assert again["id"] == second["id"], "a duplicate row appeared"
        edges = {(e["milestone_id"], e["depends_on_id"]) for e in repo.projects.dependencies(db)}
        assert (second["id"], first["id"]) in edges, "the after was thrown away again"
        # And it says so, so he does not retry a call that worked.
        assert "waits for" in (again.get("note") or "")

    def test_the_order_is_applied_on_a_freshly_made_one_too(self, db, project):
        from kith.tools import projects as tool

        first = tool.add_milestone(db, {"project_id": project, "title": "The contract is defined"})
        second = tool.add_milestone(
            db, {"project_id": project, "title": "Logging works", "after": [first["id"]]}
        )
        edges = {(e["milestone_id"], e["depends_on_id"]) for e in repo.projects.dependencies(db)}
        assert (second["id"], first["id"]) in edges

    def test_asking_for_the_same_edge_twice_is_harmless(self, db, project):
        from kith.tools import projects as tool

        first = tool.add_milestone(db, {"project_id": project, "title": "The contract is defined"})
        for _ in range(3):
            tool.add_milestone(db, {"project_id": project, "title": "Logging works", "after": [first["id"]]})
        # He does retry. Three identical calls must leave one milestone and one edge.
        assert len(repo.projects.list_milestones(db)) == 2
        assert len(repo.projects.dependencies(db)) == 1

    def test_a_different_title_still_gets_its_own(self, db, project):
        from kith.tools import projects as tool

        tool.add_milestone(db, {"project_id": project, "title": "The data model is defined"})
        tool.add_milestone(db, {"project_id": project, "title": "Logging works end to end"})
        assert len(repo.projects.list_milestones(db)) == 2
