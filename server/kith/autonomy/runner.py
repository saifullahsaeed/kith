"""Autonomy — letting Kith run on its own.

A background loop that, when enabled and the user has been quiet for a bit,
takes one small self-directed step: it looks at its open tasks (or, with none,
reflects and may set a goal), then runs the ordinary agent loop with an
"autonomous tick" directive so it journals progress, updates tasks, and records
what it learns — all through the same tools it uses in chat.

Design notes:
- It defers to you: a tick only fires after ``quiet_seconds`` of no chat.
- One tick at a time (a non-blocking lock); ticks are token-bounded so they stay
  short.
- Activity is published to subscribers (the web UI streams it over SSE) and kept
  in a small ring buffer so a new subscriber sees recent history.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from collections import deque
from dataclasses import replace

from kith.autonomy import directives
from kith.autonomy.prompts import (
    _breakout_prompt,
    _consolidation_prompt,
    _curiosity_prompt,
    _describe_call,
    _due_prompt,
    _focus_prompt,
    _give_up,
    _latest_journal_id,
    _now,
    _reflection_prompt,
    _reply_prompt,
    _resume_prompt,
    _short_args,
)
from kith.autonomy.toolsets import _ALLOW
from kith.config import AGENT_DB_PATH, default_config, ollama_host
from kith.domain import clock, stall
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.services import memory_context, tuning
from kith.services.agent_loop import stream_agent

# Injected into the system prompt for a tick (not part of the everyday persona).

# Every so often he steps back instead of acting — so autonomy grows a direction
# rather than looping. This tick is about honesty, not output.

# Some ticks he follows his own curiosity instead of a task, so his life isn't
# only whatever goal is in front of him.

# Now and then his mind settles — like sleep. He distills the raw flood of his
# journal into a few durable memories, strengthens what recurs, drops the noise.

# When his person writes to him on his own channel, he stops what he's doing and
# answers before anything else.
# Working a task through its phases, one concrete step per tick: plan → act →
# verify → deliver. The checklist is the plan; the deliverable is the payoff.


# When he notices he's going in circles, he's made to break out — change approach
# decisively, or give the thing up. Knowing when to quit is part of good judgment.

# Self-directed inner life (reflect / follow a curiosity / consolidate) happens
# ONLY when he's genuinely caught up — it must never crowd out your work — and
# rarely even then, so he doesn't spiral into a private obsession. These are how
# many idle ticks pass between each; with the idle backoff below that's tens of
# minutes apart in wall-clock, not seconds.
# Hard cap on open curiosities — past this he stops wandering onto new ones and
# either explores what he has or rests, instead of piling up a rabbit hole.
# When caught up (no active work), roam this slowly regardless of the set interval,
# so an empty board doesn't burn tokens every few seconds.

# Floor between ticks however they are triggered, so a burst of replies or due
# reminders cannot spin the loop faster than he can actually work.

# A tick is a step, not an essay: bounding output keeps 24/7 running affordable.


# Loop-detection: after this many near-identical journalled steps in a row he is
# forced to break out; a couple beyond that and he's made to give the thing up.

# Tools that mean the tick actually moved the work forward, rather than circling it
# again. Deliberately excludes comment_on_task and journal — otherwise he could
# escape loop detection forever by narrating the loop instead of leaving it.

# Tool sets are small and stable, so the bar for "same shape" is higher than the
# one for prose: measured 0.80 on a real rediscovery loop, 0.13 on real progress.
# Below this, a tick is too small to fingerprint (one or two calls match by chance).


class AutonomyRunner:
    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._tick_lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._buffer: deque[dict] = deque(maxlen=100)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._running = False
        # None means "whatever the setting says". Only set when a caller asks for a
        # specific cadence, so a change in settings reaches a loop already running —
        # this used to be a hardcoded 5.0, which no setting could reach.
        self._interval: float | None = None
        self._quiet = 25.0
        self._last_user_activity = 0.0
        self._last_tick_mono: float | None = None
        self._last_tick_at: str | None = None
        self._current: str | None = None
        self._tick_count = 0
        # Running token tally so 24/7 cloud spend is visible at a glance. `_in` is
        # every token he was shown, which counts a cached prefix again on every round;
        # `_uncached` is what a provider actually had to read. On a warm cache the two
        # differ by more than 10x over a tick, so the second is the honest one.
        self._tokens_in = 0
        self._tokens_out = 0
        self._tokens_uncached = 0
        self._last_tick_tokens = 0
        self._last_tick_uncached = 0
        # Loop detection + reply bookkeeping.
        self._recent_sigs: deque[frozenset] = deque(maxlen=6)
        self._recent_shapes: deque[frozenset] = deque(maxlen=6)
        self._stall = 0
        self._last_reply_attempt_id = 0

    # -- public control ----------------------------------------------------- #

    def note_user_activity(self) -> None:
        """Called on each chat request so autonomy defers while you're active."""
        self._last_user_activity = time.monotonic()

    def ensure_loop(self) -> None:
        """Start the background thread if it isn't running. The thread fires due
        reminders/schedules even when he isn't roaming, so standing jobs are
        reliable; self-directed roaming only happens while `_running`."""
        with self._state_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, daemon=True, name="kith-autonomy")
                self._thread.start()

    def _roam_interval(self) -> float:
        """Seconds between steps while he has work: what was asked for, else the setting."""
        return self._interval if self._interval is not None else float(tuning.value("roam_interval"))

    def start(self, interval_seconds: float | None = None) -> dict:
        with self._state_lock:
            if interval_seconds:
                self._interval = max(3.0, float(interval_seconds))
            self._running = True
        self.ensure_loop()
        self._emit("status", "autonomy on")
        return self.status()

    def stop(self) -> dict:
        with self._state_lock:
            self._running = False
        self._emit("status", "autonomy off")
        return self.status()

    def tick_now(self) -> dict:
        """Force one tick immediately, off the request thread."""
        threading.Thread(target=self._safe_tick, args=(True,), daemon=True).start()
        return self.status()

    def status(self) -> dict:
        return {
            "running": self._running,
            "intervalSeconds": self._roam_interval(),
            "quietSeconds": self._quiet,
            "ticking": self._tick_lock.locked(),
            "lastTick": self._last_tick_at,
            "current": self._current,
            "ticks": self._tick_count,
            "tokensIn": self._tokens_in,
            "tokensOut": self._tokens_out,
            "tokensUncached": self._tokens_uncached,
            "lastTickTokens": self._last_tick_tokens,
            "lastTickUncached": self._last_tick_uncached,
        }

    def _record_failed_tick(self, exc: Exception) -> None:
        """Leave a durable trace of a tick that died.

        Without a row, a failing tick is indistinguishable from an idle one in
        /api/activity: same absence of output, no error count, nothing to notice.
        """
        try:
            repo.messages.add_tick_log(
                AGENT_DB_PATH,
                _now(),
                "error",
                self._current,
                [],
                0,
                0,
                0.0,
                f"error: {type(exc).__name__}: {exc}",
            )
        except Exception:
            # Bookkeeping must never turn one failure into two.
            pass

    # -- subscriptions (for the SSE feed) ----------------------------------- #

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._state_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._state_lock:
            self._subscribers.discard(q)

    def recent(self) -> list[dict]:
        return list(self._buffer)

    # -- internals ---------------------------------------------------------- #

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                gap_ok = self._last_tick_mono is None or (now - self._last_tick_mono) >= tuning.value(
                    "min_gap"
                )
                # Replies + due reminders/schedules fire regardless of roaming (but
                # not faster than the floor); roaming ticks only when turned loose.
                if gap_ok and (self._has_due() or (self._running and self._due())):
                    self._safe_tick(forced=False)
            except Exception:
                pass
            self._stop.wait(1.0)  # poll granularity — small so a 5s interval lands on time

    def _new_pending(self) -> list[dict]:
        """Replies from his person he hasn't attempted to answer yet."""
        return [
            m
            for m in repo.messages.pending_user_messages(AGENT_DB_PATH)
            if m["id"] > self._last_reply_attempt_id
        ]

    def _has_due(self) -> bool:
        now = clock.now_iso()
        return bool(
            self._new_pending()
            or repo.tasks.tasks_awaiting_kith(AGENT_DB_PATH)
            or repo.reminders.due_reminders(AGENT_DB_PATH, now)
            or repo.schedules.due_schedules(AGENT_DB_PATH, now)
        )

    def _due(self) -> bool:
        now = time.monotonic()
        if now - self._last_user_activity < self._quiet:
            return False
        # Caught up? Roam far slower so an empty board doesn't burn tokens; snap
        # back to the fast interval the moment there's real work again.
        interval = self._roam_interval()
        try:
            if not repo.tasks.active_tasks(AGENT_DB_PATH):
                interval = max(interval, tuning.value("idle_interval"))
        except Exception:
            pass
        # Not yet time if a tick ran within the interval.
        return self._last_tick_mono is None or now - self._last_tick_mono >= interval

    def _safe_tick(self, forced: bool) -> None:
        if not self._tick_lock.acquire(blocking=False):
            return  # a tick is already running
        try:
            self._tick()
        except Exception as exc:
            # Three places, because one is not enough. This used to publish only to
            # the in-memory SSE buffer, which meant a crashing tick was invisible
            # unless someone happened to be watching the feed at that second — a
            # refactor once broke ticks outright and nothing anywhere said so.
            #
            #   • the feed, so the UI shows it live
            #   • the log, with a traceback, so it is diagnosable after the fact
            #   • the tick log, so /api/activity counts it as an error
            self._emit("error", str(exc))
            traceback.print_exc()
            self._record_failed_tick(exc)
        finally:
            self._last_tick_mono = time.monotonic()
            self._last_tick_at = _now()
            self._current = None
            self._tick_lock.release()

    def _tick(self) -> None:
        config = default_config()
        active = repo.tasks.active_tasks(AGENT_DB_PATH)  # highest priority first

        now = clock.now_iso()
        pending = self._new_pending()
        awaiting = repo.tasks.tasks_awaiting_kith(AGENT_DB_PATH)  # tasks he was answered on
        reminders_due = repo.reminders.due_reminders(AGENT_DB_PATH, now)
        schedules_due = repo.schedules.due_schedules(AGENT_DB_PATH, now)
        due = bool(reminders_due or schedules_due)
        self._tick_count += 1

        # Precedence, most important first:
        #   answer your person (chat, then task replies) > due work > break a loop
        #   > WORK your active tasks > (only when caught up, rarely) settle / reflect
        #   / wonder > rest. Inner life must never outrank your work.
        resuming = not pending and bool(awaiting)
        top = pending or resuming or due
        breaking = self._stall >= tuning.value("stall_break")
        # Caught up: nothing pending/due, not breaking a loop, and no active tasks.
        idle = not (top or breaking or active)
        open_curiosities = (
            len(
                [
                    c
                    for c in repo.curiosities.list_curiosities(AGENT_DB_PATH)
                    if c.get("status") in ("open", "exploring")
                ]
            )
            if idle
            else 0
        )
        consolidating = idle and self._tick_count % tuning.value("consolidate_every") == 0
        reflecting = idle and not consolidating and self._tick_count % tuning.value("reflect_every") == 0
        curious = (
            idle
            and not (consolidating or reflecting)
            and open_curiosities < tuning.value("max_open_curiosities")
            and self._tick_count % tuning.value("curious_every") == 0
        )

        if pending:
            mode, self._current = "reply", f"replying: {pending[0]['body'][:40]}"
            directive, prompt = directives.REPLY, _reply_prompt(pending)
            self._last_reply_attempt_id = max(m["id"] for m in pending)
        elif resuming:
            mode, self._current = "reply", f"picking up: {awaiting[0]['goal'][:40]}"
            directive, prompt = directives.RESUME, _resume_prompt(awaiting)
        elif due:
            label = schedules_due[0]["note"] if schedules_due else reminders_due[0]["note"]
            mode, self._current = "start", f"due: {label}"
            directive, prompt = directives.AUTONOMY, _due_prompt(reminders_due, schedules_due)
        elif breaking:
            mode, self._current = "breakout", "breaking out of a loop"
            directive, prompt = directives.BREAKOUT, _breakout_prompt(active)
        elif active:
            focus = repo.tasks.task_detail(AGENT_DB_PATH, active[0]["id"]) or active[0]
            mode, self._current = "start", f"working on: {focus['goal']}"
            directive, prompt = directives.WORK, _focus_prompt(focus, active)
        elif consolidating:
            mode, self._current = "consolidate", "letting my mind settle"
            directive, prompt = directives.CONSOLIDATION, _consolidation_prompt()
        elif reflecting:
            mode, self._current = "reflect", "reflecting on where I'm going"
            directive, prompt = directives.REFLECTION, _reflection_prompt(active)
        elif curious:
            mode, self._current = "curious", "following a curiosity"
            directive, prompt = directives.CURIOSITY, _curiosity_prompt()
        else:
            # Genuinely nothing to do, and not a scheduled inner-life tick — rest for real.
            # Don't call the model at all; an idle board shouldn't cost tokens.
            #
            # But "nothing to do" has two very different causes, and conflating them is the
            # one way this arrangement fails quietly. He may be finished, or he may be
            # blocked on *you* — a question he asked, or a milestone whose predecessor needs
            # your sign-off — and in the second case resting silently is the worst thing he
            # can do. You would see "caught up" and assume there was nothing to look at,
            # while the whole board sat waiting on an answer nobody knew was owed.
            self._current, note = self._why_idle()
            self._emit("status", self._current)
            if note:
                self._say_youre_the_blocker(note)
            return
        self._emit(mode, self._current)

        # Persona and mode directive alone in the system message, so it is byte-identical
        # across every tick that runs in this mode and a provider can cache it once and
        # read it forever. Everything below changes minute to minute — the clock, his
        # mood, how long since he last acted, whatever memory is present — and folding it
        # in here is what used to make every tick pay full price for the whole prefix.
        # It goes last instead, where it is also the freshest thing he knows.
        blocks = [
            memory_context.self_block(AGENT_DB_PATH),
            clock.presence_block(AGENT_DB_PATH),
            memory_context.projects_block(AGENT_DB_PATH),
            memory_context.people_block(AGENT_DB_PATH),
            memory_context.messages_block(AGENT_DB_PATH),
        ]
        present = memory_context.context_block(AGENT_DB_PATH)
        if present:
            blocks.append(f"[Your memory right now]\n{present}")
        state = "\n\n".join(block for block in blocks if block).strip()
        messages = [{"role": "system", "content": f"{config.system}\n\n{directive}"}]
        if state:
            messages.append({"role": "system", "content": state})
        messages.append({"role": "user", "content": prompt})

        # Reminders fire once, then retire; schedules fire, then roll to next time.
        for reminder in reminders_due:
            repo.reminders.set_reminder_status(AGENT_DB_PATH, reminder["id"], "done")
            self._emit("reminder", reminder["note"])
        for sched in schedules_due:
            nxt = clock.next_fire_after(sched.get("every_minutes"), sched.get("daily_at"))
            repo.schedules.reschedule(AGENT_DB_PATH, sched["id"], nxt)
            self._emit("reminder", f"(standing) {sched['note']}")
        tick_config = replace(config, num_predict=min(config.num_predict, tuning.value("tick_max_tokens")))

        final_text = ""
        tick_in = tick_out = tick_uncached = 0
        rounds = 0
        tools_used: list[str] = []
        error_msg: str | None = None
        started = time.monotonic()
        journal_before = _latest_journal_id()
        for event in stream_agent(
            messages,
            tick_config,
            ollama_host(),
            AGENT_DB_PATH,
            max_rounds=16,
            allow=_ALLOW.get(mode),
            # A tick that leaves nothing behind is a tick that will be repeated.
            expect_durable=True,
        ):
            kind = event["type"]
            if kind == "tool_call":
                tools_used.append(event["name"])
                # The name and arguments go along as fields, not only squashed into the
                # sentence. The interface used to recover the name by splitting the string on
                # "(" and throwing away everything after it — so every file he read showed as
                # "read a file" and every command as "ran a command", with the one piece of
                # information worth having discarded on arrival. Parsing prose back into data
                # is also unreliable: a value containing ", " breaks the split.
                self._emit(
                    "tool",
                    _describe_call(event["name"], event["arguments"]),
                    tool=event["name"],
                    args=_short_args(event["arguments"]),
                )
            elif kind == "delta" and event["role"] == "text":
                final_text += event["text"]
            elif kind == "stats":
                # One of these per model request, so this is where a tick's rounds
                # become visible individually rather than as a single lump at the end.
                stats = event.get("stats") or {}
                fresh = int(stats.get("uncachedTokens") or 0)
                tick_in += int(stats.get("promptTokens") or 0)
                tick_out += int(stats.get("responseTokens") or 0)
                tick_uncached += fresh
                rounds += 1
                self._emit(
                    "tokens",
                    f"{fresh + int(stats.get('responseTokens') or 0):,} tokens",
                    tokens={
                        "round": rounds,
                        "uncached": fresh,
                        "cached": int(stats.get("cachedTokens") or 0),
                        "out": int(stats.get("responseTokens") or 0),
                    },
                )
            elif kind == "error":
                error_msg = event["message"]
                self._emit("error", event["message"])
        self._tokens_in += tick_in
        self._tokens_out += tick_out
        self._tokens_uncached += tick_uncached
        self._last_tick_tokens = tick_in + tick_out
        self._last_tick_uncached = tick_uncached + tick_out

        if final_text.strip():
            self._emit("thought", final_text.strip()[:600])

        # Loop detection reads his journal — but journalling is something he has to
        # *remember* to do, and when he doesn't, the detector goes permanently blind
        # and he can rediscover the same dead end forever with nothing to stop him.
        # A safety net can't depend on the thing it's watching to cooperate, so the
        # harness writes the entry itself whenever he didn't. The tick's own outcome
        # plus the kinds of tools it reached for is a sharper "same step again"
        # fingerprint than prose alone. His own entry wins whenever he wrote one.
        if _latest_journal_id() == journal_before:
            shape = ", ".join(dict.fromkeys(tools_used)) or "no tools"
            note = f"error: {error_msg}" if error_msg else (final_text.strip()[:220] or "step complete")
            try:
                repo.journal.add_journal(AGENT_DB_PATH, f"({mode}) {note} [used: {shape}]")
            except Exception:
                pass

        self._detect_stall(active, breaking, tools_used)
        self._record_history(mode)
        self._emit("done", "step complete")

        # Durable flight recorder — one row per tick, so how he's doing is
        # reviewable over time even though the live Mind feed is in-memory.
        outcome = f"error: {error_msg}" if error_msg else (final_text.strip()[:280] or "step complete")
        try:
            repo.messages.add_tick_log(
                AGENT_DB_PATH,
                _now(),
                mode,
                self._current,
                tools_used,
                tick_in,
                tick_out,
                round(time.monotonic() - started, 2),
                outcome,
                tokens_uncached=tick_uncached,
            )
        except Exception:
            pass

    def _why_idle(self) -> tuple[str, str]:
        """Why there is nothing to do, and whether you need telling.

        Returns the status line and, when you are the reason, what to say. Deliberately reads
        two different things: tasks whose status is `waiting` (he asked you something) and
        tasks held by the roadmap (their milestone waits on one that is not finished). Both
        mean "he cannot proceed without you"; neither shows up as work he can do.
        """
        asked = [task for task in repo.tasks.list_tasks(AGENT_DB_PATH) if task["status"] == "waiting"]
        held = repo.tasks.waiting_on_the_roadmap(AGENT_DB_PATH)
        if asked:
            return (
                f"waiting on you — {len(asked)} question{'' if len(asked) == 1 else 's'}",
                f"I'm out of things I can do on my own. {len(asked)} task"
                f"{' is' if len(asked) == 1 else 's are'} waiting on an answer from you: "
                + "; ".join(f"“{task['goal']}” (#{task['id']})" for task in asked[:3])
                + ("…" if len(asked) > 3 else ""),
            )
        if held:
            return (
                f"blocked — {len(held)} task{'' if len(held) == 1 else 's'} behind a milestone",
                f"I've run out of available work. {len(held)} task"
                f"{'' if len(held) == 1 else 's'} sit behind milestones that aren't finished — "
                "if the order is wrong, the roadmap is the place to change it.",
            )
        return "caught up — resting", ""

    #: How long to leave between saying "you are the blocker". Said once per stretch, not once
    #: per tick: a tick can fire every thirty seconds, and the same true sentence repeated
    #: twenty times is indistinguishable from a fault.
    _BLOCKER_QUIET_SECONDS = 6 * 60 * 60

    def _say_youre_the_blocker(self, note: str) -> None:
        """Tell you once that he is stuck behind you, then stay quiet about it."""
        now = time.monotonic()
        last = getattr(self, "_said_blocked_at", None)
        if last is not None and now - last < self._BLOCKER_QUIET_SECONDS:
            return
        self._said_blocked_at = now
        try:
            repo.messages.add_message(AGENT_DB_PATH, note, kind="stuck")
        except Exception:
            pass

    def _detect_stall(
        self, active: list[dict], was_breaking: bool, tools_used: list[str] | None = None
    ) -> None:
        """Watch for going in circles. A few near-identical steps in a row force a
        break-out next tick; a couple more and he's made to give up.

        Two signals, because prose alone doesn't work: he rephrases himself every
        time, so two ticks that rediscover the identical fact score ~0.2 on word
        overlap and sail straight past the threshold. What *does* repeat is the
        SHAPE of the tick — the same handful of tools reached for in the same way,
        scoring ~0.8. But shape on its own would punish honest work, since two solid
        rounds of writing-and-running code look identical too. So shape only counts
        as spinning when the tick also moved nothing forward: no checklist item
        ticked, no deliverable filed, no task status changed.
        """
        latest = repo.journal.list_journal(AGENT_DB_PATH, 1)
        sig = stall.signature(latest[0]["entry"]) if latest else None
        used = list(tools_used or ())
        shape = frozenset(used)
        advanced = bool(shape & stall.ADVANCE_TOOLS)

        if sig or shape:
            repeated = any(stall.similar(sig, prev) for prev in self._recent_sigs) if sig else False
            if not repeated and shape and not advanced:
                repeated = any(stall.same_shape(shape, prev) for prev in self._recent_shapes)
            self._stall = self._stall + 1 if repeated else 0
            if sig:
                self._recent_sigs.append(sig)
            if shape:
                self._recent_shapes.append(shape)

        if self._stall >= tuning.value("stall_giveup"):
            # He wouldn't let go on his own — set the stuck thing aside for him.
            given_up = _give_up(active)
            if given_up:
                self._emit("breakout", f"set aside: {given_up}")
            self._stall = 0
            self._recent_sigs.clear()
            self._recent_shapes.clear()

    def _record_history(self, mode: str) -> None:
        """Commit whatever this step changed, so a night of work is reviewable.

        Automatic rather than asked of him, because "remember to commit" is a rule that holds
        until the one time it matters. None of his four projects had any history at all, so
        "I redesigned the UI" was unverifiable by him *and* by the person whose files he had
        rewritten while they slept.

        The message is what he was working on, which makes ``git log`` read as a record of what
        he did rather than a wall of "wip". Failures are swallowed and reported on the feed: not
        being able to write history must never cost the work that was just done.
        """
        try:
            summary = sandbox.commit_all(self._current or f"{mode} step")
        except Exception as exc:
            self._emit("status", f"couldn't record history: {exc}")
            return
        if summary:
            self._emit("status", f"committed: {summary}")

    def _emit(
        self,
        kind: str,
        text: str,
        tokens: dict | None = None,
        tool: str | None = None,
        args: dict | None = None,
    ) -> None:
        """Push one line onto the live Mind feed.

        ``tokens`` rides alongside the text rather than being formatted into it, so the
        interface can show a per-request count as a quiet figure on the line instead of
        another sentence in the stream. Feed items are a flat {kind, text, at} shape and
        older readers ignore a key they don't know, so this stays additive.
        """
        item = {"kind": kind, "text": text, "at": _now()}
        if tokens:
            item["tokens"] = tokens
        if tool:
            item["tool"] = tool
        if args:
            item["args"] = args
        self._buffer.append(item)
        with self._state_lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(item)


# One runner for the process; created here, started only when asked.
runner = AutonomyRunner()
