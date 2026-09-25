"""The helper every workflow script runs inside. Standard library only, and it has to stay so.

A workflow script is Python Kith wrote a moment ago. It runs in its own process, confined — no
network, no home folder beyond its own run folder — and it cannot do anything to the world
except ask the server to run an agent. This file is the whole of that asking: the globals a
script is written against (`agent`, `parallel`, `pipeline`, `phase`, `log`, `finish`) and the
channel they speak over.

Run as ``python kith_workflow.py script.py`` in a checkout, or ``kith-server --run-workflow
kith_workflow.py script.py`` in the packaged app, whose entry point hands straight here.

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
import json
import os
import runpy
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor


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


_channel: _Channel | None = None


def _speak() -> _Channel:
    if _channel is None:
        raise RuntimeError("kith_workflow is only usable inside a running workflow")
    return _channel


def agent(prompt: str, kind: str = "scout", label: str | None = None, schema=None):
    """Send one agent and wait for it.

    ``kind="scout"`` reads, searches and reports, and returns its report as a string.
    ``kind="builder"`` edits a private copy of the repository and returns
    ``{"report", "diff", "patch_file"}``. With ``schema`` (a JSON-shaped example such as
    ``{"files": ["str"]}``) the report comes back as that parsed JSON instead of prose.

    The prompt is everything the agent knows. It starts with none of the conversation, so say
    what to look at, what you already know, and what a useful answer contains.
    """
    reply = _speak().ask(
        {
            "op": "agent",
            "prompt": str(prompt),
            "kind": str(kind or "scout"),
            "label": None if label is None else str(label),
            "schema": schema,
        }
    )
    if not reply.get("ok"):
        raise AgentFailed(str(reply.get("error") or "the agent failed"))
    return reply.get("value")


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

    Each stage is called with what the previous one returned (the first with the item). A stage
    returning None drops that item from the rest of the pipeline, and so does one that raises.
    """

    def carry(item):
        value = item
        for stage in stages:
            value = stage(value)
            if value is None:
                return None
        return value

    return parallel([lambda item=item: carry(item) for item in list(items)])


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
    _speak().send({"op": "finish", "value": value})


def main(argv: list[str]) -> int:
    global _channel
    if len(argv) < 2:
        print("usage: kith_workflow.py script.py", file=sys.stderr)
        return 2
    _channel = _Channel()
    # A script that writes `from kith_workflow import agent` must get *this* module — the one
    # holding the open channel — not a fresh import of the same file with none.
    sys.modules["kith_workflow"] = sys.modules[__name__]
    offered = {
        "agent": agent,
        "parallel": parallel,
        "pipeline": pipeline,
        "phase": phase,
        "log": log,
        "finish": finish,
        "AgentFailed": AgentFailed,
    }
    try:
        runpy.run_path(argv[1], init_globals=offered, run_name="__main__")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    except BaseException:
        told = traceback.format_exc()
        print(told, file=sys.stderr)
        _channel.send({"op": "error", "text": told[-4000:]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
