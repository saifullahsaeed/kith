"""Starting agents, talking to them, stopping them, cleaning up after them.

Every session has these — the person's chat and every agent alike — because an agent is a whole
session and decides for itself whether its part of the work splits again. The strategy for when
and how to use them is not here: it is the strategy skill, in every session's system prompt, so it
can be changed without changing code. See `services/agents` for the mechanism.

Names are relative to the caller. A session can address its own agents and nobody else's: the
line of communication runs one link at a time, up and down the tree.
"""

from __future__ import annotations

from pathlib import Path

from kith.kernel import session_context
from kith.services import agents
from kith.tools.registry import tool

_NAMES = {
    "type": "array",
    "items": {"type": "string"},
    "description": 'Names of your agents, or ["all"] for every open one.',
}


def _here() -> str:
    return session_context.current()


@tool(
    "start_agents",
    "Start agents — full sessions like you, with every tool and skill you have — to work on parts "
    "of the task in parallel. Each gets its own conversation and, inside a git repository, its own "
    "worktree on a new branch from your last commit. This call then WAITS: it returns when the "
    "first of them reports or asks you something, and that report or question is right below. "
    "Handle it, then call `wait_for_agents` for the next. Follow the agent strategy in your "
    "instructions for when to do this and how to split the work; doing a small or tightly coupled "
    "task yourself is often right.",
    {
        "agents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short and unique among your agents — how you address it later.",
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "Everything it needs, self-contained: the goal and why, what exists "
                            "already (paths), the contract it must keep to, what it owns and must "
                            "not touch, how to check its work, and what done means. It cannot see "
                            "this conversation."
                        ),
                    },
                    "workspace": {
                        "type": "string",
                        "enum": ["own", "shared"],
                        "description": (
                            "own (default): its own worktree and branch, which you merge. shared: "
                            "it works in your folder — for research or review that changes nothing."
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": "main (default), strong, fast, or a model id.",
                    },
                },
                "required": ["name", "task"],
            },
        },
        "folder": {
            "type": "string",
            "description": (
                "Where the work is: the project folder or repository they branch from or share. "
                "Defaults to where you are working; needed when that is the whole workspace."
            ),
        },
        "wait": {
            "type": "boolean",
            "description": "Default true. false returns at once — only for work of your own that does not overlap theirs.",
        },
    },
    required=("agents",),
)
def start_agents(path: Path, args: dict):
    here = _here()
    if not here:
        return {"ok": False, "error": "Agents can only be started from a conversation."}
    specs = [one for one in args.get("agents") or [] if isinstance(one, dict)]
    if not specs:
        return {"ok": False, "error": "Name at least one agent and its task."}
    answer = agents.start(here, specs, folder=str(args.get("folder") or ""))
    # Waiting is the default because telling a handler to wait did not make it wait: on the first
    # real runs handlers read the code they had handed out, or ran `sleep` in a loop, while their
    # agents worked. A call that does not return until there is something to act on is the shape
    # of waiting; `wait: false` is the way out for work of its own.
    started = [one for one in answer.get("agents") or [] if "error" not in one]
    if started and args.get("wait", True) is not False:
        answer["waited"] = agents.wait_for(here)
    return answer


@tool(
    "message_agents",
    "Say something to your agents. If one is waiting on a question it asked you, this is the "
    "answer. If it is working, it gets this at its next step — use it to steer. If it has "
    "reported or was stopped, this starts it again with new instructions: send it back to fix or "
    "extend its work, and it keeps everything it already knows.",
    {
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "The agent's name."},
                    "text": {"type": "string", "description": "What to tell it."},
                },
                "required": ["agent", "text"],
            },
        }
    },
    required=("messages",),
)
def message_agents(path: Path, args: dict):
    here = _here()
    if not here:
        return {"ok": False, "error": "There are no agents outside a conversation."}
    said = [
        agents.message(here, str(one.get("agent") or ""), str(one.get("text") or ""))
        for one in args.get("messages") or []
        if isinstance(one, dict)
    ]
    return {"messages": said}


@tool(
    "stop_agents",
    "Stop agents' turns, and every agent under them. Their conversations and worktrees stay; "
    "message one to start it again.",
    {"agents": _NAMES},
    required=("agents",),
)
def stop_agents(path: Path, args: dict):
    here = _here()
    if not here:
        return {"ok": False, "error": "There are no agents outside a conversation."}
    return agents.stop(here, list(args.get("agents") or []))


@tool(
    "close_agents",
    "Clean up when the whole task is finished and merged: removes agents' worktrees and branches "
    "and closes them for good, with everything under them. Refuses an agent that is still working "
    "or whose branch is not merged into your checkout, unless force is true. Do this once at the "
    "end, not between rounds — a finished agent can still be given more work.",
    {
        "agents": _NAMES,
        "force": {
            "type": "boolean",
            "description": "Close even if still working or unmerged — discards that work.",
        },
    },
    required=("agents",),
)
def close_agents(path: Path, args: dict):
    here = _here()
    if not here:
        return {"ok": False, "error": "There are no agents outside a conversation."}
    return agents.close(here, list(args.get("agents") or []), force=bool(args.get("force")))


@tool(
    "wait_for_agents",
    "Wait for your agents: holds your turn until one of them reports or asks you something, then "
    "returns and the report or question is right below. Use it instead of reading their code or "
    "checking on them while they work. Also returns when your person says something, when none of "
    "your agents is working any more, or after `minutes` (default 20, at most 60) — call it again "
    "to keep waiting.",
    {"minutes": {"type": "number", "description": "How long to wait at most. Default 20."}},
)
def wait_for_agents(path: Path, args: dict):
    here = _here()
    if not here:
        return {"ok": False, "error": "There are no agents outside a conversation."}
    return agents.wait_for(here, float(args.get("minutes") or 20))
