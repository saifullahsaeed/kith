"""Milestones that decide what happens next.

They used to be decoration: the roadmap showed progress after the fact while raw task
priority decided the order, so a roadmap imposed nothing on anything. These tests are about
the one behaviour that changes that — a milestone which is waiting keeps its tasks waiting
with it — and about the ways a dependency graph goes wrong.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo


@pytest.fixture
def project(db):
    return repo.projects.add_project(db, "Ship the thing", "")["id"]


def milestone(db, project_id, title, status="todo"):
    row = repo.projects.add_milestone(db, project_id, title)
    if status != "todo":
        repo.projects.update_milestone(db, row["id"], status=status)
    return row["id"]


def task(db, project_id, milestone_id, goal="do it"):
    row = repo.tasks.add_task(db, goal, "", project_id=project_id, milestone_id=milestone_id)
    return row["id"]


class TestTheGate:
    def test_a_task_under_a_ready_milestone_is_available(self, db, project):
        first = milestone(db, project, "Design")
        task(db, project, first)
        assert len(repo.tasks.active_tasks(db)) == 1

    def test_a_task_under_a_waiting_milestone_is_not(self, db, project):
        first = milestone(db, project, "Design")
        second = milestone(db, project, "Build")
        repo.projects.add_dependency(db, second, first)
        task(db, project, second)
        assert repo.tasks.active_tasks(db) == []

    def test_finishing_the_predecessor_releases_it(self, db, project):
        first = milestone(db, project, "Design")
        second = milestone(db, project, "Build")
        repo.projects.add_dependency(db, second, first)
        task(db, project, second)
        assert repo.tasks.active_tasks(db) == []
        repo.projects.update_milestone(db, first, status="done")
        assert len(repo.tasks.active_tasks(db)) == 1

    def test_a_task_with_no_milestone_is_never_gated(self, db, project):
        """A one-off errand should not need a roadmap to be allowed to happen."""
        first = milestone(db, project, "Design")
        second = milestone(db, project, "Build")
        repo.projects.add_dependency(db, second, first)
        repo.tasks.add_task(db, "buy milk", "", project_id=project)
        assert len(repo.tasks.active_tasks(db)) == 1

    def test_held_back_work_is_distinguishable_from_no_work(self, db, project):
        """"Nothing to do" and "not this milestone's turn" are different sentences, and a
        blocked board that looks like an empty one is how someone concludes it is broken."""
        first = milestone(db, project, "Design")
        second = milestone(db, project, "Build")
        repo.projects.add_dependency(db, second, first)
        task(db, project, second)
        assert repo.tasks.active_tasks(db) == []
        assert len(repo.tasks.waiting_on_the_roadmap(db)) == 1

    def test_a_chain_releases_one_step_at_a_time(self, db, project):
        """Asserted by which milestone's work is available, not by a count — completing a
        milestone does not complete its tasks, so counting would be counting the wrong
        thing and the first version of this test did exactly that."""
        a = milestone(db, project, "A")
        b = milestone(db, project, "B")
        c = milestone(db, project, "C")
        repo.projects.add_dependency(db, b, a)
        repo.projects.add_dependency(db, c, b)
        for one in (a, b, c):
            task(db, project, one)

        def available() -> set[int]:
            return {one["milestone_id"] for one in repo.tasks.active_tasks(db)}

        assert available() == {a}
        repo.projects.update_milestone(db, a, status="done")
        assert available() == {a, b}  # A's own task is still open; B is now allowed
        repo.projects.update_milestone(db, b, status="done")
        assert available() == {a, b, c}

    def test_one_unfinished_predecessor_out_of_several_is_enough_to_hold_it(self, db, project):
        a = milestone(db, project, "A", status="done")
        b = milestone(db, project, "B")
        c = milestone(db, project, "C")
        repo.projects.add_dependency(db, c, a)
        repo.projects.add_dependency(db, c, b)
        task(db, project, c)
        assert repo.tasks.active_tasks(db) == []


class TestCycles:
    def test_a_milestone_cannot_wait_for_itself(self, db, project):
        one = milestone(db, project, "Only")
        with pytest.raises(ValueError):
            repo.projects.add_dependency(db, one, one)

    def test_a_two_step_loop_is_refused(self, db, project):
        a = milestone(db, project, "A")
        b = milestone(db, project, "B")
        repo.projects.add_dependency(db, b, a)
        with pytest.raises(ValueError):
            repo.projects.add_dependency(db, a, b)

    def test_a_longer_loop_is_refused(self, db, project):
        """A cycle is not a slow roadmap — it is one where nothing is ever available, and he
        would sit doing nothing with no way to see why."""
        a = milestone(db, project, "A")
        b = milestone(db, project, "B")
        c = milestone(db, project, "C")
        repo.projects.add_dependency(db, b, a)
        repo.projects.add_dependency(db, c, b)
        with pytest.raises(ValueError):
            repo.projects.add_dependency(db, a, c)

    def test_a_diamond_is_fine(self, db, project):
        """Two paths converging is not a cycle, and refusing it would rule out the most
        ordinary shape a roadmap has."""
        a = milestone(db, project, "A")
        b = milestone(db, project, "B")
        c = milestone(db, project, "C")
        d = milestone(db, project, "D")
        repo.projects.add_dependency(db, b, a)
        repo.projects.add_dependency(db, c, a)
        repo.projects.add_dependency(db, d, b)
        repo.projects.add_dependency(db, d, c)
        assert len(repo.projects.dependencies(db, project)) == 4

    def test_adding_the_same_edge_twice_is_not_an_error(self, db, project):
        a = milestone(db, project, "A")
        b = milestone(db, project, "B")
        repo.projects.add_dependency(db, b, a)
        repo.projects.add_dependency(db, b, a)
        assert len(repo.projects.dependencies(db, project)) == 1


class TestTheRoadmapView:
    def test_it_says_what_is_ready_and_why_the_rest_is_not(self, db, project):
        a = milestone(db, project, "Design")
        b = milestone(db, project, "Build")
        repo.projects.add_dependency(db, b, a)
        view = repo.projects.roadmap(db, project)
        by_title = {node["title"]: node for node in view["milestones"]}
        assert by_title["Design"]["ready"] is True
        assert by_title["Build"]["ready"] is False
        # Named, not just flagged: "waiting" with no reason is not information.
        assert by_title["Build"]["blocked_by"] == ["Design"]

    def test_a_finished_milestone_is_not_ready_it_is_done(self, db, project):
        one = milestone(db, project, "Design", status="done")
        node = repo.projects.roadmap(db, project)["milestones"][0]
        assert node["id"] == one
        assert node["ready"] is False
        assert node["blocked_by"] == []

    def test_it_carries_the_task_counts_the_graph_shows(self, db, project):
        one = milestone(db, project, "Design")
        task(db, project, one)
        task(db, project, one)
        node = repo.projects.roadmap(db, project)["milestones"][0]
        assert node["tasks_total"] == 2
        assert node["tasks_active"] == 2
        assert node["tasks_done"] == 0

    def test_positions_are_remembered_once_someone_arranges_it(self, db, project):
        one = milestone(db, project, "Design")
        assert repo.projects.roadmap(db, project)["milestones"][0]["x"] is None
        repo.projects.set_milestone_position(db, one, 120.5, -40.0)
        node = repo.projects.roadmap(db, project)["milestones"][0]
        assert (node["x"], node["y"]) == (120.5, -40.0)

    def test_removing_an_edge_releases_the_work(self, db, project):
        a = milestone(db, project, "A")
        b = milestone(db, project, "B")
        repo.projects.add_dependency(db, b, a)
        task(db, project, b)
        assert repo.tasks.active_tasks(db) == []
        repo.projects.remove_dependency(db, b, a)
        assert len(repo.tasks.active_tasks(db)) == 1
