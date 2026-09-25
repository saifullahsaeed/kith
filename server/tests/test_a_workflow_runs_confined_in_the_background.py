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


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    here = tmp_path / "project"
    here.mkdir()
    monkeypatch.setattr(paths, "base_dir", lambda: here)
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
        if "JSON value" in objective:
            return {
                "findings": 'found\n```json\n{"files": ["a.py", "b.py"]}\n```',
                "looked_at": "grep",
            }, _Report()
        return {"findings": f"report: {objective.splitlines()[0]}", "looked_at": "grep x2"}, _Report()

    monkeypatch.setattr(workers, "run_agent", fake)
    monkeypatch.setattr(workers, "open_copy", lambda ident: "")
    return seen


def _start(db: Path, script: str, conversation: str = "conv-1") -> dict:
    with session_context.working_in(conversation), session_context.may_run_workflows(True):
        return workflow.run_workflow(db, {"script": script})


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

        assert "may_run_workflows(asks_for_workflow(opening))" in inspect.getsource(chat.begin_turn)


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
        assert meta == {"name": "port", "description": "d", "phases": ["A", "B"]}


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
        run = _wait(_start(db, script)["run"])
        assert "no 'wizard' kind" in _result(run)
        assert not agents, "no model is paid for a request that could never run"

    def test_a_script_that_crashes_still_reports_why(self, db: Path, project: Path, agents):
        run = _wait(_start(db, 'META = {"name": "c"}\nraise ValueError("the script fell over")')["run"])
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
