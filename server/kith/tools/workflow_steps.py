"""`add_agents`: a workflow Kith keeps going one step at a time.

**Why steps, when there are scripts.** Workflow mode was on for 19 turns across two chats in
September — about 495 tool calls, seven turns of thirty or more — and not one workflow started.
The one time he weighed it he said the work was "fairly linear… not a fan-out", and by the
script's own terms he was right: a script is a decomposition written down in full before any
agent has reported, and his work was build, run, look, fix. So a run here is a sequence of
steps. Each call sends a few agents; when they report, the conversation wakes with what they
said, and the next call adds the next step to the same run — a verifier after the builder, a
revision of what it found. One builder is a whole first step.

**A step is data, not code.** A list of agents, each with a label, and ``after`` naming the
labels it builds on. Nothing to rehearse and nothing to crash — four of the first seven scripts
had a logic bug, and the commonest was handing one agent's report to another as its prompt,
which ``after`` now does correctly by construction: after a builder an agent starts from its
patch, after a scout or verifier the report is carried into its prompt.

**Everything else is the workflow's.** The agents, merges and revisions are run by the same
functions a script's requests reach (`tools/workflow`), under the same caps, on the same board,
drawn on the same map — a step is a phase there, so the map grows a column per step. Budget and
agent count are the run's, across its steps, and kept in the run folder's ``run.json`` with the
labels, so a run the server lost in a restart can still be continued. A script run can be
continued with steps too: its agents are named by their labels.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_for_any
from contextvars import copy_context
from pathlib import Path

from kith.infra.workspace import paths
from kith.kernel import session_context
from kith.services import workflows
from kith.tools import workers, workflow
from kith.tools.params import BOOL, STR
from kith.tools.registry import tool

KINDS = ("scout", "builder", "verifier", "merge", "revise")

#: The kinds whose result is a patch a later agent can start from, merge or revise.
PATCHING = frozenset({"builder", "merge", "revise"})

#: Steps in a row that may run with nobody watching — each one starts in the turn the last one
#: woke. A build, its review and two rounds of fixes is four; past five it has to reach a person,
#: because a chat left in workflow mode must not keep a run going all night on its own say-so.
MAX_STEPS_UNWATCHED = 5

#: The most of one earlier agent's report carried into a later agent's prompt.
CARRIED_REPORT_CHARS = 8_000

_LABEL_CHARS = 60


def _label(raw) -> str:
    return " ".join(str(raw or "").split())[:_LABEL_CHARS]


def read_items(raw) -> tuple[list[dict], str]:
    """The step's agents, normalised, or ``([], the first thing wrong with them)``."""
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return [], "agents must be a list of at least one agent: [{label, kind, prompt, after}]."
    items: list[dict] = []
    for number, one in enumerate(raw, 1):
        if not isinstance(one, dict):
            return [], f"agent {number} is not an object with a label, a kind and a prompt."
        label = _label(one.get("label"))
        if not label:
            return [], f"agent {number} has no label — every agent needs one, so later agents can name it."
        kind = str(one.get("kind") or "scout").strip().lower()
        if kind not in KINDS:
            return [], f'"{label}": kind must be one of {", ".join(KINDS)}, not {kind!r}.'
        after = one.get("after") or []
        if isinstance(after, str):
            after = [after]
        if not isinstance(after, list):
            return [], f'"{label}": after must be a list of labels.'
        after = list(dict.fromkeys(_label(dep) for dep in after if _label(dep)))
        prompt = str(one.get("prompt") or "").strip()
        if kind == "merge" and not after:
            return [], f'"{label}": a merge needs after=[the builders to put together].'
        if kind == "revise" and len(after) != 1:
            return (
                [],
                f'"{label}": a revise takes after=[exactly one builder] and a prompt saying what to change.',
            )
        if kind != "merge" and not prompt:
            return [], (
                f'"{label}": a revise needs a prompt — what to change.'
                if kind == "revise"
                else f'"{label}" has no prompt. Name the files, the change and what done looks like.'
            )
        items.append(
            {
                "label": label,
                "kind": kind,
                "prompt": prompt,
                "after": after,
                "model": str(one.get("model") or "").strip(),
                "effort": str(one.get("effort") or "").strip(),
                "schema": one.get("schema"),
                "tests": bool(one.get("tests")),
            }
        )
    return items, ""


def check_plan(items: list[dict], known: dict[str, dict]) -> str:
    """What is wrong with a step before anything is spent on it, or "".

    ``known`` is ``{label: {"kind": ..., "ok": ...}}`` for the run's earlier agents.
    """
    here: dict[str, dict] = {}
    for item in items:
        if item["label"] in known or item["label"] in here:
            return (
                f'"{item["label"]}" is already a label in this run. Labels must be unique, so a '
                "later agent can say which one it means."
            )
        here[item["label"]] = item
    for item in items:
        for dep in item["after"]:
            if dep == item["label"]:
                return f'"{dep}" cannot come after itself.'
            if dep in here:
                kind = here[dep]["kind"]
            elif dep in known:
                if not known[dep].get("ok"):
                    return (
                        f'"{dep}" did not finish in an earlier step, so nothing can start from it — '
                        "send a new agent for what it was doing."
                    )
                kind = str(known[dep].get("kind") or "scout")
            else:
                there = [*sorted(known), *(label for label in here if label != item["label"])]
                return (
                    f'"{item["label"]}" comes after "{dep}", and no agent in this run is called that. '
                    f"Labels there are: {', '.join(there) or 'none yet'}."
                )
            if item["kind"] in ("merge", "revise") and kind not in PATCHING:
                return (
                    f'"{item["label"]}" is a {item["kind"]}, so what it comes after must be a builder '
                    f'— "{dep}" is a {kind}.'
                )
    # A cycle inside the step would leave its agents waiting on each other forever.
    waiting = {label: {dep for dep in item["after"] if dep in here} for label, item in here.items()}
    while waiting:
        ready = [label for label, deps in waiting.items() if not deps]
        if not ready:
            return f"These agents wait on each other in a circle: {', '.join(sorted(waiting))}."
        for label in ready:
            del waiting[label]
        for deps in waiting.values():
            deps.difference_update(ready)
    return ""


# ── the run folder's ledger ─────────────────────────────────────────────────────────────────


def _folder_of(run_id: str) -> Path:
    return paths.base_dir() / ".kith" / "scratch" / "workflows" / run_id


def _ledger(folder: Path) -> dict:
    try:
        found = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _save_ledger(folder: Path, ledger: dict) -> None:
    try:
        (folder / "run.json").write_text(json.dumps(ledger, default=str), encoding="utf-8")
    except OSError:
        pass


def _records(folder: Path) -> dict[str, dict]:
    """What the run's agents produced, by label — the newest when a script reused one."""
    found: dict[str, dict] = {}
    place = folder / "agents"
    files = sorted(place.glob("*.json"), key=lambda one: one.stat().st_mtime) if place.is_dir() else []
    for record in files:
        try:
            one = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(one, dict) and _label(one.get("label")):
            found[_label(one["label"])] = one
    return found


def _known_labels(folder: Path, ledger: dict) -> dict[str, dict]:
    """Every label the run has used, with its kind and whether it finished.

    From the ledger for a step run; from the records on disk for a script run, whose agents only
    ever wrote down the ones that finished. An entry still pending in the ledger — its step was
    cut off by a restart — counts as finished only if its record made it to disk.
    """
    records = _records(folder)
    known = {label: {"kind": str(one.get("kind") or "scout"), "ok": True} for label, one in records.items()}
    for label, entry in (ledger.get("labels") or {}).items():
        if not isinstance(entry, dict):
            continue
        ok = entry.get("ok")
        known[label] = {
            "kind": str(entry.get("kind") or "scout"),
            "ok": (label in records) if ok is None else bool(ok and label in records),
        }
    return known


def _report_of(record: dict) -> str:
    """What an earlier agent said, as text another agent can read."""
    value = record.get("value")
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("report"), str):
        return value["report"]
    said = record.get("report")
    if isinstance(said, str) and said.strip() and value is None:
        return said
    return json.dumps(value, indent=1, default=str) if value is not None else str(said or "")


def _patch_of(record: dict) -> dict | None:
    value = record.get("value")
    if isinstance(value, dict) and str(value.get("diff") or "").strip():
        return value
    return None


# ── the tool ────────────────────────────────────────────────────────────────────────────────

DESCRIPTION = (
    "Hand work to agents, one step at a time, in the background. A builder makes a change in a "
    "private copy of the repo and hands back a patch; a scout reads and reports; a verifier "
    "checks a change against what the person asked and runs the tests. One call is one step. "
    "When it finishes you get a new turn with what they said, and you add the next step to the "
    'same run — add_agents(run="wf-…", agents=[...]) — for as long as the work needs: a builder, '
    "then a verifier after it, then a revise of what the verifier found. One builder is a fine "
    "first step. Allowed when workflow mode is on or their message starts with /workflow; "
    "continuing a run this chat started is always allowed.\n\n"
    "agents: [{label, kind, prompt, after}]. label: unique in the run — how later agents name "
    "this one. kind: scout (default), builder, verifier, merge (after: the builders to put "
    "together; checked in one copy), revise (after: one builder; prompt: what to change — it "
    "carries on that builder's own conversation with everything it read, so a builder that came "
    "back empty is revised, not sent again from zero). after: "
    "labels from this step or earlier ones — after a builder it starts from that patch, after a "
    "scout or verifier that report is added to its prompt. Agents without an after run at once, "
    f"{workflow.AT_ONCE} at a time. Optional per agent: model (strong|main|fast), schema.\n\n"
    "Every agent knows the project but nothing of this conversation: each prompt names the files, "
    "the change and what done looks like. Nothing is applied to the real tree — you git apply "
    "the patch you want and run the tests."
)


@tool(
    "add_agents",
    DESCRIPTION,
    {
        "agents": {
            "type": "array",
            "description": "This step's agents.",
            "items": {
                "type": "object",
                "properties": {
                    "label": STR,
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "prompt": STR,
                    "after": {"type": "array", "items": STR},
                    "model": STR,
                    "schema": {"type": "object", "additionalProperties": True},
                    "tests": BOOL,
                },
                "required": ["label"],
            },
        },
        "run": {**STR, "description": "The run to add this step to. Empty starts a new run."},
        "name": {**STR, "description": "A new run's short name."},
        "folder": {
            **STR,
            "description": (
                "A new run's working folder, when it is not this chat's project — a repository "
                "under your workspace, say. Builders' and verifiers' copies are of it. A run keeps "
                "it across its steps."
            ),
        },
        "budget_usd": {
            "type": "number",
            "description": f"A new run's ceiling across all its steps (default {workflow.DEFAULT_BUDGET_USD:g}).",
        },
        "wait": {
            **BOOL,
            "description": (
                f"Hold this turn until the step finishes (up to {workflow.WAIT_SECONDS // 60} "
                "minutes) and get its result here. For a short scout step."
            ),
        },
    },
    required=("agents",),
)
def add_agents(path: Path, args: dict):
    conversation = session_context.current()
    items, problem = read_items(args.get("agents"))
    if problem:
        return {"error": problem}
    unwatched = session_context.unattended()
    run_id = str(args.get("run") or "").strip()

    run: workflows.Run | None = None
    ledger: dict = {}
    unheard = ""
    if run_id:
        run, problem = _continuable(run_id, conversation)
        if problem:
            return {"error": problem}
        assert run is not None
        ledger = _ledger(Path(run.folder))
        in_a_row = int(ledger.get("unwatched_steps") or 0)
        if unwatched and in_a_row >= MAX_STEPS_UNWATCHED:
            return {
                "error": (
                    f"{in_a_row} steps of {run_id} have run in a row with nobody watching. Stop "
                    "here and tell them what the run has found; they can say to go on."
                )
            }
        problem = check_plan(items, _known_labels(Path(run.folder), ledger))
        if problem:
            return {"error": problem}
    else:
        refused = workflow.new_run_refusal(conversation, unwatched)
        if refused:
            return {"error": refused}
        problem = check_plan(items, {})
        if problem:
            return {"error": problem}
        try:
            budget = float(args.get("budget_usd") or workflow.DEFAULT_BUDGET_USD)
        except (TypeError, ValueError):
            return {"error": "budget_usd must be a number of dollars."}
        if budget <= 0 or budget > workflow.MAX_BUDGET_USD:
            return {"error": f"budget_usd must be more than 0 and at most {workflow.MAX_BUDGET_USD:g}."}
        workdir, refused = workflow._workdir(args, None)
        if refused:
            return {"error": refused}
        name = _label(args.get("name")) or items[0]["label"]
        ledger = {
            "workdir": workdir,
            "name": name,
            "conversation": conversation,
            "kind": "steps",
            "budget": budget,
            "spent": 0.0,
            "sent": 0,
            "steps": 0,
            "unwatched_steps": 0,
            "labels": {},
            "depth": 0,
        }

    step = int(ledger.get("steps") or 0) + 1
    phase = f"Step {step}"
    if run is None:
        run = workflows.start(
            conversation, ledger["name"], "", [phase], folder="", unwatched=unwatched, kind="steps"
        )
        if run is None:
            return {"error": _busy(conversation)}
        run.phase = phase
        folder = _folder_of(run.id)
        try:
            (folder / "agents").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            workflows.end(run, workflows.FAILED, f"The workflow `{run.name}` could not start: {exc}")
            return {"error": f"Could not set up the run folder: {exc}"}
        run.folder = str(folder)
        ledger.update(run=run.id, started=run.started)
    else:
        if workflows.running(conversation) is not None:
            return {"error": _busy(conversation)}
        # The last step's note, if the timer has not delivered it yet — reopening clears it, and a
        # step that ended seconds ago would otherwise never be heard. Taken here and handed back
        # with this call's answer instead, exactly once.
        unheard = workflows.claim(run)
        if not workflows.reopen(run, phase):
            return {"error": _busy(conversation), **({"last_step": unheard} if unheard else {})}

    # The folder its copies are of, chosen when the run started. A run restored after a restart
    # has only its ledger to say it, and `_open_copy` refuses every builder without one.
    run.workdir = str(ledger.get("workdir") or run.workdir or "")
    folder = Path(run.folder)
    ledger["steps"] = step
    ledger["unwatched_steps"] = int(ledger.get("unwatched_steps") or 0) + 1 if unwatched else 0
    ledger.setdefault("budget", run.budget or workflow.DEFAULT_BUDGET_USD)
    labels = ledger.setdefault("labels", {})
    for item in items:
        labels[item["label"]] = {"kind": item["kind"], "step": step, "ok": None}
    _save_ledger(folder, ledger)

    agents = workflow._Agents(float(ledger["budget"]))
    # A script run's folder never kept these; its board entry did, while the server still has it.
    agents.live = float(ledger.get("spent") or run.cost or 0.0)
    agents.sent = int(ledger.get("sent") or len(run.agents))
    run.budget = agents.budget

    # The turn's context travels with the step, as it does with a script's run: every agent
    # resolves its folder and its permissions through it.
    carried = copy_context()
    threading.Thread(
        target=carried.run,
        args=(_drive_step, run, path, items, step, agents),
        name=f"{run.id}-step{step}",
        daemon=True,
    ).start()

    answer: dict = {
        "run": run.id,
        "name": run.name,
        "step": step,
        "phases": list(run.phases),
        "agents": [item["label"] for item in items],
        "budget": f"${agents.budget:.2f}",
    }
    if agents.live:
        answer["spent_so_far"] = f"${agents.live:.2f}"
    if unheard:
        answer["last_step"] = unheard
    if args.get("wait") is True:
        finished = workflow._wait_for(run, conversation)
        if finished is not None:
            return {
                **answer,
                "finished": True,
                "result": finished or "It finished, and its result has already arrived as a new turn.",
            }
        answer["note"] = (
            f"Still running after {workflow.WAIT_SECONDS // 60} minutes, so it carries on in the "
            "background. End this turn saying so; you will get a new turn when it finishes."
        )
        return answer
    answer["note"] = (
        "Running in the background. End this turn now: say what you sent them to do. You get a "
        f'new turn with what they report, and add the next step with add_agents(run="{run.id}").'
    )
    return answer


def _busy(conversation: str) -> str:
    busy = workflows.running(conversation)
    return (
        f"A workflow step is already running here ({busy.name if busy else 'another'}, "
        f"{busy.id if busy else ''}). Wait for it to report — check_workflow shows where it is — "
        "or stop_workflow ends it."
    )


def _continuable(run_id: str, conversation: str) -> tuple[workflows.Run | None, str]:
    """This chat's run ``run_id``, ended and ready for another step, or why it is not."""
    run = workflows.get(run_id)
    if run is not None and run.conversation != conversation:
        run = None
    if run is None:
        folder = _folder_of(run_id)
        ledger = _ledger(folder)
        if not ledger or ledger.get("conversation") != conversation:
            return None, f"There is no run {run_id} in this chat."
        # The server restarted since; the run's record is its folder.
        steps = int(ledger.get("steps") or 0)
        phases = [f"Step {n}" for n in range(1, steps + 1)] or list(ledger.get("phases") or [])
        run = workflows.restore(
            run_id,
            conversation,
            str(ledger.get("name") or run_id),
            str(folder),
            phases,
            float(ledger.get("started") or 0.0),
            kind=str(ledger.get("kind") or "script"),
        )
    if run.state == workflows.RUNNING:
        return None, (
            f"{run_id} still has a step running. Wait for it to report — check_workflow shows "
            "where it is — or stop_workflow ends it."
        )
    return run, ""


# ── running a step ──────────────────────────────────────────────────────────────────────────


def _key(label: str, message: dict) -> str:
    """The record's name: what was asked, and by which label, so two identical asks stay two."""
    return hashlib.sha256(f"{label}\n{workflow._ask_key(message)}".encode()).hexdigest()[:24]


def _message(item: dict, phase: str, before: dict[str, dict]) -> dict:
    """The request `tools/workflow` runs for ``item``, with what it comes after filled in."""
    before = {dep: before.get(dep) or {"label": dep} for dep in item["after"]}
    patches = [patch for patch in (_patch_of(before[dep]) for dep in item["after"]) if patch]
    kind = item["kind"]
    if kind == "merge":
        message: dict = {
            "op": "merge",
            "built": patches,
            "label": item["label"],
            "check": True,
            "tests": True if item["tests"] else None,
        }
    elif kind == "revise":
        earlier = before[item["after"][0]]
        built = _patch_of(earlier) or dict(earlier.get("value") or {}) or {"label": item["after"][0]}
        message = {"op": "revise", "built": built, "feedback": item["prompt"], "label": item["label"]}
    else:
        prompt = item["prompt"]
        carried = [
            (dep, _report_of(before[dep]).strip())
            for dep in item["after"]
            if _patch_of(before[dep]) is None or str(before[dep].get("kind")) not in PATCHING
        ]
        carried = [(dep, text) for dep, text in carried if text]
        if carried:
            prompt += "\n\nWhat earlier agents in this workflow reported — start from it:\n\n" + "\n\n".join(
                f"## {dep}\n{text[:CARRIED_REPORT_CHARS]}"
                + ("\n… (cut)" if len(text) > CARRIED_REPORT_CHARS else "")
                for dep, text in carried
            )
        message = {
            "op": "agent",
            "kind": kind,
            "prompt": prompt,
            "label": item["label"],
            "after": patches,
            "schema": item["schema"],
            "model": item["model"],
            "effort": item["effort"],
            "phase": phase,
        }
    message["_key"] = _key(item["label"], message)
    return message


def _run_one(run: workflows.Run, path: Path, message: dict, agents, context: str) -> tuple[bool, str]:
    """Run one request. ``(finished, why not)``. Never raises."""
    op = message["op"]
    try:
        if op == "merge":
            workflow._merge(run, message, agents)
        elif op == "revise":
            workflow._revise(run, path, message, agents, context)
        else:
            workflow._one_agent(run, path, message, agents, context)
        return True, ""
    except workflow._Failed as exc:
        return False, str(exc)
    except Exception as exc:
        kind = str(message.get("kind") or op)
        agents.record(f'- {kind} "{message.get("label")}" — broke: {exc}', 0.0)
        return False, f"the agent broke: {exc}"


def _drive_step(run: workflows.Run, path: Path, items: list[dict], step: int, agents) -> None:
    """One step, on its own thread: start each agent when what it comes after is done."""
    with session_context.nobody_watching():
        folder = Path(run.folder)
        began = time.monotonic()
        deadline = began + workflow.MAX_SECONDS
        # Spend is counted as model calls report it; an agent whose calls never reported still
        # recorded what it cost when it ended, so the ledger keeps whichever is larger.
        spent_before = agents.live
        phase = f"Step {step}"
        why_not: dict[str, str] = {}
        finished: set[str] = set()
        state, why = workflows.FINISHED, ""
        timed_out = False
        pool = ThreadPoolExecutor(max_workers=workflow.AT_ONCE, thread_name_prefix=f"{run.id}-agent")
        going: dict[Future, dict] = {}
        pending = list(items)
        try:
            context = workers.project_brief(path, run.conversation)
            earlier = _records(folder)
            while pending or going:
                if run.halt.is_set():
                    for item in pending:
                        why_not[item["label"]] = "not started — the step was stopped"
                    pending = []
                for item in list(pending):
                    stuck = [dep for dep in item["after"] if dep in why_not]
                    if stuck:
                        why_not[item["label"]] = f'skipped — "{stuck[0]}" did not finish'
                        pending.remove(item)
                        continue
                    if not all(dep in finished or dep in earlier for dep in item["after"]):
                        continue
                    pending.remove(item)
                    if agents.over_budget():
                        why_not[item["label"]] = (
                            f"refused — the run has spent its ${agents.budget:.2f} budget"
                        )
                        continue
                    if item["kind"] != "merge" and not agents.take_slot():
                        why_not[item["label"]] = (
                            f"refused — the run has sent its {workflow.MAX_AGENTS} agents"
                        )
                        continue
                    # Read again for each start, so an agent from this step that just finished is
                    # there for the one that comes after it.
                    before = {**earlier, **_records(folder)}
                    message = _message(item, phase, before)
                    going[pool.submit(copy_context().run, _run_one, run, path, message, agents, context)] = (
                        item
                    )
                if not going:
                    for item in pending:
                        why_not[item["label"]] = "never started — what it comes after did not finish"
                    pending = []
                    break
                done, _ = wait_for_any(list(going), timeout=0.25, return_when=FIRST_COMPLETED)
                if not run.halt.is_set() and time.monotonic() >= deadline:
                    timed_out = True
                    run.halt.set()
                for future in done:
                    item = going.pop(future)
                    ok, reason = future.result()
                    if ok:
                        finished.add(item["label"])
                    else:
                        why_not[item["label"]] = f"failed: {reason}"
        except Exception as exc:
            state, why = workflows.FAILED, f"the step broke: {exc}"
            run.halt.set()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            agents.close_copies()

        if timed_out:
            state, why = (
                workflows.FAILED,
                f"it ran past its {workflow.MAX_SECONDS // 60} minutes and was ended",
            )
        elif run.halt.is_set() and state == workflows.FINISHED:
            state, why = workflows.STOPPED, "it was stopped"
        elif state == workflows.FINISHED and not finished:
            state, why = workflows.FAILED, "none of its agents finished"

        ledger = _ledger(folder)
        labels = ledger.setdefault("labels", {})
        for item in items:
            labels[item["label"]] = {"kind": item["kind"], "step": step, "ok": item["label"] in finished}
        spent = max(agents.live, spent_before + agents.cost)
        ledger["spent"] = round(spent, 6)
        ledger["sent"] = agents.sent
        _save_ledger(folder, ledger)

        note = _step_note(
            run, step, state, why, items, why_not, agents, time.monotonic() - began, ledger, spent
        )
        workflows.end(run, state, note, cost=round(spent, 4), agents=len(items))


def _step_note(
    run: workflows.Run,
    step: int,
    state: str,
    why: str,
    items: list[dict],
    why_not: dict[str, str],
    agents,
    seconds: float,
    ledger: dict,
    spent: float,
) -> str:
    """What the conversation wakes up to: what each agent did and said, and how to go on."""
    how = {
        workflows.FINISHED: "finished",
        workflows.STOPPED: "was stopped",
        workflows.FAILED: "failed",
    }.get(state, state)
    count = len(items)
    head = (
        f"Step {step} of `{run.name}` ({run.id}) {how} after {workflow._took(seconds)} — {count} "
        f"agent{'s' if count != 1 else ''}, ${agents.cost:.2f} (the run so far ${spent:.2f} "
        f"of ${agents.budget:.2f})."
    )
    if why and state != workflows.FINISHED:
        head += f" Why: {why}."
    if agents.hit_budget:
        head += " It reached its budget, and agents asked for after that were refused."
    records = _records(Path(run.folder))
    room = workflow.RESULT_PREVIEW_CHARS
    lines: list[str] = []
    patched = merged = False
    for left, item in enumerate(items):
        label, kind = item["label"], item["kind"]
        if label in why_not or label not in records:
            lines.append(f'- {kind} "{label}" — {why_not.get(label, "did not report")}')
            continue
        record = records[label]
        if _patch_of(record):
            patched = True
            merged = merged or kind == "merge"
        outcome = str(record.get("outcome") or "").strip() or (
            "changed nothing" if kind in PATCHING else "reported"
        )
        said = _report_of(record).strip() if kind != "merge" else ""
        share = max(300, room // max(1, count - left))
        if said:
            shown = said[:share] + (
                "\n  … (cut — the whole report is in its agents/ file)" if len(said) > share else ""
            )
            room -= min(len(said), share)
            lines.append(f'- {kind} "{label}" — {outcome}:\n  ' + shown.replace("\n", "\n  "))
        else:
            lines.append(f'- {kind} "{label}" — {outcome}')
    parts = [head, "\n".join(lines)]
    if merged:
        parts.append(
            "Nothing has been applied. The merged patch holds everything that fit together and has "
            "been checked in a copy — apply that one (`git apply` from the project root), not the "
            "builders' separate patches, then run the tests."
        )
    elif patched:
        parts.append(
            "Nothing has been applied. Read the patches, `git apply` the ones you want from the "
            "project root, check they fit together, and run the tests."
        )
    usable = [label for label, entry in (ledger.get("labels") or {}).items() if entry.get("ok")]
    usable += [label for label in records if label not in usable]
    parts.append(
        f'Next: add_agents(run="{run.id}", agents=[...]) adds a step to this run'
        + (f" — later agents can go after any of: {', '.join(usable)}." if usable else ".")
        + " Or, if the work is done, apply what you want and tell them."
    )
    parts.append(f"Everything the run wrote is in {run.folder}{workflow._transcripts_line(run)}.")
    return "\n\n".join(parts)
