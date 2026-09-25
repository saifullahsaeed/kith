#!/usr/bin/env python3
"""Scored eval for a real chat turn, against a saved baseline.

    python scripts/eval.py                  # run the suite, print deltas vs the baseline
    python scripts/eval.py --save-baseline  # make this run the new baseline
    python scripts/eval.py --case tools     # one case, by name
    python scripts/eval.py --gate           # exit 1 if anything regressed past a threshold

**Why this was rewritten rather than edited.** The previous version imported ``kith.autonomy``
and ran ``runner._safe_tick(True)``. Neither has existed for some time — the module was moved
out from under it by the layering refactor and the tick itself was removed when scheduling
went. So the file did not merely go stale in its instructions, as it had once before: it
stopped importing at all, and nothing noticed, because nothing runs it. An eval nobody runs is
indistinguishable from no eval, and that is what this has been.

**What it measures, and why those numbers.** Not whether an answer is good — that needs a
judge, and a judge is a second model with its own variance sitting between you and the thing
you are trying to read. These are the numbers the flight recorder already keeps, which is the
point: they are the ones a harness change moves.

* ``uncached`` — prompt tokens with cache hits removed. What a change actually costs to run.
  The number the cache-prefix work exists to hold down, and the first thing a careless edit
  to the prompt order breaks.
* ``rounds`` — model calls to finish. Fewer is better for the same outcome; a change that
  holds tokens flat but doubles the rounds has not helped.
* ``calls_per_round`` — tool calls per round. The single-call rate, inverted so that up is
  better. This is the one that moved when tool *shape* changed and instructions did not.
* ``failures`` — calls that came back failed, via ``tooling.call_failed``, which reads both
  levels of the envelope. A tool failing inside a successful-looking answer counts.
* ``seconds`` — wall clock, reported but never gated on. It tracks the provider's mood as
  much as ours, so it informs and does not fail.

**It never touches the live databases.** ``KITH_DATA_DIR`` is pointed at a temporary directory
before ``kith`` is imported, with ``agent.db`` and ``config.db`` copied into it — copied rather
than created empty, because an agent with no memories, no notes and no projects answers a
different question than the one being asked and would make every reading unrepresentative.
Skills and plugins stay pointed at the real folders, read-only in practice and 135MB not worth
duplicating per run. The copy is deleted at the end unless ``--keep`` is passed.

**It is deliberately not in ``./check``.** That script is hermetic, fast, and runs on a clean
checkout with no model behind it; this one makes real provider calls, costs money, and takes
minutes. Gating every commit on it would either make the gate useless or make it ignored, and
this repo has written down before what happens then. Run it when the harness changes: the
prompt order, the tool set, the context ledger, the loop.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import cast

SERVER_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = SERVER_ROOT / "evals" / "baseline.json"

# Run as a script, `sys.path[0]` is `scripts/` rather than `server/`, so `kith` is not importable
# — the one difference between `python scripts/eval.py` and `python -m scripts.eval`, and the
# usage line at the top promises the first.
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def _copy_database(source: Path, into: Path) -> None:
    """Copy a SQLite database, write-ahead log included.

    ``shutil.copy2`` on the ``.db`` alone is the trap, and it is silent: these run in WAL mode,
    so the newest writes sit in ``agent.db-wal`` until a checkpoint folds them in, and a copy
    that takes only the main file takes only what has been checkpointed.

    Measured rather than assumed, because the size is misleading: the WAL was 4.1MB against a
    6.0MB main file, but almost all of it was already-checkpointed pages, and copying the ``.db``
    alone lost exactly one ``turn_log`` row. So the bug was small on the day it was found — and
    worth fixing anyway, because *how much* it loses depends on when the last checkpoint
    happened to run, which is not a property a baseline should vary with. This repository has
    paid for the same class of mistake once already, in the commit about a read-only open not
    being able to replay a write-ahead log.

    ``VACUUM INTO`` rather than copying the three files, because it asks SQLite for a consistent
    snapshot instead of hoping three separate file copies catch the same instant. It also cannot
    be caught mid-checkpoint, which a `cp` of a live database can.
    """
    import sqlite3

    db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        db.execute("VACUUM INTO ?", (str(into),))
    finally:
        db.close()


def _isolate() -> Path:
    """Point the data directory at a throwaway copy, before anything reads it.

    Has to run at import time rather than inside ``main``: ``settings.DATA_DIR`` is resolved
    once when the module is imported, so a variable set after the first ``import kith``
    anything is a variable that arrives too late. The two fixtures in ``conftest`` have the
    same constraint and solve it the same way.
    """
    live = Path(os.environ.get("KITH_DATA_DIR") or SERVER_ROOT / "data")
    sandbox = Path(tempfile.mkdtemp(prefix="kith-eval-"))
    for name in ("agent.db", "config.db"):
        source = live / name
        if source.exists():
            _copy_database(source, sandbox / name)
    # The tunables. Without these the run uses defaults for `max_rounds`, the live tool budget
    # and everything else in `domain/tuning` — so it would be measuring a differently-configured
    # agent than the one that actually runs, which is the one thing a baseline must not do.
    for name in ("settings.json", "memory.md"):
        source = live / name
        if source.exists():
            shutil.copy2(source, sandbox / name)
    os.environ["KITH_DATA_DIR"] = str(sandbox)
    # The two folders that are capability rather than state. Left pointing at the real ones so
    # the run sees the skills and plugins he actually has; copying 135MB per run to protect
    # against a write that does not happen would be paying a lot for very little.
    os.environ.setdefault("KITH_SKILLS_DIR", str(live / "skills"))
    os.environ.setdefault("KITH_PLUGINS_DIR", str(live / "plugins"))
    return sandbox


SANDBOX = _isolate()

from kith import tools  # noqa: E402  — must follow _isolate; see its docstring
from kith.config import default_config, ollama_host  # noqa: E402
from kith.domain import tooling  # noqa: E402
from kith.services import conversations  # noqa: E402
from kith.services.agent_loop import stream_agent  # noqa: E402
from kith.services.turn.prompt import _build_messages  # noqa: E402
from kith.settings import AGENT_DB_PATH  # noqa: E402

#: The suite. Each case is one turn from a cold conversation, so nothing carries between them.
#:
#: Chosen to exercise different parts of the harness rather than to be hard: a case that only
#: ever answers from the persona measures the prompt and nothing else. `name` is what
#: ``--case`` matches and what the baseline is keyed on, so renaming one orphans its history.
#:
#: **Every prompt must be independent of where the workspace happens to be.** The first draft
#: named paths inside this repository — "the server package", "server/kith/kernel/events.py" —
#: on the assumption that Kith's workspace is the checkout he is built from. It is not; it is
#: ``~/Kith``, and it holds different projects entirely. So those two cases sent him hunting for
#: files that were not there: one failed `shell` twice and the other burnt the whole 600s
#: timeout, and both looked exactly like tool defects until the workspace was checked. A case
#: that fails because of its own wording measures nothing and accuses the harness.
CASES = [
    {
        "name": "recall",
        "prompt": "In three bullets, what am I working on right now? Answer from your notes.",
        "why": "memory and project context in the prompt — no tools needed if the state block works",
    },
    {
        "name": "tools",
        "prompt": "List the top-level entries in my workspace and say which one holds the most files.",
        "why": "shell and file tools; the single-call rate lives or dies here",
    },
    {
        "name": "reading",
        "prompt": "Read your designing-a-screen skill and tell me in two sentences what it is for.",
        "why": "one big read into one answer — the case where re-reading shows up as waste",
    },
    {
        "name": "grounded",
        "prompt": "What is in my task board that is not done? Just list them.",
        "why": "board tools and a short answer; catches a turn that narrates instead of finishing",
    },
]


class _Metrics:
    """Reads the event stream the way ``_MindFeed`` does, and publishes nothing.

    Kept separate from that class rather than reused: it is welded to the feed, the stop
    switch and the transcript, and an eval that had to stand all three up to count four
    numbers would be measuring the scaffolding as much as the turn.
    """

    def __init__(self) -> None:
        self.rounds = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.uncached = 0
        self.calls: list[str] = []
        self.failed: list[str] = []
        self.said = ""
        self.error: str | None = None

    def saw(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            self.calls.append(str(event.get("name") or ""))
        elif kind == "tool_result":
            if tooling.call_failed(event.get("result")):
                self.failed.append(str(event.get("name") or ""))
        elif kind == "delta" and event.get("role") == "text":
            self.said += event.get("text") or ""
        elif kind == "stats":
            stats = event.get("stats") or {}
            self.rounds += 1
            self.tokens_in += int(stats.get("promptTokens") or 0)
            self.tokens_out += int(stats.get("responseTokens") or 0)
            self.uncached += int(stats.get("uncachedTokens") or 0)
        elif kind == "error":
            self.error = str(event.get("message") or "")

    def scored(self, seconds: float) -> dict:
        return {
            "uncached": self.uncached,
            "out": self.tokens_out,
            "rounds": self.rounds,
            "calls": len(self.calls),
            # Per round rather than per turn, so a case that legitimately needs more work is
            # not scored as worse than one that needed less. Rounds of 0 only happens on a
            # turn that died before its first reply.
            "calls_per_round": round(len(self.calls) / self.rounds, 2) if self.rounds else 0.0,
            "failures": len(self.failed),
            "failed_tools": sorted(set(self.failed)),
            "seconds": round(seconds, 1),
            "answered": bool(self.said.strip()),
            "error": self.error,
        }


#: Longest a single case may run before it is abandoned and scored as a timeout.
#:
#: There has to be one. The first full run of this suite sat for twenty-eight minutes with the
#: process idle at zero CPU and nothing on stdout, and there was no way to tell a slow case from
#: a stuck one — which is the state a gate must never be in, because a gate that can hang is a
#: gate that gets removed from whatever was waiting on it.
#:
#: Generous, because a real case legitimately runs minutes: the `recall` case measured 160s on
#: its own, and a case that reads and greps its way to an answer is several rounds of that.
CASE_TIMEOUT = 600.0


def _run_case(case: dict) -> dict:
    """One turn, from its own fresh conversation, scored.

    The timeout is checked *between* events, which is the same granularity the errand loop in
    `tools/workers.py` settles for and for the same reason: the generator yields nothing
    while a tool call or a provider read is in flight, so a single wedged call is not
    interruptible from here. It bounds a turn that keeps going, not one that has stopped dead —
    for that there is the process, and now a number on the screen saying how long it has been.
    """
    convo = conversations.start(AGENT_DB_PATH, case["prompt"])
    conversation_id = convo["id"]
    conversations.record(AGENT_DB_PATH, conversation_id, "user", case["prompt"])

    config = default_config()
    messages = _build_messages(
        conversations.full_messages(conversation_id),
        config,
        conversation_id,
    )

    metrics = _Metrics()
    started = time.monotonic()
    # Cast because `stream_agent` is annotated `Iterator[dict]`, which has no `close`. It is a
    # generator — the `finally` that clears the errand board is right there in it — and the
    # narrower annotation is about what callers may assume, not about what this is.
    turn = cast(
        Generator[dict, None, None],
        stream_agent(
            messages,
            config,
            ollama_host(),
            AGENT_DB_PATH,
            tools.host(AGENT_DB_PATH),
            conversation_id=conversation_id,
        ),
    )
    try:
        for event in turn:
            metrics.saw(event)
            if time.monotonic() - started > CASE_TIMEOUT:
                metrics.error = f"timed out after {CASE_TIMEOUT:.0f}s"
                # Closing the generator runs its `finally`, which is what clears the errand
                # board for the conversation. Abandoning it to the collector instead would
                # leave that until some arbitrary later moment.
                turn.close()
                break
    except Exception as exc:  # a case that dies is a result, not a crash
        metrics.error = f"{type(exc).__name__}: {exc}"
    return metrics.scored(time.monotonic() - started)


#: How much worse a number may get before ``--gate`` calls it a regression.
#:
#: Ratios rather than absolutes because the numbers differ by an order of magnitude between
#: cases, and generous because a provider's caching is not perfectly deterministic — the point
#: is to catch a change that moved something structurally, not to chase noise. `seconds` is
#: absent on purpose: it is the provider's mood as much as ours.
THRESHOLDS = {"uncached": 1.15, "rounds": 1.34, "failures": 1.0}


def _delta(now: dict, before: dict) -> dict:
    """Per-metric change against the baseline, with a verdict on the gated ones."""
    out: dict = {}
    for key, limit in THRESHOLDS.items():
        was, is_now = before.get(key), now.get(key)
        if was is None or is_now is None:
            continue
        # A baseline of zero has no ratio. Any increase off zero is a regression on its own
        # terms — one failure where there were none is exactly what this should catch.
        grew = (is_now > was * limit) if was else (is_now > was)
        out[key] = {"was": was, "now": is_now, "regressed": grew}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--save-baseline", action="store_true", help="write this run as the new baseline")
    parser.add_argument("--case", help="run one case by name")
    parser.add_argument("--gate", action="store_true", help="exit 1 if a gated metric regressed")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox data dir for inspection")
    args = parser.parse_args()

    cases = [c for c in CASES if not args.case or c["name"] == args.case]
    if not cases:
        print(f"no case named {args.case!r} — have: {', '.join(c['name'] for c in CASES)}", file=sys.stderr)
        return 2

    baseline = {}
    if BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text()).get("cases", {})

    print(f"eval — {len(cases)} case(s), sandbox at {SANDBOX}\n")
    results, regressed = {}, []
    for case in cases:
        # Announced before it runs, and flushed. Redirected to a file Python buffers stdout in
        # 8KB blocks, so a run that printed only on completion showed an empty log for as long
        # as it took — indistinguishable from a run that had died. Every print below flushes for
        # the same reason: this is a command someone watches.
        print(f"• {case['name']}  — {case['why']}", flush=True)
        scored = _run_case(case)
        results[case["name"]] = scored
        if scored["error"]:
            print(f"    error:   {scored['error']}", flush=True)
        print(
            f"    {scored['uncached']:,} uncached · {scored['rounds']} rounds · "
            f"{scored['calls']} calls ({scored['calls_per_round']}/round) · "
            f"{scored['failures']} failed · {scored['seconds']}s",
            flush=True,
        )
        if scored["failed_tools"]:
            print(f"    failing: {', '.join(scored['failed_tools'])}", flush=True)
        for key, change in _delta(scored, baseline.get(case["name"], {})).items():
            arrow = "↑" if change["now"] > change["was"] else "↓"
            flag = "  REGRESSED" if change["regressed"] else ""
            print(f"    {key:<10} {change['was']:,} → {change['now']:,} {arrow}{flag}", flush=True)
            if change["regressed"]:
                regressed.append(f"{case['name']}/{key}")
        print(flush=True)

    if args.save_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "cases": results}, indent=2)
        )
        print(f"baseline written to {BASELINE_PATH.relative_to(SERVER_ROOT)}")
    elif not baseline:
        print("no baseline yet — run with --save-baseline once you are happy with these numbers")

    if not args.keep:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    else:
        print(f"sandbox kept at {SANDBOX}")

    if regressed:
        print(f"\n{len(regressed)} regression(s): {', '.join(regressed)}")
        return 1 if args.gate else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
