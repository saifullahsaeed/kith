"""A workflow Kith keeps going one step at a time: `add_agents`, and the board that reopens a run.

What is promised, each tested where it is kept:

* **A step is checked before anything is spent.** Labels, kinds, `after` and cycles are refused
  in the tool result, in words that say what to fix.
* **`after` means "builds on what it made".** After a builder an agent starts from its patch;
  after a scout or verifier the report rides in its prompt — in the same step or a later one.
* **The run goes on.** A finished run reopens for the next step: same id, one more phase, the
  budget and the agent count carried over — also after a restart, and also for a script run.
* **It stops.** Five steps in a row with nobody watching, a spent budget, or Stop.

The agents are stubbed at `workers.run_agent`, as in the script tests: under test here is the
step — the scheduling, the ledger, the note — not the agent loop.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from kith import settings
from kith.infra import confinement
from kith.infra.workspace import paths, worktrees
from kith.kernel import session_context
from kith.services import workflows
from kith.tools import workers, workflow, workflow_steps
from kith.tools.workflow_steps import check_plan, read_items

needs_confinement = pytest.mark.skipif(not confinement.available(), reason="sandbox-exec is macOS-only")


class _Report:
    def __init__(self, cost: float = 0.01, cut_short: bool = False) -> None:
        self.cost = cost
        self.cut_short = cut_short
        self.error = ""
        self.text = "said"
        self.said_before = ""
        self.calls: list[str] = ["grep"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    here = tmp_path / "project"
    here.mkdir()
    monkeypatch.setattr(paths, "base_dir", lambda: here)
    monkeypatch.setattr(paths, "root", lambda: here)
    monkeypatch.setattr(worktrees, "base_dir", lambda: here)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    workflows.forget_all()
    yield here
    for run in list(workflows.listing()):
        workflows.stop(run["run"])
    workflows.forget_all()


@pytest.fixture
def agents(monkeypatch):
    """Agents that report the first line of what they were asked, and remember all of it."""
    seen: list[dict] = []
    lock = threading.Lock()

    def fake(path, kind, objective, conversation, **kw):
        with lock:
            seen.append({"kind": kind, "objective": objective, **kw})
        if "boom" in objective:
            raise RuntimeError("kaboom")
        if kind == "builder":
            return {"findings": "changed it", "looked_at": "edit_files", "patch": ""}, _Report()
        return {"findings": f"report: {objective.splitlines()[0]}", "looked_at": "grep"}, _Report()

    monkeypatch.setattr(workers, "run_agent", fake)
    monkeypatch.setattr(workers, "open_copy", lambda ident: "")
    return seen


def _git_project(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    (project / ".gitignore").write_text(".kith/\n")
    (project / "base.py").write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=project, check=True)


@pytest.fixture
def builders(project: Path, monkeypatch):
    """Real copies of a real repository; a builder asked to "write NAME" writes NAME in its copy.

    Each call records which files its copy held when it started, so a test can see what an
    agent was started on top of.
    """
    _git_project(project)
    seen: list[dict] = []

    def fake(path, kind, objective, conversation, **kw):
        copy = Path(kw.get("worktree") or "")
        seen.append(
            {
                "kind": kind,
                "objective": objective,
                "had": sorted(p.name for p in copy.glob("*.py")) if kw.get("worktree") else [],
                **kw,
            }
        )
        if kind == "builder" and kw.get("worktree"):
            carried = kw.get("carried")
            asked = str(carried[-1]["content"]) if carried else objective
            name = asked.split("write ", 1)[1].split()[0] if "write " in asked else "revised.py"
            target = copy / name
            target.write_text((target.read_text() if target.exists() else "") + f"{name} line\n")
            return {"findings": f"wrote {name}", "patch": worktrees.diff_in(copy)}, _Report()
        return {"findings": f"report: {objective.splitlines()[0]}"}, _Report()

    monkeypatch.setattr(workers, "run_agent", fake)
    return seen


def _add(db: Path, conversation: str = "conv-1", allowed: bool = True, **args) -> dict:
    with session_context.working_in(conversation):
        if allowed:
            with session_context.may_run_workflows(True):
                return workflow_steps.add_agents(db, args)
        return workflow_steps.add_agents(db, args)


def _wait(run_id: str, seconds: float = 30) -> workflows.Run:
    run = workflows.get(run_id)
    assert run is not None
    deadline = time.monotonic() + seconds
    while run.state == workflows.RUNNING and time.monotonic() < deadline:
        time.sleep(0.05)
    assert run.state != workflows.RUNNING, "the step never ended"
    return run


def _ledger(run: workflows.Run) -> dict:
    return json.loads((Path(run.folder) / "run.json").read_text())


# ── the board ───────────────────────────────────────────────────────────────────────────────


class TestTheBoardReopensARun:
    def test_an_ended_run_opens_for_one_more_step(self, project: Path):
        run = workflows.start("conv-1", "build", "", ["Step 1"], folder="")
        assert run is not None
        run.halt.set()
        workflows.end(run, workflows.STOPPED, "stopped")
        assert workflows.finished_since_last_look() == {"conv-1": ["stopped"]}

        assert workflows.reopen(run, "Step 2")
        assert run.state == workflows.RUNNING and run.phase == "Step 2"
        assert run.phases == ["Step 1", "Step 2"]
        assert not run.halt.is_set(), "a stopped step's switch must not stop the next one"
        assert not run.reported and not run.note and not run.ended

        workflows.end(run, workflows.FINISHED, "step two done")
        assert workflows.finished_since_last_look() == {"conv-1": ["step two done"]}

    def test_it_says_so_on_the_feed_with_the_whole_plan(self, project: Path, monkeypatch):
        said: list[dict] = []
        monkeypatch.setattr(
            workflows, "say", lambda run, state, text, **extra: said.append({"state": state, **extra})
        )
        run = workflows.start("conv-1", "build", "", ["Step 1"], folder="")
        assert run is not None
        workflows.end(run, workflows.FINISHED, "done")
        workflows.reopen(run, "Step 2")
        assert said[-1]["state"] == "step" and said[-1]["phases"] == ["Step 1", "Step 2"]

    def test_not_while_the_chat_has_a_run_going(self, project: Path):
        first = workflows.start("conv-1", "one", "", [], folder="")
        assert first is not None
        workflows.end(first, workflows.FINISHED, "done")
        second = workflows.start("conv-1", "two", "", [], folder="")
        assert second is not None
        assert not workflows.reopen(first, "Step 2")
        assert not workflows.reopen(second, "Step 2"), "it is running already"

    def test_a_lost_run_is_put_back_ended_and_heard(self, project: Path):
        run = workflows.restore("wf-abc123", "conv-1", "build", "/x", ["Step 1"], 5.0, kind="steps")
        assert run.state == workflows.FINISHED and run.reported and run.kind == "steps"
        assert workflows.finished_since_last_look() == {}
        assert workflows.restore("wf-abc123", "conv-1", "other", "/y", [], 0.0) is run


# ── reading and checking a step ─────────────────────────────────────────────────────────────


class TestAStepIsCheckedFirst:
    @pytest.mark.parametrize(
        ("raw", "says"),
        [
            ([], "at least one agent"),
            ([{"prompt": "x"}], "no label"),
            ([{"label": "a", "kind": "wizard", "prompt": "x"}], "kind must be one of"),
            ([{"label": "a", "kind": "merge"}], "a merge needs after"),
            ([{"label": "a", "kind": "revise", "prompt": "x", "after": ["b", "c"]}], "exactly one builder"),
            ([{"label": "a", "kind": "revise", "after": ["b"]}], "what to change"),
            ([{"label": "a", "kind": "builder"}], "has no prompt"),
        ],
    )
    def test_what_is_wrong_is_said(self, raw, says):
        items, problem = read_items(raw)
        assert not items and says in problem

    def test_after_takes_one_label_as_a_string_too(self):
        items, problem = read_items([{"label": "b", "prompt": "x", "after": "a"}])
        assert not problem and items[0]["after"] == ["a"] and items[0]["kind"] == "scout"

    def test_labels_are_unique_in_the_run(self):
        items, _ = read_items([{"label": "a", "prompt": "x"}, {"label": "a", "prompt": "y"}])
        assert "already a label" in check_plan(items, {})
        items, _ = read_items([{"label": "survey", "prompt": "x"}])
        assert "already a label" in check_plan(items, {"survey": {"kind": "scout", "ok": True}})

    def test_an_unknown_label_is_named_with_the_ones_there_are(self):
        items, _ = read_items([{"label": "b", "prompt": "x", "after": ["servey"]}])
        problem = check_plan(items, {"survey": {"kind": "scout", "ok": True}})
        assert '"servey"' in problem and "survey" in problem.split("Labels there are:")[1]

    def test_nothing_starts_from_an_agent_that_did_not_finish(self):
        items, _ = read_items([{"label": "b", "prompt": "x", "after": ["a"]}])
        assert "did not finish" in check_plan(items, {"a": {"kind": "builder", "ok": False}})

    def test_a_merge_or_revise_comes_after_builders(self):
        items, _ = read_items([{"label": "m", "kind": "merge", "after": ["look"]}])
        assert "must be a builder" in check_plan(items, {"look": {"kind": "scout", "ok": True}})

    def test_a_circle_is_refused(self):
        items, _ = read_items(
            [{"label": "a", "prompt": "x", "after": ["b"]}, {"label": "b", "prompt": "y", "after": ["a"]}]
        )
        assert "circle" in check_plan(items, {})

    def test_a_chain_across_steps_is_fine(self):
        items, _ = read_items(
            [
                {"label": "build", "kind": "builder", "prompt": "x", "after": ["survey"]},
                {"label": "check", "kind": "verifier", "prompt": "y", "after": ["build"]},
                {"label": "fix", "kind": "revise", "prompt": "z", "after": ["build"]},
            ]
        )
        assert check_plan(items, {"survey": {"kind": "scout", "ok": True}}) == ""


# ── running a step ──────────────────────────────────────────────────────────────────────────


class TestAStep:
    def test_a_new_run_needs_the_mode_or_the_prefix(self, db: Path, project: Path, agents):
        answer = _add(db, allowed=False, agents=[{"label": "a", "prompt": "look"}])
        assert "/workflow" in answer["error"] and not workflows.listing()

    def test_it_returns_at_once_and_wakes_the_chat_with_what_they_said(self, db: Path, project: Path, agents):
        answer = _add(
            db,
            name="survey",
            agents=[
                {"label": "files", "prompt": "find the files"},
                {"label": "tests", "prompt": "find the tests"},
            ],
        )
        assert answer["step"] == 1 and answer["phases"] == ["Step 1"] and "add_agents" in answer["note"]
        run = _wait(answer["run"])
        assert run.state == workflows.FINISHED and run.kind == "steps"
        assert run.note.startswith(f"Step 1 of `survey` ({run.id}) finished")
        assert 'scout "files" — reported:\n  report: find the files' in run.note
        assert f'add_agents(run="{run.id}"' in run.note and "files, tests" in run.note
        assert workflows.finished_since_last_look() == {"conv-1": [run.note]}

    def test_a_report_rides_into_what_comes_after_it_in_the_same_step(self, db: Path, project: Path, agents):
        answer = _add(
            db,
            agents=[
                {"label": "change", "kind": "builder", "prompt": "make the change", "after": ["survey"]},
                {"label": "survey", "prompt": "find where the loop is"},
            ],
        )
        _wait(answer["run"])
        survey, change = agents
        assert survey["objective"].startswith("find where the loop is"), "the one with no after went first"
        assert "## survey\nreport: find where the loop is" in change["objective"]

    def test_the_next_step_goes_on_in_the_same_run(self, db: Path, project: Path, agents):
        first = _add(db, agents=[{"label": "survey", "prompt": "find where the loop is"}])
        run = _wait(first["run"])
        second = _add(
            db,
            allowed=False,
            run=run.id,
            agents=[{"label": "check", "kind": "verifier", "prompt": "check it", "after": ["survey"]}],
        )
        assert second["run"] == run.id and second["step"] == 2 and second["phases"] == ["Step 1", "Step 2"]
        run = _wait(run.id)
        assert run.note.startswith(f"Step 2 of `survey` ({run.id})")
        assert "report: find where the loop is" in agents[-1]["objective"]
        ledger = _ledger(run)
        assert ledger["steps"] == 2 and ledger["sent"] == 2
        assert ledger["spent"] == pytest.approx(0.02)
        assert ledger["labels"]["check"] == {"kind": "verifier", "step": 2, "ok": True}

    def test_a_label_is_not_used_twice_across_steps(self, db: Path, project: Path, agents):
        run = _wait(_add(db, agents=[{"label": "survey", "prompt": "look"}])["run"])
        answer = _add(db, run=run.id, agents=[{"label": "survey", "prompt": "look again"}])
        assert "already a label" in answer["error"]
        assert run.state == workflows.FINISHED, "a refused step does not reopen the run"

    def test_a_failure_skips_what_comes_after_it_and_says_why(self, db: Path, project: Path, agents):
        answer = _add(
            db,
            agents=[
                {"label": "bad", "prompt": "boom"},
                {"label": "next", "prompt": "carry on", "after": ["bad"]},
                {"label": "fine", "prompt": "unrelated"},
            ],
        )
        run = _wait(answer["run"])
        assert run.state == workflows.FINISHED, "one agent finished, so the step did"
        assert 'scout "bad" — failed: the agent broke: kaboom' in run.note
        assert 'scout "next" — skipped — "bad" did not finish' in run.note
        assert [one["objective"].splitlines()[0] for one in agents if "carry on" in one["objective"]] == []
        refused = _add(db, run=run.id, agents=[{"label": "again", "prompt": "x", "after": ["bad"]}])
        assert "did not finish" in refused["error"]

    def test_a_step_where_nothing_finished_failed(self, db: Path, project: Path, agents):
        run = _wait(_add(db, agents=[{"label": "bad", "prompt": "boom"}])["run"])
        assert run.state == workflows.FAILED and "none of its agents finished" in run.note

    def test_two_identical_asks_keep_their_own_records(self, db: Path, project: Path, agents):
        run = _wait(
            _add(
                db,
                agents=[{"label": "one", "prompt": "same thing"}, {"label": "two", "prompt": "same thing"}],
            )["run"]
        )
        assert len(list((Path(run.folder) / "agents").glob("*.json"))) == 2
        answer = _add(db, run=run.id, agents=[{"label": "both", "prompt": "x", "after": ["one", "two"]}])
        _wait(answer["run"])
        assert "## one" in agents[-1]["objective"] and "## two" in agents[-1]["objective"]

    def test_wait_hands_back_the_note_and_does_not_wake_the_chat(self, db: Path, project: Path, agents):
        answer = _add(db, wait=True, agents=[{"label": "look", "prompt": "look around"}])
        assert answer["finished"] is True and "report: look around" in answer["result"]
        assert workflows.finished_since_last_look() == {}

    def test_a_restart_between_steps_does_not_end_the_run(self, db: Path, project: Path, agents):
        run = _wait(_add(db, name="loop", agents=[{"label": "survey", "prompt": "find it"}])["run"])
        run_id = run.id
        workflows.forget_all()
        answer = _add(db, run=run_id, agents=[{"label": "check", "prompt": "check it", "after": ["survey"]}])
        assert answer["step"] == 2 and answer["phases"] == ["Step 1", "Step 2"]
        run = _wait(run_id)
        assert run.name == "loop" and "report: find it" in agents[-1]["objective"]
        assert _ledger(run)["spent"] == pytest.approx(0.02), "what step one spent still counts"

    def test_another_chat_cannot_add_to_it(self, db: Path, project: Path, agents):
        run = _wait(_add(db, agents=[{"label": "a", "prompt": "x"}])["run"])
        answer = _add(db, conversation="conv-2", run=run.id, agents=[{"label": "b", "prompt": "y"}])
        assert f"no run {run.id} in this chat" in answer["error"]

    def test_one_step_at_a_time(self, db: Path, project: Path, monkeypatch):
        release = threading.Event()

        def slow(path, kind, objective, conversation, **kw):
            release.wait(10)
            return {"findings": "done"}, _Report()

        monkeypatch.setattr(workers, "run_agent", slow)
        first = _add(db, agents=[{"label": "a", "prompt": "x"}])
        try:
            again = _add(db, run=first["run"], agents=[{"label": "b", "prompt": "y"}])
            assert "still has a step running" in again["error"]
            fresh = _add(db, agents=[{"label": "c", "prompt": "z"}])
            assert "already running" in fresh["error"]
        finally:
            release.set()
        _wait(first["run"])


class TestItStops:
    def test_five_steps_in_a_row_with_nobody_there_then_it_reports(self, db: Path, project: Path, agents):
        run = _wait(_add(db, agents=[{"label": "a", "prompt": "x"}])["run"])
        for n in range(workflow_steps.MAX_STEPS_UNWATCHED):
            with session_context.working_in("conv-1"), session_context.nobody_watching():
                answer = workflow_steps.add_agents(
                    db, {"run": run.id, "agents": [{"label": f"s{n}", "prompt": "go on"}]}
                )
            assert "error" not in answer, answer
            _wait(run.id)
        with session_context.working_in("conv-1"), session_context.nobody_watching():
            answer = workflow_steps.add_agents(
                db, {"run": run.id, "agents": [{"label": "more", "prompt": "go on"}]}
            )
        assert "nobody watching" in answer["error"]
        # A person's turn resets the count.
        assert "error" not in _add(db, run=run.id, agents=[{"label": "more", "prompt": "go on"}])
        _wait(run.id)
        assert _ledger(run)["unwatched_steps"] == 0

    def test_the_budget_is_the_runs_across_steps(self, db: Path, project: Path, agents):
        run = _wait(_add(db, budget_usd=0.01, agents=[{"label": "a", "prompt": "x"}])["run"])
        answer = _add(db, run=run.id, agents=[{"label": "b", "prompt": "y"}])
        run = _wait(answer["run"])
        assert "refused — the run has spent its $0.01 budget" in run.note
        assert run.state == workflows.FAILED

    def test_stop_ends_the_step_and_the_run_can_go_on(self, db: Path, project: Path, monkeypatch):
        started = threading.Event()

        def patient(path, kind, objective, conversation, **kw):
            started.set()
            while not kw["give_up"]():
                time.sleep(0.02)
            return {"findings": ""}, _Report(cut_short=True)

        monkeypatch.setattr(workers, "run_agent", patient)
        answer = _add(
            db,
            agents=[
                {"label": "slow", "prompt": "take ages"},
                {"label": "then", "prompt": "x", "after": ["slow"]},
            ],
        )
        assert started.wait(5)
        with session_context.working_in("conv-1"):
            assert workflow.stop_workflow(db, {})["stopping"] == answer["run"]
        run = _wait(answer["run"])
        assert run.state == workflows.STOPPED
        assert 'scout "then" — not started — the step was stopped' in run.note
        assert 'scout "slow" — failed: the agent did not finish: stopped' in run.note

        monkeypatch.setattr(
            workers,
            "run_agent",
            lambda path, kind, objective, conversation, **kw: ({"findings": "ok"}, _Report()),
        )
        again = _add(db, run=run.id, agents=[{"label": "retry", "prompt": "try again"}])
        assert _wait(again["run"]).state == workflows.FINISHED


class TestBuildingOnPatches:
    def test_a_builder_after_a_builder_starts_from_its_patch(self, db: Path, project: Path, builders):
        run = _wait(_add(db, agents=[{"label": "one", "kind": "builder", "prompt": "write one.py"}])["run"])
        assert "patch" in run.note and "Nothing has been applied" in run.note
        answer = _add(
            db,
            run=run.id,
            agents=[{"label": "two", "kind": "builder", "prompt": "write two.py", "after": ["one"]}],
        )
        run = _wait(answer["run"])
        assert run.state == workflows.FINISHED, run.note
        assert builders[-1]["had"] == ["base.py", "one.py"]
        assert not (project / "one.py").exists(), "nothing reaches the real tree"

    def test_a_merge_and_a_revise_in_later_steps(self, db: Path, project: Path, builders):
        run = _wait(
            _add(
                db,
                agents=[
                    {"label": "one", "kind": "builder", "prompt": "write one.py"},
                    {"label": "two", "kind": "builder", "prompt": "write two.py"},
                ],
            )["run"]
        )
        run = _wait(
            _add(
                db,
                run=run.id,
                agents=[{"label": "fix", "kind": "revise", "prompt": "now write extra.py", "after": ["one"]}],
            )["run"]
        )
        assert run.state == workflows.FINISHED, run.note
        run = _wait(
            _add(db, run=run.id, agents=[{"label": "all", "kind": "merge", "after": ["fix", "two"]}])["run"]
        )
        assert "2 of 2 applied" in run.note and "apply that one" in run.note
        merged = next((Path(run.folder)).glob("all-*.patch")).read_text()
        assert "one.py" in merged and "extra.py" in merged and "two.py" in merged


@needs_confinement
class TestAScriptRunGoesOnInSteps:
    def test_its_agents_are_named_by_label(self, db: Path, project: Path, agents):
        script = 'META = {"name": "s"}\nfinish(agent("find the loop", label="survey"))'
        with session_context.working_in("conv-1"), session_context.may_run_workflows(True):
            started = workflow.run_workflow(db, {"script": script})
        run = _wait(started["run"])
        assert f'add_agents(run="{run.id}"' in run.note
        answer = _add(
            db,
            run=run.id,
            agents=[{"label": "check", "kind": "verifier", "prompt": "check", "after": ["survey"]}],
        )
        assert answer["step"] == 1 and answer["run"] == run.id
        _wait(run.id)
        assert "## survey\nreport: find the loop" in agents[-1]["objective"]

    def test_resuming_a_step_run_as_a_script_says_what_to_do(self, db: Path, project: Path, agents):
        run = _wait(_add(db, agents=[{"label": "a", "prompt": "x"}])["run"])
        with session_context.working_in("conv-1"), session_context.may_run_workflows(True):
            answer = workflow.run_workflow(db, {"resume": run.id})
        assert "add_agents" in answer["error"]
