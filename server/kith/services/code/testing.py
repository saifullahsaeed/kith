"""Running a project's tests, and reporting only what failed.

He could typecheck, lint and build, and he could not run a single test. So "I've finished
the feature" was a claim resting on the code compiling — which is the weakest evidence
available and exactly the evidence that looks strongest. Every real check this project has
been through found something that compiled fine.

**The output is the whole problem.** A suite is designed to be read by a person scrolling a
terminal: pytest prints a traceback per failure, jest prints a diff per failure, and a
hundred-test run that fails twelve times is tens of thousands of characters of which about
four hundred matter. Piping that into a turn is the same mistake as reading a whole file to
find one line, except worse, because it happens exactly when he is mid-way through fixing
something and has least room left.

So this asks the runner for its terse form, pulls out the counts and the failing test names,
and hands back that. The full log is clipped and kept underneath for when the names are not
enough.

**Detection over configuration.** Nobody is going to tell Kith how this project runs its
tests, and the answer is written down in the project already — a `pytest.ini`, a `test`
script in `package.json`, a `go.mod`. Guessing from the files is right nearly always, and
when it is wrong the answer names what it tried so the shell is one step away.

**A slow suite is not a broken one.** The first version ran the suite the same way `shell`
runs anything — one blocked call with a hard timeout — at 300 seconds, past which it was
killed and everything it had printed was thrown away. That is fine for the suite that runs in
four seconds and wrong for the one that genuinely takes twelve minutes: it was never going to
get a result, only a slower way of getting none. So this starts the suite the way
`start_process` starts a server — a handle it can still see — waits a short while for the
common case where it is done almost immediately, and if it is not, hands back "still running"
instead of blocking the turn on it. Calling this again re-attaches to the same run rather than
starting a second one, and once it finishes — whenever that is — the counts and failures come
back exactly as if it had been fast, because the parsing runs on the whole log either way.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kith.services.code.processes import ProcessError, processes

#: The name this claims in the background-process registry. Fixed, not derived from the
#: command: there is only ever one suite worth watching at a time, and a fixed name is what
#: makes calling `run` again find the same run instead of starting another.
_PROCESS_NAME = "run-tests"

#: How long the *first* call waits before handing control back. Long enough that a normal
#: suite finishes inside a single call and nothing about the caller's experience changes;
#: short enough that a slow one does not block the turn it arrived on.
WAIT = 20.0

#: The most any later call waits. A suite already known to be slow does not need checking
#: every `WAIT` seconds for however long it runs — that is a lot of round trips spent asking
#: "done yet?" for a ten-minute suite. Waiting longer each time, capped here, gets the same
#: total wait in far fewer of them.
MAX_WAIT = 60.0

#: Most failures to name individually. Past this the list stops being a thing to act on and
#: becomes the log again — and twelve failures are nearly always one cause.
MAX_FAILURES = 12

#: Cap on the raw log kept underneath the summary.
MAX_LOG = 4_000


class TestingError(Exception):
    """The suite could not be run, with a reason worth reading."""


@dataclass(frozen=True)
class Runner:
    name: str
    #: Files that mean this runner is the right one, checked in order.
    markers: tuple[str, ...]
    #: The command, with `{filter}` where a narrowing argument goes.
    command: str
    #: The command when nothing is being narrowed.
    plain: str


#: Ordered: the first whose marker is present wins. Python before Node because a repository
#: with both is usually a Python service with a web front end, and the tests worth running
#: from the root are the service's.
RUNNERS: tuple[Runner, ...] = (
    # No `-q`. It is tempting — the output is the problem this module exists for — and it is
    # how the first version was written, and it silently broke the counts on any project that
    # already sets `-q` in its own `addopts`. Two `-q`s is `-qq`, and `-qq` suppresses the
    # summary line that says how many passed. Kith's own suite is configured that way, so
    # running its 1,629 tests reported `ok: true` and no numbers at all.
    #
    # Verbosity is the project's business. `-rf` (name the failures) and `--tb=short` are
    # additive and safe to ask for on top of whatever it already does.
    Runner(
        "pytest",
        ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py"),
        "{python} -m pytest --tb=short -rf -p no:warnings {filter}",
        "{python} -m pytest --tb=short -rf -p no:warnings",
    ),
    Runner("go test", ("go.mod",), "go test ./... -run {filter}", "go test ./..."),
    Runner("cargo test", ("Cargo.toml",), "cargo test {filter}", "cargo test"),
    Runner("npm test", ("package.json",), "npm test --silent -- {filter}", "npm test --silent"),
)

#: Summary lines, per runner family. Enough regexes to cover what people actually use; a
#: runner nobody here has still reports honestly through the exit code.
_COUNTS: tuple[tuple[str, str], ...] = (
    (r"(\d+) failed", "failed"),
    (r"(\d+) passed", "passed"),
    (r"(\d+) skipped", "skipped"),
    (r"(\d+) error", "errors"),
    # jest / vitest: "Tests:  1 failed, 2 passed, 3 total"
    (r"Tests:\s+(?:(\d+) failed)", "failed"),
    # cargo: "test result: FAILED. 3 passed; 1 failed;"
    (r"(\d+) passed;", "passed"),
    (r"(\d+) failed;", "failed"),
)


def detect(root: Path) -> Runner | None:
    """Which runner this project uses, or None if nothing says."""
    for runner in RUNNERS:
        for marker in runner.markers:
            if not (root / marker).is_file():
                continue
            if marker == "pyproject.toml" and not _mentions_pytest(root / marker):
                continue
            if marker == "package.json" and not _has_test_script(root / marker):
                continue
            if marker == "setup.cfg" and "[tool:pytest]" not in _read(root / marker):
                continue
            return runner
        # A `tests/` folder with no config at all is still almost certainly pytest.
        if runner.name == "pytest" and (root / "tests").is_dir():
            return runner
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _mentions_pytest(path: Path) -> bool:
    text = _read(path)
    return "[tool.pytest" in text or "pytest" in text


def _has_test_script(path: Path) -> bool:
    """A `test` script that actually does something.

    `npm init` writes one that echoes "no test specified" and exits 1, and running it would
    report a failing test suite for a project that has none — the worst of both answers.
    """
    try:
        scripts = (json.loads(_read(path)) or {}).get("scripts") or {}
    except ValueError:
        return False
    script = str(scripts.get("test") or "")
    return bool(script) and "no test specified" not in script


#: Where a Python project keeps its interpreter. The same reasoning as finding a language
#: server in the project rather than on PATH, and the same reason: the project's own
#: environment is the one its tests are written against.
_PROJECT_PYTHONS = (
    ".venv/bin/python",
    "venv/bin/python",
    "env/bin/python",
    ".venv/Scripts/python.exe",
    "venv/Scripts/python.exe",
)


def python_for(root: Path) -> str:
    """The interpreter to run this project's tests with.

    Not the bare word `python`, which is what the first version used and which does not exist
    on this machine at all — macOS has shipped only `python3` for years, so every run died
    with `bash: python: command not found` before pytest was ever reached. And not the system
    `python3` either when the project has a virtualenv, because the system one is precisely
    the interpreter that does not have the project's dependencies in it.
    """
    for candidate in _PROJECT_PYTHONS:
        found = root / candidate
        if found.is_file():
            return str(found)
    return shutil.which("python3") or shutil.which("python") or "python3"


def _looks_like_a_path(text: str) -> bool:
    return "/" in text or text.endswith((".py", ".ts", ".tsx", ".js", ".go", ".rs"))


def _narrowed(runner: Runner, filter_: str, root: Path) -> str:
    """The command, narrowed to one file or one test name.

    Iterating on a single failure is the common case and running the whole suite for it is
    both slow and noisy. pytest takes a path directly and a name with `-k`, which is a
    distinction the caller should not have to know, so it is worked out here.
    """
    wanted = filter_.strip()
    template = runner.plain if not wanted else runner.command
    if wanted and runner.name == "pytest" and not _looks_like_a_path(wanted):
        wanted = f"-k {shlex.quote(wanted)}"
    return template.replace("{filter}", wanted).replace("{python}", shlex.quote(python_for(root))).strip()


def _counts(output: str) -> dict[str, int]:
    found: dict[str, int] = {}
    tail = output[-2_000:]  # summaries are at the end; a test *named* "failed" is not one
    for pattern, key in _COUNTS:
        match = re.search(pattern, tail)
        if match and key not in found:
            try:
                found[key] = int(match.group(1))
            except (TypeError, ValueError):
                continue
    return found


def _failures(output: str) -> list[str]:
    """The failing tests, named, without their tracebacks.

    `-rf` makes pytest print exactly this at the end, one per line. jest and vitest mark
    theirs with a cross, and go with `--- FAIL:`. Anything else falls through to the log.
    """
    found: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("FAILED ", "ERROR ")):
            found.append(stripped)
        elif stripped.startswith("--- FAIL:"):
            found.append(stripped[4:])
        elif stripped.startswith(("✕", "×", "✗")):
            found.append(stripped.lstrip("✕×✗ "))
        if len(found) >= MAX_FAILURES:
            break
    return found


def _not_installed(runner: Runner, output: str) -> str:
    """Is this "the runner isn't here" rather than "the tests failed"?"""
    text = output.lower()
    if "no module named pytest" in text:
        return (
            "pytest is not installed in the interpreter this project uses. `pip install "
            "pytest` in its environment, then try again."
        )
    if "command not found" in text or "is not recognized" in text:
        first = output.strip().splitlines()[0] if output.strip() else ""
        return f"{runner.name} could not be run here — {first or 'the command was not found'}."
    return ""


def run(path: str = ".", filter_: str = "", wait: float | None = None) -> dict[str, Any]:
    """Run the project's tests and report what failed — or that it is still running.

    A second call while the same run is still going re-attaches instead of starting another;
    a call naming a different path or filter while one is in flight is refused, because
    starting it would mean two suites racing over the same files with only one handle back.

    ``wait`` defaults to a schedule rather than a constant: the first call waits `WAIT`
    seconds, and a call that finds the suite already running longer than that waits longer
    still, up to `MAX_WAIT` — see `_next_wait`. Passing an explicit value (tests do, to stay
    fast) always wins over the schedule.
    """
    from kith.infra import workspace as sandbox

    root = Path(sandbox.resolve(path))
    if not root.is_dir():
        root = root.parent
    runner = detect(root)
    if runner is None:
        raise TestingError(
            f"Nothing in {path} says how this project's tests are run — no pytest config, no "
            "test script in package.json, no go.mod or Cargo.toml. If you know the command, "
            "run it with `shell`."
        )

    command = _narrowed(runner, filter_, root)
    full_command = f"cd {shlex.quote(str(root))} && {command}"

    # Checked before starting, rather than started-and-caught, so a *different* reason
    # `start` might refuse (the whole registry is full, say) surfaces as itself instead of
    # being misread as this same collision — both errors happen to say "already running".
    existing = _peek(_PROCESS_NAME)
    if existing is not None and existing.get("alive"):
        if existing.get("command") != full_command:
            raise TestingError(
                f"Already running a different test run ({existing.get('command')!r}, "
                f"started {existing.get('for')} ago). Wait for it to finish, or check it "
                f"with check_process('{_PROCESS_NAME}'), before starting another."
            ) from None
    else:
        try:
            processes.start(full_command, _PROCESS_NAME)
        except ProcessError as exc:
            raise TestingError(str(exc)) from None

    # Whether this call found the suite already going — i.e. this is the *second or later*
    # check, not the first. That distinction decides which note comes back: the first still-
    # running is nothing to worry about, but a second one means this is genuinely slow, and
    # sitting in this same turn checking on it again is worse than saying so and moving on.
    already_checked_once = existing is not None and existing.get("alive")

    if wait is None:
        wait = _next_wait(processes.elapsed(_PROCESS_NAME))
    state = _await(_PROCESS_NAME, wait)
    if state.get("alive"):
        if already_checked_once:
            note = (
                "Still running, and this is not the first check — it is genuinely slow, not "
                "just slower than instant. Sitting in this turn polling it further is the "
                "wrong move now: tell them it's running, set a reminder for a few minutes out "
                "to check back (`set_reminder`), and end this turn. Calling run_tests again "
                "later — this turn or a fresh one, it makes no difference — re-attaches to the "
                "same run and gets the real result whenever it finishes."
            )
        else:
            note = (
                "Still running — slower than a quick check, but not yet slow enough to call "
                "genuinely long. Calling run_tests again with the same path and filter, once "
                "more, is fine here — you'll know from the next response whether it's actually "
                "a long one or was just this close to done."
            )
        return {
            "ran": command,
            "runner": runner.name,
            "status": "running",
            "for": state.get("for"),
            "note": note,
        }

    output = processes.full_output(_PROCESS_NAME)
    passed = state.get("exitCode") == 0
    processes.stop(_PROCESS_NAME)  # done either way; free the name for the next run

    missing = _not_installed(runner, output)
    if missing:
        # Distinguished from a failing suite, because they call for opposite actions: one is
        # a bug to fix, the other is a thing to install. Reporting "tests failed" for a
        # missing runner is how you spend a round debugging code that is fine.
        raise TestingError(missing)

    counts = _counts(output)
    failures = _failures(output)
    # `ok` for the verdict, `passed` for the count of passing tests. They were both called
    # `passed` at first and the count silently overwrote the verdict, so an all-green run
    # reported `passed: 1` and a narrowed failing run reported `passed: False` — the same
    # field arriving as an int or a bool depending on what the suite did.
    result: dict[str, Any] = {
        "ran": command,
        "runner": runner.name,
        "ok": passed,
        **counts,
    }
    if failures:
        result["failures"] = failures
        if len(failures) >= MAX_FAILURES:
            result["note"] = f"only the first {MAX_FAILURES} failures are listed"
    if not passed:
        # The log is the fallback when the names are not enough to act on. Kept from the end,
        # which is where every runner puts the summary and the last traceback.
        result["log"] = output[-MAX_LOG:]
    elif not counts:
        # It exited zero and said nothing recognisable. Worth a look rather than a green tick:
        # a suite that collected no tests also exits zero on some runners.
        result["log"] = output[-800:]
    return result


def _next_wait(elapsed: float | None) -> float:
    """How long an unspecified call may wait, given how long the suite has already run.

    ``elapsed`` is `None` for a suite just started by this call — nothing to scale from, so
    the base `WAIT`. Otherwise the wait grows with how long it has already been running,
    capped at `MAX_WAIT`: a suite still going after `WAIT` seconds gets a longer look next
    time, and one still going well past that gets the longest look every time from then on,
    rather than a fixed twenty-second check repeated for as long as it runs.
    """
    if elapsed is None:
        return WAIT
    return min(MAX_WAIT, WAIT + elapsed)


def _await(name: str, wait: float) -> dict[str, Any]:
    """Poll a background run until it finishes or the wait runs out, whichever first."""
    deadline = time.monotonic() + wait
    state = processes.check(name)
    while state.get("alive") and time.monotonic() < deadline:
        time.sleep(0.3)
        state = processes.check(name)
    return state


def _peek(name: str) -> dict[str, Any] | None:
    """The registry's view of a named process, or None if it has never existed here.

    A read, not a claim — unlike `check`, this must not raise just because nothing by that
    name is running, since "nothing to see" is the expected answer the first time `run` is
    ever called.
    """
    try:
        return processes.check(name)
    except ProcessError:
        return None
