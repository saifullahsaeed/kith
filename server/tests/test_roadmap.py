"""Milestones that decide what happens next.

They used to be decoration: the roadmap showed progress after the fact while raw task
priority decided the order, so a roadmap imposed nothing on anything. These tests are about
the one behaviour that changes that — a milestone which is waiting keeps its tasks waiting
with it — and about the ways a dependency graph goes wrong.
"""

from __future__ import annotations

import itertools

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
    row = repo.tasks.add_task(
        db, goal, "", status="planned", project_id=project_id, milestone_id=milestone_id
    )
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
        repo.tasks.add_task(db, "buy milk", "", status="planned", project_id=project)
        assert len(repo.tasks.active_tasks(db)) == 1

    def test_held_back_work_is_distinguishable_from_no_work(self, db, project):
        """ "Nothing to do" and "not this milestone's turn" are different sentences, and a
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


class TestTheMilestoneLink:
    """A task pointing at a milestone that does not exist.

    It happened to a real task: the column stored whatever it was given and only looked the
    milestone up in order to copy its project across, so a stray 0 — what `Number("")` produces
    in an interface — was written straight in. The roadmap then could not gate the task, the
    task page showed an empty milestone, and nothing anywhere said the link was broken.
    """

    def test_a_real_milestone_links_and_carries_its_project(self, db, project):
        one = milestone(db, project, "Design")
        row = repo.tasks.add_task(db, "sketch it", "")
        repo.tasks.set_task_milestone(db, row["id"], one)
        detail = repo.tasks.task_detail(db, row["id"])
        assert detail["milestone_id"] == one
        assert detail["project_id"] == project

    def test_an_id_that_is_not_a_milestone_is_refused(self, db, project):
        one = milestone(db, project, "Design")
        row = repo.tasks.add_task(db, "sketch it", "", project_id=project, milestone_id=one)
        with pytest.raises(ValueError):
            repo.tasks.set_task_milestone(db, row["id"], 9999)

    def test_a_refusal_leaves_the_existing_link_alone(self, db, project):
        """The failure mode that made this expensive: a bad write that also destroyed the
        good value would turn one mistake into two."""
        one = milestone(db, project, "Design")
        row = repo.tasks.add_task(db, "sketch it", "", project_id=project, milestone_id=one)
        with pytest.raises(ValueError):
            repo.tasks.set_task_milestone(db, row["id"], 9999)
        assert repo.tasks.task_detail(db, row["id"])["milestone_id"] == one

    def test_zero_means_no_milestone_rather_than_milestone_zero(self, db, project):
        one = milestone(db, project, "Design")
        row = repo.tasks.add_task(db, "sketch it", "", project_id=project, milestone_id=one)
        repo.tasks.set_task_milestone(db, row["id"], 0)
        assert repo.tasks.task_detail(db, row["id"])["milestone_id"] is None

    def test_none_clears_it(self, db, project):
        one = milestone(db, project, "Design")
        row = repo.tasks.add_task(db, "sketch it", "", project_id=project, milestone_id=one)
        repo.tasks.set_task_milestone(db, row["id"], None)
        assert repo.tasks.task_detail(db, row["id"])["milestone_id"] is None

    def test_an_unlinked_task_is_never_gated(self, db, project):
        """Which is why a broken link was invisible: it looked exactly like no link at all."""
        first = milestone(db, project, "Design")
        second = milestone(db, project, "Build")
        repo.projects.add_dependency(db, second, first)
        row = repo.tasks.add_task(db, "loose", "", status="planned", project_id=project, milestone_id=second)
        assert repo.tasks.active_tasks(db) == []
        repo.tasks.set_task_milestone(db, row["id"], None)
        assert len(repo.tasks.active_tasks(db)) == 1


class TestADependencyHasToPointAtSomething:
    """The bug that made a five-step roadmap look like it had no order at all.

    Asked to plan a project, he created five milestones and ordered them by calling
    ``add_milestone`` with ``after: [0]`` — a *position*, not an id. Four rows were written
    pointing at milestone 0, which does not exist, and every layer downstream hid it:
    :func:`roadmap` drops a dependency whose target is not a known milestone, so the graph
    drew no edges and reported "5 ready to work". He believed he had laid out an order. The
    screen showed a plan with no order in it. Nothing anywhere mentioned the number 0.

    Validating at the write is the only place that catches it, because it is the only place
    that still knows the id was wrong.
    """

    def test_an_id_that_is_not_a_milestone_is_refused(self, db, project):
        first = milestone(db, project, "Scope it")
        with pytest.raises(ValueError) as caught:
            repo.projects.add_dependency(db, first, 0)
        # The message has to name the mistake he actually made, or he retries the same call.
        assert "not a milestone" in str(caught.value)
        assert "position" in str(caught.value)

    def test_the_waiting_milestone_must_exist_too(self, db, project):
        first = milestone(db, project, "Scope it")
        with pytest.raises(ValueError):
            repo.projects.add_dependency(db, 9999, first)

    def test_nothing_is_stored_when_it_is_refused(self, db, project):
        first = milestone(db, project, "Scope it")
        with pytest.raises(ValueError):
            repo.projects.add_dependency(db, first, 0)
        assert repo.projects.dependencies(db, project) == []

    def test_a_cross_project_edge_is_refused(self, db, project):
        other = repo.projects.add_project(db, "Something else", "")["id"]
        here = milestone(db, project, "Ours")
        there = milestone(db, other, "Theirs")
        with pytest.raises(ValueError) as caught:
            repo.projects.add_dependency(db, here, there)
        # A roadmap is read per project, so this would be stored and then never shown or
        # honoured — the same silent nothing in a different disguise.
        assert "same project" in str(caught.value)

    def test_the_tool_turns_the_refusal_into_a_warning_he_can_read(self, db, project):
        from kith.tools import registry

        first = repo.projects.add_milestone(db, project, "Scope it")
        add = registry.get("add_milestone")
        handler = add.run if hasattr(add, "run") else add
        result = handler(db, {"project_id": project, "title": "Build it", "after": [0]})

        # The milestone is still created — losing it as well would turn one mistake into two.
        assert result["id"] != first["id"]
        assert result.get("warnings"), "he has to be told the ordering did not happen"
        assert "not a milestone" in " ".join(result["warnings"])

    def test_ordering_by_real_ids_still_works(self, db, project):
        ids = [milestone(db, project, f"Step {n}") for n in range(1, 5)]
        for earlier, later in itertools.pairwise(ids):
            repo.projects.add_dependency(db, later, earlier)

        graph = repo.projects.roadmap(db, project)
        ready = [n["id"] for n in graph["milestones"] if n["ready"]]

        # Exactly one thing to start on, which is the entire point of a roadmap.
        assert ready == [ids[0]]


class TestAMissingPredecessorMustNotDeadlockHim:
    """Five hours of a live agent doing nothing, and the screen said everything was fine.

    Four dependency rows pointed at milestone 0, which does not exist. The two functions that
    read those rows disagreed about what it meant:

    * ``roadmap()`` skipped the unknown predecessor, so the interface showed "5 ready to work".
    * ``blocked_milestone_ids()`` asked ``status.get(dep) != "done"``, which is True for a
      milestone that was never real — so every one of them was blocked, permanently, because
      a milestone that does not exist can never become done.

    He posted "I've run out of available work" at 05:10 and then spent 5h10m and 735,000
    prompt tokens reflecting on the same sentence, twice resolving to start the task the
    machinery would not hand him. The moment a fresh unblocked task arrived he finished it in
    four minutes — he was starved, not broken.

    Bad rows are refused at the write now. This is the second line: if one exists anyway, the
    failure has to be "he can work" rather than a deadlock nothing on screen can explain.
    """

    def test_a_dependency_on_a_milestone_that_does_not_exist_is_ignored(self, db, project):
        import sqlite3

        first = milestone(db, project, "Do this")
        with sqlite3.connect(db) as raw:
            raw.execute("insert into milestone_deps values (?, 0)", (first,))

        assert repo.projects.blocked_milestone_ids(db) == set()

    def test_the_screen_and_the_task_picker_agree(self, db, project):
        import sqlite3

        ids = [milestone(db, project, f"Step {n}") for n in range(3)]
        with sqlite3.connect(db) as raw:
            for one in ids[1:]:
                raw.execute("insert into milestone_deps values (?, 0)", (one,))

        graph = repo.projects.roadmap(db, project)
        ready = {m["id"] for m in graph["milestones"] if m["ready"]}
        blocked = repo.projects.blocked_milestone_ids(db)

        # The contradiction is the bug. Either answer is survivable; disagreeing is not,
        # because the one the person sees is not the one that decides what he does.
        assert ready & blocked == set()
        assert ready == set(ids)

    def test_his_tasks_stay_reachable(self, db, project):
        import sqlite3

        first = milestone(db, project, "Cart and checkout")
        mine = task(db, project, first, "Build the cart")
        with sqlite3.connect(db) as raw:
            raw.execute("insert into milestone_deps values (?, 0)", (first,))

        # The whole cost of the bug, in one assertion: this task was invisible to him for
        # five hours while its milestone showed as ready.
        assert mine in [t["id"] for t in repo.tasks.active_tasks(db)]

    def test_a_real_unfinished_predecessor_still_blocks(self, db, project):
        earlier = milestone(db, project, "First")
        later = milestone(db, project, "Second")
        repo.projects.add_dependency(db, later, earlier)
        assert later in repo.projects.blocked_milestone_ids(db)

        repo.projects.update_milestone(db, earlier, status="done")
        assert later not in repo.projects.blocked_milestone_ids(db)


class TestTheHandoffBetweenTicks:
    """A tick keeps no conversation, so his working file *is* the handoff — and it was being
    read from the wrong end.

    Nothing carries between ticks: the whole message list is a system prompt, a state line and a
    prompt. Continuity is re-derived, and the working file is the main carrier of it. He appends
    to that file as he works, so the newest thing in it — usually a literal "### NEXT" list of
    what he was about to do — is at the bottom. The prompt showed the *first* 1,500 characters.
    On his 6,749-char task-57 notes that is the oldest 22%, so his own handoff was reliably the
    part he could not see, and he resumed by re-reading the codebase: App.jsx twice and
    styles.css three times in one step.
    """
