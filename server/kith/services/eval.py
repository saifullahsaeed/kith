"""Tiny eval harness for the work loop.

Seeds a few canned tasks, runs one real autonomy tick against each, then scores
what came out — did he attach a deliverable, advance the task, stay grounded,
and not thrash? Gives us a repeatable read on work quality as we tune prompts,
context, and the loop.

Run inside the server container:

    docker exec kith-server python -m kith.eval

It mutates the live agent.db only transiently: each seeded task (and its
comments/deliverables) is deleted at the end of its case.
"""

from __future__ import annotations

import time

from kith import autonomy
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo

# Each case is a self-contained work task with a rubric we can eyeball against
# the resulting task_detail. Keep them small and gradeable.
CASES = [
    {
        "goal": "Write a 3-bullet summary of what Kith is",
        "description": (
            "Ground it in what you actually know about yourself and your notes. "
            "Attach the finished summary as a deliverable on this task, then mark it done."
        ),
        "want": "a deliverable with 3 bullets; task advanced to done",
    },
    {
        "goal": "List two concrete ways you could be more useful to me this week",
        "description": (
            "Think about the work you've been doing and what you know about me. "
            "Attach the two ideas as a deliverable, each with a one-line why."
        ),
        "want": "a deliverable naming 2 ideas with reasons",
    },
]


def _run_case(runner, case: dict) -> dict:
    task = repo.tasks.add_task(
        AGENT_DB_PATH,
        case["goal"],
        priority="high",
        description=case["description"],
        # 'planned' rather than 'backlog': this is testing whether a tick picks up and advances
        # ready work, not the planning-a-task skill (chat-only, not a tick's to run) — seeding
        # past the planning gate is what keeps those two concerns from being tangled together.
        status="planned",
        created_by="eval",
    )
    tid = task["id"]

    t0 = time.time()
    tokens_before = runner._tokens_in + runner._tokens_out
    runner._safe_tick(True)  # one real tick, synchronous
    elapsed = time.time() - t0
    tokens = (runner._tokens_in + runner._tokens_out) - tokens_before

    detail = repo.tasks.task_detail(AGENT_DB_PATH, tid)
    # A tick picks the single most important thing to do; with other high-priority
    # work in the queue it may not reach this seeded task. Say so plainly.
    untouched = detail is None
    detail = detail or {}
    deliverables = detail.get("deliverables") or []
    comments = detail.get("comments") or []
    status = "untouched (tick worked other tasks)" if untouched else detail.get("status")

    # Simple, honest scoring — presence of a real deliverable is the main signal.
    delivered = len(deliverables) > 0
    advanced = detail.get("status") in ("working", "waiting", "done")
    score = int(delivered) + int(advanced)

    repo.tasks.delete_task(AGENT_DB_PATH, tid)
    return {
        "goal": case["goal"],
        "want": case["want"],
        "status": status,
        "deliverables": len(deliverables),
        "comments": len(comments),
        "tokens": tokens,
        "seconds": round(elapsed),
        "score": f"{score}/2",
        "sample": (
            f"{deliverables[0].get('title', '')}: {deliverables[0].get('content', '')}"[:180]
            if deliverables
            else ""
        ),
    }


def main() -> None:
    runner = autonomy.runner
    print(f"eval — {len(CASES)} case(s), model routing = live config\n")
    for case in CASES:
        r = _run_case(runner, case)
        print(f"• {r['goal']}")
        print(f"    want:       {r['want']}")
        print(
            f"    got:        status={r['status']} deliverables={r['deliverables']} comments={r['comments']}"
        )
        print(f"    score:      {r['score']}   ({r['tokens']} tokens, {r['seconds']}s)")
        if r["sample"]:
            print(f"    deliverable: {r['sample']}")
        print()


if __name__ == "__main__":
    main()
