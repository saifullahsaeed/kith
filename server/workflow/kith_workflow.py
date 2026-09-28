"""The helper every workflow script runs inside. Standard library only, and it has to stay so.

A workflow script is Python Kith wrote a moment ago, or one of the saved workflows in
`library/`. It runs in its own process, confined — no network, no home folder beyond its own run
folder — and it cannot do anything to the world except ask the server to run an agent. This file
is the whole of that asking: the globals a script is written against (`agent`, `merge`, `revise`,
`parallel`, `pipeline`, `workflow`, `phase`, `log`, `finish`, `budget`, `ARGS`, `ASKED`) and the
channel they speak over.

Run as ``python kith_workflow.py script.py`` in a checkout, or ``kith-server --run-workflow
kith_workflow.py script.py`` in the packaged app, whose entry point hands straight here. With
``--dry`` first, it runs the script against stand-in agents instead — see :class:`_Rehearsal`.

**The channel is a private copy of stdout, and stdout itself is pointed at stderr.** A script
that calls ``print`` — and a script written by a model will — would otherwise write straight into
the protocol and corrupt it. So the real stdout descriptor is duplicated for the protocol before
anything else runs, and descriptor 1 is then re-pointed at stderr, which the server writes to the
run's log. Stdin is taken the same way and replaced with /dev/null.

**Requests carry an id; replies are matched by it.** `parallel` runs its calls on threads, so
several `agent` requests are out at once and their answers come back in whatever order the
agents finish. How many actually *run* at once is the server's decision, not this file's.

If the server goes away — it was stopped, or it restarted — stdin closes, every waiting call is
released with an error, and the process exits. There is nothing left to be a workflow for.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import runpy
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

#: The kinds of agent there are. Mirrors `tools/workers.ROLES`; checked here too so a rehearsal
#: can name a misspelt kind before anything is spent.
KINDS = ("scout", "builder", "verifier")
MODELS = ("main", "strong", "fast")


class AgentFailed(Exception):
    """An agent could not do what it was sent for. `parallel` and `pipeline` turn it into None."""


class _Channel:
    def __init__(self) -> None:
        self._out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
        self._in = os.fdopen(os.dup(0), "r", encoding="utf-8")
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        quiet = os.open(os.devnull, os.O_RDONLY)
        os.dup2(quiet, 0)
        os.close(quiet)

        self._lock = threading.Lock()
        self._waiting: dict[int, list] = {}
        self._next = 0
        threading.Thread(target=self._read, name="workflow-replies", daemon=True).start()

    def send(self, message: dict) -> None:
        line = json.dumps(message, default=str)
        with self._lock:
            self._out.write(line + "\n")
            self._out.flush()

    def ask(self, message: dict) -> dict:
        arrived = threading.Event()
        slot: list = [arrived, None]
        with self._lock:
            self._next += 1
            ident = self._next
            self._waiting[ident] = slot
        self.send({**message, "id": ident})
        arrived.wait()
        return slot[1] or {"ok": False, "error": "no reply"}

    def _read(self) -> None:
        for line in self._in:
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                slot = self._waiting.pop(reply.get("id"), None)
            if slot is not None:
                slot[1] = reply
                slot[0].set()
        # The server hung up. Release everyone, then go: nothing can be answered any more.
        with self._lock:
            waiting = list(self._waiting.values())
            self._waiting.clear()
        for slot in waiting:
            slot[1] = {"ok": False, "error": "the server stopped this workflow"}
            slot[0].set()
        os._exit(3)


class _Rehearsal:
    """A channel with no server behind it: every agent answers at once with a stand-in.

    Run before the real thing, so a script that crashes — `with parallel(...)`, a misspelt kind,
    a key the schema never promised — crashes here, in a second, costing nothing, instead of ten
    minutes and a dollar into a run nobody is watching. It also catches the mistake that wasted
    four agents in the first seven real runs: ``agent(survey())`` where `survey` already sends an
    agent, so the second agent's *prompt* is the first one's report. A stand-in report is marked,
    so a prompt that is one is recognisable.
    """

    MARK = "[rehearsal: the report of agent "

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0
        self.agents = 0
        self.builders = 0
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def send(self, message: dict) -> None:
        pass

    def ask(self, message: dict) -> dict:
        op = message.get("op")
        with self._lock:
            self._next += 1
            number = self._next
        if op == "budget":
            return {"ok": True, "value": {"total": message.get("total"), "spent": 0.0}}
        if op == "merge":
            return {"ok": True, "value": _stand_in_merge(number, message)}
        if op == "revise":
            return {"ok": True, "value": _stand_in_built(number, message.get("label") or "revised")}
        return self._agent(number, message)

    def _agent(self, number: int, message: dict) -> dict:
        kind = str(message.get("kind") or "scout")
        where = _script_line()
        prompt = str(message.get("prompt") or "")
        with self._lock:
            self.agents += 1
            if kind not in KINDS:
                self.problems.append(f"{where}: there is no {kind!r} kind of agent — use {', '.join(KINDS)}")
            model = message.get("model")
            if model and model not in MODELS:
                self.problems.append(f"{where}: model must be one of {', '.join(MODELS)}, not {model!r}")
            if prompt.strip().startswith(self.MARK):
                self.problems.append(
                    f"{where}: this agent's prompt is another agent's report — agent() was called on "
                    "the result of something that already sends an agent. Pass the report inside a "
                    "prompt you write, or call the function without wrapping it in agent()."
                )
            if not prompt.strip():
                self.problems.append(f"{where}: an agent was sent with an empty prompt")
            if kind in ("verifier", "scout") and self.builders and not message.get("after"):
                wording = prompt.lower()
                if kind == "verifier" or any(word in wording for word in ("review", "verify", "check the change")):
                    self.warnings.append(
                        f"{where}: a {kind} sent after builders without after=[...] reads the "
                        "unchanged tree, not what they built"
                    )
            if kind == "builder":
                self.builders += 1
        label = str(message.get("label") or f"agent {number}")
        schema = message.get("schema")
        if kind == "builder":
            built = _stand_in_built(number, label)
            if schema is not None:
                built["report"] = _stand_in(schema)
            return {"ok": True, "value": built}
        if schema is not None:
            return {"ok": True, "value": _stand_in(schema)}
        return {"ok": True, "value": f"{self.MARK}{number}: {label}]"}


#: What a stand-in builder "changed". Not empty, so a rehearsal walks the path a script takes
#: when builders succeed — merge, verify, fix — which is the long path and the one worth checking.
STAND_IN_DIFF = "diff --git a/rehearsal b/rehearsal\n(a stand-in patch: nothing real was changed)\n"


def _stand_in_built(number: int, label: str) -> dict:
    return {
        "report": f"{_Rehearsal.MARK}{number}: {label}]",
        "diff": STAND_IN_DIFF,
        "patch_file": "",
        "label": label,
        "agent": f"rehearsal-{number}",
    }


def _stand_in_merge(number: int, message: dict) -> dict:
    return {
        **_stand_in_built(number, str(message.get("label") or "merged")),
        "applied": [str(one.get("label") or "") for one in message.get("built") or [] if isinstance(one, dict)],
        "conflicts": [],
        "check": [],
        "tests": [],
        "clean": True,
    }


def _stand_in(schema):
    """A value of the shape asked for — an example shape or JSON Schema — to stand in for a report."""
    if isinstance(schema, dict) and isinstance(schema.get("type"), str):
        kind = schema["type"]
        if kind == "object":
            return {key: _stand_in(sub) for key, sub in (schema.get("properties") or {}).items()}
        if kind == "array":
            return [_stand_in(schema.get("items") or {"type": "string"})]
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            return schema["enum"][0]
        return {"string": "rehearsal", "integer": 0, "number": 0, "boolean": False}.get(kind)
    if isinstance(schema, dict):
        return {key: _stand_in(value) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_stand_in(schema[0])] if schema else []
    if isinstance(schema, str):
        word = schema.strip().lower()
        if word in ("int", "integer", "float", "number"):
            return 0
        if word in ("bool", "boolean"):
            return False
        return "rehearsal"
    return schema


def _script_line() -> str:
    """Where in the script the current call came from, for a rehearsal's report."""
    for frame in reversed(traceback.extract_stack()):
        if frame.filename.endswith("script.py") or "library/" in frame.filename:
            return f"{Path(frame.filename).name} line {frame.lineno}"
    return "the script"


_channel: _Channel | _Rehearsal | None = None


def _speak():
    if _channel is None:
        raise RuntimeError("kith_workflow is only usable inside a running workflow")
    return _channel


def _as_patch(one) -> dict | None:
    if isinstance(one, dict) and str(one.get("diff") or "").strip():
        return {"diff": str(one["diff"]), "label": str(one.get("label") or "")}
    if isinstance(one, str) and one.strip():
        return {"diff": one, "label": ""}
    return None


def agent(
    prompt: str,
    kind: str = "scout",
    label: str | None = None,
    schema=None,
    after=None,
    model: str | None = None,
    effort: str | None = None,
    phase: str | None = None,
):
    """Send one agent and wait for it.

    ``kind="scout"`` reads, searches and reports, and returns its report as a string.
    ``kind="builder"`` edits a private copy of the repository — and can run the project's check
    and tests there — and returns ``{"report", "diff", "patch_file", "label", "agent"}``.
    ``kind="verifier"`` checks a change against what the person asked (it is given their words
    automatically); send it ``after=[...]`` the change it is checking. With ``schema`` (a
    JSON-shaped example such as ``{"files": ["str"]}``, or JSON Schema) the agent hands its
    answer back through a checked `submit` call and you get that value instead of prose.

    ``model`` is ``"strong"``, ``"fast"`` or ``"main"`` (the role's default when omitted: scouts
    fast, builders main, verifiers strong). ``effort`` is a reasoning effort such as ``"low"`` or
    ``"high"``. ``phase`` files the agent under a phase in the panel without changing the phase.

    Every agent starts from the repository as it is — it does NOT see what earlier builders in
    this workflow changed. ``after=[built, ...]`` (builder or merge results, or diff strings)
    starts it from a copy with those patches applied, in order: that is how an integration step,
    a review or a verifier sees the new code. A ``None`` in the list (a builder that failed) is
    skipped.

    The prompt is everything the agent knows about the task. It starts knowing the project (its
    notes and references are given to every agent), but none of the conversation — so say what
    to change or find, which files, and what done looks like.
    """
    earlier = [patch for patch in (_as_patch(one) for one in after or []) if patch]
    reply = _speak().ask(
        {
            "op": "agent",
            "prompt": str(prompt),
            "kind": str(kind or "scout"),
            "label": None if label is None else str(label),
            "schema": schema,
            "after": earlier,
            "model": model,
            "effort": effort,
            "phase": phase,
        }
    )
    if not reply.get("ok"):
        raise AgentFailed(str(reply.get("error") or "the agent failed"))
    return reply.get("value")


def merge(*built, label: str | None = None, check: bool = True, tests=False) -> dict:
    """Put builders' patches together in one copy, check them there, and hand back the result.

    Each patch is applied on its own, in order; one that does not fit is left out and named in
    ``conflicts`` rather than stopping the rest. With ``check`` the project's own checker runs on
    every project the patches touch; with ``tests=True`` (or a filter string) its tests run too —
    all inside the copy's boundary. Returns ``{"diff", "patch_file", "label", "agent", "applied",
    "conflicts", "check", "tests", "clean"}``: a builder-shaped result, so it can go in
    ``after=[...]`` or be handed to :func:`revise` to fix what the check found.
    """
    items = list(built[0]) if len(built) == 1 and isinstance(built[0], (list, tuple)) else list(built)
    patches = []
    for one in items:
        patch = _as_patch(one)
        if patch:
            patches.append(patch)
    reply = _speak().ask(
        {
            "op": "merge",
            "built": patches,
            "label": None if label is None else str(label),
            "check": bool(check),
            "tests": tests if isinstance(tests, str) else bool(tests),
        }
    )
    if not reply.get("ok"):
        raise AgentFailed(str(reply.get("error") or "the merge failed"))
    return reply.get("value")


def revise(built: dict, feedback: str, label: str | None = None) -> dict:
    """Send a builder (or a merge) back into its own copy with feedback, and get the new result.

    The copy still has every edit that was made, and the builder is handed back its brief and its
    report, so it picks up where it stopped instead of starting over. Use it for a builder that
    came back empty or half done, for a seam two builders disagree on, or to fix what a merge's
    check or a verifier found. Returns the same shape as a builder, with the *whole* diff so far.
    """
    if not isinstance(built, dict):
        raise AgentFailed("revise() takes a builder's or a merge's result")
    reply = _speak().ask(
        {
            "op": "revise",
            "built": {
                "agent": str(built.get("agent") or ""),
                "diff": str(built.get("diff") or ""),
                "label": str(built.get("label") or ""),
                "report": built.get("report") if isinstance(built.get("report"), str) else json.dumps(built.get("report"), default=str),
            },
            "feedback": str(feedback),
            "label": None if label is None else str(label),
        }
    )
    if not reply.get("ok"):
        raise AgentFailed(str(reply.get("error") or "the revision failed"))
    return reply.get("value")


def _positional(fn) -> int:
    """How many positional arguments ``fn`` takes, or 1 when that cannot be told."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return 1
    if any(p.kind == p.VAR_POSITIONAL for p in params):
        return 3
    return sum(1 for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty)


def parallel(calls) -> list:
    """Run every zero-argument callable at once. Results in order; None where one raised.

    In a loop, bind the loop variable: ``[lambda p=p: agent(f"look at {p}") for p in parts]``.
    Without the default argument every lambda sees the last ``p``.
    """
    calls = list(calls)
    if not calls:
        return []
    results: list = [None] * len(calls)

    def one(index: int, call) -> None:
        try:
            results[index] = call()
        except Exception as exc:
            log(f"a parallel call failed: {exc}")
            results[index] = None

    with ThreadPoolExecutor(max_workers=min(32, len(calls))) as pool:
        for index, call in enumerate(calls):
            pool.submit(one, index, call)
    return results


def pipeline(items, *stages) -> list:
    """Push each item through ``stages`` on its own, without waiting for the others.

    Each stage is called with what the previous one returned (the first with the item) — and,
    if it takes them, with the original item and its index too: ``lambda report, part, i: …``.
    A stage returning None drops that item from the rest of the pipeline, and so does one that
    raises.
    """
    items = list(items)

    def carry(item, index):
        value = item
        for stage in stages:
            value = stage(value, item, index) if _positional(stage) >= 3 else stage(value)
            if value is None:
                return None
        return value

    return parallel([lambda item=item, index=index: carry(item, index) for index, item in enumerate(items)])


class _Budget:
    """What this run may spend, and has. ``budget.remaining()`` is what is left, in dollars.

    The ceiling is hard: once the run has spent it, further agents are refused and running ones
    are stopped. Size a fan-out to it: ``for part in parts[: int(budget.remaining() // 0.4)]``.
    """

    def __init__(self, total: float | None) -> None:
        self.total = total

    def spent(self) -> float:
        reply = _speak().ask({"op": "budget", "total": self.total})
        value = reply.get("value") or {}
        return float(value.get("spent") or 0.0)

    def remaining(self) -> float:
        if self.total is None:
            return float("inf")
        return max(0.0, float(self.total) - self.spent())


budget = _Budget(None)

#: Where a `workflow()` call inside a script keeps its child's `finish()` value.
_finishing = threading.local()


@contextlib.contextmanager
def phase(title: str):
    """Mark the part of the plan that is running now, for the panel."""
    _speak().send({"op": "phase", "title": str(title)})
    yield


def log(text) -> None:
    """A progress line in the panel. ``print`` goes to the run's log file instead."""
    _speak().send({"op": "log", "text": str(text)})


def finish(value) -> None:
    """Hand back the result. Called once, at the end; the last call wins."""
    held = getattr(_finishing, "slot", None)
    if held is not None:
        held["value"] = value
        return
    _speak().send({"op": "finish", "value": value})


def workflow(name: str, args=None):
    """Run a saved workflow as one step of this one, and return what it finished with.

    It runs here, in this run: its agents share this run's limits and budget and appear under
    this run in the panel. One level only — a saved workflow that calls `workflow()` fails.
    """
    if getattr(_finishing, "slot", None) is not None:
        raise RuntimeError("workflow() can only be called from the top-level script, one level deep")
    wanted = str(name or "").strip()
    found = Path("library") / f"{wanted}.py"
    if not wanted or "/" in wanted or not found.is_file():
        known = sorted(p.stem for p in Path("library").glob("*.py")) if Path("library").is_dir() else []
        raise RuntimeError(f"there is no saved workflow {wanted!r} — there is {', '.join(known) or 'none'}")
    slot: dict = {}
    _finishing.slot = slot
    try:
        runpy.run_path(str(found), init_globals={**_globals(), "ARGS": args}, run_name="__workflow__")
    finally:
        _finishing.slot = None
    return slot.get("value")


def _inputs() -> dict:
    """What the server handed this run beyond the script: its args, their words, its budget."""
    try:
        found = json.loads(Path("inputs.json").read_text(encoding="utf-8"))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def _globals() -> dict:
    handed = _inputs()
    return {
        "agent": agent,
        "merge": merge,
        "revise": revise,
        "parallel": parallel,
        "pipeline": pipeline,
        "workflow": workflow,
        "phase": phase,
        "log": log,
        "finish": finish,
        "budget": budget,
        "AgentFailed": AgentFailed,
        "ARGS": handed.get("args"),
        "ASKED": str(handed.get("asked") or ""),
    }


def main(argv: list[str]) -> int:
    global _channel
    rehearsing = len(argv) > 1 and argv[1] == "--dry"
    if rehearsing:
        argv = [argv[0], *argv[2:]]
    if len(argv) < 2:
        print("usage: kith_workflow.py [--dry] script.py", file=sys.stderr)
        return 2
    _channel = _Rehearsal() if rehearsing else _Channel()
    # A script that writes `from kith_workflow import agent` must get *this* module — the one
    # holding the open channel — not a fresh import of the same file with none.
    sys.modules["kith_workflow"] = sys.modules[__name__]
    budget.total = _inputs().get("budget")
    code = 0
    told = ""
    try:
        runpy.run_path(argv[1], init_globals=_globals(), run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
    except BaseException:
        told = traceback.format_exc()
        print(told, file=sys.stderr)
        if not rehearsing:
            _channel.send({"op": "error", "text": told[-4000:]})
        code = 1
    if isinstance(_channel, _Rehearsal):
        Path("rehearsal.json").write_text(
            json.dumps(
                {
                    "crashed": told[-3000:],
                    "problems": _channel.problems,
                    "warnings": sorted(set(_channel.warnings)),
                    "agents": _channel.agents,
                }
            ),
            encoding="utf-8",
        )
        return 0
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
