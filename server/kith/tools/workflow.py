"""`run_workflow`: a script Kith writes that fans work out to agents, run in the background.

**Why a script, and why only when asked.** The three tools this replaced — scout, builder,
follow-up — each left the decision to fan out to a choice made fresh on every round, and it was
never made: every scout in the record was sent right after being told to, and builders were
never sent at all, because the work was framed as one list to walk before delegation was ever
considered. So the person makes that call (a message starting ``/workflow``), and the model does
the part it is good at — writing the decomposition down as code: survey, `parallel` over the
parts, review. The shape is Claude Code's Workflow tool.

**The script only orchestrates.** It runs in its own process under a `sandbox-exec` profile — no
network, nothing under the home folder but its own run folder and the interpreter — and all it
can do is print a request for an agent. The agents run here, in the server, as ordinary workers
(`tools/workers`): a scout reads, a builder edits a private `git worktree` and hands back a diff.
If this machine cannot confine a process, this refuses rather than run model-written code open.

**In the background, and reported like a background process.** The call returns at once and
the turn ends. The run lives on a thread of its own with the conversation's context copied in;
when it ends it leaves a note on `services/workflows`, and the scheduler's timer wakes the
conversation with it — the same door a finished shell job uses. Nothing a builder produced is
applied: its diff is saved as a `.patch` beside the result, and the turn that wakes applies what
it wants. That is the only return path that still terminates with nobody watching.
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from kith import settings
from kith.domain.workflows import PREFIX
from kith.infra import confinement
from kith.infra.workspace import paths
from kith.kernel import session_context
from kith.services import workflows
from kith.tools import workers
from kith.tools.params import STR
from kith.tools.registry import tool

#: Agents running at once, across one run. The same six the old errand board allowed, for its
#: reason: these are live model streams with budgets of their own, not six searches.
AT_ONCE = 6

#: Agents one run may send in total. A script that loops over a directory listing can ask for
#: two hundred; thirty is a real fan-out and still a bill someone would recognise.
MAX_AGENTS = 30

#: Wall clock for a whole run. Generous, because builders are slow, and still an end: a run
#: nobody is watching must not be the thing that is still spending money tomorrow.
MAX_SECONDS = 60 * 60

#: How much of the result rides in the note that wakes the conversation. The rest is one read
#: away in `result.json`, and the note says so.
RESULT_PREVIEW_CHARS = 6_000

#: The helper a script runs inside — see `server/workflow/kith_workflow.py`. A data file beside
#: persona and skills rather than a module in this package, because it is copied into the run
#: folder and read by a separate interpreter, and a frozen build keeps package modules only as
#: bytecode inside its archive.
HELPER = "kith_workflow.py"

DESCRIPTION = (
    f"Run a workflow: a short Python script that fans work out to agents and runs in the "
    f"background. ONLY when the person's message starts with {PREFIX} — otherwise this refuses. "
    "Write the decomposition as code: survey first if you need to, then parallel() over the "
    "parts, then review. It returns at once with a run id; end your turn by saying what you "
    "started. When it finishes you get a new turn with the result, one line per agent, and a "
    ".patch file per builder. Builders never touch the real tree: you apply their patches "
    "yourself with `git apply`, check they fit together, and run the tests.\n\n"
    "The script is plain Python; these are globals, no imports needed:\n"
    'META = {"name": "short-name", "description": "one line", "phases": ["Survey", "Build", '
    '"Review"]}  — required.\n'
    'agent(prompt, kind="scout"|"builder", label=None, schema=None) — scout: reads and searches, '
    'returns its report (str). builder: edits a private copy of the repo, returns {"report", '
    '"diff", "patch_file", "label"}. With schema (e.g. {"files": ["str"]}) the report comes back '
    "as that JSON. Every agent starts knowing NOTHING about this conversation: each prompt must "
    "stand alone — paths, names, what done looks like. Raises AgentFailed.\n"
    "Every agent starts from the repo AS IT IS, not from earlier builders' edits. To build on "
    "them or review them, pass after=[built1, built2]: the agent starts from a copy with those "
    "patches applied.\n"
    "parallel([fn, ...]) — results in order, None where one failed. In a loop bind the variable: "
    '[lambda p=p: agent(f"... {p}") for p in parts].\n'
    "pipeline(items, stage1, stage2, ...) — each item through the stages on its own; a stage "
    "returning None drops it.\n"
    'with phase("Build"): ...   log(text)   finish(value) — value is the result you are handed.\n\n'
    f"The script has no network and no files beyond its own folder; it only orchestrates. "
    f"Limits: {AT_ONCE} agents at once, {MAX_AGENTS} per run, {MAX_SECONDS // 60} minutes. "
    "Builders cannot run commands or tests."
)


@tool(
    "run_workflow",
    DESCRIPTION,
    {"script": {**STR, "description": "The whole workflow script, starting with META."}},
    required=("script",),
)
def run_workflow(path: Path, args: dict):
    if not session_context.workflows_allowed():
        return {
            "error": (
                f"Workflows run only when the person asks for one — their message starts with "
                f"{PREFIX}. Do the work in this turn instead, or suggest they send it as a "
                f"{PREFIX} request if it really wants fanning out."
            )
        }
    script = str(args.get("script") or "")
    meta, problem = read_meta(script)
    if problem:
        return {"error": problem}
    if not confinement.available():
        return {
            "error": (
                "This machine cannot confine a subprocess, so a workflow script would run with "
                "your full access. Refused rather than run model-written code open — do the "
                "work in this turn instead."
            )
        }

    conversation = session_context.current()
    run = workflows.start(conversation, meta["name"], meta["description"], meta["phases"], folder="")
    if run is None:
        busy = workflows.running(conversation)
        return {
            "error": (
                f"A workflow is already running here ({busy.name if busy else 'another'}). Wait "
                "for it to report, or ask them to stop it from the Work panel."
            )
        }

    folder = paths.base_dir() / ".kith" / "scratch" / "workflows" / run.id
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "tmp").mkdir(exist_ok=True)
        (folder / "script.py").write_text(script, encoding="utf-8")
        shutil.copyfile(settings.SERVER_ROOT / "workflow" / HELPER, folder / HELPER)
    except OSError as exc:
        workflows.end(run, workflows.FAILED, f"The workflow `{run.name}` could not start: {exc}")
        return {"error": f"Could not set up the run folder: {exc}"}
    run.folder = str(folder)

    # The turn's context travels with the run — which conversation, which project — because every
    # agent resolves its paths and its permissions through it. Copied here, on the thread that has
    # it; a thread started bare begins on the defaults and would read the wrong folder.
    carried = copy_context()
    threading.Thread(
        target=carried.run,
        args=(_drive, run, path),
        name=f"workflow-{run.id}",
        daemon=True,
    ).start()
    return {
        "run": run.id,
        "name": run.name,
        "phases": run.phases,
        "folder": str(folder),
        "note": (
            "Running in the background. End this turn now: say what you started and what each "
            "phase does. You will get a new turn with the result when it finishes."
        ),
    }


def read_meta(script: str) -> tuple[dict, str]:
    """``META`` out of a script without running it, or a sentence saying what is wrong.

    Parsed and compiled here, in the turn, so a syntax error comes back as the tool result and
    is fixed in the same turn — not discovered by a background run nobody is watching.
    """
    if not script.strip():
        return {}, "The script is empty. Write the workflow, starting with META."
    try:
        tree = ast.parse(script, filename="script.py")
        compile(tree, "script.py", "exec")
    except SyntaxError as exc:
        return {}, f"The script does not parse — line {exc.lineno}: {exc.msg}. Fix it and call again."
    meta = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "META" for target in node.targets
        ):
            try:
                meta = ast.literal_eval(node.value)
            except ValueError:
                return {}, "META must be a literal dict — plain strings and lists, nothing computed."
    if not isinstance(meta, dict) or not str(meta.get("name") or "").strip():
        return {}, 'Start the script with META = {"name": ..., "description": ..., "phases": [...]}.'
    phases = meta.get("phases") or []
    if not isinstance(phases, list):
        phases = []
    return {
        "name": str(meta["name"]).strip()[:60],
        "description": str(meta.get("description") or "").strip()[:240],
        "phases": [str(one).strip()[:40] for one in phases if str(one).strip()][:12],
    }, ""


class _Agents:
    """What the run's agents did, for the closing note and the cap."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sent = 0
        self.lines: list[str] = []
        self.cost = 0.0

    def take_slot(self) -> bool:
        with self.lock:
            if self.sent >= MAX_AGENTS:
                return False
            self.sent += 1
            return True

    def record(self, line: str, cost: float) -> None:
        with self.lock:
            self.lines.append(line)
            self.cost += cost


def _drive(run: workflows.Run, path: Path) -> None:
    """The whole run, on its own thread: launch the script, answer it, and close the books.

    Under `nobody_watching`: an agent has no `ask`, and a permission prompt raised mid-run
    would be put to nobody. With it set, anything that would prompt refuses at once instead.
    """
    with session_context.nobody_watching():
        folder = Path(run.folder)
        began = time.monotonic()
        agents = _Agents()
        state, why = workflows.FINISHED, ""
        result = None
        told_error = ""
        timed_out = threading.Event()
        pool = ThreadPoolExecutor(max_workers=AT_ONCE, thread_name_prefix=f"{run.id}-agent")
        writing = threading.Lock()
        log_file = open(folder / "run.log", "w", encoding="utf-8")  # noqa: SIM115 - closed below
        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(
                confinement_argv(run, folder),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_file,
                cwd=str(folder),
                env=_environment(folder),
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            over = threading.Event()
            threading.Thread(
                target=_watchdog, args=(run, process, timed_out, over), name=f"{run.id}-watch", daemon=True
            ).start()

            def reply(message: dict) -> None:
                with writing:
                    try:
                        assert process is not None and process.stdin is not None
                        process.stdin.write(json.dumps(message, default=str) + "\n")
                        process.stdin.flush()
                    except (BrokenPipeError, ValueError, OSError):
                        pass  # the script is gone; nobody is waiting for this any more

            assert process.stdout is not None
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                op = message.get("op")
                if op == "agent":
                    ident = message.get("id")
                    if run.halt.is_set():
                        reply({"id": ident, "ok": False, "error": "the workflow was stopped"})
                    elif not agents.take_slot():
                        reply(
                            {"id": ident, "ok": False, "error": f"this run has sent its {MAX_AGENTS} agents"}
                        )
                    else:
                        pool.submit(copy_context().run, _answer, run, path, message, agents, reply)
                elif op == "phase":
                    workflows.say(run, "phase", str(message.get("title") or ""))
                elif op == "log":
                    workflows.say(run, "log", str(message.get("text") or "")[:300])
                elif op == "finish":
                    result = message.get("value")
                elif op == "error":
                    told_error = str(message.get("text") or "")
            code = process.wait()
            over.set()
            stopped = run.halt.is_set()
            if code != 0:
                # A script that died leaves its agents talking to nobody. Stop them rather than
                # pay for answers no line of the script is still waiting to read.
                run.halt.set()
            pool.shutdown(wait=True, cancel_futures=True)
            if timed_out.is_set():
                state, why = workflows.FAILED, f"it ran past its {MAX_SECONDS // 60} minutes and was ended"
            elif stopped:
                state, why = workflows.STOPPED, "it was stopped from the Work panel"
            elif code != 0:
                state, why = workflows.FAILED, f"the script exited with code {code}"
        except Exception as exc:
            state, why = workflows.FAILED, f"the run broke: {exc}"
            run.halt.set()
            if process is not None and process.poll() is None:
                process.kill()
            pool.shutdown(wait=True, cancel_futures=True)
        finally:
            log_file.close()
            if process is not None:
                # Closed here, deliberately, rather than left to the garbage collector: a reply
                # buffered for a script that has already died would otherwise surface later as
                # a BrokenPipeError raised from nowhere in particular.
                for stream in (process.stdin, process.stdout):
                    with contextlib.suppress(Exception):
                        if stream is not None:
                            stream.close()

        if result is not None:
            try:
                (folder / "result.json").write_text(
                    json.dumps(result, indent=2, default=str), encoding="utf-8"
                )
            except OSError:
                pass
        note = _closing_note(run, state, why, result, agents, time.monotonic() - began, told_error)
        workflows.end(run, state, note)


def _watchdog(
    run: workflows.Run, process: subprocess.Popen, timed_out: threading.Event, over: threading.Event
) -> None:
    """End the script on Stop or when the clock runs out. Agents see the same switch.

    Polls rather than waiting on the halt switch alone, so a run that simply finishes does not
    leave this thread parked for an hour.
    """
    deadline = time.monotonic() + MAX_SECONDS
    while not over.wait(0.25):
        if not run.halt.is_set() and time.monotonic() >= deadline:
            timed_out.set()
            run.halt.set()
        if run.halt.is_set():
            if process.poll() is None:
                process.kill()
            return


def _answer(run: workflows.Run, path: Path, message: dict, agents: _Agents, reply) -> None:
    """Run one agent the script asked for and send its answer back. Never raises."""
    ident = message.get("id")
    try:
        value = _one_agent(run, path, message, agents)
        reply({"id": ident, "ok": True, "value": value})
    except _Failed as exc:
        reply({"id": ident, "ok": False, "error": str(exc)})
    except Exception as exc:
        # Recorded as well as answered: the script may swallow the failure, and the note is the
        # only place the person would otherwise learn an agent went down.
        kind = str(message.get("kind") or "scout")
        label = (
            str(message.get("label") or "").strip() or " ".join(str(message.get("prompt") or "").split())[:60]
        )
        agents.record(f'- {kind} "{label}" — broke: {exc}', 0.0)
        reply({"id": ident, "ok": False, "error": f"the agent broke: {exc}"})


class _Failed(Exception):
    pass


def _one_agent(run: workflows.Run, path: Path, message: dict, agents: _Agents):
    kind = str(message.get("kind") or "scout")
    if kind not in workers.ROLES:
        raise _Failed(f"there is no {kind!r} kind of agent — use 'scout' or 'builder'")
    prompt = str(message.get("prompt") or "").strip()
    if not prompt:
        raise _Failed("an agent needs a prompt")
    schema = message.get("schema")
    agent_id = uuid.uuid4().hex[:6]
    label = str(message.get("label") or "").strip() or " ".join(prompt.split())[:60]
    objective = prompt if schema is None else prompt + _asking_for(schema)

    def mark(state: str, text: str, tool_name: str = "", arguments: dict | None = None) -> None:
        try:
            from kith.services.activity import feed, short_args

            feed.publish(
                "errand",
                text,
                tool=tool_name or None,
                args=short_args(arguments or {}) or None,
                errand={"id": agent_id, "state": state, "objective": label, "role": kind, "run": run.id},
                conversation=run.conversation,
            )
        except Exception:
            pass

    def watch(event: dict) -> None:
        if event.get("type") == "tool_call":
            name = str(event.get("name") or "")
            mark("step", name, tool_name=name, arguments=event.get("arguments") or {})

    # What earlier builders made, if this agent was sent `after` them. Any kind may be: a review
    # is a scout that has to read code which so far exists only as patches.
    earlier = [
        one
        for one in (message.get("after") or [])
        if isinstance(one, dict) and str(one.get("diff") or "").strip()
    ]
    on_top_of = [str(one.get("label") or "").strip() or "an earlier patch" for one in earlier]

    worktree = ""
    if kind == "builder" or earlier:
        try:
            worktree = workers.open_copy(f"{run.id}-{agent_id}")
        except workers.WorkerError as exc:
            agents.record(f'- {kind} "{label}" — could not start: {exc}', 0.0)
            raise _Failed(str(exc)) from None
        if earlier:
            from kith.infra.workspace import worktrees

            refused = worktrees.apply_in(Path(worktree), [str(one["diff"]) for one in earlier])
            if refused:
                worktrees.close(Path(worktree))
                agents.record(f'- {kind} "{label}" — could not start: {refused}', 0.0)
                raise _Failed(f"the earlier patches do not apply together — {refused}")

    mark("running", label)
    try:
        answer, report = workers.run_agent(
            path,
            kind,
            objective,
            run.conversation,
            worktree=worktree,
            give_up=run.halt.is_set,
            watch=watch,
        )
        said = str(answer.get("findings") or "")
        cost = report.cost
        if report.cut_short or (report.error and not (report.text.strip() or report.said_before.strip())):
            why = "stopped" if report.cut_short else report.error
            mark("done", f"failed: {why}"[:120])
            agents.record(f'- {kind} "{label}" — failed: {why}', cost)
            raise _Failed(f"the agent did not finish: {why}")

        shaped = said
        if schema is not None:
            shaped, extra = _parse_or_repair(path, kind, objective, said, schema, run)
            cost += extra
            if shaped is None:
                mark("done", "no JSON in its report")
                agents.record(f'- {kind} "{label}" — reported, but not as the JSON asked for', cost)
                raise _Failed("the agent's report did not end with the JSON the schema asked for")

        tally = str(answer.get("looked_at") or "")
        if kind == "scout":
            mark("done", tally or "reported")
            agents.record(f'- scout "{label}" — reported ({tally or "no calls"})', cost)
            return shaped

        diff = str(answer.get("patch") or "")
        patch_file = ""
        if diff.strip():
            patch = Path(run.folder) / f"{_slug(label)}-{agent_id}.patch"
            patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8")
            patch_file = str(patch)
            added, removed = _diffstat(diff)
            outcome = f"patch {patch_file} (+{added} −{removed})"
            if on_top_of:
                # Said, because order now matters: this patch was made against a tree with
                # those applied, so it goes on after them.
                outcome += f", made on top of {', '.join(on_top_of)} — apply those first"
        else:
            outcome = "changed nothing"
        mark("done", tally or outcome)
        agents.record(f'- builder "{label}" — {outcome}', cost)
        return {"report": shaped, "diff": diff, "patch_file": patch_file, "label": label}
    finally:
        if worktree:
            from kith.infra.workspace import worktrees

            worktrees.close(Path(worktree))


def _asking_for(schema) -> str:
    return (
        "\n\nEnd your report with ONE JSON value matching this shape, in a ```json fence, with "
        f"nothing after it:\n{json.dumps(schema, default=str)}"
    )


def _parse_or_repair(path: Path, kind: str, objective: str, said: str, schema, run: workflows.Run):
    """The JSON the agent was asked to end with, asking it once more if it did not. ``(value, cost)``."""
    found = last_json(said, schema)
    if found is not None:
        return found, 0.0
    if run.halt.is_set():
        return None, 0.0
    ask = (
        "Your report did not end with the JSON value asked for. Reply with ONLY that value, "
        f"matching this shape, and nothing else:\n{json.dumps(schema, default=str)}"
    )
    answer, report = workers.run_agent(
        path,
        kind,
        objective,
        run.conversation,
        carried=workers.follow_on(kind, objective, said, ask),
        tools=set(),
        rounds=1,
        give_up=run.halt.is_set,
    )
    return last_json(str(answer.get("findings") or ""), schema), report.cost


def last_json(text: str, schema=None):
    """The last top-level JSON value in ``text`` of the schema's type, or None.

    Fenced blocks first, then a scan: each opening brace is tried with ``raw_decode`` and a
    success skips past everything it consumed, so an object nested inside another is never
    mistaken for the answer. The last value standing is the report's conclusion.
    """
    wanted: type | tuple[type, ...] = (dict, list)
    if isinstance(schema, dict):
        wanted = dict
    elif isinstance(schema, list):
        wanted = list
    for block in reversed(re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)):
        try:
            value = json.loads(block.strip())
        except ValueError:
            continue
        if isinstance(value, wanted):
            return value
    decoder = json.JSONDecoder()
    found = None
    index = 0
    text = text or ""
    while index < len(text):
        if text[index] in "{[":
            try:
                value, end = decoder.raw_decode(text, index)
            except ValueError:
                index += 1
                continue
            if isinstance(value, wanted):
                found = value
            index = end
        else:
            index += 1
    return found


def confinement_argv(run: workflows.Run, folder: Path) -> list[str]:
    """The command that runs the script inside its boundary.

    The server's own interpreter, so there is nothing to install and the packaged app works the
    same: a checkout runs ``python -E -s -B`` (no environment, no user site, no bytecode), and a
    frozen build runs itself with ``--run-workflow``, which `app.py` hands straight to the helper
    before importing anything of Kith's.
    """
    if settings.FROZEN:
        command = [sys.executable, "--run-workflow", HELPER, "script.py"]
    else:
        command = [sys.executable, "-E", "-s", "-B", HELPER, "script.py"]
    profile_path = _write_profile(run, folder)
    return [confinement.SANDBOX_EXEC, "-f", str(profile_path), *command]


def _write_profile(run: workflows.Run, folder: Path) -> Path:
    """No network; nothing under the home folder or Kith's data but the run folder and the runtime.

    Built with the plugin boundary's own profile writer, which carries every lesson that file
    records — each deny carries its exceptions, metadata stays readable, the working directory is
    load-bearing. The interpreter's trees are granted for reading because a program that cannot
    read itself cannot run: a checkout's virtualenv lives under the home folder.

    Kept outside the run folder, which the script can write: a boundary the confined program
    could rewrite is not one, even for a single run.
    """
    runtime = [Path(sys.prefix), Path(sys.base_prefix), Path(sys.executable).parent]
    if settings.FROZEN:
        runtime.append(Path(settings.SERVER_ROOT))
    reach = confinement.Resolved(
        read=tuple(confinement._real(one) for one in runtime),
        write=(),
        network=False,
    )
    text = confinement.profile(reach, folder, runtime=sys.executable)
    place = Path(settings.DATA_DIR) / "workflow-profiles"
    place.mkdir(parents=True, exist_ok=True)
    profile_path = place / f"{run.id}.sb"
    profile_path.write_text(text, encoding="utf-8")
    return profile_path


def _environment(folder: Path) -> dict[str, str]:
    """A constructed environment, never the server's: no keys, no tokens, nothing inherited."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(folder),
        "TMPDIR": str(folder / "tmp"),
        "LANG": "en_US.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _closing_note(
    run: workflows.Run,
    state: str,
    why: str,
    result,
    agents: _Agents,
    seconds: float,
    told_error: str,
) -> str:
    """What the conversation wakes up to: how it ended, what came back, and where the patches are."""
    minutes, rest = divmod(int(seconds), 60)
    took = f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"
    how = {
        workflows.FINISHED: "finished",
        workflows.STOPPED: "was stopped",
        workflows.FAILED: "failed",
    }.get(state, state)
    count = len(agents.lines)
    head = (
        f"The workflow `{run.name}` ({run.id}) {how} after {took} — {count} "
        f"agent{'s' if count != 1 else ''}, ${agents.cost:.2f}."
    )
    if why and state != workflows.FINISHED:
        head += f" Why: {why}."
    parts = [head]
    if result is not None:
        shown = json.dumps(result, indent=1, default=str)
        if len(shown) > RESULT_PREVIEW_CHARS:
            shown = (
                shown[:RESULT_PREVIEW_CHARS] + f"\n… (cut — the whole result is in {run.folder}/result.json)"
            )
        parts.append(f"Result:\n{shown}")
    elif state == workflows.FINISHED:
        parts.append("The script never called finish(), so there is no result beyond the agents below.")
    if agents.lines:
        parts.append("Agents:\n" + "\n".join(agents.lines))
    if told_error or (state == workflows.FAILED and not agents.lines):
        tail = told_error or _log_tail(Path(run.folder) / "run.log")
        if tail:
            parts.append(f"The script's last words:\n{tail[-1500:]}")
    if any(".patch" in line for line in agents.lines):
        parts.append(
            "Nothing has been applied. Read the patches, `git apply` the ones you want from the "
            "project root, check they fit together, and run the tests."
        )
    parts.append(f"Everything the run wrote is in {run.folder}.")
    return "\n\n".join(parts)


def _log_tail(log: Path) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")[-1500:].strip()
    except OSError:
        return ""


def _slug(label: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.") or "builder")[:40]


def _diffstat(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed
