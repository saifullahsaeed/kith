"""A workflow is a script Kith writes, run confined, whose agents run here — and only when asked.

Three promises, each tested where it is kept rather than where it is described:

* **Only when asked.** `run_workflow` refuses unless the turn's opening message started with
  `/workflow`. The opt-in is the person's own text, so nothing but a person can make it.
* **The script only orchestrates.** It runs under `sandbox-exec` with no network and nothing under
  the home folder but its own run folder. Tested against a real folder under the real `$HOME`,
  because a temporary directory lives under `/private/var`, outside the deny, and proves nothing —
  the lesson `infra/confinement` paid for once already.
* **It reports like a background job.** The call returns at once; the run ends with a note on
  `services/workflows`, which the scheduler's timer turns into a turn in the same conversation.

The agents themselves are stubbed at `workers.run_agent`, which has its own tests: what is under
test here is the script, the channel, the limits and the books.
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from kith import settings
from kith.domain.workflows import asks_for_workflow
from kith.infra import confinement
from kith.infra.workspace import paths
from kith.kernel import session_context
from kith.services import scheduler, workflows
from kith.tools import workers, workflow

needs_confinement = pytest.mark.skipif(not confinement.available(), reason="sandbox-exec is macOS-only")


class _Report:
    def __init__(self, cost: float = 0.01) -> None:
        self.cost = cost
        self.cut_short = False
        self.error = ""
        self.text = "said"
        self.said_before = ""
        self.calls: list[str] = ["grep"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    from kith.infra.workspace import worktrees

    here = tmp_path / "project"
    here.mkdir()
    monkeypatch.setattr(paths, "base_dir", lambda: here)
    # `worktrees` bound `base_dir` at import, so the patch above does not reach it. The real one
    # asks the database which project "conv-1" is bound to, and an earlier test in the session
    # can have bound it to a folder that is no repository — every builder then failed with "no
    # repository here", or not, depending on the order the tests ran in.
    monkeypatch.setattr(worktrees, "base_dir", lambda: here)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    workflows.forget_all()
    yield here
    for run in list(workflows.listing()):
        workflows.stop(run["run"])
    workflows.forget_all()


@pytest.fixture
def agents(monkeypatch):
    """A stand-in for the agent loop: echoes its objective, and records how it was called."""
    seen: list[dict] = []
    lock = threading.Lock()

    def fake(path, kind, objective, conversation, **kw):
        with lock:
            seen.append({"kind": kind, "objective": objective, "conversation": conversation, **kw})
        if "boom" in objective:
            raise RuntimeError("kaboom")
        if kind == "builder":
            return {
                "findings": "changed it",
                "looked_at": "edit_files",
                "patch": "--- a/x\n+++ b/x\n@@\n-a\n+b\n",
            }, _Report()
        if kw.get("schema") is not None:
            # What run_agent hands back when the agent answered through `submit`.
            return {
                "findings": "found",
                "looked_at": "grep",
                "submitted": {"files": ["a.py", "b.py"]},
            }, _Report()
        if "JSON value" in objective:
            return {
                "findings": 'found\n```json\n{"files": ["a.py", "b.py"]}\n```',
                "looked_at": "grep",
            }, _Report()
        return {"findings": f"report: {objective.splitlines()[0]}", "looked_at": "grep x2"}, _Report()

    monkeypatch.setattr(workers, "run_agent", fake)
    monkeypatch.setattr(workers, "open_copy", lambda ident: "")
    return seen


def _start(db: Path, script: str, conversation: str = "conv-1", **more) -> dict:
    with session_context.working_in(conversation), session_context.may_run_workflows(True):
        return workflow.run_workflow(db, {"script": script, **more})


def _wait(run_id: str, seconds: float = 30) -> workflows.Run:
    run = workflows.get(run_id)
    assert run is not None
    deadline = time.monotonic() + seconds
    while run.state == workflows.RUNNING and time.monotonic() < deadline:
        time.sleep(0.05)
    assert run.state != workflows.RUNNING, "the run never ended"
    return run


def _result(run: workflows.Run):
    import json

    return json.loads((Path(run.folder) / "result.json").read_text())


class TestOnlyWhenAsked:
    @pytest.mark.parametrize(
        "said", ["/workflow port the rag package", "  /workflow", "/Workflow go", "/workflow\nand more"]
    )
    def test_the_prefix_opts_in(self, said):
        assert asks_for_workflow(said)

    @pytest.mark.parametrize(
        "said", ["port it with a workflow", "/workflows", "/workflowish", "", "x /workflow"]
    )
    def test_anything_else_does_not(self, said):
        assert not asks_for_workflow(said)

    def test_without_the_prefix_it_refuses(self, db: Path, project: Path):
        with session_context.working_in("conv-1"):
            answer = workflow.run_workflow(db, {"script": 'META = {"name": "x"}'})
        assert "/workflow" in answer["error"]
        assert not workflows.listing()

    def test_a_turn_is_not_opted_in_by_default(self):
        assert session_context.workflows_allowed() is False
        with session_context.may_run_workflows(True):
            assert session_context.workflows_allowed() is True
        assert session_context.workflows_allowed() is False

    def test_the_turn_reads_it_off_the_opening_message(self, monkeypatch):
        """Wired where the turn begins, so nothing but a typed message can opt in."""
        import inspect

        from kith.api.routes import chat

        assert "may_run_workflows(_workflow_permission(conversation_id, opening))" in inspect.getsource(
            chat.begin_turn
        )


class TestTheScriptIsCheckedBeforeAnythingRuns:
    def test_a_syntax_error_comes_back_in_the_turn(self, db: Path, project: Path):
        answer = _start(db, 'META = {"name": "x"}\nagent("a"\n')
        assert "does not parse" in answer["error"]
        assert not workflows.listing()

    def test_meta_is_required(self, db: Path, project: Path):
        answer = _start(db, 'agent("look")')
        assert "META" in answer["error"]

    def test_meta_must_be_literal(self, db: Path, project: Path):
        answer = _start(db, 'META = {"name": "x" + "y"}')
        assert "literal" in answer["error"]

    def test_meta_is_read_without_running_anything(self):
        meta, problem = workflow.read_meta(
            'META = {"name": "port", "description": "d", "phases": ["A", "B"]}'
        )
        assert problem == ""
        assert {k: meta[k] for k in ("name", "description", "phases")} == {
            "name": "port",
            "description": "d",
            "phases": ["A", "B"],
        }
        assert meta["budget_usd"] == workflow.DEFAULT_BUDGET_USD


@needs_confinement
class TestARun:
    def test_it_returns_at_once_and_reports_when_done(self, db: Path, project: Path, agents):
        script = """
META = {"name": "smoke", "description": "d", "phases": ["Survey", "Read"]}
print("this goes to the log, not the channel")
with phase("Survey"):
    found = agent("list the files", schema={"files": ["str"]})
with phase("Read"):
    reports = parallel([lambda f=f: agent(f"read {f}") for f in found["files"]] + [lambda: agent("boom")])
    piped = pipeline([1, 2, 3], lambda x: x * 2, lambda x: None if x == 4 else x)
finish({"found": found, "reports": reports, "piped": piped})
"""
        answer = _start(db, script)
        assert answer["run"].startswith("wf-")
        assert answer["phases"] == ["Survey", "Read"]

        run = _wait(answer["run"])
        assert run.state == workflows.FINISHED
        result = _result(run)
        assert result["found"] == {"files": ["a.py", "b.py"]}
        # In order, and the failure is a hole rather than an exception that took the rest down.
        assert result["reports"] == ["report: read a.py", "report: read b.py", None]
        assert result["piped"] == [2, None, 6]
        assert "this goes to the log" in (Path(run.folder) / "run.log").read_text()

        assert "`smoke`" in run.note and "finished" in run.note
        assert 'scout "read a.py"' in run.note
        assert "broke: kaboom" in run.note, "a failure the script swallowed still reaches the note"

    def test_every_agent_runs_in_the_conversation_that_sent_it(self, db: Path, project: Path, agents):
        run = _wait(_start(db, 'META = {"name": "x"}\nfinish(agent("look"))', conversation="conv-9")["run"])
        assert run.state == workflows.FINISHED
        assert [one["conversation"] for one in agents] == ["conv-9"]

    def test_a_builder_hands_back_a_patch_file_and_touches_nothing(self, db: Path, project: Path, agents):
        script = 'META = {"name": "b"}\nbuilt = agent("change x", kind="builder", label="the change")\nfinish(built)'
        run = _wait(_start(db, script)["run"])
        result = _result(run)
        assert result["patch_file"].endswith(".patch")
        assert Path(result["patch_file"]).read_text().startswith("--- a/x")
        assert "+b" in result["diff"]
        assert "(+1 −1)" in run.note
        assert "Nothing has been applied" in run.note

    def test_an_unknown_kind_is_refused(self, db: Path, project: Path, agents):
        script = 'META = {"name": "k"}\ntry:\n    agent("x", kind="wizard")\nexcept AgentFailed as e:\n    finish(str(e))'
        # Named by the rehearsal, before the run exists...
        refused = _start(db, script)
        assert "no 'wizard' kind" in refused["error"] and "script.py line 3" in refused["error"]
        assert not workflows.listing()
        # ...and still refused at run time for a script that skipped it.
        run = _wait(_start(db, script, rehearse=False)["run"])
        assert "no 'wizard' kind" in _result(run)
        assert not agents, "no model is paid for a request that could never run"

    def test_a_script_that_crashes_still_reports_why(self, db: Path, project: Path, agents):
        run = _wait(
            _start(db, 'META = {"name": "c"}\nraise ValueError("the script fell over")', rehearse=False)[
                "run"
            ]
        )
        assert run.state == workflows.FAILED
        assert "the script fell over" in run.note

    def test_one_run_at_a_time_per_conversation(self, db: Path, project: Path, monkeypatch):
        release = threading.Event()

        def slow(path, kind, objective, conversation, **kw):
            release.wait(10)
            return {"findings": "done"}, _Report()

        monkeypatch.setattr(workers, "run_agent", slow)
        first = _start(db, 'META = {"name": "one"}\nagent("wait")')
        second = _start(db, 'META = {"name": "two"}\nagent("wait")')
        assert "already running" in second["error"]
        # Another conversation is not blocked by this one.
        other = _start(db, 'META = {"name": "three"}\nagent("wait")', conversation="conv-2")
        assert other.get("run")
        release.set()
        _wait(first["run"])
        _wait(other["run"])


@needs_confinement
class TestTheLimitsHold:
    def test_agents_never_run_more_than_the_cap_at_once(self, db: Path, project: Path, monkeypatch):
        monkeypatch.setattr(workflow, "AT_ONCE", 2)
        live = [0]
        most = [0]
        lock = threading.Lock()

        def counted(path, kind, objective, conversation, **kw):
            with lock:
                live[0] += 1
                most[0] = max(most[0], live[0])
            time.sleep(0.3)
            with lock:
                live[0] -= 1
            return {"findings": "ok"}, _Report()

        monkeypatch.setattr(workers, "run_agent", counted)
        script = (
            'META = {"name": "fan"}\nfinish(parallel([lambda i=i: agent(f"part {i}") for i in range(6)]))'
        )
        run = _wait(_start(db, script)["run"])
        assert _result(run) == ["ok"] * 6
        assert most[0] == 2

    def test_the_run_cannot_send_more_than_its_share(self, db: Path, project: Path, agents, monkeypatch):
        monkeypatch.setattr(workflow, "MAX_AGENTS", 2)
        script = (
            'META = {"name": "many"}\nfinish(parallel([lambda i=i: agent(f"part {i}") for i in range(3)]))'
        )
        run = _wait(_start(db, script)["run"])
        assert _result(run).count(None) == 1
        assert len(agents) == 2

    def test_stop_ends_the_script_and_its_agents(self, db: Path, project: Path, monkeypatch):
        gave_up = threading.Event()

        def patient(path, kind, objective, conversation, *, give_up=None, **kw):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if give_up and give_up():
                    gave_up.set()
                    return {"findings": "partial"}, _Report()
                time.sleep(0.02)
            return {"findings": "finished anyway"}, _Report()

        monkeypatch.setattr(workers, "run_agent", patient)
        answer = _start(db, 'META = {"name": "long"}\nagent("take your time")\nfinish("never")')
        time.sleep(0.5)
        assert workflows.stop(answer["run"]) is True
        run = _wait(answer["run"])
        assert run.state == workflows.STOPPED
        assert gave_up.is_set(), "the agent was told to stop, not left running"
        assert "was stopped" in run.note

    def test_the_clock_ends_a_run_that_runs_too_long(self, db: Path, project: Path, agents, monkeypatch):
        monkeypatch.setattr(workflow, "MAX_SECONDS", 1)
        run = _wait(_start(db, 'META = {"name": "slow"}\nimport time\ntime.sleep(30)')["run"])
        assert run.state == workflows.FAILED
        assert "ran past" in run.note


@needs_confinement
class TestTheScriptIsConfined:
    @pytest.fixture
    def secret(self) -> Iterator[Path]:
        """A real file under the real home folder — the only place the deny can be seen."""
        folder = Path.home() / f".kith-workflow-probe-{uuid.uuid4().hex[:8]}"
        folder.mkdir()
        (folder / "secret.txt").write_text("the real secret")
        yield folder / "secret.txt"
        shutil.rmtree(folder, ignore_errors=True)

    def test_it_cannot_read_the_home_folder(self, db: Path, project: Path, agents, secret: Path):
        script = f"""
META = {{"name": "probe"}}
out = {{}}
try:
    out["file"] = open({str(secret)!r}).read()
except OSError as exc:
    out["file"] = "refused"
import os
try:
    out["listing"] = os.listdir({str(Path.home())!r})
except OSError:
    out["listing"] = "refused"
finish(out)
"""
        run = _wait(_start(db, script)["run"])
        assert _result(run) == {"file": "refused", "listing": "refused"}

    def test_it_cannot_reach_the_network(self, db: Path, project: Path, agents):
        script = """
META = {"name": "net"}
import socket
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3)
    finish("open")
except OSError:
    finish("refused")
"""
        run = _wait(_start(db, script)["run"])
        assert _result(run) == "refused"

    def test_it_inherits_none_of_the_servers_environment(self, db: Path, project: Path, agents, monkeypatch):
        monkeypatch.setenv("KITH_TEST_SECRET_TOKEN", "sk-live-nope")
        run = _wait(_start(db, 'META = {"name": "env"}\nimport os\nfinish(sorted(os.environ))')["run"])
        assert "KITH_TEST_SECRET_TOKEN" not in _result(run)

    def test_it_can_write_its_own_folder(self, db: Path, project: Path, agents):
        script = 'META = {"name": "w"}\nopen("mine.txt", "w").write("ok")\nfinish("wrote")'
        run = _wait(_start(db, script)["run"])
        assert (Path(run.folder) / "mine.txt").read_text() == "ok"


class TestItWakesTheConversation:
    def test_a_finished_run_is_reported_once(self, project: Path):
        run = workflows.start("conv-7", "x", "", [], folder=str(project))
        assert run is not None
        workflows.end(run, workflows.FINISHED, "The workflow `x` finished.")
        assert workflows.finished_since_last_look() == {"conv-7": ["The workflow `x` finished."]}
        assert workflows.finished_since_last_look() == {}

    def test_a_stopped_run_is_reported_too(self, project: Path):
        """Unlike a stopped shell job: its finished agents still have work worth applying."""
        run = workflows.start("conv-8", "x", "", [], folder=str(project))
        assert run is not None
        workflows.end(run, workflows.STOPPED, "The workflow `x` was stopped.")
        assert "conv-8" in workflows.finished_since_last_look()

    def test_the_scheduler_wakes_the_chat_with_the_note(self, project: Path, monkeypatch):
        run = workflows.start("conv-5", "x", "", [], folder=str(project))
        assert run is not None
        workflows.end(run, workflows.FINISHED, "The workflow `x` finished.")
        woken: list[tuple[str, str]] = []
        scheduler.wake_finished(lambda conversation, trigger: woken.append((conversation, trigger)))
        assert woken and woken[0][0] == "conv-5"
        assert "The workflow `x` finished." in woken[0][1]


class TestReadingTheJsonAnAgentEndedWith:
    def test_a_fenced_block_wins(self):
        text = 'I looked.\n```json\n{"files": ["a"]}\n```'
        assert workflow.last_json(text, {"files": ["str"]}) == {"files": ["a"]}

    def test_the_last_top_level_value_not_a_nested_one(self):
        text = 'first {"a": 1} then the answer {"files": ["x"], "meta": {"n": 2}}'
        assert workflow.last_json(text, {"files": ["str"]}) == {"files": ["x"], "meta": {"n": 2}}

    def test_the_type_has_to_match_the_schema(self):
        assert workflow.last_json('["a", "b"]', {"files": ["str"]}) is None
        assert workflow.last_json('["a", "b"]', ["str"]) == ["a", "b"]

    def test_nothing_parseable_is_none(self):
        assert workflow.last_json("no json here {oops", {"a": 1}) is None


@needs_confinement
class TestEndToEndThroughTheRealAgentLoop:
    """Everything above stubs the agent. This one lets the real loop run inside the run's thread,
    with only the model faked — because the seams that matter here are the ones a stub hides: the
    conversation's context reaching the agent's tools, the builder's pin into its own copy, and
    the copy being cleaned up afterwards. A builder that edited the real tree would pass every
    stubbed test in this file."""

    def test_a_builder_edits_its_copy_and_the_real_tree_is_untouched(
        self, db: Path, tmp_path: Path, monkeypatch
    ):
        import json
        import subprocess

        from kith.services import agent_loop

        repo = tmp_path / "project"
        repo.mkdir()
        for line in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(line, cwd=repo, check=True)
        (repo / "kept.py").write_text("original\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "first"], cwd=repo, check=True)
        # `root`, never `base_dir`: patching `base_dir` would bypass the builder's pin, which is
        # the very thing under test.
        monkeypatch.setattr(paths, "root", lambda: repo)
        monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
        workflows.forget_all()

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            building = "isolated builder" in str(convo[0].get("content"))
            if building and not any(m.get("role") == "tool" for m in convo):
                arguments = json.dumps({"path": "kept.py", "content": "the builder version\n"})
                call = {"function": {"name": "write_file", "arguments": arguments}}
                yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}
                return
            said = "Rewrote kept.py." if building else "kept.py holds one line."
            yield {"type": "delta", "role": "text", "text": said}
            yield {"type": "turn", "content": said, "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        script = """
META = {"name": "e2e", "phases": ["Look", "Build"]}
with phase("Look"):
    seen = agent("what is in kept.py")
with phase("Build"):
    built = agent("rewrite kept.py", kind="builder", label="rewrite")
finish({"seen": seen, "report": built["report"], "patch_file": built["patch_file"]})
"""
        run = _wait(_start(db, script, conversation="conv-e2e")["run"])
        assert run.state == workflows.FINISHED, run.note
        result = _result(run)
        assert result["seen"] == "kept.py holds one line."
        assert result["report"] == "Rewrote kept.py."
        assert "the builder version" in Path(result["patch_file"]).read_text()
        # The assertion the whole design is for.
        assert (repo / "kept.py").read_text() == "original\n"
        # And the copy is gone: a workflow's builder is never followed up, so nothing is kept.
        copies = tmp_path / "data" / "workers"
        assert not copies.exists() or not any(copies.iterdir())
        workflows.forget_all()


@needs_confinement
class TestALaterAgentSeesWhatEarlierBuildersMade:
    """Every agent starts from the repository as it is, which made the first real workflow's
    review step read a tree where the new module did not exist. `after=` starts an agent from a
    copy with the earlier patches applied — and its own patch stays only its own work."""

    def test_a_review_reads_the_new_file_and_a_second_builder_stacks(
        self, db: Path, tmp_path: Path, monkeypatch
    ):
        import json
        import subprocess

        from kith.services import agent_loop

        repo = tmp_path / "project"
        repo.mkdir()
        for line in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(line, cwd=repo, check=True)
        (repo / "kept.py").write_text("original\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "first"], cwd=repo, check=True)
        monkeypatch.setattr(paths, "root", lambda: repo)
        monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
        workflows.forget_all()

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            asked = str(convo[1].get("content"))
            results = [m for m in convo if m.get("role") == "tool"]
            if "write the module" in asked and not results:
                arguments = json.dumps({"path": "discover.py", "content": "def probe(): pass\n"})
            elif "wire it" in asked and not results:
                arguments = json.dumps({"path": "app.py", "content": "import discover\n"})
            elif "review" in asked and not results:
                call = {
                    "function": {"name": "read_file", "arguments": json.dumps({"paths": ["discover.py"]})}
                }
                yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}
                return
            else:
                said = "saw: " + (str(results[-1].get("content"))[:200] if results else "nothing")
                yield {"type": "delta", "role": "text", "text": said}
                yield {"type": "turn", "content": said, "tool_calls": [], "stats": {}}
                return
            call = {"function": {"name": "write_file", "arguments": arguments}}
            yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        script = """
META = {"name": "stack"}
module = agent("write the module", kind="builder", label="module")
wired = agent("wire it", kind="builder", label="wiring", after=[module])
review = agent("review discover.py", after=[module, wired, None])
finish({"review": review, "wired": wired["diff"]})
"""
        run = _wait(_start(db, script, conversation="conv-stack")["run"])
        assert run.state == workflows.FINISHED, run.note
        result = _result(run)
        assert "def probe" in result["review"], "the reviewer read a file that exists only as a patch"
        assert "app.py" in result["wired"] and "discover.py" not in result["wired"]
        assert "made on top of module — apply those first" in run.note
        assert (repo / "kept.py").read_text() == "original\n"
        assert not (repo / "discover.py").exists()
        workflows.forget_all()


class TestAnAgentIsNeverHandedPluginCommands:
    def test_the_tool_host_it_runs_on_has_no_plugins_and_no_mcp(self, db: Path, monkeypatch):
        """A builder asked for a mockup opened a real browser window through a plugin command:
        the loop allows whatever it offers, and plugins were being offered."""
        from kith import tools as tool_layer
        from kith.services import agent_loop

        seen: dict = {}
        real = tool_layer.host

        def spy(path, **kw):
            seen.update(kw)
            return real(path, **kw)

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            yield {"type": "delta", "role": "text", "text": "done"}
            yield {"type": "turn", "content": "done", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(tool_layer, "host", spy)
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        workers.run_agent(db, "scout", "look", "")
        assert seen.get("mcp") == [] and seen.get("plugin") == []


@needs_confinement
class TestTheBoardCanDrawARunTheFeedHasForgotten:
    """The feed is a ring of the last few hundred lines; a busy conversation pushes a run out of it
    in minutes. The board keeps what the agent map needs, so a window opened later can still draw
    the run — including one still going."""

    def test_listing_carries_the_agents(self, db: Path, project: Path, agents):
        script = """
META = {"name": "board", "phases": ["Look", "Build"]}
with phase("Look"):
    agent("what is in kept.py", label="look")
with phase("Build"):
    agent("change x", kind="builder", label="change")
finish("ok")
"""
        run = _wait(_start(db, script)["run"])
        [listed] = workflows.listing("conv-1")
        assert listed["state"] == workflows.FINISHED
        assert listed["phases"] == ["Look", "Build"]
        assert listed["said"].startswith("The workflow `board`")
        by_label = {one["label"]: one for one in listed["agents"]}
        assert by_label["look"]["phase"] == "Look" and by_label["look"]["kind"] == "scout"
        assert by_label["change"]["phase"] == "Build" and by_label["change"]["kind"] == "builder"
        assert by_label["change"]["patch"].endswith(".patch")
        assert (
            by_label["look"]["state"] == "done" and by_label["look"]["ended"] >= by_label["look"]["started"]
        )
        assert by_label["look"]["prompt"] == "what is in kept.py"
        assert run.id == listed["run"]


class TestWorkflowMode:
    """The prefix opts one message in; the mode opts a whole chat in, so he decides where a long
    session's work is worth fanning out — and nothing about the prefix changes."""

    def _conversation(self, on: bool) -> str:
        from kith.api.routes import chat
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH as _unused  # noqa: F401 - patched by conftest

        conversation_id = f"c-{uuid.uuid4().hex[:6]}"
        repo.conversations.create(chat.AGENT_DB_PATH, conversation_id, "t", "s")
        repo.conversations.set_workflow_mode(chat.AGENT_DB_PATH, conversation_id, on)
        return conversation_id

    def test_the_turn_is_opted_in_by_the_mode_or_the_prefix(self):
        from kith.api.routes import chat

        on, off = self._conversation(True), self._conversation(False)
        assert chat._workflow_permission(on, "do the thing") == "mode"
        assert chat._workflow_permission(off, "do the thing") == ""
        assert chat._workflow_permission(off, "/workflow do the thing") == "asked"
        assert chat._workflow_permission(on, "/workflow do the thing") == "asked"

    def test_he_is_told_which_it_is(self):
        from kith.services import memory_context

        with session_context.may_run_workflows("mode"):
            assert "Workflow mode is ON" in "\n".join(memory_context._workflow_lines())
        with session_context.may_run_workflows("asked"):
            said = "\n".join(memory_context._workflow_lines())
            assert "/workflow" in said and "add_agents" in said
        assert memory_context._workflow_lines() == []

    def test_he_is_told_a_run_is_going(self, project: Path):
        from kith.services import memory_context

        run = workflows.start("conv-seen", "port-rag", "", ["Build"], folder=str(project))
        assert run is not None
        run.phase = "Build"
        with session_context.working_in("conv-seen"):
            said = "\n".join(memory_context._workflow_lines())
        assert "`port-rag`" in said and "check_workflow" in said
        workflows.end(run, workflows.FINISHED, "done")

    def test_the_mode_lets_run_workflow_start_without_the_prefix(self, db: Path, project: Path, agents):
        with session_context.working_in("conv-mode"), session_context.may_run_workflows("mode"):
            answer = workflow.run_workflow(db, {"script": 'META = {"name": "m"}\nfinish(agent("look"))'})
        assert answer.get("run"), answer
        _wait(answer["run"])

    def test_without_the_mode_nobody_present_can_start_a_new_one(self, db: Path, project: Path, agents):
        """The prefix is a yes to one message, and the turn a finished run wakes is not it."""
        with (
            session_context.working_in("conv-asked"),
            session_context.may_run_workflows("asked"),
            session_context.nobody_watching(),
        ):
            answer = workflow.run_workflow(db, {"script": 'META = {"name": "m"}\nfinish(1)'})
        assert "Nobody is here" in answer["error"]

    def test_in_the_mode_a_woken_turn_can_start_the_next_step(self, db: Path, project: Path, agents):
        """Survey, then build, then review: the second and third start in the turn the one before
        wakes. Refusing that is how three runs were refused in one evening with the person right
        there — and the work they were for got done serially, and half done."""
        with (
            session_context.working_in("conv-chain"),
            session_context.may_run_workflows("mode"),
            session_context.nobody_watching(),
        ):
            answer = workflow.run_workflow(db, {"script": 'META = {"name": "next"}\nfinish(1)'})
        assert answer.get("run"), answer
        assert _wait(answer["run"]).unwatched

    def test_but_the_chain_has_an_end(self, db: Path, project: Path, agents):
        """A mode left on must not chain runs overnight: past a few in a row it reaches a person."""
        for _ in range(workflow.MAX_UNWATCHED_RUNS):
            run = workflows.start("conv-long", "step", "", [], folder="", unwatched=True)
            assert run is not None
            workflows.end(run, workflows.FINISHED, "done")
        with (
            session_context.working_in("conv-long"),
            session_context.may_run_workflows("mode"),
            session_context.nobody_watching(),
        ):
            answer = workflow.run_workflow(db, {"script": 'META = {"name": "m"}\nfinish(1)'})
        assert "in a row" in answer["error"]

    def test_a_run_the_person_started_resets_the_chain(self):
        for unwatched in (True, True, False, True):
            run = workflows.start("conv-reset", "step", "", [], folder="", unwatched=unwatched)
            assert run is not None
            workflows.end(run, workflows.FINISHED, "done")
            time.sleep(0.01)  # `started` orders them
        assert workflows.unwatched_in_a_row("conv-reset") == 1


@needs_confinement
class TestKithHasFullControlOfARun:
    """He can see where a run has got to, stop it, and — when it breaks — fix the script and resume
    it, with what already finished reused rather than paid for twice."""

    def test_check_shows_the_agents_and_stop_ends_it(self, db: Path, project: Path, monkeypatch):
        release = threading.Event()

        def slow(path, kind, objective, conversation, *, give_up=None, **kw):
            while not release.is_set() and not (give_up and give_up()):
                time.sleep(0.02)
            return {"findings": "done"}, _Report()

        monkeypatch.setattr(workers, "run_agent", slow)
        started = _start(
            db,
            'META = {"name": "long", "phases": ["Look"]}\nwith phase("Look"):\n    agent("look", label="looker")',
        )
        time.sleep(0.6)
        with session_context.working_in("conv-1"):
            seen = workflow.check_workflow(db, {})
            assert seen["state"] == "running" and seen["phase"] == "Look"
            assert seen["agents"][0]["label"] == "looker" and seen["agents"][0]["state"] == "running"
            assert workflow.stop_workflow(db, {})["stopping"] == started["run"]
        run = _wait(started["run"])
        assert run.state == workflows.STOPPED
        release.set()

    def test_another_chat_cannot_see_or_stop_it(self, db: Path, project: Path, agents):
        started = _start(db, 'META = {"name": "mine"}\nfinish(1)')
        _wait(started["run"])
        with session_context.working_in("someone-else"):
            assert "error" in workflow.check_workflow(db, {"run": started["run"]})
            assert "error" in workflow.stop_workflow(db, {"run": started["run"]})

    def test_a_broken_run_is_fixed_and_resumed_reusing_what_finished(self, db: Path, project: Path, agents):
        broken = """
META = {"name": "fixme"}
first = agent("survey the code", label="survey")
second = agent("boom now", label="builder-ish")
finish([first, second])
"""
        run = _wait(_start(db, broken)["run"])
        assert run.state == workflows.FAILED
        assert "resume=" in run.note
        with session_context.working_in("conv-1"):
            seen = workflow.check_workflow(db, {"run": run.id})
        assert "next" in seen and "resume" in seen["next"]
        calls_before = len(agents)

        fixed = broken.replace("boom now", "build it now")
        # Resuming needs no fresh opt-in: the person agreed to this run.
        with session_context.working_in("conv-1"):
            again = workflow.run_workflow(db, {"script": fixed, "resume": run.id})
        assert again.get("resumed_from") == run.id and again["reusable"] == 1
        second = _wait(again["run"])
        assert second.state == workflows.FINISHED, second.note
        assert _result(second) == ["report: survey the code", "report: build it now"]
        # Only the changed agent ran again.
        assert [one["objective"] for one in agents[calls_before:]] == ["build it now"]
        assert "reusing 1 finished agent" in second.note

    def test_a_run_lost_to_a_restart_can_still_be_resumed(self, db: Path, project: Path, agents):
        run = _wait(_start(db, 'META = {"name": "r"}\nfinish(agent("look once"))')["run"])
        workflows.forget_all()  # the server restarted
        with session_context.working_in("conv-1"):
            seen = workflow.check_workflow(db, {"run": run.id})
            assert seen["state"] == "ended with a server restart" and seen["finished_agents"] == 1
            again = workflow.run_workflow(db, {"resume": run.id})
        calls_before = len(agents)
        second = _wait(again["run"])
        assert _result(second) == "report: look once"
        assert len(agents) == calls_before, "the finished agent was reused, not run again"

    def test_resuming_is_bounded(self, db: Path, project: Path, agents, monkeypatch):
        monkeypatch.setattr(workflow, "MAX_RESUMES", 1)
        script = 'META = {"name": "loop"}\nraise RuntimeError("still broken")'
        first = _wait(_start(db, script, rehearse=False)["run"])
        with session_context.working_in("conv-1"):
            second = _wait(workflow.run_workflow(db, {"resume": first.id, "rehearse": False})["run"])
            refused = workflow.run_workflow(db, {"resume": second.id, "rehearse": False})
        assert "resumed 1 times" in refused["error"]

    def test_only_this_chats_runs_can_be_resumed(self, db: Path, project: Path, agents):
        run = _wait(_start(db, 'META = {"name": "mine"}\nfinish(1)')["run"])
        with session_context.working_in("someone-else"):
            assert "no run" in workflow.run_workflow(db, {"resume": run.id})["error"].lower()


# --------------------------------------------------------------------------------------------- #
# Saved workflows, rehearsal, merge, revise, budget, wait — added 2026-09-28.
# --------------------------------------------------------------------------------------------- #


def _git_project(project: Path) -> Path:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    (project / ".gitignore").write_text(".kith/\n")
    (project / "base.py").write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=project, check=True)
    return project


@pytest.fixture
def builders(project: Path, monkeypatch):
    """Real copies of a real repository, and builders that write the file their prompt names.

    A prompt "write NAME" writes NAME in the builder's own copy; a revision appends to it. What
    comes back is the copy's real diff, so merge and revise are exercised against git.
    """
    from kith.infra.workspace import worktrees

    _git_project(project)
    # `worktrees` bound `base_dir` at import; the project fixture patches it there too.
    monkeypatch.setattr(paths, "root", lambda: project)
    seen: list[dict] = []

    def fake(path, kind, objective, conversation, **kw):
        seen.append({"kind": kind, "objective": objective, **kw})
        copy = Path(kw.get("worktree") or "")
        if kind == "builder" and kw.get("worktree"):
            carried = kw.get("carried")
            asked = str(carried[-1]["content"]) if carried else objective
            name = asked.split("write ", 1)[1].split()[0] if "write " in asked else "revised.py"
            target = copy / name
            target.write_text((target.read_text() if target.exists() else "") + f"{name} line\n")
            if kw.get("watch"):
                kw["watch"]({"type": "stats", "stats": {"costUsd": 0.01}})
            return {"findings": f"wrote {name}", "patch": worktrees.diff_in(copy)}, _Report()
        return {"findings": f"report: {objective.splitlines()[0]}"}, _Report()

    monkeypatch.setattr(workers, "run_agent", fake)
    return seen


@needs_confinement
class TestTheCopyStubbedToNothingDeletesNothing:
    def test_a_builder_with_an_empty_copy_leaves_the_working_directory_alone(
        self, db: Path, project: Path, agents, tmp_path: Path, monkeypatch
    ):
        """The regression: an empty copy path reached `rmtree` and took the whole folder."""
        here = tmp_path / "cwd"
        here.mkdir()
        (here / "precious.txt").write_text("keep me")
        monkeypatch.chdir(here)
        run = _wait(_start(db, 'META = {"name": "b"}\nfinish(agent("change x", kind="builder"))')["run"])
        assert run.state == workflows.FINISHED, run.note
        assert (here / "precious.txt").read_text() == "keep me"


@needs_confinement
class TestARehearsalGoesFirst:
    def test_a_crash_comes_back_in_the_turn_and_nothing_starts(self, db: Path, project: Path, agents):
        answer = _start(db, 'META = {"name": "c"}\nwith parallel([lambda: agent("a")]) as reports:\n    pass')
        assert "crashed in a rehearsal" in answer["error"]
        assert not workflows.listing() and not agents

    def test_an_agent_sent_another_agents_report_is_named(self, db: Path, project: Path, agents):
        script = (
            'META = {"name": "twice"}\n'
            'def survey():\n    return agent("look at the code")\n'
            'finish(agent(survey(), label="recheck"))'
        )
        answer = _start(db, script)
        assert "another agent's report" in answer["error"] and "script.py line 4" in answer["error"]
        assert not agents

    def test_a_verifier_without_the_change_is_warned_about(self, db: Path, project: Path, agents):
        script = (
            'META = {"name": "w"}\n'
            'built = agent("change x", kind="builder")\n'
            'finish(agent("check it", kind="verifier"))'
        )
        answer = _start(db, script)
        assert any("unchanged tree" in one for one in answer["rehearsal_warnings"])
        _wait(answer["run"])

    def test_a_model_that_is_not_an_alias_is_refused(self, db: Path, project: Path, agents):
        answer = _start(db, 'META = {"name": "m"}\nfinish(agent("x", model="gpt-9"))')
        assert "model must be one of" in answer["error"]

    def test_every_shipped_workflow_rehearses_clean(self, db: Path, project: Path):
        inputs = {
            "args": {
                "goal": "add a thing",
                "areas": ["a", "b"],
                "change": "rename x",
                "question": "where is y",
            },
            "asked": "please add the thing",
            "budget": 5,
        }
        for script in sorted(workflow.shipped_library().glob("*.py")):
            refused, warnings = workflow.rehearse(script.read_text(), inputs)
            assert refused == "", f"{script.name}: {refused}"
            assert not any("unchanged tree" in one for one in warnings), f"{script.name}: {warnings}"


@needs_confinement
class TestSavedWorkflows:
    def test_the_shipped_ones_are_in_the_description(self):
        from kith.tools import registry

        described = registry.get("run_workflow").description
        for name in ("build-feature", "review-changes", "audit", "migrate-sites"):
            assert f"- {name}:" in described

    def test_one_runs_by_name_with_its_args_and_their_words(
        self, db: Path, project: Path, agents, monkeypatch
    ):
        mine = project / ".kith" / "workflows"
        mine.mkdir(parents=True)
        (mine / "echo.py").write_text('META = {"name": "echo"}\nfinish({"args": ARGS, "asked": ASKED})')
        monkeypatch.setattr(workflow, "_their_words", lambda conversation: "make it blue")
        with session_context.working_in("conv-1"), session_context.may_run_workflows(True):
            answer = workflow.run_workflow(db, {"name": "echo", "args": {"colour": "blue"}})
        run = _wait(answer["run"])
        assert _result(run) == {"args": {"colour": "blue"}, "asked": "make it blue"}

    def test_an_unknown_name_is_refused(self, db: Path, project: Path, agents):
        with session_context.working_in("conv-1"), session_context.may_run_workflows(True):
            answer = workflow.run_workflow(db, {"name": "no-such-thing"})
        assert "no saved workflow" in answer["error"] and "build-feature" in answer["error"]

    def test_one_workflow_can_run_another_as_a_step(self, db: Path, project: Path, agents):
        mine = project / ".kith" / "workflows"
        mine.mkdir(parents=True)
        (mine / "child.py").write_text('META = {"name": "child"}\nfinish(agent(f"look at {ARGS}"))')
        run = _wait(_start(db, 'META = {"name": "parent"}\nfinish(workflow("child", "the api"))')["run"])
        assert _result(run) == "report: look at the api"


@needs_confinement
class TestAVerifierIsHandedTheirWords:
    def test_whatever_the_script_wrote(self, db: Path, project: Path, agents, monkeypatch):
        monkeypatch.setattr(workflow, "_their_words", lambda conversation: "every screen, not just the API")
        _wait(_start(db, 'META = {"name": "v"}\nfinish(agent("check the change", kind="verifier"))')["run"])
        told = next(one for one in agents if one["kind"] == "verifier")["objective"]
        assert "every screen, not just the API" in told
        assert told.index("check the change") < told.index("every screen")


@needs_confinement
class TestPuttingItTogether:
    def test_merge_applies_the_builders_patches_in_one_copy(self, db: Path, project: Path, builders):
        script = (
            'META = {"name": "m"}\n'
            'a = agent("write one.py", kind="builder", label="one")\n'
            'b = agent("write two.py", kind="builder", label="two")\n'
            "merged = merge(a, b)\n"
            'finish({"applied": merged["applied"], "clean": merged["clean"], "diff": merged["diff"]})'
        )
        run = _wait(_start(db, script)["run"])
        assert run.state == workflows.FINISHED, run.note
        result = _result(run)
        assert result["applied"] == ["one", "two"] and result["clean"] is True
        assert "one.py" in result["diff"] and "two.py" in result["diff"]
        assert 'merge "merged"' in run.note and "apply that one" in run.note
        assert (project / "base.py").read_text() == "base\n" and not (project / "one.py").exists()

    def test_a_patch_that_does_not_fit_is_named_not_forced(self, db: Path, project: Path, builders):
        script = (
            'META = {"name": "m"}\n'
            'a = agent("write same.py", kind="builder", label="first")\n'
            'b = agent("write same.py", kind="builder", label="second")\n'
            "merged = merge(a, b)\n"
            'finish({"applied": merged["applied"], "conflicts": [c["label"] for c in merged["conflicts"]]})'
        )
        result = _result(_wait(_start(db, script)["run"]))
        assert result == {"applied": ["first"], "conflicts": ["second"]}

    def test_revise_carries_on_in_the_same_copy(self, db: Path, project: Path, builders):
        script = (
            'META = {"name": "r"}\n'
            'built = agent("write first.py", kind="builder")\n'
            'again = revise(built, "now write second.py as well")\n'
            'finish(again["diff"])'
        )
        run = _wait(_start(db, script)["run"])
        diff = _result(run)
        assert "first.py" in diff and "second.py" in diff, "the revision kept the first edit"
        revision = [one for one in builders if one.get("carried")]
        assert revision and revision[0]["worktree"] == builders[0]["worktree"]

    def test_every_copy_is_gone_when_the_run_ends(self, db: Path, project: Path, builders):
        from kith.infra.workspace import worktrees

        _wait(
            _start(db, 'META = {"name": "g"}\nfinish(merge(agent("write x.py", kind="builder"))["agent"])')[
                "run"
            ]
        )
        left = (
            [p for p in worktrees._home().iterdir() if not p.name.startswith(".")]
            if worktrees._home().is_dir()
            else []
        )
        assert left == []


@needs_confinement
class TestTheBudgetIsACeiling:
    def test_agents_past_it_are_refused(self, db: Path, project: Path, monkeypatch):
        sent: list[str] = []

        def pricey(path, kind, objective, conversation, **kw):
            sent.append(objective)
            if kw.get("watch"):
                kw["watch"]({"type": "stats", "stats": {"costUsd": 1.0}})
            return {"findings": "ok"}, _Report(cost=1.0)

        monkeypatch.setattr(workers, "run_agent", pricey)
        script = (
            'META = {"name": "b", "budget_usd": 1.5}\n'
            "out = []\n"
            "for i in range(4):\n"
            "    try:\n"
            '        out.append(agent(f"look {i}"))\n'
            "    except AgentFailed as e:\n"
            "        out.append(str(e))\n"
            "finish(out)"
        )
        run = _wait(_start(db, script)["run"])
        result = _result(run)
        assert len(sent) == 2
        assert "budget" in result[2] and "budget" in result[3]
        assert "reached its $1.50 budget" in run.note

    def test_the_script_can_see_what_is_left(self, db: Path, project: Path, agents):
        run = _wait(_start(db, 'META = {"name": "b", "budget_usd": 2}\nfinish(budget.remaining())')["run"])
        assert 1.9 < _result(run) <= 2.0

    def test_a_budget_out_of_range_is_refused(self):
        assert "budget_usd" in workflow.read_meta('META = {"name": "x", "budget_usd": 500}')[1]


@needs_confinement
class TestWaitingForAShortRun:
    def test_the_result_comes_back_in_the_turn_and_does_not_wake_it_again(
        self, db: Path, project: Path, agents
    ):
        answer = _start(db, 'META = {"name": "quick"}\nfinish(agent("look"))', wait=True)
        assert answer["finished"] is True
        assert "`quick`" in answer["result"] and "report: look" in answer["result"]
        assert "conv-1" not in workflows.finished_since_last_look()


@needs_confinement
class TestPipelineStages:
    def test_a_stage_may_take_the_item_and_its_index(self, db: Path, project: Path, agents):
        run = _wait(
            _start(db, 'META = {"name": "p"}\nfinish(pipeline([10, 20], lambda v, item, i: v + i))')["run"]
        )
        assert _result(run) == [10, 21]
