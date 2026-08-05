"""Running the tests, and running things in the background.

Two capabilities that were both missing for the same reason: `shell` waits for a command to
finish and clips its output, which is wrong for a suite (too much output) and wrong for a
server (never finishes). What both need is a bound on what comes back and a handle on what
is still going.

The assertions worth having are about honesty. A suite that could not be run must not report
as a suite that failed — those call for opposite actions. And a background process must be
able to say "it died and here is what it said", because the alternative is a dev server that
is silently not serving.
"""

from __future__ import annotations

import contextlib
import shlex
import subprocess
import sys
import time

import pytest

from kith.services.code import testing
from kith.services.code.processes import ProcessError, Processes, processes


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    from kith.infra import workspace

    monkeypatch.setattr(workspace, "configured_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def running(workspace_root):
    """A registry of its own, always emptied — a leaked `sleep 60` outlives the test run."""
    one = Processes()
    yield one
    one.stop_all()


class TestWorkingOutHowTestsAreRun:
    def test_a_pytest_project_is_recognised(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        assert testing.detect(tmp_path).name == "pytest"

    def test_a_tests_folder_alone_is_enough(self, tmp_path):
        (tmp_path / "tests").mkdir()
        assert testing.detect(tmp_path).name == "pytest"

    def test_the_placeholder_npm_test_script_is_not_a_test_suite(self, tmp_path):
        """`npm init` writes one that exits 1. Running it reports a failing suite for a
        project that has no tests, which is the worst of both answers."""
        (tmp_path / "package.json").write_text(
            '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
        )
        assert testing.detect(tmp_path) is None

    def test_a_real_npm_test_script_is_recognised(self, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
        assert testing.detect(tmp_path).name == "npm test"

    def test_a_project_with_nothing_says_so(self, tmp_path):
        (tmp_path / "readme.md").write_text("hi")
        assert testing.detect(tmp_path) is None

    def test_the_projects_own_interpreter_wins(self, tmp_path):
        """Not the bare word `python`, which does not exist on macOS at all, and not the
        system python3, which is the one without the project's dependencies."""
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("#!/bin/sh\n")
        (venv / "python").chmod(0o755)

        assert testing.python_for(tmp_path) == str(venv / "python")

    def test_without_a_venv_it_falls_back_to_something_that_exists(self, tmp_path):
        chosen = testing.python_for(tmp_path)
        assert chosen != "python", "`python` is not a command on a modern mac"

    def test_a_name_filter_becomes_a_k_expression(self, tmp_path):
        runner = testing.RUNNERS[0]
        assert "-k " in testing._narrowed(runner, "test_thing", tmp_path)

    def test_a_path_filter_is_passed_straight_through(self, tmp_path):
        runner = testing.RUNNERS[0]
        command = testing._narrowed(runner, "tests/test_a.py", tmp_path)
        assert "-k" not in command and "tests/test_a.py" in command


class TestActuallyRunningASuite:
    """Against real pytest, in a real project, because the parsing is the whole feature."""

    @pytest.fixture
    def project(self, workspace_root):
        (workspace_root / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        (workspace_root / "tests").mkdir()
        subprocess.run([sys.executable, "-m", "venv", str(workspace_root / ".venv")], capture_output=True)
        subprocess.run(
            [str(workspace_root / ".venv/bin/pip"), "install", "-q", "pytest"], capture_output=True
        )
        return workspace_root

    def test_failures_are_named_without_their_tracebacks(self, project):
        (project / "tests" / "test_a.py").write_text(
            "def test_ok(): assert True\ndef test_bad(): assert 2 + 2 == 5, 'arithmetic is broken'\n"
        )

        result = testing.run(".")

        assert result["ok"] is False
        assert result["passed"] == 1 and result["failed"] == 1
        assert any("test_bad" in one for one in result["failures"])
        assert any("arithmetic is broken" in one for one in result["failures"])

    def test_a_green_suite_says_so(self, project):
        (project / "tests" / "test_a.py").write_text("def test_ok(): assert True\n")
        result = testing.run(".")
        assert result["ok"] is True
        assert result["passed"] == 1
        assert "failures" not in result

    def test_the_verdict_and_the_count_are_different_fields(self, project):
        """They were both called `passed` at first, and the count overwrote the verdict — so
        a green run reported `passed: 1` and a failing one `passed: False`. Same field, two
        types, depending on what the suite did."""
        (project / "tests" / "test_a.py").write_text("def test_ok(): assert True\n")
        result = testing.run(".")
        assert result["ok"] is True
        assert isinstance(result["passed"], int) and not isinstance(result["passed"], bool)

    def test_it_can_be_narrowed_to_one_test(self, project):
        (project / "tests" / "test_a.py").write_text(
            "def test_wanted(): assert False\ndef test_other(): assert False\n"
        )

        result = testing.run(".", filter_="test_wanted")

        assert result["failed"] == 1, "the filter did not narrow the run"
        assert all("test_other" not in one for one in result["failures"])

    def test_a_huge_number_of_failures_is_capped(self, project):
        (project / "tests" / "test_many.py").write_text(
            "\n".join(f"def test_f{i}(): assert False" for i in range(40))
        )

        result = testing.run(".")

        assert len(result["failures"]) <= testing.MAX_FAILURES
        assert "note" in result
        assert len(result.get("log", "")) <= testing.MAX_LOG

    def test_counts_survive_a_project_that_already_sets_q_itself(self, project):
        """The bug this caught, which only a real project would show.

        Adding `-q` to a project whose `addopts` already has one makes `-qq`, and `-qq`
        suppresses the summary line the counts are parsed from. Kith's own `pytest.ini` is
        configured exactly this way, so running its suite reported `ok: true` and no numbers.
        """
        (project / "pytest.ini").write_text("[pytest]\naddopts = -q --tb=short\n")
        (project / "tests" / "test_a.py").write_text(
            "def test_one(): assert True\ndef test_two(): assert True\n"
        )

        result = testing.run(".")

        assert result["ok"] is True
        assert result["passed"] == 2, f"the counts were lost: {result}"

    def test_a_missing_runner_is_not_reported_as_a_failing_suite(self, workspace_root):
        """Opposite actions: one is a bug to fix, the other is a thing to install."""
        (workspace_root / "tests").mkdir()
        (workspace_root / "tests" / "test_a.py").write_text("def test_ok(): assert True\n")

        with pytest.raises(testing.TestingError) as caught:
            testing.run(".")

        assert "pip install pytest" in str(caught.value)

    def test_a_project_with_no_runner_at_all_explains_itself(self, workspace_root):
        (workspace_root / "readme.md").write_text("hi")
        with pytest.raises(testing.TestingError) as caught:
            testing.run(".")
        assert "shell" in str(caught.value)


class TestASlowSuiteDoesNotBlockOrGetKilled:
    """The whole reason `run` starts the suite in the background instead of just waiting.

    The old version had one blocking call with a hard 300-second timeout: past it, the
    process was killed and everything it had printed was gone. A suite slower than a quick
    check now hands back `status: "running"` instead, and a later call re-attaches to the
    same run rather than starting a second one.
    """

    @pytest.fixture
    def project(self, workspace_root):
        (workspace_root / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        (workspace_root / "tests").mkdir()
        subprocess.run([sys.executable, "-m", "venv", str(workspace_root / ".venv")], capture_output=True)
        subprocess.run(
            [str(workspace_root / ".venv/bin/pip"), "install", "-q", "pytest"], capture_output=True
        )
        return workspace_root

    @pytest.fixture(autouse=True)
    def drain_the_shared_slot(self):
        """`run` shares one registry entry across every test in this class — the same one
        `check_process`/`stop_process` would see. Left alive, a slow suite from one test
        would still be running (or still occupying the name) when the next test starts."""
        yield
        with contextlib.suppress(ProcessError):
            processes.stop(testing._PROCESS_NAME)

    def test_a_slow_suite_reports_running_then_the_real_result_once_done(self, project):
        marker = project / "ran.marker"
        (project / "tests" / "test_a.py").write_text(
            "import time, pathlib\n"
            f"MARKER = pathlib.Path({str(marker)!r})\n"
            "def test_slow():\n"
            "    MARKER.open('a').write('ran\\n')\n"
            "    time.sleep(1.5)\n"
            "    assert True\n"
        )

        first = testing.run(".", wait=0.3)
        assert first.get("status") == "running", f"expected still-running, got: {first}"

        second = testing.run(".", wait=5)

        assert second.get("ok") is True
        assert second.get("passed") == 1
        assert marker.read_text().count("ran") == 1, (
            "the suite ran more than once — the second call restarted it instead of "
            "re-attaching to the one already going"
        )

    def test_a_different_run_while_one_is_in_flight_is_refused(self, project):
        (project / "tests" / "test_a.py").write_text(
            "import time\n"
            "def test_a():\n    time.sleep(1.5)\n"
            "def test_b():\n    assert True\n"
        )

        first = testing.run(".", wait=0.3)
        assert first.get("status") == "running"

        with pytest.raises(testing.TestingError) as caught:
            testing.run(".", filter_="test_b", wait=0.3)
        assert "different test run" in str(caught.value)

        # Let the original finish so teardown's stop() is tidying up something dead, not
        # racing a live one.
        testing.run(".", wait=5)


class TestBackgroundProcesses:
    def test_it_starts_and_reports_what_was_printed(self, running):
        result = running.start("echo hello-there", "greeter")
        assert "hello-there" in result["output"]

    def test_it_cannot_block_on_a_prompt_nobody_is_there_to_answer(self, running):
        """The same env `shell` already gets, and for the sharper reason: a background
        process stuck waiting for a keypress looks identical to a healthy slow one — nothing
        in `check` tells them apart. Only one of the non-interactive vars is asserted; the
        point is that the whole set arrived, not any one var in particular."""
        result = running.start("echo \"terminal-prompt=$GIT_TERMINAL_PROMPT\"", "quiet-git")
        assert "terminal-prompt=0" in result["output"]

    def test_full_output_is_not_capped_like_check_is(self, running):
        running.start("for i in $(seq 1 4000); do echo 'a line of output here'; done", "verbose")
        time.sleep(1.2)

        from kith.services.code import processes as module

        whole = running.full_output("verbose")

        assert len(whole) > module.MAX_CHUNK, "expected the uncapped log, got something clipped"

    def test_full_output_of_an_unknown_name_explains_itself(self, running):
        with pytest.raises(ProcessError) as caught:
            running.full_output("ghost")
        assert "no background process" in str(caught.value)

    def test_each_check_shows_only_what_is_new(self, running):
        """A dev server prints a banner once and a line per request. Re-reading the banner
        every time is how a watcher fills a context.

        Kept alive across both checks on purpose — a *finished* process replays its tail
        instead, which is the opposite behaviour and is tested below.
        """
        running.start("for i in 1 2 3; do echo line-$i; sleep 0.4; done; sleep 20", "ticker")
        time.sleep(1.6)

        first = running.check("ticker")
        second = running.check("ticker")

        assert first["alive"] and second["alive"], "it exited; this tests the running case"
        assert "line-3" in first["output"]
        assert second["output"] == "", f"it repeated itself: {second['output']!r}"

    def test_a_finished_process_replays_its_ending_rather_than_saying_nothing(self, running):
        """`start` reads the first output itself, so without this a server that died on a
        port collision reported an empty string to the very next check."""
        running.start("echo why-it-died", "shortlived")
        time.sleep(0.5)

        again = running.check("shortlived")

        assert again["alive"] is False
        assert "why-it-died" in again["output"]

    def test_a_command_that_dies_is_reported_as_dead_with_its_words(self, running):
        """Not silence. The whole failure this replaces is a dev server that is not serving
        and no way to find out."""
        result = running.start("this-command-does-not-exist", "broken")

        assert result["alive"] is False
        assert result["exitCode"] != 0
        assert "not found" in result["output"]

    def test_starting_a_name_that_is_running_is_refused(self, running):
        running.start("sleep 20", "server")
        with pytest.raises(ProcessError) as caught:
            running.start("sleep 20", "server")
        assert "already running" in str(caught.value)

    def test_a_name_whose_process_has_finished_can_be_reused(self, running):
        running.start("echo one", "job")
        time.sleep(0.5)
        result = running.start("echo two", "job")
        assert "two" in result["output"]

    def test_stopping_takes_the_whole_tree(self, running):
        """`npm run dev` is a shell that spawns node. Killing the shell alone leaves node
        holding the port, and the next start fails with something unrelated-looking."""
        marker = "kith-test-tree-marker"
        running.start(f"{_sleeper(marker, 45)} & {_sleeper(marker, 45)}", "tree")
        time.sleep(0.8)
        before = _matching(marker)

        running.stop("tree")
        time.sleep(1.0)

        assert before >= 1, "the test did not manage to start anything"
        assert _matching(marker) == 0, "something survived the stop and is holding its port"

    def test_a_child_that_outlives_its_parent_is_still_killed(self, running):
        """The shape that leaked a real dev server.

        `npm run dev` is bash → npm → node → vite. SIGTERM killed bash, `wait` on the child
        we held returned, and the stop reported success while node carried on holding port
        8610 — found afterwards with `lsof`, not by anything that failed. So the group is
        what gets waited on, and this reproduces it: a parent that exits immediately while
        the grandchild keeps running.
        """
        marker = "kith-test-orphan-marker"
        # The parent backgrounds the child and then exits, which is the case the early return
        # in `stop` used to skip entirely: the direct child was dead, so it did nothing and
        # reported success while the grandchild kept running.
        running.start(f"{_sleeper(marker, 45)} & sleep 0.3", "orphan-maker")
        time.sleep(1.0)
        assert _matching(marker) >= 1, "the test did not manage to start the grandchild"

        running.stop("orphan-maker")
        time.sleep(1.0)

        assert _matching(marker) == 0, "the grandchild survived and is still holding whatever it held"

    def test_stopping_leaves_other_peoples_processes_alone(self, running):
        """The obvious fix — `pkill -f vite` — would also kill the unrelated dev server the
        person has running for their own project in another window."""
        marker = "kith-test-bystander-marker"
        bystander = subprocess.Popen(["bash", "-c", f"sleep 30 # {marker}"], start_new_session=True)
        try:
            running.start("sleep 30", "ours")
            running.stop("ours")
            time.sleep(0.5)
            assert bystander.poll() is None, "it killed something it did not start"
        finally:
            with contextlib.suppress(Exception):
                bystander.terminate()
                bystander.wait(timeout=2)

    def test_listing_shows_everything(self, running):
        running.start("sleep 20", "one")
        running.start("sleep 20", "two")
        names = {one["name"] for one in running.check()["running"]}
        assert {"one", "two"} <= names

    def test_an_unknown_name_lists_what_does_exist(self, running):
        running.start("sleep 20", "real")
        with pytest.raises(ProcessError) as caught:
            running.check("imaginary")
        assert "real" in str(caught.value)

    def test_there_is_a_ceiling(self, running):
        from kith.services.code import processes as module

        for i in range(module.MAX_RUNNING):
            running.start("sleep 20", f"p{i}")
        with pytest.raises(ProcessError) as caught:
            running.start("sleep 20", "one-too-many")
        assert "already running" in str(caught.value)

    def test_a_torrent_of_output_is_capped_and_says_so(self, running):
        from kith.services.code import processes as module

        running.start("for i in $(seq 1 4000); do echo 'a line of output here'; done", "noisy")
        time.sleep(1.2)

        result = running.check("noisy")

        assert len(result["output"]) <= module.MAX_CHUNK
        assert "skipped" in result

    def test_the_newest_output_is_the_part_kept(self, running):
        """The end of a log is where the error is; the beginning is where the banner is."""
        running.start("for i in $(seq 1 3000); do echo filler; done; echo THE-IMPORTANT-BIT", "tail-test")
        time.sleep(1.2)
        assert "THE-IMPORTANT-BIT" in running.check("tail-test")["output"]

    def test_stopping_something_already_dead_is_not_an_error(self, running):
        running.start("echo done", "quick")
        time.sleep(0.5)
        assert "stopped" in running.stop("quick")

    def test_stop_all_leaves_nothing(self, running):
        running.start("sleep 30", "a")
        running.start("sleep 30", "b")
        running.stop_all()
        assert running.check()["running"] == []

    def test_a_name_with_awkward_characters_is_tidied(self, running):
        result = running.start("echo ok", "Dev Server/2!")
        assert result["name"] == "dev-server-2"

    def test_a_nameless_process_is_refused(self, running):
        with pytest.raises(ProcessError):
            running.start("echo ok", "   ")

    def test_an_empty_command_is_refused(self, running):
        with pytest.raises(ProcessError):
            running.start("", "empty")


def _sleeper(marker: str, seconds: int) -> str:
    """A long-running leaf process whose marker survives into its own `argv`.

    `sleep 45 # marker` does not work and looked like it did: the `#` is a shell comment,
    stripped before exec, so the only process carrying the marker was the wrapper shell.
    `pgrep -f` then reported the marker gone the moment the *shell* died, whether or not the
    sleep underneath it survived — which is precisely the thing these tests exist to catch,
    so they were passing without testing it.
    """
    return f"{shlex.quote(sys.executable)} -c 'import time; time.sleep({seconds})  # {marker}'"


def _matching(marker: str) -> int:
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return len([one for one in found.stdout.split() if one.strip()])


class TestTheTools:
    def test_run_tests_reaches_the_registry(self, workspace_root, tmp_path):
        from kith.tools import registry

        (workspace_root / "readme.md").write_text("hi")
        result = registry.get("run_tests").run(tmp_path / "agent.db", {})
        assert "error" in result, "a project with no runner should explain, not raise"

    def test_the_process_tools_reach_the_registry(self, workspace_root, tmp_path):
        from kith.tools import registry

        db = tmp_path / "agent.db"
        started = registry.get("start_process").run(db, {"command": "echo via-tool", "name": "t1"})
        assert "via-tool" in started["output"]

        listed = registry.get("check_process").run(db, {})
        assert any(one["name"] == "t1" for one in listed["running"])

        stopped = registry.get("stop_process").run(db, {"name": "t1"})
        assert stopped["stopped"] == "t1"

    def test_stopping_something_that_does_not_exist_explains_itself(self, workspace_root, tmp_path):
        from kith.tools import registry

        result = registry.get("stop_process").run(tmp_path / "agent.db", {"name": "ghost"})
        assert "error" in result
