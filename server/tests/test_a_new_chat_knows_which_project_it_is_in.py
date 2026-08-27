"""A conversation is told about the project it is in, and only named the others.

Before this, the prompt had no project-scoped region at all. It had a global one — every
active project with its next milestone, every active task across all of them with no project
named on any row — and a project region that went silent exactly when it was needed: the
fallback that found `.kith/memory.md` declined whenever more than one project was open, and
the binding it preferred was written by the *client*, over a second request, after the first
turn had already been assembled and sent.

So the first turn of every chat — the turn where somebody says what the work is — was built
with no project. He was shown three projects and told nothing about the one he was in, and
went and read the other ones. That is not wandering; it is answering the only question the
context actually asked. Fifty-four of sixty-six conversations on the machine this was found
on never acquired a binding at all.

Three things are held here: the binding arrives with the turn that creates the conversation,
the project region says what is done and what is pending, and the other projects shrink to
their names.
"""

from __future__ import annotations

import pytest

from kith.config import default_config
from kith.infra.db import repositories as repo
from kith.services import conversations, project_context
from kith.services.turn.prompt import _build_messages, _present_state


@pytest.fixture
def two_projects(db, tmp_path):
    """Two active projects with folders — the state in which the old fallback gave up."""
    here = tmp_path / "portal"
    here.mkdir()
    mine = repo.projects.add_project(db, "Client portal", "", str(here))
    other = repo.projects.add_project(db, "Something else", "", str(tmp_path))
    return mine, other, here


class TestResolvingWhichProject:
    def test_the_binding_decides(self, db, two_projects):
        mine, _, _ = two_projects
        conversation = conversations.start(db, "a chat")
        repo.conversations.set_project(db, conversation["id"], mine["id"])

        assert project_context.resolve(db, conversation["id"])["id"] == mine["id"]

    def test_two_open_projects_and_no_binding_resolves_to_nothing(self, db, two_projects):
        # It declines rather than guessing, and that is correct — guessing is how a
        # conversation about one codebase writes memory into another. What makes it safe is
        # that a chat started inside a project is bound before this is ever asked.
        assert project_context.resolve(db, "unbound") is None

    def test_one_open_project_is_still_assumed(self, db, tmp_path):
        only = repo.projects.add_project(db, "The only one", "", str(tmp_path))

        assert project_context.resolve(db, "unbound")["id"] == only["id"]


class TestBindingArrivesWithTheTurn:
    """The half that made the whole thing possible.

    The binding used to be written by the client, over a second request, once the stream had
    reported an id — so the *first* turn of every chat was assembled unbound. `_bind_to_project`
    runs on the request that creates the conversation, before `_build_messages` asks which
    project this is.
    """

    def test_the_project_sent_with_the_first_turn_binds_it(self, never_the_real_database, tmp_path):
        from kith.api.routes import chat as route

        db = never_the_real_database
        mine = repo.projects.add_project(db, "Client portal", "", str(tmp_path))
        conversation = conversations.start(db, "a chat")

        route._bind_to_project(conversation["id"], mine["id"])

        assert repo.conversations.project_of(db, conversation["id"]) == mine["id"]

    def test_the_prompt_of_that_same_turn_already_has_the_project(self, never_the_real_database, tmp_path):
        # The whole point. Binding after the turn is binding a turn too late.
        from kith.api.routes import chat as route

        db = never_the_real_database
        here = tmp_path / "portal"
        here.mkdir()
        (here / ".kith").mkdir()
        (here / ".kith" / "memory.md").write_text("# Project memory\n\nTests run with `just test`.")
        mine = repo.projects.add_project(db, "Client portal", "", str(here))
        repo.projects.add_project(db, "Something else", "", str(tmp_path))
        conversation = conversations.start(db, "let us carry on")

        route._bind_to_project(conversation["id"], mine["id"])

        assert "just test" in _present_state(conversation["id"])

    def test_no_project_sent_binds_nothing(self, never_the_real_database):
        from kith.api.routes import chat as route

        db = never_the_real_database
        conversation = conversations.start(db, "a chat")

        route._bind_to_project(conversation["id"], None)

        assert repo.conversations.project_of(db, conversation["id"]) is None

    def test_a_stale_project_cannot_move_an_existing_binding(self, never_the_real_database, tmp_path):
        """A resumed conversation carries whatever the client last knew. It must not be able to
        change the subject with it — a conversation is stuck with the project it picked."""
        from kith.api.routes import chat as route

        db = never_the_real_database
        mine = repo.projects.add_project(db, "Client portal", "", str(tmp_path))
        other = repo.projects.add_project(db, "Something else", "", str(tmp_path))
        conversation = conversations.start(db, "a chat")
        route._bind_to_project(conversation["id"], mine["id"])

        route._bind_to_project(conversation["id"], other["id"])

        assert repo.conversations.project_of(db, conversation["id"]) == mine["id"]


class TestWhatTheProjectRegionSays:
    def _block(self, db, project):
        return project_context.block(db, project)

    def test_it_names_the_project_and_its_folder(self, db, two_projects):
        mine, _, here = two_projects

        block = self._block(db, mine)

        assert "Client portal" in block
        assert str(here) in block

    def test_done_in_flight_and_waiting_are_all_there(self, db, two_projects):
        mine, _, _ = two_projects
        for goal, status in (
            ("ship the login page", "done"),
            ("build the KYC views", "working"),
            ("harden navigation", "approved"),
        ):
            repo.tasks.add_task(db, goal=goal, project_id=mine["id"], status=status)

        block = self._block(db, mine)

        assert "ship the login page" in block
        assert "build the KYC views" in block
        assert "harden navigation" in block

    def test_another_project_s_tasks_are_not_in_it(self, db, two_projects):
        mine, other, _ = two_projects
        repo.tasks.add_task(db, goal="not your problem", project_id=other["id"], status="working")

        assert "not your problem" not in self._block(db, mine)

    def test_a_task_on_no_project_is_not_in_it_either(self, db, two_projects):
        # An orphan belongs to nobody, so it must not read as belonging to whoever is looking.
        mine, _, _ = two_projects
        repo.tasks.add_task(db, goal="filed before projects existed", status="working")

        assert "filed before projects existed" not in self._block(db, mine)

    def test_the_next_milestone_is_the_one_that_is_ready(self, db, two_projects):
        mine, _, _ = two_projects
        first = repo.projects.add_milestone(db, mine["id"], "Lay the foundations")
        second = repo.projects.add_milestone(db, mine["id"], "Build on them")
        repo.projects.add_dependency(db, second["id"], first["id"])

        block = self._block(db, mine)

        assert "NOW: Lay the foundations" in block
        assert "later: Build on them" in block

    def test_the_project_s_own_memory_comes_with_it(self, db, two_projects):
        mine, _, here = two_projects
        (here / ".kith").mkdir()
        (here / ".kith" / "memory.md").write_text("# Project memory\n\nTests run with `just test`.")

        assert "just test" in self._block(db, mine)

    def test_the_project_s_references_come_with_it(self, db, two_projects):
        mine, _, here = two_projects
        (here / ".kith").mkdir()
        (here / ".kith" / "references.md").write_text("# References\n\n- The SRS: `docs/srs.md`")

        assert "docs/srs.md" in self._block(db, mine)

    def test_a_project_with_no_references_is_asked_for_them_once(self, db, two_projects):
        # Never scaffolded — an empty file of headings would be committed to somebody's
        # repository and then read on every turn saying nothing. One sentence instead, and it
        # stops the moment anything is written there.
        mine, _, _ = two_projects

        assert "references.md" in self._block(db, mine)

    def test_no_project_means_no_region_at_all(self, db):
        # Not a paragraph explaining that there is no project. A chat with no project is an
        # ordinary thing and would pay for that explanation on every turn.
        assert project_context.block(db, None) == ""


class TestTheOtherProjectsShrink:
    def test_they_are_named_and_nothing_more(self, db, two_projects):
        mine, other, _ = two_projects
        repo.projects.add_milestone(db, other["id"], "Their next milestone")

        said = project_context.others(db, mine["id"])

        assert "Something else" in said
        assert "Their next milestone" not in said

    def test_the_project_in_hand_is_not_among_them(self, db, two_projects):
        mine, _, _ = two_projects

        assert "Client portal" not in project_context.others(db, mine["id"])

    def test_a_lone_project_leaves_nothing_to_say(self, db, tmp_path):
        only = repo.projects.add_project(db, "The only one", "", str(tmp_path))

        assert project_context.others(db, only["id"]) == ""


class TestInTheActualPrompt:
    """The region has to reach the request, and the cross-project listing has to leave it."""

    def _tail(self, conversation_id):
        built = _build_messages(
            [{"role": "user", "content": "where were we"}], default_config(), conversation_id
        )
        return str(built[-1]["content"])

    def test_a_bound_conversation_gets_its_project_and_not_the_others_tasks(
        self, never_the_real_database, tmp_path
    ):
        db = never_the_real_database
        here = tmp_path / "portal"
        here.mkdir()
        mine = repo.projects.add_project(db, "Client portal", "", str(here))
        other = repo.projects.add_project(db, "Something else", "", str(tmp_path))
        repo.tasks.add_task(db, goal="build the KYC views", project_id=mine["id"], status="working")
        repo.tasks.add_task(db, goal="not your problem", project_id=other["id"], status="working")
        conversation = conversations.start(db, "a chat")
        repo.conversations.set_project(db, conversation["id"], mine["id"])

        tail = self._tail(conversation["id"])

        assert "build the KYC views" in tail
        assert "not your problem" not in tail
        assert "Something else" in tail  # named, so a foreign write can be explained

    def test_an_unbound_conversation_still_gets_the_menu(self, never_the_real_database, tmp_path):
        db = never_the_real_database
        repo.projects.add_project(db, "Client portal", "", str(tmp_path))
        repo.projects.add_project(db, "Something else", "", str(tmp_path))
        repo.tasks.add_task(db, goal="build the KYC views", status="working")

        tail = self._tail("nothing-bound-here")

        assert "Client portal" in tail
        assert "Something else" in tail

    def test_every_task_in_the_menu_says_whose_it_is(self, never_the_real_database, tmp_path):
        # The bug, in one line: `- #31 [working] (high) Connect the client access flows to the
        # lifecycle API` — nothing on it says which codebase that is.
        db = never_the_real_database
        repo.projects.add_project(db, "Client portal", "", str(tmp_path))
        repo.projects.add_project(db, "Something else", "", str(tmp_path))
        mine = repo.projects.list_projects(db)[0]
        repo.tasks.add_task(db, goal="build the KYC views", project_id=mine["id"], status="working")
        repo.tasks.add_task(db, goal="filed before projects existed", status="working")

        tail = self._tail("nothing-bound-here")

        assert "build the KYC views — " in tail
        assert "filed before projects existed — not on any project" in tail

    def test_the_region_rides_in_the_live_tail_block(self, never_the_real_database, tmp_path):
        # Everything here is paid for on every round and must sit after the cached prefix, or
        # it invalidates the persona in front of it. See `prompt._assemble`.
        db = never_the_real_database
        here = tmp_path / "portal"
        here.mkdir()
        mine = repo.projects.add_project(db, "Client portal", "", str(here))
        conversation = conversations.start(db, "a chat")
        repo.conversations.set_project(db, conversation["id"], mine["id"])

        built = _build_messages([{"role": "user", "content": "hello"}], default_config(), conversation["id"])

        assert built[-1].get("_live") is True
        assert "Client portal" in str(built[-1]["content"])
        assert not any("Client portal" in str(m.get("content") or "") for m in built[:-1])

    def test_the_ledger_can_tell_it_apart_from_the_clock(self, never_the_real_database, tmp_path):
        """Otherwise it is invisible. "Where he is right now: 5,112" cannot say whether to prune
        a memory file or stop carrying a task list, and those are the only two things a person
        can do about that number."""
        from kith.llm import ledger

        db = never_the_real_database
        here = tmp_path / "portal"
        here.mkdir()
        mine = repo.projects.add_project(db, "Client portal", "", str(here))
        conversation = conversations.start(db, "a chat")
        repo.conversations.set_project(db, conversation["id"], mine["id"])

        built = _build_messages([{"role": "user", "content": "hello"}], default_config(), conversation["id"])
        book = ledger.take(built, window=1_000_000, chars_per_token=1.0)

        assert book.of("project") > 0
        assert book.of("project") == len(project_context.block(db, repo.projects.get_project(db, mine["id"])))

    def test_the_preview_of_the_prompt_carries_it_too(self, never_the_real_database, tmp_path):
        db = never_the_real_database
        mine = repo.projects.add_project(db, "Client portal", "", str(tmp_path))
        conversation = conversations.start(db, "a chat")
        repo.conversations.set_project(db, conversation["id"], mine["id"])

        assert "Client portal" in _present_state(conversation["id"])
