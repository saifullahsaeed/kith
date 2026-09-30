"""`run_workflow`: a script Kith writes that fans work out to agents, run in the background.

**Why a script, and why only when asked.** The three tools this replaced — scout, builder,
follow-up — each left the decision to fan out to a choice made fresh on every round, and it was
never made: every scout in the record was sent right after being told to, and builders were
never sent at all, because the work was framed as one list to walk before delegation was ever
considered. So the person makes that call (a message starting ``/workflow``, or workflow mode on
in the chat), and the model does the part it is good at — writing the decomposition down as code:
survey, `parallel` over the parts, merge, verify. The shape is Claude Code's Workflow tool.

**Saved workflows, because the scripts were where runs broke.** Of the first seven real runs,
four had a logic bug in a script written fresh that turn — a crash on ``with parallel(...)``, an
agent sent another agent's report as its prompt, reviews run on the unchanged tree — and three
more did not parse. So the shapes that work are kept as tested scripts in `workflow/library/`
(and a project's own in `.kith/workflows/`), run by name with ``args``; and every script, saved
or not, is rehearsed first against stand-in agents (`kith_workflow._Rehearsal`), so a crash costs
a second instead of a run.

**The script only orchestrates.** It runs in its own process under a `sandbox-exec` profile — no
network, nothing under the home folder but its own run folder and the interpreter — and all it
can do is ask for an agent, a merge or a revision. Those run here, in the server, as ordinary
workers (`tools/workers`): a scout reads, a builder edits a private `git worktree` and hands back
a diff, a verifier checks a change against what the person asked. If this machine cannot confine
a process, this refuses rather than run model-written code open.

**Putting the work together happens here, not in the turn that wakes.** `merge()` applies the
builders' patches in one copy and runs the project's check and tests there, so what reaches the
conversation is one patch that has been checked, or an exact list of what does not fit. Until
then that was left to the woken main turn, on the chat model, often hundreds of thousands of
tokens into its context — the hardest step in the pipeline under the worst conditions.

**In the background, and reported like a background process.** The call returns at once and
the turn ends — unless ``wait`` is set, for a short run whose answer the turn wants now. The run
lives on a thread of its own with the conversation's context copied in; when it ends it leaves a
note on `services/workflows`, and the scheduler's timer wakes the conversation with it. Nothing a
builder produced is applied to the real tree: its diff is saved as a `.patch`, and the turn that
wakes applies what it wants. That is the only return path that still terminates with nobody
watching.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
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
from kith.kernel import session_context, stopping
from kith.services import workflows
from kith.tools import workers
from kith.tools.params import BOOL, STR
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

#: What a run may spend when its META does not say, and the most it may say. A hard ceiling:
#: past it, new agents are refused and running ones stop at their next step. The largest of the
#: first seven real runs cost $1.31, so the default is room for a real build and not for a loop.
DEFAULT_BUDGET_USD = 5.0
MAX_BUDGET_USD = 50.0

#: How many times one run may be resumed without the person asking. Each failed resume wakes
#: him again, so without a bound a run that keeps breaking is a loop with a bill.
MAX_RESUMES = 3

#: How many new runs may start in a row with nobody there, in workflow mode. A survey, a build
#: and a review is a chain of three, and the second and third start in the turn the one before
#: wakes. Past that it has to reach a person: a mode left on must not chain runs overnight.
MAX_UNWATCHED_RUNS = 3

#: How much of the result rides in the note that wakes the conversation. The rest is one read
#: away in `result.json`, and the note says so.
RESULT_PREVIEW_CHARS = 6_000

#: How much of an agent's report the board keeps for the agent map and `check_workflow`. The
#: whole report is kept on disk in the run's `agents/` folder; this is only what is drawn.
REPORT_ON_BOARD = 6_000

#: How long ``wait=true`` holds the turn before handing back "still running". A short scout
#: run is back in well under this; a build is not, and belongs in the background.
WAIT_SECONDS = 240

#: How long a rehearsal may take. Stand-in agents answer instantly, so a script that is still
#: going after this is looping on its agents' answers, which stand-ins cannot drive to an end.
REHEARSAL_SECONDS = 20

#: How much of the person's own words rides into a run for verifiers and `ASKED`.
THEIR_WORDS_CHARS = 6_000

#: The helper a script runs inside — see `server/workflow/kith_workflow.py`. A data file beside
#: persona and skills rather than a module in this package, because it is copied into the run
#: folder and read by a separate interpreter, and a frozen build keeps package modules only as
#: bytecode inside its archive.
HELPER = "kith_workflow.py"

#: Where saved workflows live, inside the server's `workflow/` folder and inside a project.
LIBRARY = "library"
PROJECT_WORKFLOWS = (".kith", "workflows")

_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")


def read_meta(script: str) -> tuple[dict, str]:
    """``META`` out of a script without running it, or a sentence saying what is wrong.

    Parsed and compiled here, in the turn, so a syntax error comes back as the tool result and
    is fixed in the same turn — not discovered by a background run nobody is watching.
    """
    if not script.strip():
        return {}, "The script is empty. Write the workflow, starting with META — or run a saved one by name."
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
    try:
        budget = float(meta.get("budget_usd") or DEFAULT_BUDGET_USD)
    except (TypeError, ValueError):
        return {}, "META's budget_usd must be a number of dollars."
    if budget <= 0 or budget > MAX_BUDGET_USD:
        return {}, f"META's budget_usd must be more than 0 and at most {MAX_BUDGET_USD:g}."
    return {
        "name": str(meta["name"]).strip()[:60],
        "description": str(meta.get("description") or "").strip()[:240],
        "phases": [str(one).strip()[:40] for one in phases if str(one).strip()][:12],
        "budget_usd": budget,
        "when_to_use": str(meta.get("when_to_use") or "").strip()[:240],
        "args": str(meta.get("args") or "").strip()[:240],
    }, ""


def shipped_library() -> Path:
    return Path(settings.SERVER_ROOT) / "workflow" / LIBRARY


def _library_folders() -> list[Path]:
    """Where saved workflows are looked for, the project's own first so it can override one."""
    return [paths.base_dir().joinpath(*PROJECT_WORKFLOWS), shipped_library()]


def saved(name: str) -> Path | None:
    """The file of the saved workflow called ``name``, or None."""
    wanted = str(name or "").strip()
    if not _NAME.fullmatch(wanted):
        return None
    for folder in _library_folders():
        found = folder / f"{wanted}.py"
        if found.is_file():
            return found
    return None


def _listing() -> str:
    """The shipped saved workflows, one line each, for the tool description."""
    lines = []
    folder = shipped_library()
    for script in sorted(folder.glob("*.py")) if folder.is_dir() else []:
        try:
            meta, problem = read_meta(script.read_text(encoding="utf-8"))
        except OSError:
            continue
        if problem:
            continue
        line = f"- {script.stem}: {meta['description']}"
        if meta.get("when_to_use"):
            line += f" Use when {meta['when_to_use']}"
        if meta.get("args"):
            line += f" args: {meta['args']}"
        lines.append(line)
    return "\n".join(lines)


DESCRIPTION = (
    f"Run a workflow: agents fanned out by a short Python script, in the background. Allowed when "
    f"their message starts with {PREFIX} or workflow mode is on in this chat (your context says "
    "which) — otherwise this refuses. Only worth it when the work really splits; do small work "
    "yourself.\n\n"
    "Prefer a saved workflow — tested scripts, run by name with args: run_workflow(name=..., "
    "args={...}). A project may keep its own in .kith/workflows/. Shipped:\n"
    "{listing}\n\n"
    "Or write one (script=...). It is rehearsed against stand-in agents before it starts, so a "
    "crash comes back here at no cost. It returns at once with a run id; end your turn saying "
    "what you started — or pass wait=true for a short scout-only run whose answer you want in "
    "this turn. When it finishes you get a new turn with the result. Fix and resume a failed or "
    "stopped run with resume=<run id>: agents asked the same thing are reused instantly.\n\n"
    "Script globals, no imports needed:\n"
    'META = {"name": "short-name", "description": "one line", "phases": ["Survey", "Build", '
    '"Verify"], "budget_usd": 5}  — required; budget_usd is a hard ceiling (default '
    f"{DEFAULT_BUDGET_USD:g}).\n"
    'agent(prompt, kind="scout"|"builder"|"verifier", label=None, schema=None, after=None, '
    "model=None, effort=None) — scout: reads and reports (str). builder: edits a private copy of "
    "the repo and can run check_code and run_tests there; returns {report, diff, patch_file, "
    "label, agent}. verifier: checks a change against what the person asked — it is given their "
    "words automatically — and runs the tests; send it after=[the change]. schema (e.g. "
    '{"files": ["str"]}) makes the agent hand back that value through a checked submit call. '
    'model: "strong", "fast" or "main" (defaults: scout fast, builder main, verifier strong). '
    "Every agent knows the project (its notes are given to all of them) but nothing of this "
    "conversation: each prompt names the files, the change and what done looks like. Every "
    "agent starts from the repo AS IT IS; after=[built, ...] starts it from a copy with those "
    "patches applied. Raises AgentFailed.\n"
    "merge(*built, label=None, check=True, tests=False) — the patches applied together in one "
    "copy, the project check (and tests) run there: {diff, patch_file, applied, conflicts, check, "
    "tests, clean, agent}. Use it before verifying, and hand the woken turn its patch.\n"
    "revise(built, feedback) — the builder (or merge) goes back into its copy, edits intact, with "
    "your feedback; returns the whole diff so far. For an empty or half-done patch, a seam, or "
    "what a check or verifier found.\n"
    "parallel([fn, ...]) — results in order, None where one failed; bind loop variables: "
    "[lambda p=p: agent(...) for p in parts]. pipeline(items, stage1, stage2, ...) — each item "
    "through the stages on its own (a stage may take (value, item, index)); None drops it.\n"
    "workflow(name, args) — run a saved workflow as one step. ARGS — the args passed in. ASKED — "
    'the person\'s own recent words. budget.remaining() — dollars left. with phase("Build"): ...  '
    "log(text)  finish(value) — value is the result you are handed.\n\n"
    "Build shape that works: survey what you need to name the parts; one builder writes the "
    "shared contract (types, signatures, stubs) first and the rest run after=[contract], so "
    "they cannot disagree on an interface; merge(); a verifier after=[merged]; revise() what it "
    "finds. Size a builder to a few files — one screen, one endpoint — and name them. Never "
    "wrap agent() around something that already returns an agent's report.\n\n"
    f"Limits: {AT_ONCE} agents at once, {MAX_AGENTS} per run, {MAX_SECONDS // 60} minutes. The "
    "script has no network and no files beyond its folder. Builders never touch the real tree: "
    "you apply the merged patch with `git apply` and run the tests."
)


def _description() -> str:
    return DESCRIPTION.replace("{listing}", _listing() or "(none)")


@tool(
    "run_workflow",
    _description(),
    {
        "name": {**STR, "description": "A saved workflow to run, instead of writing a script."},
        "args": {
            "type": "object",
            "description": "What the saved workflow asks for (its args), or anything your script reads as ARGS.",
            "additionalProperties": True,
        },
        "script": {**STR, "description": "The whole workflow script, starting with META."},
        "resume": {
            **STR,
            "description": (
                "A run id from this chat to fix and resume. Its finished agents are reused where "
                "the script asks them the same thing. Leave script empty to rerun it unchanged."
            ),
        },
        "wait": {
            **BOOL,
            "description": (
                f"Hold this turn until it finishes (up to {WAIT_SECONDS // 60} minutes) and get the "
                "result here. For a short scout-only run; a build belongs in the background."
            ),
        },
        "rehearse": {
            **BOOL,
            "description": (
                "Default true. Pass false only when the rehearsal failed purely because a stand-in "
                "report is not what a real one would be."
            ),
        },
    },
    required=(),
)
def run_workflow(path: Path, args: dict):
    conversation = session_context.current()
    resume = str(args.get("resume") or "").strip()
    earlier = _earlier_run(resume, conversation) if resume else None
    if resume and earlier is None:
        return {"error": f"There is no run {resume} from this chat to resume."}
    # Resuming a run this chat already started needs no fresh opt-in: the person agreed to that
    # run, and fixing it is finishing what they agreed to — including in the turn that wakes
    # when it breaks, which nobody typed a prefix into. Bounded, because that same turn wakes
    # again if the fix fails too: a run that keeps breaking must reach a person, not loop.
    if (
        earlier is not None
        and earlier["depth"] >= MAX_RESUMES
        and session_context.workflow_permission() != "asked"
    ):
        return {
            "error": (
                f"{resume} has already been resumed {earlier['depth']} times. Stop here and tell them "
                "what keeps failing — a fourth try should be their call."
            )
        }
    unwatched = session_context.unattended()
    if earlier is None:
        refused = new_run_refusal(conversation, unwatched)
        if refused:
            return {"error": refused}
    elif not earlier["script"].strip():
        return {
            "error": (
                f"{resume} is a step-by-step run, not a script. Continue it with "
                f'add_agents(run="{resume}", agents=[...]).'
            )
        }

    name = str(args.get("name") or "").strip()
    script = str(args.get("script") or "")
    handed = args.get("args")
    if name and not script.strip():
        found = saved(name)
        if found is None:
            return {"error": f"There is no saved workflow called {name!r}. Shipped: {_names() or 'none'}."}
        script = found.read_text(encoding="utf-8")
    if not script.strip() and earlier is not None:
        script = earlier["script"]
        if handed is None:
            handed = earlier.get("args")
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

    inputs = {"args": handed, "asked": _their_words(conversation), "budget": meta["budget_usd"]}
    warnings: list[str] = []
    if args.get("rehearse") is not False:
        refused, warnings = rehearse(script, inputs)
        if refused:
            return {"error": refused, **({"warnings": warnings} if warnings else {})}

    run = workflows.start(
        conversation, meta["name"], meta["description"], meta["phases"], folder="", unwatched=unwatched
    )
    if run is None:
        busy = workflows.running(conversation)
        return {
            "error": (
                f"A workflow is already running here ({busy.name if busy else 'another'}, "
                f"{busy.id if busy else ''}). check_workflow shows where it is; stop_workflow ends it."
            )
        }
    if earlier is not None:
        run.resumed_from = resume
        run.reuse = earlier["reuse"]

    folder = paths.base_dir() / ".kith" / "scratch" / "workflows" / run.id
    try:
        (folder / "tmp").mkdir(parents=True, exist_ok=True)
        (folder / "agents").mkdir(exist_ok=True)
        (folder / "script.py").write_text(script, encoding="utf-8")
        (folder / "run.json").write_text(
            json.dumps(
                {
                    "run": run.id,
                    "conversation": conversation,
                    "name": run.name,
                    "saved": name,
                    "args": handed,
                    "started": run.started,
                    "resumed_from": run.resumed_from,
                    "depth": (earlier["depth"] + 1) if earlier is not None else 0,
                },
                default=str,
            ),
            encoding="utf-8",
        )
        _furnish(folder, inputs)
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
        args=(_drive, run, path, float(meta["budget_usd"])),
        name=f"workflow-{run.id}",
        daemon=True,
    ).start()
    answer: dict = {
        "run": run.id,
        "name": run.name,
        "phases": run.phases,
        "folder": str(folder),
        "budget": f"${meta['budget_usd']:.2f}",
    }
    if warnings:
        answer["rehearsal_warnings"] = warnings
    if earlier is not None:
        answer["resumed_from"] = resume
        answer["reusable"] = len(run.reuse)
    if args.get("wait") is True:
        finished = _wait_for(run, conversation)
        if finished is not None:
            return {
                **answer,
                "finished": True,
                "result": finished or "It finished, and its result has already arrived as a new turn.",
            }
        answer["note"] = (
            f"Still running after {WAIT_SECONDS // 60} minutes, so it carries on in the background. "
            "End this turn saying so; you will get a new turn with the result when it finishes."
        )
        return answer
    answer["note"] = (
        "Running in the background. End this turn now: say what you started and what each "
        "phase does. You will get a new turn with the result when it finishes."
    )
    return answer


def new_run_refusal(conversation: str, unwatched: bool) -> str:
    """Why this turn may not open a new run, or "" when it may. For `run_workflow` and `add_agents`.

    A new run in a turn nobody opened — the one a finished run or test wakes — is refused unless
    workflow mode is on, because the mode is the person's standing yes and the prefix is only a
    yes to one message. Even then bounded: that turn is exactly where the next step of a survey →
    build → review chain belongs (three of these were refused in one evening while the person was
    right there), and also how a mode left on would spend money overnight.
    """
    if unwatched:
        if session_context.workflow_permission() != "mode":
            return (
                "Nobody is here to agree to a new workflow. Report what you have, and start the "
                "next one when they are back — resuming a run that broke is still allowed."
            )
        chained = workflows.unwatched_in_a_row(conversation)
        if chained >= MAX_UNWATCHED_RUNS:
            return (
                f"{chained} workflows have started in a row here with nobody watching. Stop, "
                "tell them what those runs found, and let them start the next one."
            )
    if not session_context.workflows_allowed():
        return (
            f"Workflows run only when the person asks for one — their message starts with "
            f"{PREFIX} — or when workflow mode is on in this chat. Do the work in this turn "
            f"instead, or suggest a {PREFIX} request if it really wants fanning out."
        )
    return ""


def _names() -> str:
    folder = shipped_library()
    return ", ".join(sorted(p.stem for p in folder.glob("*.py"))) if folder.is_dir() else ""


def _furnish(folder: Path, inputs: dict) -> None:
    """Give a run folder what the script runs with: the helper, its inputs, the saved workflows."""
    shutil.copyfile(Path(settings.SERVER_ROOT) / "workflow" / HELPER, folder / HELPER)
    (folder / "inputs.json").write_text(json.dumps(inputs, default=str), encoding="utf-8")
    library = folder / LIBRARY
    library.mkdir(exist_ok=True)
    # Shipped first, then the project's own over them, so a project can replace one by name.
    for source in reversed(_library_folders()):
        for script in sorted(source.glob("*.py")) if source.is_dir() else []:
            if _NAME.fullmatch(script.stem):
                shutil.copyfile(script, library / script.name)


def _their_words(conversation: str) -> str:
    """What the person has said most recently here, newest last, for verifiers and `ASKED`.

    The last few of their own messages, not the one alone: "yes, and the admin screen too" is
    half a request. A woken turn's note is the harness speaking and is not a user message, so it
    is never mistaken for them. The ``/workflow`` prefix is theirs but it is not the ask.
    """
    if not conversation:
        return ""
    try:
        from kith.services import conversations

        entries = conversations.read(conversation)
    except Exception:
        return ""
    said = [
        str(entry.get("content") or "").strip()
        for entry in entries
        if entry.get("type") == "message" and entry.get("role") == "user"
    ]
    said = [re.sub(r"(?i)^/workflow\b\s*", "", one) for one in said if one][-3:]
    text = "\n\n---\n\n".join(one for one in said if one)
    return text[-THEIR_WORDS_CHARS:]


def _wait_for(run: workflows.Run, conversation: str) -> str | None:
    """Hold the turn until ``run`` ends, and claim its note so it does not also wake the chat.

    None when it is still running at the deadline. Stop reaches it: a person who stops the turn
    stops the run it is waiting on.
    """
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if run.state != workflows.RUNNING:
            return workflows.claim(run)
        if stopping.asked_to_stop(conversation):
            workflows.stop(run.id)
        time.sleep(0.25)
    return None


def rehearse(script: str, inputs: dict) -> tuple[str, list[str]]:
    """Run ``script`` against stand-in agents. ``(why it cannot start, warnings)``.

    In the same boundary a real run gets, in a folder of its own that is removed afterwards.
    A rehearsal that cannot itself run — no interpreter, a timeout — is not a reason to refuse
    the real thing: it comes back as a warning, and the run goes ahead as it always did.
    """
    place = Path(settings.DATA_DIR) / "workflow-rehearsals" / uuid.uuid4().hex[:8]
    try:
        (place / "tmp").mkdir(parents=True, exist_ok=True)
        (place / "script.py").write_text(script, encoding="utf-8")
        _furnish(place, inputs)
        try:
            subprocess.run(
                confinement_argv(f"rehearsal-{place.name}", place, rehearse=True),
                cwd=str(place),
                env=_environment(place),
                capture_output=True,
                timeout=REHEARSAL_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return "", [
                f"The rehearsal did not finish in {REHEARSAL_SECONDS}s — a loop that runs until its "
                "agents say something cannot be driven to an end by stand-ins, so this was not checked."
            ]
        except OSError as exc:
            return "", [f"The rehearsal could not run ({exc}), so this was not checked."]
        try:
            told = json.loads((place / "rehearsal.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "", ["The rehearsal left no report, so this was not checked."]
        warnings = [str(one) for one in told.get("warnings") or []]
        crashed = str(told.get("crashed") or "").strip()
        problems = [str(one) for one in told.get("problems") or []]
        if crashed:
            return (
                "The script crashed in a rehearsal with stand-in agents, before anything was spent:\n"
                f"{crashed[-1500:]}\nFix it and call again. (If it failed only because a stand-in "
                "report is not what a real one would be, pass rehearse=false.)",
                warnings,
            )
        if problems:
            return (
                "The rehearsal found mistakes that would waste agents:\n- "
                + "\n- ".join(problems[:8])
                + "\nFix them and call again.",
                warnings,
            )
        if not int(told.get("agents") or 0):
            warnings.append("The script sent no agents in its rehearsal.")
        return "", warnings
    finally:
        # Only ever this rehearsal's own folder, which was made above under Kith's data folder.
        if place.is_absolute() and place.parent.name == "workflow-rehearsals":
            shutil.rmtree(place, ignore_errors=True)
        (Path(settings.DATA_DIR) / "workflow-profiles" / f"rehearsal-{place.name}.sb").unlink(missing_ok=True)


@tool(
    "check_workflow",
    "See where a workflow in this chat has got to: its state and phase, every agent with what it "
    "is doing or what it came back with, what it has cost against its budget, and the script's "
    "log. Use it when they ask how it is going, before starting another, or when a run failed — "
    "the log and the failed agents are how you find what to fix before resuming it with "
    "run_workflow(resume=...).",
    {"run": {**STR, "description": "The run id. Empty for the latest run in this chat."}},
)
def check_workflow(path: Path, args: dict):
    conversation = session_context.current()
    run_id = str(args.get("run") or "").strip()
    run = workflows.get(run_id) if run_id else workflows.latest(conversation)
    if run is not None and run.conversation != conversation:
        run = None
    if run is None:
        found = _earlier_run(run_id, conversation) if run_id else None
        if found is None:
            return {
                "error": "No workflow has run in this chat."
                if not run_id
                else f"No run {run_id} in this chat."
            }
        # A run the server no longer holds — it restarted, and the run ended with it.
        return {
            "run": run_id,
            "state": "ended with a server restart",
            "folder": found["folder"],
            "finished_agents": len(found["reuse"]),
            "log": _log_tail(Path(found["folder"]) / "run.log"),
            "next": "Resume it with run_workflow(resume=...) — its finished agents are reused.",
        }
    now = run.ended or time.time()
    agents = []
    for one in sorted(run.agents.values(), key=lambda a: a.get("started", 0)):
        entry = {
            "label": one.get("label", ""),
            "kind": one.get("kind", ""),
            "phase": one.get("phase", ""),
            "state": "failed" if one.get("failed") else one.get("state", ""),
            "calls": one.get("calls", 0),
        }
        if one.get("cost"):
            entry["cost"] = f"${float(one['cost']):.2f}"
        if one.get("state") == "done":
            entry["outcome"] = one.get("outcome") or one.get("tally", "")
            if one.get("report"):
                entry["said"] = str(one["report"])[:600]
        elif one.get("steps"):
            last = one["steps"][-1]
            entry["doing"] = f"{last.get('tool')} {json.dumps(last.get('args') or {})[:120]}"
        if one.get("reused"):
            entry["reused"] = True
        agents.append(entry)
    answer: dict = {
        "run": run.id,
        "name": run.name,
        "state": run.state,
        "phase": run.phase,
        "elapsed": _took(now - run.started),
        "agents": agents,
        "folder": run.folder,
    }
    if run.resumed_from:
        answer["resumed_from"] = run.resumed_from
    spent = sum(float(one.get("cost") or 0) for one in run.agents.values())
    if spent:
        answer["cost_so_far"] = f"${spent:.2f}"
    if run.budget:
        answer["budget"] = f"${run.budget:.2f}"
    log = _log_tail(Path(run.folder) / "run.log") if run.folder else ""
    if log:
        answer["log"] = log
    if run.state != workflows.RUNNING:
        answer["note"] = run.note
        if run.state in (workflows.FAILED, workflows.STOPPED):
            answer["next"] = (
                "Fix the script (it is in the folder as script.py) and call "
                f'run_workflow(script=..., resume="{run.id}"): agents asked the same thing are '
                "reused, so only what changed runs again."
            )
    return answer


@tool(
    "stop_workflow",
    "Stop a workflow running in this chat — when it is heading the wrong way and letting it finish "
    "would only cost money. Its script ends and every agent stops at its next step; you still get "
    "the turn that reports what finished, and can resume it later with run_workflow(resume=...).",
    {"run": {**STR, "description": "The run id. Empty for the one running in this chat."}},
)
def stop_workflow(path: Path, args: dict):
    conversation = session_context.current()
    run_id = str(args.get("run") or "").strip()
    run = workflows.get(run_id) if run_id else workflows.running(conversation)
    if run is None or run.conversation != conversation:
        return {
            "error": "No workflow is running in this chat."
            if not run_id
            else f"No run {run_id} in this chat."
        }
    if not workflows.stop(run.id):
        return {"error": f"{run.id} is not running — it already {run.state}."}
    return {"stopping": run.id, "note": "It stops at the next step; the run's report comes as a new turn."}


def _earlier_run(run_id: str, conversation: str) -> dict | None:
    """A run of this chat, read back from its folder: its script, its args and its finished agents.

    From the folder rather than the board, so a run the server lost in a restart can still be
    resumed — its agents' results were written down as each finished.
    """
    if not run_id or not re.fullmatch(r"wf-[0-9a-f]{6}", run_id):
        return None
    folder = paths.base_dir() / ".kith" / "scratch" / "workflows" / run_id
    try:
        header = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        script = (folder / "script.py").read_text(encoding="utf-8")
    except OSError:
        # A run `add_agents` keeps going has no script — its steps are the plan.
        script = ""
    if not isinstance(header, dict) or header.get("conversation") != conversation:
        return None
    reuse: dict[str, dict] = {}
    for record in sorted((folder / "agents").glob("*.json")) if (folder / "agents").is_dir() else []:
        try:
            one = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(one, dict) and one.get("key"):
            reuse[str(one["key"])] = one
    return {
        "script": script,
        "args": header.get("args"),
        "reuse": reuse,
        "folder": str(folder),
        "depth": int(header.get("depth") or 0),
    }


def _ask_key(message: dict) -> str:
    """What a request asked, as a hash: its op, kind, prompt, schema, model and starting patches."""
    op = str(message.get("op") or "agent")
    after = [str(one.get("diff") or "") for one in (message.get("after") or []) if isinstance(one, dict)]
    asked: dict = {
        "kind": str(message.get("kind") or "scout"),
        "prompt": str(message.get("prompt") or "").strip(),
        "schema": message.get("schema"),
        "after": after,
    }
    # Only when set, so every agent a run recorded before these existed still matches.
    for extra in ("model", "effort"):
        if message.get(extra):
            asked[extra] = str(message[extra])
    if op == "merge":
        asked = {
            "op": "merge",
            "patches": [
                str(one.get("diff") or "") for one in message.get("built") or [] if isinstance(one, dict)
            ],
            "check": bool(message.get("check")),
            "tests": message.get("tests"),
        }
    elif op == "revise":
        built = message.get("built") or {}
        asked = {
            "op": "revise",
            "diff": str(built.get("diff") or ""),
            "feedback": str(message.get("feedback") or ""),
        }
    return hashlib.sha256(json.dumps(asked, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _keep(run: workflows.Run, key: str, record: dict) -> None:
    """Write down what a finished agent produced, whole, so a resumed run can reuse it."""
    try:
        (Path(run.folder) / "agents" / f"{key}.json").write_text(
            json.dumps({"key": key, **record}, default=str), encoding="utf-8"
        )
    except OSError:
        pass


def _reused(run: workflows.Run, message: dict, record: dict, agents: _Agents) -> object:
    """Answer a request from the run this one resumes. Costs nothing and takes no slot."""
    value = record.get("value")
    kind = str(record.get("kind") or message.get("kind") or "scout")
    label = str(message.get("label") or "").strip() or str(record.get("label") or "")
    if isinstance(value, dict) and str(value.get("diff") or "").strip():
        # Its patch is copied into this run's folder, so a finished run's patches are all in one
        # place however many resumes it took to get there.
        patch = Path(run.folder) / Path(str(value.get("patch_file") or f"{_slug(label)}.patch")).name
        try:
            patch.write_text(str(value["diff"]), encoding="utf-8")
            value = {**value, "patch_file": str(patch)}
        except OSError:
            pass
    agent_id = uuid.uuid4().hex[:6]
    workflows.note_agent(
        run,
        agent_id,
        state="done",
        label=label,
        kind=kind,
        phase=run.phase,
        reused=True,
        started=time.time(),
        tally=f"reused from {run.resumed_from}",
        outcome=str(record.get("outcome") or ""),
        report=str(record.get("report") or "")[:REPORT_ON_BOARD],
        patch=str(value.get("patch_file") or "") if isinstance(value, dict) else "",
    )
    try:
        from kith.services.activity import feed

        for state in ("running", "done"):
            feed.publish(
                "errand",
                f"reused from {run.resumed_from}",
                errand={
                    "id": agent_id,
                    "state": state,
                    "objective": label,
                    "role": kind,
                    "run": run.id,
                    "phase": run.phase,
                    "reused": True,
                },
                conversation=run.conversation,
            )
    except Exception:
        pass
    agents.record(f'- {kind} "{label}" — reused from {run.resumed_from}', 0.0)
    _keep(run, str(record.get("key") or _ask_key(message)), {**record, "value": value})
    return value


def _real_copy(worktree: str) -> bool:
    """Whether ``worktree`` names an actual copy: absolute and on disk. "" never is.

    Asked before anything uses a copy's path for a git command or keeps it for later — the
    empty string is the current directory to every one of them.
    """
    return bool(worktree) and Path(worktree).is_absolute() and Path(worktree).is_dir()


class _Agents:
    """What the run's agents did, for the closing note, the caps, and the copies still open."""

    def __init__(self, budget: float = DEFAULT_BUDGET_USD) -> None:
        self.lock = threading.Lock()
        self.sent = 0
        self.lines: list[str] = []
        self.cost = 0.0
        self.budget = budget
        #: Spent so far, counted as each model call reports — the ceiling's measure, which has to
        #: see an agent that is still running, not only the ones that have finished.
        self.live = 0.0
        self.hit_budget = False
        #: Builders' (and merges') copies, kept open until the run ends so `revise` can send one
        #: back into its own edits. Keyed by the ``agent`` field of the result.
        self.copies: dict[str, dict] = {}

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

    def spend(self, amount: float) -> None:
        with self.lock:
            self.live += max(0.0, amount)
            if self.live >= self.budget:
                self.hit_budget = True

    def over_budget(self) -> bool:
        with self.lock:
            return self.live >= self.budget

    def keep_copy(self, key: str, **facts) -> bool:
        """Hold a copy open for `revise`. Refused — False — for anything that is not a real copy."""
        if not _real_copy(str(facts.get("worktree") or "")):
            return False
        with self.lock:
            self.copies[key] = facts
        return True

    def copy_of(self, key: str) -> dict | None:
        with self.lock:
            return self.copies.get(key)

    def close_copies(self) -> None:
        from kith.infra.workspace import worktrees

        with self.lock:
            held = list(self.copies.values())
            self.copies.clear()
        for one in held:
            with contextlib.suppress(Exception):
                worktrees.close(Path(one["worktree"]))


def _drive(run: workflows.Run, path: Path, budget: float = DEFAULT_BUDGET_USD) -> None:
    """The whole run, on its own thread: launch the script, answer it, and close the books.

    Under `nobody_watching`: an agent has no `ask`, and a permission prompt raised mid-run
    would be put to nobody. With it set, anything that would prompt refuses at once instead.
    """
    with session_context.nobody_watching():
        folder = Path(run.folder)
        began = time.monotonic()
        agents = _Agents(budget)
        run.budget = budget
        # Built once, so every agent of the run is handed the same project notes and the
        # provider's prefix cache serves the second agent onwards.
        context = workers.project_brief(path, run.conversation)
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
                confinement_argv(run.id, folder),
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
                if op in ("agent", "merge", "revise"):
                    ident = message.get("id")
                    message["_key"] = _ask_key(message)
                    if run.halt.is_set():
                        reply({"id": ident, "ok": False, "error": "the workflow was stopped"})
                    elif message["_key"] in run.reuse:
                        reply(
                            {
                                "id": ident,
                                "ok": True,
                                "value": _reused(run, message, run.reuse[message["_key"]], agents),
                            }
                        )
                    elif agents.over_budget():
                        reply(
                            {
                                "id": ident,
                                "ok": False,
                                "error": f"this run has spent its ${agents.budget:.2f} budget",
                            }
                        )
                    elif op != "merge" and not agents.take_slot():
                        reply(
                            {"id": ident, "ok": False, "error": f"this run has sent its {MAX_AGENTS} agents"}
                        )
                    else:
                        pool.submit(copy_context().run, _answer, run, path, message, agents, reply, context)
                elif op == "budget":
                    reply(
                        {
                            "id": message.get("id"),
                            "ok": True,
                            "value": {"total": agents.budget, "spent": agents.live},
                        }
                    )
                elif op == "phase":
                    run.phase = str(message.get("title") or "")
                    workflows.say(run, "phase", run.phase)
                elif op == "log":
                    run.said = str(message.get("text") or "")[:300]
                    workflows.say(run, "log", run.said)
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
            agents.close_copies()
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
        workflows.end(run, state, note, cost=round(agents.cost, 4), agents=len(agents.lines))


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


def _answer(run: workflows.Run, path: Path, message: dict, agents: _Agents, reply, context: str = "") -> None:
    """Run one request the script made — an agent, a merge, a revision — and answer it. Never raises."""
    ident = message.get("id")
    op = message.get("op") or "agent"
    try:
        if op == "merge":
            value = _merge(run, message, agents)
        elif op == "revise":
            value = _revise(run, path, message, agents, context)
        else:
            value = _one_agent(run, path, message, agents, context)
        reply({"id": ident, "ok": True, "value": value})
    except _Failed as exc:
        reply({"id": ident, "ok": False, "error": str(exc)})
    except Exception as exc:
        # Recorded as well as answered: the script may swallow the failure, and the note is the
        # only place the person would otherwise learn an agent went down.
        kind = str(message.get("kind") or op)
        label = (
            str(message.get("label") or "").strip() or " ".join(str(message.get("prompt") or "").split())[:60]
        )
        agents.record(f'- {kind} "{label}" — broke: {exc}', 0.0)
        reply({"id": ident, "ok": False, "error": f"the agent broke: {exc}"})


class _Failed(Exception):
    pass


def _marker(run: workflows.Run, agent_id: str, label: str, kind: str, phase: str):
    """The two ways an agent is shown while it runs: the live feed and the run's board."""

    def mark(state: str, text: str, tool_name: str = "", arguments: dict | None = None, **extra) -> None:
        """One line on the live feed about this agent, and the same facts on the run's board.
        ``extra`` is what the agent map shows when you click it: the prompt and phase when it
        starts, the cost, the report and the patch when it ends. Never stored in the transcript
        or sent to a model."""
        if state == "step":
            from kith.services.activity import short_args

            # Shortened, as on the feed: a `write_file` call's arguments are the whole file.
            workflows.note_agent(run, agent_id, step={"tool": tool_name, "args": short_args(arguments or {})})
        else:
            workflows.note_agent(
                run,
                agent_id,
                state=state,
                label=label,
                kind=kind,
                tally=text if state == "done" else "",
                **extra,
            )
        try:
            from kith.services.activity import feed, short_args

            feed.publish(
                "errand",
                text,
                tool=tool_name or None,
                args=short_args(arguments or {}) or None,
                errand={
                    "id": agent_id,
                    "state": state,
                    "objective": label,
                    "role": kind,
                    "run": run.id,
                    **({"phase": phase} if phase and "phase" not in extra else {}),
                    **extra,
                },
                conversation=run.conversation,
            )
        except Exception:
            pass

    return mark


def _watching(mark, agents: _Agents):
    """What each agent's events feed: its steps to the map, its spend to the run's ceiling."""

    def watch(event: dict) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            name = str(event.get("name") or "")
            mark("step", name, tool_name=name, arguments=event.get("arguments") or {})
        elif kind == "stats":
            agents.spend(float((event.get("stats") or {}).get("costUsd") or 0.0))

    return watch


def _one_agent(run: workflows.Run, path: Path, message: dict, agents: _Agents, context: str = ""):
    from kith.domain.enums import REASONING_EFFORTS

    kind = str(message.get("kind") or "scout")
    if kind not in workers.ROLES:
        raise _Failed(f"there is no {kind!r} kind of agent — use {', '.join(workers.ROLES)}")
    prompt = str(message.get("prompt") or "").strip()
    if not prompt:
        raise _Failed("an agent needs a prompt")
    model = str(message.get("model") or "").strip()
    if model and model not in workers.MODELS:
        raise _Failed(f"model must be one of {', '.join(workers.MODELS)}, not {model!r}")
    effort = str(message.get("effort") or "").strip()
    if effort and effort not in REASONING_EFFORTS:
        raise _Failed(f"effort must be one of {', '.join(REASONING_EFFORTS)}, not {effort!r}")
    schema = message.get("schema")
    agent_id = uuid.uuid4().hex[:6]
    label = str(message.get("label") or "").strip() or " ".join(prompt.split())[:60]
    phase = str(message.get("phase") or "").strip() or run.phase
    objective = prompt
    if kind == "verifier":
        # Their words ride with every verifier, whatever the script remembered to include — the
        # brief it would otherwise check against is the one Kith wrote, and that is the brief
        # that turned "configure everything" into "configure it through the API".
        asked = _their_words(run.conversation)
        if asked:
            objective += (
                f"\n\nWhat the person asked, in their own words (this outranks any brief above):\n{asked}"
            )
    if schema is not None:
        objective += (
            "\n\nWhen you have the answer, call `submit` with it — its parameters are the shape "
            "this workflow asked for. That call is how your answer is handed back."
        )

    mark = _marker(run, agent_id, label, kind, phase)
    watch = _watching(mark, agents)

    # What earlier builders made, if this agent was sent `after` them. Any kind may be: a review
    # is a scout that has to read code which so far exists only as patches.
    earlier = [
        one
        for one in (message.get("after") or [])
        if isinstance(one, dict) and str(one.get("diff") or "").strip()
    ]
    on_top_of = [str(one.get("label") or "").strip() or "an earlier patch" for one in earlier]

    worktree = ""
    kept = False
    if workers.ROLES[kind]["copy"] or earlier:
        try:
            worktree = workers.open_copy(f"{run.id}-{agent_id}")
        except workers.WorkerError as exc:
            agents.record(f'- {kind} "{label}" — could not start: {exc}', 0.0)
            raise _Failed(str(exc)) from None
        if earlier:
            from kith.infra.workspace import worktrees

            if not _real_copy(worktree):
                raise _Failed("there is no copy to apply the earlier patches in")
            refused = worktrees.apply_in(Path(worktree), [str(one["diff"]) for one in earlier])
            if refused:
                worktrees.close(Path(worktree))
                agents.record(f'- {kind} "{label}" — could not start: {refused}', 0.0)
                raise _Failed(f"the earlier patches do not apply together — {refused}")

    mark("running", label, prompt=prompt[:4000], phase=phase, after=on_top_of)
    try:
        answer, report = workers.run_agent(
            path,
            kind,
            objective,
            run.conversation,
            worktree=worktree,
            give_up=lambda: run.halt.is_set() or agents.over_budget(),
            watch=watch,
            model=model,
            effort=effort,
            context=context,
            schema=schema,
        )
        said = str(answer.get("findings") or "")
        cost = report.cost
        submitted = "submitted" in answer
        if not submitted and (
            report.cut_short or (report.error and not (report.text.strip() or report.said_before.strip()))
        ):
            why = "stopped" if report.cut_short else report.error
            if report.cut_short and agents.over_budget():
                why = f"the run's ${agents.budget:.2f} budget ran out"
            mark("done", f"failed: {why}"[:120], failed=True, cost=round(cost, 4), calls=len(report.calls))
            agents.record(f'- {kind} "{label}" — failed: {why}', cost)
            raise _Failed(f"the agent did not finish: {why}")

        shaped = said
        if schema is not None:
            if submitted:
                shaped = answer["submitted"]
            else:
                shaped, extra = _parse_or_repair(path, kind, objective, said, schema, run, context, watch)
                cost += extra
            if shaped is None:
                mark(
                    "done", "no JSON in its report", failed=True, cost=round(cost, 4), calls=len(report.calls)
                )
                agents.record(f'- {kind} "{label}" — reported, but not as the JSON asked for', cost)
                raise _Failed("the agent's report did not end with the JSON the schema asked for")

        tally = str(answer.get("looked_at") or "")
        key = str(message.get("_key") or _ask_key(message))
        if kind != "builder":
            _keep(run, key, {"kind": kind, "label": label, "value": shaped, "report": said})
            mark(
                "done",
                tally or "reported",
                cost=round(cost, 4),
                calls=len(report.calls),
                report=said[:REPORT_ON_BOARD],
            )
            agents.record(f'- {kind} "{label}" — reported ({tally or "no calls"})', cost)
            return shaped

        diff = str(answer.get("patch") or "")
        patch_file = _save_patch(run, label, agent_id, diff)
        outcome = _outcome(patch_file, diff, on_top_of)
        mark(
            "done",
            tally or outcome,
            cost=round(cost, 4),
            calls=len(report.calls),
            report=said[:REPORT_ON_BOARD],
            patch=patch_file,
            outcome=outcome,
        )
        agents.record(f'- builder "{label}" — {outcome}', cost)
        built = {"report": shaped, "diff": diff, "patch_file": patch_file, "label": label, "agent": agent_id}
        # Kept open for `revise`, closed with the run. The brief and report ride along, so a
        # revision is handed back exactly what this builder was told and said.
        kept = agents.keep_copy(agent_id, worktree=worktree, objective=objective, report=said, label=label)
        _keep(
            run,
            key,
            {"kind": kind, "label": label, "value": built, "report": said, "outcome": outcome},
        )
        return built
    finally:
        if not kept and _real_copy(worktree):
            from kith.infra.workspace import worktrees

            worktrees.close(Path(worktree))


def _save_patch(run: workflows.Run, label: str, agent_id: str, diff: str) -> str:
    if not diff.strip():
        return ""
    patch = Path(run.folder) / f"{_slug(label)}-{agent_id}.patch"
    patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8")
    return str(patch)


def _outcome(patch_file: str, diff: str, on_top_of: list[str]) -> str:
    if not patch_file:
        return "changed nothing"
    added, removed = _diffstat(diff)
    outcome = f"patch {patch_file} (+{added} −{removed})"
    if on_top_of:
        # Said, because order now matters: this patch was made against a tree with those
        # applied, so it goes on after them.
        outcome += f", made on top of {', '.join(on_top_of)} — apply those first"
    return outcome


#: What marks the top of a project for a merge's check — the checker's markers and the test
#: runner's together, since a merge runs both.
_PROJECT_MARKERS = (
    "tsconfig.json",
    "package.json",
    "pyproject.toml",
    "ruff.toml",
    "pytest.ini",
    "setup.cfg",
    "go.mod",
    "Cargo.toml",
)

#: The most projects one merge checks. A patch set that touches more than this is not one change.
_MERGE_ROOTS = 3


def _touched_roots(copy: Path, diff: str) -> list[str]:
    """The projects a diff touches, as folders relative to the copy, nearest first."""
    roots: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        probe = (copy / line[len("+++ b/") :].strip()).parent
        while True:
            if any((probe / marker).is_file() for marker in _PROJECT_MARKERS):
                relative = str(probe.relative_to(copy)) if probe != copy else "."
                if relative not in roots:
                    roots.append(relative)
                break
            if probe == copy or copy not in probe.parents:
                break
            probe = probe.parent
    return roots[:_MERGE_ROOTS]


def _merge(run: workflows.Run, message: dict, agents: _Agents) -> dict:
    """The builders' patches in one copy, checked there. See `kith_workflow.merge`."""
    from kith.engine.run import testing
    from kith.infra import workspace as sandbox
    from kith.infra.workspace import worktrees

    patches = [
        one
        for one in message.get("built") or []
        if isinstance(one, dict) and str(one.get("diff") or "").strip()
    ]
    if not patches:
        raise _Failed("merge() was given no patches — every builder it was handed changed nothing")
    agent_id = uuid.uuid4().hex[:6]
    label = str(message.get("label") or "").strip() or "merged"
    mark = _marker(run, agent_id, label, "merge", run.phase)
    mark("running", label, prompt=f"merge {len(patches)} patches", phase=run.phase)
    try:
        worktree = workers.open_copy(f"{run.id}-{agent_id}")
    except workers.WorkerError as exc:
        mark("done", f"failed: {exc}"[:120], failed=True)
        raise _Failed(str(exc)) from None
    if not _real_copy(worktree):
        mark("done", "failed: no copy to merge in", failed=True)
        raise _Failed("there is no copy to merge the patches in")
    copy = Path(worktree)
    kept = False
    try:
        outcomes = worktrees.merge_in(copy, [str(one["diff"]) for one in patches])
        pairs = list(zip(patches, outcomes, strict=False))
        applied = [str(one.get("label") or f"patch {i + 1}") for i, (one, why) in enumerate(pairs) if not why]
        conflicts = [
            {"label": str(one.get("label") or f"patch {i + 1}"), "why": why}
            for i, (one, why) in enumerate(pairs)
            if why
        ]
        diff = worktrees.diff_in(copy)
        checks: list[dict] = []
        tested: list[dict] = []
        tests = message.get("tests")
        with session_context.working_from(worktree, mirror_of=str(workers._source_of(worktree))):
            for root in _touched_roots(copy, diff):
                if message.get("check", True):
                    try:
                        found = sandbox.check_code(root)
                    except Exception as exc:
                        found = {"clean": False, "problems": str(exc)}
                    checks.append(
                        {
                            "root": root,
                            "ran": found.get("ran", ""),
                            "clean": found.get("clean"),
                            **({"problems": found["problems"]} if found.get("problems") else {}),
                        }
                    )
                if tests:
                    try:
                        ran = testing.run(root, tests if isinstance(tests, str) else "")
                    except testing.TestingError as exc:
                        ran = {"ok": False, "error": str(exc)}
                    tested.append({"root": root, **ran})
        checks_clean = all(one.get("clean") is not False for one in checks)
        tests_pass = all(one.get("ok") is not False for one in tested)
        clean = not conflicts and checks_clean and tests_pass
        patch_file = _save_patch(run, label, agent_id, diff)
        added, removed = _diffstat(diff)
        outcome = (
            f"patch {patch_file} (+{added} −{removed}), {len(applied)} of {len(patches)} applied"
            + (f", did not fit: {', '.join(one['label'] for one in conflicts)}" if conflicts else "")
            + ((", check clean" if checks_clean else ", check failing") if checks else "")
            + ((", tests pass" if tests_pass else ", tests failing") if tested else "")
        )
        mark("done", outcome[:120], patch=patch_file, outcome=outcome)
        agents.record(f'- merge "{label}" — {outcome}', 0.0)
        kept = agents.keep_copy(
            agent_id,
            worktree=worktree,
            objective=(
                f"Put these changes together so they work as one: {', '.join(applied) or 'the patches'}. "
                "They are applied in your copy already."
            ),
            report=f"Merged {len(applied)} of {len(patches)} patches.",
            label=label,
        )
        value = {
            "report": f"Merged {len(applied)} of {len(patches)} patches.",
            "diff": diff,
            "patch_file": patch_file,
            "label": label,
            "agent": agent_id,
            "applied": applied,
            "conflicts": conflicts,
            "check": checks,
            "tests": tested,
            "clean": clean,
        }
        _keep(
            run,
            str(message.get("_key") or _ask_key(message)),
            {"kind": "merge", "label": label, "value": value, "outcome": outcome},
        )
        return value
    finally:
        if not kept:
            worktrees.close(copy)


def _revise(run: workflows.Run, path: Path, message: dict, agents: _Agents, context: str = "") -> dict:
    """A builder sent back into its copy with feedback. See `kith_workflow.revise`."""
    from kith.infra.workspace import worktrees

    built = message.get("built") or {}
    feedback = str(message.get("feedback") or "").strip()
    if not feedback:
        raise _Failed("revise() needs feedback — what to change")
    earlier_label = str(built.get("label") or "builder")
    label = str(message.get("label") or "").strip() or f"revise {earlier_label}"
    agent_id = uuid.uuid4().hex[:6]
    held = agents.copy_of(str(built.get("agent") or ""))
    rebuilt = False
    if held is not None and _real_copy(str(held.get("worktree") or "")):
        worktree, objective, said, key = (
            held["worktree"],
            held["objective"],
            held["report"],
            str(built["agent"]),
        )
    else:
        # Its copy is gone — reused from an earlier run, or never kept. Rebuilt from its patch,
        # which is all a builder's real state ever was.
        try:
            worktree = workers.open_copy(f"{run.id}-{agent_id}")
        except workers.WorkerError as exc:
            raise _Failed(str(exc)) from None
        if not _real_copy(worktree):
            raise _Failed("there is no copy to revise in")
        rebuilt = True
        diff = str(built.get("diff") or "")
        if diff.strip():
            refused = worktrees.merge_in(Path(worktree), [diff])
            if refused and refused[0]:
                worktrees.close(Path(worktree))
                raise _Failed(f"its patch no longer applies — {refused[0]}")
        objective = f"Continue the change {earlier_label!r}, which is already applied in your copy."
        said = str(built.get("report") or "")
        key = agent_id
    so_far = worktrees.diff_in(Path(worktree))
    added, removed = _diffstat(so_far)
    ask = (
        f"{feedback}\n\nYour copy still has every edit made so far (+{added} −{removed} lines) — "
        "`changes` shows them. Carry on from there: make the edits this needs, check them, and "
        "report what you changed this time."
    )
    rounds = int(workers.ROLES["builder"]["rounds"]())
    mark = _marker(run, agent_id, label, "builder", run.phase)
    watch = _watching(mark, agents)
    mark("running", label, prompt=feedback[:4000], phase=run.phase, after=[earlier_label])
    kept = not rebuilt
    try:
        answer, report = workers.run_agent(
            path,
            "builder",
            objective,
            run.conversation,
            worktree=worktree,
            give_up=lambda: run.halt.is_set() or agents.over_budget(),
            watch=watch,
            carried=workers.follow_on("builder", objective, said, ask, context, rounds),
            rounds=rounds,
            context=context,
        )
        said_now = str(answer.get("findings") or "")
        if report.cut_short or (report.error and not (report.text.strip() or report.said_before.strip())):
            why = "stopped" if report.cut_short else report.error
            mark(
                "done",
                f"failed: {why}"[:120],
                failed=True,
                cost=round(report.cost, 4),
                calls=len(report.calls),
            )
            agents.record(f'- builder "{label}" — failed: {why}', report.cost)
            raise _Failed(f"the revision did not finish: {why}")
        diff = str(answer.get("patch") or "")
        patch_file = _save_patch(run, label, agent_id, diff)
        outcome = _outcome(patch_file, diff, [])
        mark(
            "done",
            outcome[:120],
            cost=round(report.cost, 4),
            calls=len(report.calls),
            report=said_now[:REPORT_ON_BOARD],
            patch=patch_file,
            outcome=outcome,
        )
        agents.record(f'- builder "{label}" — {outcome}', report.cost)
        kept = (
            agents.keep_copy(key, worktree=worktree, objective=objective, report=said_now, label=label)
            or kept
        )
        value = {"report": said_now, "diff": diff, "patch_file": patch_file, "label": label, "agent": key}
        _keep(
            run,
            str(message.get("_key") or _ask_key(message)),
            {"kind": "builder", "label": label, "value": value, "report": said_now, "outcome": outcome},
        )
        return value
    finally:
        if not kept:
            worktrees.close(Path(worktree))


def _parse_or_repair(
    path: Path,
    kind: str,
    objective: str,
    said: str,
    schema,
    run: workflows.Run,
    context: str = "",
    watch=None,
):
    """The JSON an agent that never called `submit` put in its report, or one more round asking."""
    found = last_json(said, schema)
    if found is not None:
        return found, 0.0
    if run.halt.is_set():
        return None, 0.0
    ask = (
        "Your report did not hand back the structured value asked for. Reply with ONLY that value "
        f"as JSON, matching this shape, and nothing else:\n{json.dumps(schema, default=str)}"
    )
    answer, report = workers.run_agent(
        path,
        kind,
        objective,
        run.conversation,
        carried=workers.follow_on(kind, objective, said, ask, context),
        tools=set(),
        rounds=1,
        give_up=run.halt.is_set,
        watch=watch,
        context=context,
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
        wanted = list if schema.get("type") == "array" else dict
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


def confinement_argv(name: str, folder: Path, rehearse: bool = False) -> list[str]:
    """The command that runs the script inside its boundary.

    The server's own interpreter, so there is nothing to install and the packaged app works the
    same: a checkout runs ``python -E -s -B`` (no environment, no user site, no bytecode), and a
    frozen build runs itself with ``--run-workflow``, which `app.py` hands straight to the helper
    before importing anything of Kith's. ``rehearse`` runs it against stand-in agents.
    """
    dry = ["--dry"] if rehearse else []
    if settings.FROZEN:
        command = [sys.executable, "--run-workflow", HELPER, *dry, "script.py"]
    else:
        command = [sys.executable, "-E", "-s", "-B", HELPER, *dry, "script.py"]
    profile_path = _write_profile(name, folder)
    return [confinement.SANDBOX_EXEC, "-f", str(profile_path), *command]


def _write_profile(name: str, folder: Path) -> Path:
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
    profile_path = place / f"{name}.sb"
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
    if run.resumed_from:
        reused = sum(1 for line in agents.lines if "reused from" in line)
        head += (
            f" It resumed {run.resumed_from}, reusing {reused} finished agent{'s' if reused != 1 else ''}."
        )
    if why and state != workflows.FINISHED:
        head += f" Why: {why}."
    if agents.hit_budget:
        head += f" It reached its ${agents.budget:.2f} budget, and agents asked for after that were refused."
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
    merged = [line for line in agents.lines if line.startswith("- merge ") and ".patch" in line]
    if merged:
        parts.append(
            "Nothing has been applied. The merged patch holds everything that fit together and has "
            "been checked in a copy — apply that one (`git apply` from the project root), not the "
            "builders' separate patches, then run the tests."
        )
    elif any(".patch" in line for line in agents.lines):
        parts.append(
            "Nothing has been applied. Read the patches, `git apply` the ones you want from the "
            "project root, check they fit together, and run the tests."
        )
    if state in (workflows.FAILED, workflows.STOPPED):
        parts.append(
            f"To fix it: check_workflow shows the failed agents and the log; correct the script and "
            f'call run_workflow(script=..., resume="{run.id}") — agents asked the same thing are reused.'
        )
    parts.append(f"Everything the run wrote is in {run.folder}.")
    return "\n\n".join(parts)


def _took(seconds: float) -> str:
    minutes, rest = divmod(int(max(0, seconds)), 60)
    return f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"


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
