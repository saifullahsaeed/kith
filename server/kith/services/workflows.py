"""Workflow runs that are going, and the ones that have finished and not been heard about.

The tool that starts a run (`tools/workflow`) is an adapter, and the scheduler that wakes a
conversation when one finishes is a service — so the scheduler cannot import the tool, and the
fact they share has to live down here. This module is that fact and nothing else: which runs
exist, whether each has been asked to stop, and what each said when it ended. It starts no
processes and runs no agents.

**In memory, deliberately.** A run is a live subprocess and a pool of model streams; after a
restart there is nothing left to be the run, and a record claiming otherwise would put a Stop
button in the panel that stops nothing. The files a run wrote survive in its folder, which is
where anything worth keeping already is.

**Reported the way a background process is reported.** `finished_since_last_look` is shaped
exactly like `engine.run.processes.finished_since_last_look` and read on the same timer, because
it answers the same question: something this conversation started has come back, and it should
hear about it. Unlike a process, a stopped run *is* reported — a workflow stopped part-way still
has agents that finished and patches worth applying, and nobody knows what those are yet.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from kith.services.activity import feed

#: Words for how a run ended, which are also the states the panel draws.
RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
STOPPED = "stopped"


@dataclass
class Run:
    id: str
    conversation: str
    name: str
    description: str
    phases: list[str]
    folder: str
    started: float = field(default_factory=time.time)
    state: str = RUNNING
    #: The phase the script last announced, so an agent can be filed under the phase it ran in.
    phase: str = ""
    #: Set by Stop. The runner reads it between protocol lines and every agent reads it between
    #: its own events, so one switch reaches the script and all of its agents.
    halt: threading.Event = field(default_factory=threading.Event)
    #: What the conversation is told when it wakes. Written once, when the run ends.
    note: str = ""
    reported: bool = False
    #: The last `log()` line while it runs, the first line of the note once it ends.
    said: str = ""
    ended: float = 0.0
    cost: float = 0.0
    #: Every agent it sent, by id, as the agent map draws them: kind, label, phase, state, the
    #: prompt, what it said, what it cost, its patch, and its last few steps.
    #:
    #: Kept here as well as published on the feed, because the feed is a ring of the last few
    #: hundred lines and a busy conversation pushes a run's lines out of it within minutes —
    #: after which a window opened fresh had no way to draw a run that was still going.
    agents: dict[str, dict] = field(default_factory=dict)
    #: The run this one resumes, and what that run's agents already produced, by the hash of
    #: what each was asked. An agent() call that matches one is answered from here instead of
    #: being run again — which is what makes "fix the script and resume" cost only the fix.
    resumed_from: str = ""
    reuse: dict[str, dict] = field(default_factory=dict)
    #: Started in a turn nobody opened — the one a finished run or process wakes. See
    #: :func:`unwatched_in_a_row`.
    unwatched: bool = False

    def public(self) -> dict:
        return {
            "run": self.id,
            "conversation": self.conversation,
            "name": self.name,
            "description": self.description,
            "phases": list(self.phases),
            "phase": self.phase,
            "folder": self.folder,
            "started": self.started,
            "ended": self.ended,
            "state": self.state,
            "said": self.said,
            "cost": self.cost,
            "resumed_from": self.resumed_from,
            "agents": sorted(
                (dict(one) for one in self.agents.values()), key=lambda one: one.get("started", 0)
            ),
        }


_RUNS: dict[str, Run] = {}
_LOCK = threading.Lock()


def start(
    conversation: str,
    name: str,
    description: str,
    phases: list[str],
    folder: str,
    unwatched: bool = False,
) -> Run | None:
    """Open a run, or None when this conversation already has one going.

    One at a time per conversation. Two workflows fanning out over the same repository at once
    are two sets of patches written against the same tree without either knowing about the
    other, and the panel would have to tell apart two headers that both say "running".
    """
    with _LOCK:
        if any(one.conversation == conversation and one.state == RUNNING for one in _RUNS.values()):
            return None
        run = Run(
            id=f"wf-{uuid.uuid4().hex[:6]}",
            conversation=conversation,
            name=name,
            description=description,
            phases=list(phases),
            folder=folder,
            unwatched=unwatched,
        )
        _RUNS[run.id] = run
    say(run, "started", name, description=description, phases=list(phases), folder=folder)
    return run


def get(run_id: str) -> Run | None:
    return _RUNS.get(run_id)


def latest(conversation: str) -> Run | None:
    """The newest run this process knows about for a conversation, running or not."""
    with _LOCK:
        mine = [one for one in _RUNS.values() if one.conversation == conversation]
    return max(mine, key=lambda one: one.started) if mine else None


def unwatched_in_a_row(conversation: str) -> int:
    """How many of this conversation's newest runs were started with nobody there, back to the
    last one a person's turn started.

    The bound on a chain in workflow mode: survey, then build, then review is three runs, and the
    second and third start in the turn the one before wakes. In memory like the rest of the
    board, so a restart forgets the chain — and a restart is also the end of every run in it.
    """
    with _LOCK:
        mine = sorted(
            (one for one in _RUNS.values() if one.conversation == conversation),
            key=lambda one: one.started,
            reverse=True,
        )
    count = 0
    for one in mine:
        if not one.unwatched:
            break
        count += 1
    return count


def running(conversation: str) -> Run | None:
    with _LOCK:
        return next(
            (one for one in _RUNS.values() if one.conversation == conversation and one.state == RUNNING), None
        )


def listing(conversation: str = "") -> list[dict]:
    """Every run this process knows about, newest first, optionally for one conversation."""
    with _LOCK:
        runs = [one for one in _RUNS.values() if not conversation or one.conversation == conversation]
    return [one.public() for one in sorted(runs, key=lambda one: one.started, reverse=True)]


def stop(run_id: str) -> bool:
    """Ask a run to stop. True when there was a running one to ask."""
    run = _RUNS.get(run_id)
    if run is None or run.state != RUNNING:
        return False
    run.halt.set()
    return True


#: Steps kept per agent for the map. The feed carries every one live; the board only has to be
#: able to say what an agent was doing lately to a window that just opened.
KEPT_STEPS = 8


def note_agent(run: Run, agent_id: str, *, step: dict | None = None, **fields) -> None:
    """Record what one agent is, or has just done, for the agent map. Never raises."""
    try:
        with _LOCK:
            agent = run.agents.setdefault(agent_id, {"id": agent_id, "steps": []})
            if "state" in fields and fields["state"] == "running" and "started" not in agent:
                agent["started"] = time.time()
            if fields.get("state") == "done":
                agent["ended"] = time.time()
            agent.update(fields)
            if step is not None:
                agent["steps"] = [*agent["steps"], step][-KEPT_STEPS:]
                agent["calls"] = int(agent.get("calls") or 0) + 1
    except Exception:
        pass


def end(run: Run, state: str, note: str, **extra) -> None:
    """Close a run and leave the note its conversation will be woken with."""
    with _LOCK:
        run.state = state
        run.note = note
        run.ended = time.time()
        run.said = note.splitlines()[0] if note else state
        run.cost = float(extra.get("cost") or 0.0)
    say(
        run,
        state,
        note.splitlines()[0] if note else state,
        seconds=round(time.time() - run.started, 1),
        **extra,
    )


def say(run: Run, state: str, text: str, **extra) -> None:
    """One line on the live feed about this run. Never the thing that takes a run down."""
    try:
        feed.publish(
            "workflow",
            text,
            workflow={"run": run.id, "state": state, "name": run.name, **extra},
            conversation=run.conversation,
        )
    except Exception:
        pass


def finished_since_last_look() -> dict[str, list[str]]:
    """Every run that has ended and not been reported, grouped by the chat that started it.

    Returns ``{conversation_id: notes}`` — what happened, not what to do about it, the same
    contract as the process table's version of this, and read by the same caller.
    """
    by_chat: dict[str, list[str]] = {}
    with _LOCK:
        for run in _RUNS.values():
            if run.state == RUNNING or run.reported:
                continue
            run.reported = True
            if run.conversation and run.note:
                by_chat.setdefault(run.conversation, []).append(run.note)
    return by_chat


def forget_all() -> None:
    """For tests: a clean board."""
    with _LOCK:
        _RUNS.clear()
