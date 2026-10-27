"""An agent is a whole conversation, started by another one, and talks only to that one.

What these hold, layer by layer:

* its worktree is a real branch, started from its handler's last commit, carrying the notes git
  will not, and cleaned up only on purpose (`infra/workspace/worktrees`);
* a turn pinned to it lands every path there — relative ones, and absolute ones into the
  person's checkout or the handler's worktree — and may write there without a prompt;
* the mail between sessions is delivered once, into the right transcript, in the right role;
* when an agent's turn ends its report reaches its handler — unless its own agents are still
  busy, or its handler stopped it;
* `ask` inside an agent goes to its handler and waits for `message_agents`;
* stop and close act on whole subtrees, and close refuses to throw away unmerged work;
* after a restart, agents that were working are resumed and undelivered mail is delivered;
* and the whole loop through the real turn machinery: a handler starts an agent, the agent's
  turn runs, its report wakes the handler.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kith.infra import permissions
from kith.infra.db import repositories as repo
from kith.infra.workspace import paths, worktrees
from kith.kernel import live_turns, session_context, turns
from kith.services import agents, conversations


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def db(never_the_real_database) -> Path:
    """The database every module here already points at, so the service and the test agree."""
    return never_the_real_database


@pytest.fixture
def client():
    """The API without the app around it: the routes, and none of the auth or the background."""
    from apiflask import APIFlask

    import kith.api.routes  # noqa: F401 - every route registered before the blueprint is
    from kith.api.blueprint import api

    app = APIFlask("agents-under-test")
    app.register_blueprint(api, url_prefix="/api")
    return app.test_client()


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    here = tmp_path / "project"
    here.mkdir()
    _git(here, "init", "-q", "-b", "main")
    (here / "app.py").write_text("print('hi')\n")
    (here / ".gitignore").write_text(".kith/\nnode_modules/\n.env\n")
    _git(here, "add", "-A")
    _git(here, "commit", "-q", "-m", "start")
    return here


@pytest.fixture
def no_turns(monkeypatch):
    """Deliveries recorded instead of turns started — the mechanism without the model."""
    started: list[str] = []

    def start(request: turns.Request) -> bool:
        mail = agents.take(request.conversation_id)
        if not mail:
            return False
        started.append(request.conversation_id)
        # Over as soon as it began, so it never holds a slot.
        turns.ended(request)
        return True

    turns.install(start)
    return started


def _chat(db: Path) -> str:
    return conversations.start(db, "build the thing")["id"]


def _transcript(conversation_id: str) -> list[dict]:
    return [entry for entry in conversations.read(conversation_id) if entry.get("type") == "message"]


# --------------------------------------------------------------------------- #
# The worktree
# --------------------------------------------------------------------------- #


class TestTheWorktree:
    def test_it_is_a_new_branch_from_the_last_commit(self, repo_dir: Path):
        opened = worktrees.open_branch("a1", repo_dir, "api")
        assert opened is not None
        assert opened.branch.startswith("kith/api-")
        assert (opened.copy / "app.py").read_text() == "print('hi')\n"
        assert _git(opened.copy, "rev-parse", "--abbrev-ref", "HEAD") == opened.branch
        assert opened.base == _git(repo_dir, "rev-parse", "HEAD")
        # Outside the repository it copies.
        assert repo_dir not in opened.copy.parents

    def test_uncommitted_changes_stay_behind_and_are_reported(self, repo_dir: Path):
        (repo_dir / "app.py").write_text("print('changed')\n")
        assert worktrees.uncommitted(repo_dir) == ["app.py"]
        opened = worktrees.open_branch("a2", repo_dir, "api")
        assert (opened.copy / "app.py").read_text() == "print('hi')\n"

    def test_ignored_notes_and_environment_are_carried(self, repo_dir: Path):
        (repo_dir / ".kith").mkdir()
        (repo_dir / ".kith" / "brief.md").write_text("the brief")
        (repo_dir / ".env").write_text("SECRET=1")
        opened = worktrees.open_branch("a3", repo_dir, "api")
        assert (opened.copy / ".kith" / "brief.md").read_text() == "the brief"
        assert (opened.copy / ".env").read_text() == "SECRET=1"
        # Carried, and ignored there too — so never committed on the agent's branch.
        assert worktrees.uncommitted(opened.copy) == []

    def test_a_project_folder_inside_the_repository_is_where_the_work_lands(self, repo_dir: Path):
        (repo_dir / "web").mkdir()
        (repo_dir / "web" / "index.html").write_text("<p>")
        _git(repo_dir, "add", "-A")
        _git(repo_dir, "commit", "-q", "-m", "web")
        opened = worktrees.open_branch("a4", repo_dir / "web", "web")
        assert opened.folder == opened.copy / "web"

    def test_an_agents_agent_branches_from_the_agents_branch(self, repo_dir: Path):
        parent = worktrees.open_branch("p1", repo_dir, "parent")
        (parent.copy / "contract.py").write_text("SHAPE = 1\n")
        _git(parent.copy, "add", "-A")
        _git(parent.copy, "commit", "-q", "-m", "contract")
        child = worktrees.open_branch("c1", parent.copy, "child")
        assert (child.copy / "contract.py").read_text() == "SHAPE = 1\n"
        assert child.base == _git(parent.copy, "rev-parse", "HEAD")

    def test_leftovers_are_committed_and_counted(self, repo_dir: Path):
        opened = worktrees.open_branch("a5", repo_dir, "api")
        (opened.copy / "new.py").write_text("x = 1\n")
        assert worktrees.commit_leftovers(opened.copy, "left") == ["new.py"]
        assert worktrees.status(opened.copy, opened.base) == {"ahead": 1, "uncommitted": []}

    def test_merged_means_every_commit_is_in_the_checkout(self, repo_dir: Path):
        opened = worktrees.open_branch("a6", repo_dir, "api")
        assert worktrees.merged(opened.branch, repo_dir)  # nothing on it yet
        (opened.copy / "new.py").write_text("x = 1\n")
        worktrees.commit_leftovers(opened.copy, "work")
        assert not worktrees.merged(opened.branch, repo_dir)
        _git(repo_dir, "merge", "-q", opened.branch)
        assert worktrees.merged(opened.branch, repo_dir)

    def test_closing_removes_the_worktree_and_the_branch(self, repo_dir: Path):
        opened = worktrees.open_branch("a7", repo_dir, "api")
        worktrees.close(opened.copy, opened.branch)
        assert not opened.copy.exists()
        assert opened.branch not in _git(repo_dir, "branch", "--list")
        assert (repo_dir / "app.py").exists()

    def test_there_is_none_without_a_repository(self, tmp_path: Path):
        assert worktrees.open_branch("a8", tmp_path, "x") is None

    def test_several_at_once_all_open(self, repo_dir: Path):
        made: list = []
        threads = [
            threading.Thread(
                target=lambda n=n: made.append(worktrees.open_branch(f"many{n}", repo_dir, f"a{n}"))
            )
            for n in range(4)
        ]
        for one in threads:
            one.start()
        for one in threads:
            one.join()
        assert len({opened.branch for opened in made if opened}) == 4


# --------------------------------------------------------------------------- #
# A turn pinned to it
# --------------------------------------------------------------------------- #


class TestAPinnedTurn:
    def test_absolute_paths_into_either_original_land_in_the_copy(self, repo_dir: Path, tmp_path: Path):
        copy = tmp_path / "copy"
        copy.mkdir()
        handler = tmp_path / "handler-worktree"
        with session_context.working_from(str(copy), mirror_of=[str(repo_dir), str(handler)]):
            assert paths.resolve(str(repo_dir / "app.py")) == str(copy / "app.py")
            assert paths.resolve(str(handler / "x" / "y.py")) == str(copy / "x" / "y.py")
            assert paths.resolve(str(copy / "z.py")) == str(copy / "z.py")
            assert paths.resolve("/etc/hosts") == "/etc/hosts"
            assert paths.resolve("rel.txt") == str(copy / "rel.txt")

    def test_a_copy_inside_its_original_is_not_bent_into_itself(self, repo_dir: Path):
        copy = repo_dir / "data" / "agents" / "a1"
        copy.mkdir(parents=True)
        with session_context.working_from(str(copy), mirror_of=[str(repo_dir)]):
            assert paths.resolve(str(copy / "f.py")) == str(copy / "f.py")

    def test_writing_in_the_worktree_needs_no_permission(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)
        copy = tmp_path / "copy"
        copy.mkdir()
        with session_context.working_from(str(copy), mirror_of=[str(tmp_path / "orig")]):
            assert permissions.check_path("write", copy / "a.py", tmp_path / "workspace").allowed


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


@pytest.fixture
def started(db: Path, repo_dir: Path, no_turns, monkeypatch):
    """A chat that has started one agent with its own worktree."""
    monkeypatch.setattr(paths, "base_dir", lambda: repo_dir)
    monkeypatch.setattr(agents, "base_dir", lambda: repo_dir)
    chat = _chat(db)
    answer = agents.start(chat, [{"name": "api", "task": "build the api"}])
    agent = answer["agents"][0]
    return chat, agent


class TestStarting:
    def test_an_agent_is_a_conversation_under_its_handler(self, db: Path, started, no_turns):
        chat, agent = started
        row = repo.conversations.get(db, agent["id"])
        assert row["parent_id"] == chat
        assert row["agent_name"] == "api"
        assert row["title"] == "api"
        assert row["agent_state"] == agents.WORKING
        assert agent["branch"].startswith("kith/api-")
        assert Path(row["agent_worktree"]).is_dir()
        assert no_turns == [agent["id"]]

    def test_its_task_is_its_first_message_from_its_handler(self, started):
        _, agent = started
        first = _transcript(agent["id"])[0]
        assert first["role"] == "user"
        assert first["from"] == {"id": started[0], "name": "conductor", "kind": "task"}
        assert "build the api" in first["content"]

    def test_names_are_unique_among_open_agents(self, started):
        chat, _ = started
        again = agents.start(chat, [{"name": "API", "task": "again"}])
        assert "already" in again["agents"][0]["error"]

    def test_uncommitted_work_in_the_handlers_checkout_is_named(self, db, repo_dir, no_turns, monkeypatch):
        monkeypatch.setattr(agents, "base_dir", lambda: repo_dir)
        (repo_dir / "app.py").write_text("changed\n")
        answer = agents.start(_chat(db), [{"name": "x", "task": "t"}])
        assert "app.py" in answer["note"]

    def test_a_shared_agent_has_no_worktree_of_its_own(self, db, repo_dir, no_turns, monkeypatch):
        monkeypatch.setattr(agents, "base_dir", lambda: repo_dir)
        answer = agents.start(_chat(db), [{"name": "reader", "task": "t", "workspace": "shared"}])
        row = repo.conversations.get(db, answer["agents"][0]["id"])
        assert answer["agents"][0]["workspace"] == "shared"
        assert row["agent_branch"] == ""

    def test_the_agent_is_not_in_the_person_s_list(self, db, started):
        chat, agent = started
        listed = [one["id"] for one in conversations.recent(db, 50, chats_only=True)]
        assert chat in listed and agent["id"] not in listed
        row = next(one for one in conversations.recent(db, 50, chats_only=True) if one["id"] == chat)
        assert row["agents"] == 1 and row["agentsWorking"] == 1


class TestWhereTheWorkIs:
    """Trial 2 (2026-10-20): a conductor bound to no project works from the whole workspace, and
    four builders each got a 2.5 GB checkout of it instead of the project they were building."""

    def test_the_bare_workspace_is_refused_with_where_the_message_came_from(self, db, no_turns, monkeypatch):
        workspace = paths.root()
        monkeypatch.setattr(agents, "base_dir", lambda: workspace)
        with session_context.arriving_from("/somewhere/project"):
            answer = agents.start(_chat(db), [{"name": "x", "task": "t"}])
        assert answer["ok"] is False
        assert "pass `folder`" in answer["error"] and "/somewhere/project" in answer["error"]

    def test_a_named_folder_is_where_they_branch_from(self, db, repo_dir, no_turns, monkeypatch):
        monkeypatch.setattr(agents, "base_dir", lambda: paths.root())
        answer = agents.start(_chat(db), [{"name": "x", "task": "t"}], folder=str(repo_dir))
        row = repo.conversations.get(db, answer["agents"][0]["id"])
        assert row["agent_source"].splitlines()[-1] == str(repo_dir.resolve())
        assert (Path(row["agent_worktree"]) / "app.py").exists()

    def test_a_shared_agent_works_in_the_folder_without_being_granted_it(
        self, db, repo_dir, no_turns, monkeypatch
    ):
        monkeypatch.setattr(agents, "base_dir", lambda: paths.root())
        answer = agents.start(
            _chat(db), [{"name": "r", "task": "t", "workspace": "shared"}], folder=str(repo_dir)
        )
        with agents.pinned(answer["agents"][0]["id"]):
            assert session_context.isolated_base() == str(repo_dir)
            assert session_context.isolated_copy() == ""

    def test_start_agents_waits_for_the_first_report(self, db, repo_dir, no_turns, monkeypatch):
        from kith.tools import agents as tool

        monkeypatch.setattr(agents, "base_dir", lambda: repo_dir)
        seen = {}
        monkeypatch.setattr(
            agents, "wait_for", lambda here, minutes=20: seen.setdefault("waited", here) and {"ok": True}
        )
        chat = _chat(db)
        with session_context.working_in(chat):
            answer = tool.start_agents(Path("x"), {"agents": [{"name": "a", "task": "t"}]})
            assert seen["waited"] == chat and "waited" in answer
            seen.clear()
            tool.start_agents(Path("x"), {"agents": [{"name": "b", "task": "t"}], "wait": False})
            assert "waited" not in seen


class TestMail:
    def test_a_running_turn_takes_it_at_its_next_step(self, db, started, no_turns, monkeypatch):
        chat, agent = started
        # A context, never `monkeypatch.undo()`: that would also undo the fixtures pointing every
        # module away from the real database.
        with monkeypatch.context() as patched:
            patched.setattr(live_turns, "current", lambda conversation_id: object())
            assert agents.message(chat, "api", "also add tests")["delivered"].startswith("it is working")
        taken = agents.take(agent["id"])
        assert "also add tests" in taken[0]["content"]
        assert agents.take(agent["id"]) == []  # once

    def test_a_report_is_a_wake_in_the_handler(self, db, started, no_turns):
        chat, agent = started
        agents.post(chat, agent["id"], "report", "done it")
        last = _transcript(chat)[-1]
        assert last["role"] == "system"
        assert last["from"]["name"] == "api"
        assert "Report from your agent “api”" in last["content"]


class TestTheReport:
    def test_it_goes_to_the_handler_with_its_branch(self, db, started, no_turns):
        chat, agent = started
        row = repo.conversations.get(db, agent["id"])
        (Path(row["agent_folder"]) / "api.py").write_text("x = 1\n")
        conversations.record(db, agent["id"], "assistant", "Built the api; tests pass.")
        agents.turn_ended(agent["id"])
        report = _transcript(chat)[-1]["content"]
        assert "Built the api; tests pass." in report
        assert row["agent_branch"] in report
        assert "1 commit(s)" in report and "committed for it" in report
        assert repo.conversations.get(db, agent["id"])["agent_state"] == agents.DONE

    def test_not_while_its_own_agents_are_busy(self, db, started, no_turns, repo_dir, monkeypatch):
        chat, agent = started
        row = repo.conversations.get(db, agent["id"])
        monkeypatch.setattr(agents, "base_dir", lambda: Path(row["agent_folder"]))
        with session_context.working_in(agent["id"]):
            agents.start(agent["id"], [{"name": "tests", "task": "write tests", "workspace": "shared"}])
        before = len(_transcript(chat))
        agents.turn_ended(agent["id"])
        assert len(_transcript(chat)) == before
        assert repo.conversations.get(db, agent["id"])["agent_state"] == agents.WAITING

    def test_not_when_its_handler_stopped_it(self, db, started, no_turns, monkeypatch):
        chat, agent = started
        with monkeypatch.context() as patched:
            patched.setattr(live_turns, "current", lambda conversation_id: object())
            agents.stop(chat, ["api"])
        before = len(_transcript(chat))
        agents.turn_ended(agent["id"], stopped=True)
        assert len(_transcript(chat)) == before
        assert repo.conversations.get(db, agent["id"])["agent_state"] == agents.STOPPED

    def test_a_message_resumes_a_stopped_agent(self, db, started, no_turns):
        chat, agent = started
        agents.stop(chat, ["api"])
        agents.message(chat, "api", "carry on")
        assert repo.conversations.get(db, agent["id"])["agent_state"] == agents.WORKING
        assert no_turns[-1] == agent["id"]


class TestAsking:
    def test_the_question_goes_up_and_the_answer_comes_down(self, started, no_turns):
        chat, agent = started
        answer: dict = {}
        asking = threading.Thread(
            target=lambda: answer.update(agents.ask_up(agent["id"], [{"question": "Postgres or SQLite?"}]))
        )
        asking.start()
        for _ in range(250):
            said = _transcript(chat)
            if said and "Postgres or SQLite?" in said[-1]["content"]:
                break
            time.sleep(0.02)
        question = _transcript(chat)[-1]
        assert "Postgres or SQLite?" in question["content"]
        assert agents.message(chat, "api", "SQLite")["delivered"].startswith("answered")
        asking.join(timeout=5)
        assert answer == {"ok": True, "answered": True, "from": "conductor", "answer": "SQLite"}

    def test_stopping_lets_go_of_the_question(self, started, no_turns):
        _, agent = started
        answer: dict = {}
        asking = threading.Thread(target=lambda: answer.update(agents.ask_up(agent["id"], ["Which?"])))
        asking.start()
        for _ in range(100):
            if agents.asking(agent["id"]):
                break
            time.sleep(0.02)
        agents.release(agent["id"])
        asking.join(timeout=5)
        assert answer["answered"] is False


class TestWaiting:
    """`wait_for_agents`: a handler's way to do nothing while its agents work — measured as the
    missing piece on the first real run, where handlers read instead."""

    def test_it_returns_when_an_agent_reports(self, started, no_turns, monkeypatch):
        chat, agent = started
        # The handler's turn is running — it is the turn calling `wait_for_agents` — so the report
        # waits for its next step instead of starting a turn of its own.
        real = live_turns.current
        monkeypatch.setattr(live_turns, "current", lambda one: object() if one == chat else real(one))
        later = threading.Timer(0.3, lambda: agents.post(chat, agent["id"], "report", "done"))
        later.start()
        began = time.monotonic()
        answer = agents.wait_for(chat, minutes=1)
        assert answer == {"ok": True, "arrived": "report from api — it is below."}
        assert time.monotonic() - began < 5

    def test_it_returns_when_nothing_is_working_any_more(self, db, started, no_turns):
        chat, agent = started
        repo.conversations.set_agent(db, agent["id"], agent_state=agents.DONE)
        assert "None of your agents is working" in agents.wait_for(chat, minutes=1)["note"]

    def test_stopping_the_turn_ends_the_wait(self, started, no_turns, monkeypatch):
        chat, _ = started
        switch = threading.Event()
        monkeypatch.setattr(agents.stopping, "current", lambda conversation_id: switch)
        answer: dict = {}
        waiting = threading.Thread(target=lambda: answer.update(agents.wait_for(chat, minutes=1)))
        waiting.start()
        time.sleep(0.2)
        switch.set()
        agents.release(chat)
        waiting.join(timeout=5)
        assert answer == {"ok": True, "note": "Stopped."}

    def test_there_is_nothing_to_wait_for_without_agents(self, db):
        assert agents.wait_for(_chat(db), minutes=1)["ok"] is False


class TestTheDefaultStrategy:
    def test_it_is_lead_dont_read_and_it_ships(self):
        from kith import settings
        from kith.services import tuning

        assert tuning.value("agent_strategy") == "lead-dont-read"
        manifest = (settings.BUNDLED_SKILLS_DIR / "lead-dont-read" / "SKILL.md").read_text()
        assert "name: lead-dont-read" in manifest and "wait_for_agents" in manifest


class TestClosing:
    def test_unmerged_work_is_refused_then_merged_work_is_closed(self, db, started, no_turns, repo_dir):
        chat, agent = started
        row = repo.conversations.get(db, agent["id"])
        repo.conversations.set_agent(db, agent["id"], agent_state=agents.DONE)
        (Path(row["agent_folder"]) / "api.py").write_text("x = 1\n")
        worktrees.commit_leftovers(Path(row["agent_worktree"]), "api")

        refused = agents.close(chat, ["api"])
        assert refused["closed"] == [] and "not merged" in refused["refused"][0]["why"]

        _git(repo_dir, "merge", "-q", row["agent_branch"])
        closed = agents.close(chat, ["all"])
        assert closed["closed"] == ["api"]
        assert not Path(row["agent_worktree"]).exists()
        assert repo.conversations.get(db, agent["id"])["agent_state"] == agents.CLOSED
        assert agents.children(chat) == []

    def test_a_working_agent_is_refused_unless_forced(self, db, started, no_turns):
        chat, _ = started
        assert agents.close(chat, ["api"])["refused"][0]["why"].startswith("still working")
        assert agents.close(chat, ["api"], force=True)["closed"] == ["api"]

    def test_a_shared_agent_never_removes_the_folder_it_shares(self, db, repo_dir, no_turns, monkeypatch):
        monkeypatch.setattr(agents, "base_dir", lambda: repo_dir)
        chat = _chat(db)
        agents.start(chat, [{"name": "reader", "task": "t", "workspace": "shared"}])
        agents.close(chat, ["all"], force=True)
        assert (repo_dir / "app.py").exists()


class TestRestart:
    def test_working_agents_are_resumed_and_mail_is_delivered(self, db, started, no_turns):
        chat, agent = started
        no_turns.clear()
        repo.agent_mail.post(db, chat, agent["id"], "report", "finished while you were down")
        assert agents.recover() == 2
        assert set(no_turns) == {chat, agent["id"]}
        resumed = _transcript(agent["id"])[-1]
        assert "Kith restarted" in resumed["content"]

    def test_an_interrupted_ask_in_an_agent_is_closed_without_a_card(self, started):
        from kith.services import questions

        _, agent = started
        conversations.record_event(
            agent["id"],
            "tool_call",
            {"id": "c1", "name": "ask", "arguments": {"questions": [{"question": "q?", "options": ["a"]}]}},
        )
        assert questions.recover_interrupted() == 1
        assert questions.open_question(agent["id"]) is None
        closed = [e for e in conversations.read(agent["id"]) if e.get("type") == "tool_result"]
        assert "your handler" in closed[-1]["result"]["note"]

    def test_deleting_a_chat_closes_its_agents_and_their_worktrees(self, db, started):
        chat, agent = started
        worktree = Path(repo.conversations.get(db, agent["id"])["agent_worktree"])
        agents.forget(chat)
        assert repo.conversations.get(db, agent["id"])["agent_state"] == agents.CLOSED
        assert not worktree.exists()


# --------------------------------------------------------------------------- #
# What a session is told
# --------------------------------------------------------------------------- #


class TestWhatItIsTold:
    def test_an_agent_is_framed_by_its_handler_and_a_chat_is_not(self, started):
        from kith.services.turn import prompt

        chat, agent = started
        framed = prompt.directive_for(agent["id"])
        assert "“api”, started by conductor" in framed and agent["branch"] in framed
        assert prompt.directive_for(chat) == prompt.CHAT_DIRECTIVE

    def test_the_map_carries_each_agent_s_figures_and_task(self, db, started, client):
        chat, agent = started
        repo.conversations.add_usage(
            db, agent["id"], tokens=446_512, cost=0.73, context=120_000, model="m/one"
        )
        repo.conversations.add_usage(db, agent["id"], tokens=1_000, seconds=12.5)
        repo.conversations.add_usage(db, chat, context=522_600, model="m/conductor")
        body = client.get(f"/api/conversations/{chat}/agents").get_json()
        node = body["agents"][0]
        assert node["tokens"] == 447_512 and node["costUsd"] == 0.73 and node["seconds"] == 12.5
        assert node["contextTokens"] == 120_000 and node["modelUsed"] == "m/one"
        assert node["task"] == "build the api"
        assert body["self"]["contextTokens"] == 522_600 and body["self"]["name"] == "conductor"

    def test_a_handler_sees_its_agents(self, started):
        chat, _ = started
        block = agents.agents_block(chat)
        assert "[Your agents" in block and "- api: working" in block

    def test_the_strategy_rides_whole_in_the_cached_head(self, monkeypatch):
        from kith.config import default_config
        from kith.services import skills

        monkeypatch.setattr(skills, "_strategy_name", lambda: "divide-and-conquer")
        system = default_config().system
        assert "## How you work with agents (strategy: divide-and-conquer)" in system
        assert "**divide-and-conquer**" not in skills.index()


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #


class TestTheTools:
    def test_ask_inside_an_agent_goes_to_its_handler(self, started, monkeypatch):
        from kith.tools import asking

        _, agent = started
        seen = {}
        monkeypatch.setattr(
            agents, "ask_up", lambda conversation_id, questions: seen.setdefault("up", conversation_id)
        )
        with session_context.working_in(agent["id"]):
            asking.ask(Path("x"), {"questions": [{"question": "q", "options": ["a"]}]})
        assert seen["up"] == agent["id"]

    def test_reach_out_inside_an_agent_is_a_note_to_its_handler(self, started, no_turns):
        from kith.tools import outreach

        chat, agent = started
        with session_context.working_in(agent["id"]):
            answer = outreach.reach_out(Path("x"), {"message": "found something odd"})
        assert answer == {"ok": True, "to": "conductor"}
        assert "Note from your agent “api”" in _transcript(chat)[-1]["content"]

    def test_an_agent_can_not_be_typed_at(self, started, client):
        _, agent = started
        refused = client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "conversationId": agent["id"]}
        )
        assert refused.status_code == 409


# --------------------------------------------------------------------------- #
# Through the real turn machinery
# --------------------------------------------------------------------------- #


class TestTheWholeLoop:
    def test_a_handler_starts_an_agent_and_its_report_wakes_the_handler(self, db, repo_dir, monkeypatch):
        from kith.api.routes import chat as route

        turns: list[tuple[str, str, str]] = []

        def fake_turn(
            recorder, messages, config, conversation_id, opening="", *, stop_switch=None, tool_host=None
        ):
            turns.append((conversation_id, opening, session_context.isolated_copy()))
            reply = "Done: added api.py." if agents.is_agent(conversation_id) else "Merged it."
            if agents.is_agent(conversation_id):
                (Path(paths.base_dir()) / "api.py").write_text("x = 1\n")
            conversations.record(db, conversation_id, "assistant", reply)
            yield '{"type": "delta", "role": "text", "text": "ok"}\n'

        monkeypatch.setattr(route, "_turn", fake_turn)
        monkeypatch.setattr(route, "_build_messages", lambda history, *a, **k: list(history))
        monkeypatch.setattr(route, "_tool_block_chars", lambda: 0)
        monkeypatch.setattr(route.tools, "host", lambda *a, **k: SimpleNamespace(schemas=lambda *a, **k: []))
        monkeypatch.setattr(agents, "base_dir", lambda: repo_dir)
        from kith.kernel import turns as supervisor

        supervisor.install(route.run_queued)

        chat = _chat(db)
        with session_context.working_in(chat):
            agent = agents.start(chat, [{"name": "api", "task": "add api.py"}])["agents"][0]

        for _ in range(200):
            if len(turns) >= 2 and not live_turns.live():
                break
            time.sleep(0.02)

        assert [who for who, _, _ in turns] == [agent["id"], chat]
        # The agent ran in its own worktree, with nothing of the handler's context.
        row = repo.conversations.get(db, agent["id"])
        assert turns[0][2] == row["agent_worktree"]
        assert turns[1][2] == ""
        # Its report woke the handler.
        assert "Report from your agent “api”" in turns[1][1]
        assert "Done: added api.py." in turns[1][1]
        assert repo.conversations.get(db, agent["id"])["agent_state"] == agents.DONE
        assert _git(Path(row["agent_worktree"]), "log", "-1", "--format=%s").endswith(
            "left uncommitted when it reported"
        )
