"""Taking the next step on work that is already underway.

A background loop that advances sessions which are working, fires reminders and schedules
when they come due, and answers you when you have written to him.

What it no longer does is decide *whether* he should be working. That used to be the bulk of
this file — a roam switch, an interval, an idle backoff, a quiet period, and a nine-branch
ladder of things to do when there was nothing to do. All of it was guessing at one question,
"when may he act without me", and a session answers that structurally: it is working or it is
not, and you say which.

What went with the guessing: reflection, consolidation and curiosity as scheduled modes. They
were self-directed inner life on a timer — every twentieth idle tick a reflection, every
thirtieth a curiosity — which is a strange thing to schedule and, in practice, was work
happening on a board nobody was watching. Reflection is still worth doing; it is a note he
writes when he has something to say, not a mode the clock puts him in.

Design notes:
- One step at a time per session (a non-blocking lock), and token-bounded.
- Activity is published to subscribers (the UI streams it over SSE) and kept in a small ring
  buffer so a new subscriber sees recent history.
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
    _breakdown_prompt,
    _breakout_prompt,
    _describe_call,
    _due_prompt,
    _focus_prompt,
    _give_up,
    _latest_journal_id,
    _now,
    _reply_prompt,
    _resume_prompt,
    _short_args,
)
from kith.autonomy.toolsets import _ALLOW
from kith.config import AGENT_DB_PATH, default_config, ollama_host
from kith.domain import clock, stall
from kith.infra.db import repositories as repo
from kith.services import memory_context, session_context, tuning
from kith.services.agent_loop import stream_agent

# Injected into the system prompt for a tick (not part of the everyday persona).

# When his person writes to him on his own channel, he stops what he's doing and
# answers before anything else.
# Working a task through its phases, one concrete step per tick: plan → act →
# verify → deliver. The checklist is the plan; the deliverable is the payoff.


# When he notices he's going in circles, he's made to break out — change approach
# decisively, or give the thing up. Knowing when to quit is part of good judgment.


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

        # Set to stop the step that is running, without touching whether he roams.
        self._cancel = threading.Event()
        # None means "whatever the setting says". Only set when a caller asks for a
        # specific cadence, so a change in settings reaches a loop already running —
        # this used to be a hardcoded 5.0, which no setting could reach.
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
        # Dollars, as the provider billed them. Every token count here is a proxy for this,
        # and OpenRouter returns it on every call — we simply were not reading it.
        self._cost_usd = 0.0
        self._last_tick_tokens = 0
        self._last_tick_uncached = 0
        # Per-session prompt-token meter, keyed by conversation_id. The tallies above are
        # process-wide and keyed to nothing, so they cannot bound one runaway session; this
        # can. In-memory and reset when a session (re)enters keep_working — a restart mid-run
        # forgets it, a bounded gap, not the 11-hour all-nighter this exists to stop.
        self._session_tokens: dict[str, int] = {}
        # And what it has actually cost, as the provider billed it. The cap is enforced on
        # this; tokens are only the fallback for a provider that reports no price.
        self._session_cost: dict[str, float] = {}
        # Sessions already stopped for budget, so a late charge can't post the note twice.
        self._session_capped: set[str] = set()
        # Loop detection + reply bookkeeping.
        self._recent_sigs: deque[frozenset] = deque(maxlen=6)
        self._recent_shapes: deque[frozenset] = deque(maxlen=6)
        self._stall = 0
        # Grind detection: the same task worked with no NET progress (items ticked or
        # deliverables filed) for too many ticks — the loop the prose/shape detectors miss
        # because it rewords itself while getting nowhere.
        self._focus_id: int | None = None
        self._focus_progress = 0
        self._focus_grind = 0
        # After a provider error, don't tick again until this monotonic time — a brief backoff
        # so a flapping upstream (a 502, a rate-limit) isn't hammered every second.
        self._error_backoff_until = 0.0
        self._last_reply_attempt_id = 0
        # When the focused task's status was last checked mid-step. See `_task_moved_on`.
        self._last_freshness_check = 0.0

    # -- public control ----------------------------------------------------- #

    def ensure_loop(self) -> None:
        """Start the background thread if it isn't running. The thread fires due
        reminders/schedules even when he isn't roaming, so standing jobs are
        reliable; self-directed roaming only happens while `_running`."""
        with self._state_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, daemon=True, name="kith-autonomy")
                self._thread.start()

    def keep_working(self, conversation_id: str) -> dict:
        """This session takes the next step on its own until it is done or you stop it.

        Per session, which is the whole difference from roaming. Roaming was one switch over
        one board: on meant every open task everywhere was fair game, off meant nothing
        happened at all, and with two projects going there was no way to say "continue this
        one". A session is the thing you actually want to start and stop.
        """
        repo.conversations.set_working(AGENT_DB_PATH, conversation_id, True)
        # Fresh session, fresh meter — starting (or restarting) work zeroes the budget so a
        # previous run's spend can't carry over and trip the cap the moment it resumes.
        self._session_tokens[conversation_id] = 0
        self._session_cost[conversation_id] = 0.0
        self._session_capped.discard(conversation_id)
        self.ensure_loop()
        self._emit("status", "working")
        return self.status()

    def nudge(self, conversation_id: str = "", why: str = "") -> None:
        """Something happened that this session should get on with.

        The loop only wakes for a session that is *working*, and that used to be settable
        only by a button. So filing a task in a conversation did nothing until someone found
        and pressed a control whose two copies both called the same function — you asked for
        a thing, he wrote it down, and then both of you waited.

        Distinct from `keep_working`, deliberately, and the difference is the token meter.
        Starting work zeroes the budget on purpose so a resumed session is not instantly
        capped by an earlier run; doing that on every task filed would mean the cap could
        never be reached at all. A nudge starts the loop and leaves the meter alone.

        A session stopped for budget stays stopped. That cap exists to be hit, and an event
        arriving afterwards is not a reason to spend past it.
        """
        if not conversation_id or conversation_id in self._session_capped:
            return
        try:
            repo.conversations.set_working(AGENT_DB_PATH, conversation_id, True)
        except Exception:
            return  # bookkeeping; never take down the thing that triggered it
        self.ensure_loop()
        self._emit("status", why or "working", conversation=conversation_id)

    def rest(self, conversation_id: str = "") -> dict:
        """Stop taking steps. One session, or all of them when none is named.

        Naming none stops everything, which is what a person means by "stop" when they are
        not looking at a particular conversation — and it is what the old global switch did,
        so nothing that called it loses its meaning.
        """
        if conversation_id:
            repo.conversations.set_working(AGENT_DB_PATH, conversation_id, False)
        else:
            for row in repo.conversations.working_sessions(AGENT_DB_PATH):
                repo.conversations.set_working(AGENT_DB_PATH, row["id"], False)
        self._emit("status", "resting")
        return self.status()

    def tick_now(self) -> dict:
        """Force one tick immediately, off the request thread."""
        threading.Thread(target=self._safe_tick, args=(True,), daemon=True).start()
        return self.status()

    def cancel_tick(self) -> dict:
        """Stop the step that is running now, and leave roaming exactly as it is.

        Two separate things were sharing one word. "Stop" turned roaming off, and a step
        already in flight kept going regardless — for up to sixteen rounds, with the only
        control on screen greyed out while it ran. So watching him start down a wrong path
        meant watching him finish it.

        Independent on purpose: stopping this step does not decide whether there should be a
        next one. If he is roaming, the loop takes the next step as usual; if he is not,
        this is simply the end of it.
        """
        with self._state_lock:
            if not self._tick_lock.locked():
                return self.status()
            self._cancel.set()
        self._emit("status", "stopping this step")
        return self.status()

    def _working_ids(self) -> list[str]:
        try:
            return [row["id"] for row in repo.conversations.working_sessions(AGENT_DB_PATH)]
        except Exception:
            return []

    def status(self) -> dict:
        return {
            # Which sessions are mid-work. Replaces the single `running` boolean, which
            # could only ever be true for everything or false for everything.
            "working": self._working_ids(),
            "ticking": self._tick_lock.locked(),
            "stopping": self._cancel.is_set(),
            "lastTick": self._last_tick_at,
            "current": self._current,
            "ticks": self._tick_count,
            "tokensIn": self._tokens_in,
            "tokensOut": self._tokens_out,
            "tokensUncached": self._tokens_uncached,
            "costUsd": round(self._cost_usd, 6),
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

    def _charge_session(self, conversation_id: str, uncached_in: int, cost_usd: float) -> None:
        """Add this tick's spend to the session's meter; rest it once past the cap.

        **Money, not tokens.** This charged `tokens_in` — the whole prompt, cached re-reads
        included — on the reasoning that it was the number a person reacts to. It is, and that
        turned out to be the problem: at a 78% cache hit it is more than four times the volume
        actually read, so the meter runs four times too fast against a ceiling set in the same
        units. A real session was stopped for "running through 6,544,155 tokens, past its
        budget of 5,000,000" having spent, at the blended rate the provider was charging that
        day, about twenty-six cents.

        A cap whose job is "turn a stuck all-nighter into a message in the morning" has to be
        denominated in the thing that hurts. The provider reports cost on every call and it
        was already being summed for the dashboard; it just was not the thing being enforced.

        The token cap survives as a fallback for providers that report nothing — a local model
        costs nothing, so there is no money to measure and a runaway is bounded by time
        instead. Even there it now counts the *uncached* slice, which is the honest measure of
        work done rather than of prompt re-sent.
        """
        if not conversation_id or conversation_id in self._session_capped:
            return

        spent = self._session_cost.get(conversation_id, 0.0) + max(0.0, float(cost_usd))
        self._session_cost[conversation_id] = spent
        read = self._session_tokens.get(conversation_id, 0) + max(0, int(uncached_in))
        self._session_tokens[conversation_id] = read

        cap_cents = float(tuning.value("session_cost_cents"))
        token_cap = int(tuning.value("session_token_cap"))
        # Cost is authoritative whenever the provider gives us any. Only a provider that has
        # reported nothing at all falls through to tokens — otherwise a cheap model would be
        # held to a token ceiling it can never sensibly reach, which is the bug inverted.
        if spent > 0:
            if spent * 100 < cap_cents:
                return
            reached = f"${spent:,.2f}, past its budget of ${cap_cents / 100:,.2f}"
            short = f"budget reached: ${spent:,.2f} this session — resting"
        else:
            if read < token_cap:
                return
            reached = f"{read:,} tokens read, past its budget of {token_cap:,}"
            short = f"budget reached: {read:,} tokens this session — resting"

        self._session_capped.add(conversation_id)
        try:
            repo.conversations.set_working(AGENT_DB_PATH, conversation_id, False)
            repo.messages.add_message(
                AGENT_DB_PATH,
                f"I stopped this session — it ran through {reached}. I've rested it so it "
                "can't keep spending while you're away. Tell me to keep going if you want "
                "more, or raise the session budget in settings.",
                kind="stuck",
            )
            self._emit("done", short, conversation=conversation_id)
        except Exception:
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

    def publish(self, kind: str, text: str, **fields) -> None:
        """Put a line on the Mind feed from outside the loop.

        A conversation is work too, and it used to be the one kind that left no trace here.
        Public rather than reaching into `_emit` from another module, so the feed keeps a
        single door and adding a field to a line stays one edit.
        """
        self._emit(kind, text, **fields)

    # -- internals ---------------------------------------------------------- #

    def _loop(self) -> None:
        """Advance what is already underway. Never decide that something should be.

        Two reasons to act, and neither is a schedule. Something is due — a reminder, a
        standing job, a reply he owes you. Or a session is working, which is a thing you
        turned on for that session and can turn off for that session.

        What is gone from here is the whole apparatus for guessing when he may act: a roam
        switch, an interval, a 600-second idle backoff, a quiet period after you last spoke.
        Every one of them was answering "when may he work without me", and a session answers
        that by existing. The floor between steps stays, because a burst of due reminders
        should not spin this faster than he can actually work.
        """
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                gap_ok = self._last_tick_mono is None or (now - self._last_tick_mono) >= tuning.value(
                    "min_gap"
                )
                # Hold off if a provider error just backed us off, so a flapping upstream is
                # retried in a few seconds rather than every second.
                if (
                    gap_ok
                    and now >= self._error_backoff_until
                    and (self._has_due() or self._sessions_working())
                ):
                    self._safe_tick(forced=False)
            except Exception:
                pass
            self._stop.wait(1.0)

    def _sessions_working(self) -> bool:
        """Is any session mid-work?

        Cheap enough for a one-second poll: one indexed read of a table with as many rows as
        you have had conversations.
        """
        try:
            return bool(repo.conversations.working_sessions(AGENT_DB_PATH))
        except Exception:
            return False

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

    def _safe_tick(self, forced: bool) -> None:
        if not self._tick_lock.acquire(blocking=False):
            return  # a tick is already running
        # Cleared here rather than after the tick: a cancel arriving in the moment between
        # one step ending and the next beginning would otherwise kill the innocent step.
        self._cancel.clear()
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

    def _next_session(self) -> dict | None:
        """The working session whose turn it is, and what it has claimed.

        Longest-waiting first, which ``working_sessions`` already orders for us, and the
        conversation is touched at the end of its tick so it goes to the back of the queue.
        A busy session cannot starve the others simply by having more to do.

        None means nobody is working — which happens on "Run once", and there the whole
        board is fair game exactly as it was.
        """
        sessions = self._sessions()
        return sessions[0] if sessions else None

    def _sessions(self) -> list[dict]:
        try:
            return repo.conversations.working_sessions(AGENT_DB_PATH)
        except Exception:
            return []

    @staticmethod
    def _in_scope(row: dict, project: int | None, claimed: set[int]) -> bool:
        """Is this task (or milestone) this session's to work on?

        Two rules, and the second is the one that keeps sessions out of each other's way:

        * A session bound to a project works that project and nothing else. This is what
          makes two projects at once actually two projects — before it, both sessions read
          the same global board and both advanced whatever happened to be top priority.
        * A session bound to nothing works everything *except* what another working session
          has claimed. So a general session still picks up one-off errands and unfiled work,
          and cannot wander into the middle of a project someone else is driving.

        With nobody working — "Run once" — `claimed` is empty and this is the whole board,
        which is the behaviour that button has always had.
        """
        pid = row.get("project_id")
        if project:
            return pid == project
        return pid not in claimed

    def _tick(self) -> None:
        """Advance one session, bound to it for the whole step.

        The binding is what lets a tool record which session did the work: starting a project
        mid-step is that session adopting it, and a handler is called as ``(path, args)`` with
        no idea who asked. Set here rather than deeper in because the step is the unit that
        belongs to a session — every tool call inside it does too.
        """
        # Whose turn it is. Everything that reads the board reads it through this, because
        # work belongs to a session now and a tick that ignored that was two sessions racing
        # for the same top-priority task.
        session = self._next_session()
        conversation_id = str(session["id"]) if session else ""
        with session_context.working_in(conversation_id):
            try:
                self._step(session, conversation_id)
            finally:
                # Back of the queue, so the next tick takes a different session. Ordered by
                # `updated_at`, so touching it here is the round-robin — without it the
                # busiest session would hold the loop and the others would never run.
                if conversation_id:
                    try:
                        repo.conversations.touch(AGENT_DB_PATH, conversation_id)
                    except Exception:
                        pass

    def _step(self, session: dict | None, conversation_id: str) -> None:
        config = default_config()

        project = int(session["project_id"]) if session and session.get("project_id") else None
        claimed = {int(s["project_id"]) for s in self._sessions() if s.get("project_id")}

        active = [
            task
            for task in repo.tasks.active_tasks(AGENT_DB_PATH)  # highest priority first
            if self._in_scope(task, project, claimed)
        ]

        now = clock.now_iso()
        pending = self._new_pending()
        awaiting = repo.tasks.tasks_awaiting_kith(AGENT_DB_PATH)  # tasks he was answered on
        reminders_due = repo.reminders.due_reminders(AGENT_DB_PATH, now)
        schedules_due = repo.schedules.due_schedules(AGENT_DB_PATH, now)
        due = bool(reminders_due or schedules_due)
        # A laid-out project with no tasks under it is work, not quiet. Read before
        # `idle` is computed, because otherwise a whole project sits inert and he rests.
        unplanned = [
            milestone
            for milestone in repo.projects.milestones_needing_tasks(AGENT_DB_PATH)
            if self._in_scope(milestone, project, claimed)
        ]
        self._tick_count += 1

        # Precedence, most important first: answer your person, then anything due, then
        # break a loop you are stuck in, then work a task, then plan a milestone nobody can
        # act on. Five branches where there were nine — the four that went were the
        # scheduled inner life, which is not a thing a clock should decide.
        resuming = not pending and bool(awaiting)
        breaking = self._stall >= tuning.value("stall_break")
        # Which task this tick actually WORKS (the `active` branch below), for grind
        # detection. Stays None on every other mode — reply, plan, breakout, idle.
        working_task_id: int | None = None
        # Which project this tick is *on*, which is not the same as which project its session
        # is bound to. An unbound session picks up a task in someone's folder-linked project
        # and `project` above is None — so before this, the tick got neither that project's
        # folder (`base_dir` fell back to ~/Kith, and every relative path he wrote went there)
        # nor its `.kith/memory.md` (nothing to look it up by). Both failures came from asking
        # the conversation a question only the task could answer.
        working_project: int | None = project
        if pending:
            mode, self._current = "reply", f"replying: {pending[0]['body'][:40]}"
            directive, prompt = directives.REPLY, _reply_prompt(pending)
            self._last_reply_attempt_id = max(m["id"] for m in pending)
        elif resuming:
            mode, self._current = "reply", f"picking up: {awaiting[0]['goal'][:40]}"
            directive, prompt = directives.RESUME, _resume_prompt(awaiting)
            working_project = working_project or self._project_of(awaiting[0])
        elif due:
            label = schedules_due[0]["note"] if schedules_due else reminders_due[0]["note"]
            mode, self._current = "start", f"due: {label}"
            directive, prompt = directives.AUTONOMY, _due_prompt(reminders_due, schedules_due)
        elif breaking:
            mode, self._current = "breakout", "breaking out of a loop"
            directive, prompt = directives.BREAKOUT, _breakout_prompt(active)
        elif active:
            focus = repo.tasks.task_detail(AGENT_DB_PATH, active[0]["id"]) or active[0]
            working_task_id = active[0]["id"]
            working_project = working_project or self._project_of(focus) or self._project_of(active[0])
            mode, self._current = "start", f"working on: {focus['goal']}"
            directive, prompt = directives.WORK, _focus_prompt(focus, active)
        elif unplanned:
            # Below working a task he already has — a half-finished task beats planning the
            # next thing — and above every kind of inner life, because a project nobody can
            # act on is not a reason to go and reflect.
            next_up = unplanned[0]
            working_project = working_project or self._project_of(next_up)
            mode, self._current = "start", f"planning: {next_up['title'][:40]}"
            directive, prompt = directives.WORK, _breakdown_prompt(next_up)
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
            self._current, note = self._why_idle(project, claimed)
            self._emit("status", self._current, conversation=conversation_id)
            if note:
                self._say_youre_the_blocker(note)
            # And stop working, because there is nothing left to work on.
            #
            # Without this a session runs forever. Measured: a task to write three haiku
            # finished in a few steps, and the loop then took thirty more, waking every
            # second to rediscover an empty board. It cost little because an idle tick calls
            # no model — but "keep going until I stop you" has to mean "until the work is
            # done or you stop me", or the promise is one nobody would make deliberately.
            #
            # Stopping here rather than at the end of a task is what makes it right in the
            # cases that are not simply finished: blocked on a question, or waiting on a
            # milestone. Those are all "nothing I can do next", and in each of them the
            # honest thing is to stop and have said why — which the note above just did.
            #
            # This session, not every session. It used to stop all of them, which was
            # invisible while the board was global and everyone ran out of work together —
            # and is plainly wrong now: one project finishing would have downed every other
            # session mid-task.
            if conversation_id:
                repo.conversations.set_working(AGENT_DB_PATH, conversation_id, False)
            return
        self._emit(mode, self._current, conversation=conversation_id)

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
        # What this project knows about itself. A conversation has been shown this since
        # `.kith/memory.md` existed and a tick never was — so everything the project learned
        # was visible while you were watching and invisible the moment he was on his own,
        # which is precisely backwards. The unattended step is the one with nobody to remind
        # him how the thing is built.
        blocks.append(self._project_memory(working_project))
        state = "\n\n".join(block for block in blocks if block).strip()
        messages = [{"role": "system", "content": f"{config.system}\n\n{directive}"}]
        if state:
            messages.append({"role": "system", "content": state})
        messages.append({"role": "user", "content": prompt})

        # Reminders fire once, then retire; schedules fire, then roll to next time.
        for reminder in reminders_due:
            repo.reminders.set_reminder_status(AGENT_DB_PATH, reminder["id"], "done")
            self._emit("reminder", reminder["note"], conversation=conversation_id)
        for sched in schedules_due:
            nxt = clock.next_fire_after(sched.get("every_minutes"), sched.get("daily_at"))
            repo.schedules.reschedule(AGENT_DB_PATH, sched["id"], nxt)
            self._emit("reminder", f"(standing) {sched['note']}", conversation=conversation_id)
        # A tick is a step, not an essay, and bounding its output is what keeps running all
        # day affordable. This was `min(config.num_predict, tick_max_tokens)` and did nothing:
        # `num_predict` is -1 on this install — the sentinel for "no limit" — so the min is
        # -1 and every tick has been running uncapped since the knob was added. A sentinel
        # that sorts below every real value silently wins any comparison meant to bound it.
        tick_cap = tuning.value("tick_max_tokens")
        wanted = config.num_predict
        tick_config = replace(config, num_predict=tick_cap if wanted <= 0 else min(wanted, tick_cap))

        final_text = ""
        tick_in = tick_out = tick_uncached = 0
        tick_cost = 0.0
        rounds = 0
        tools_used: list[str] = []
        error_msg: str | None = None
        cancelled = False
        started = time.monotonic()
        journal_before = _latest_journal_id()
        # Tools run inside this loop, and `base_dir()` reads the project from here — this
        # is what makes a relative path he writes land in the project rather than in his
        # own folder. Bound for the duration of the step only; see `working_on`.
        with session_context.working_on(working_project):
            for event in stream_agent(
                messages,
                tick_config,
                ollama_host(),
                AGENT_DB_PATH,
                max_rounds=16,
                allow=_ALLOW.get(mode),
                # A tick that leaves nothing behind is a tick that will be repeated.
                expect_durable=True,
                # Which OpenRouter stickiness id this step lands on. Chat has always passed
                # its conversation and a tick passed nothing, falling through to the one
                # install-wide id — so a session's chat turns and that same session's ticks
                # were guaranteed to be on different upstream hosts, each keeping its own copy
                # of the persona. The cacheable region is the persona and nothing else (see
                # caching.stable_head), and it is byte-identical between the two, so they were
                # paying to warm the same bytes twice. Empty for a step run from "Run" with
                # nobody working, which falls back to the install-wide id exactly as before.
                conversation_id=conversation_id,
            ):
                # Cancellation happens here rather than inside the agent loop, because here it
                # is safe by construction: stream_agent is a generator, so abandoning it stops
                # it between events. A tool call that has already run has already finished —
                # nothing is left half-applied, and no file is half-written.
                if self._cancel.is_set():
                    cancelled = True
                    self._emit("status", "stopped", conversation=conversation_id)
                    break
                # The task can change under him while he works it. A tick reads the board once
                # and then runs for a couple of minutes, so blocking a task, sending it back to
                # the backlog or closing it yourself all used to be invisible — he carried on
                # against a picture that was true when he started and was not any more. Pulling
                # a task out from under him is a clear instruction to stop touching it, and it
                # should not have to wait for him to finish first.
                if working_task_id and self._task_moved_on(working_task_id):
                    self._emit(
                        "status",
                        "stopping — that task changed while I was on it",
                        conversation=conversation_id,
                    )
                    break
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
                        conversation=conversation_id,
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
                    tick_cost += float(stats.get("costUsd") or 0.0)
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
                        conversation=conversation_id,
                    )
                elif kind == "error":
                    error_msg = event["message"]
                    self._emit("error", event["message"], conversation=conversation_id)
        self._tokens_in += tick_in
        self._tokens_out += tick_out
        self._tokens_uncached += tick_uncached
        self._cost_usd += tick_cost
        self._last_tick_tokens = tick_in + tick_out
        self._last_tick_uncached = tick_uncached + tick_out
        # Bound the whole session, not just this tick: add this step's prompt volume to the
        # session meter and rest the session if it has now crossed its budget.
        self._charge_session(conversation_id, tick_uncached, tick_cost)

        if final_text.strip():
            self._emit("thought", final_text.strip()[:600], conversation=conversation_id)

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
        if error_msg:
            # A provider error is not evidence the work is stuck, so it must not count toward
            # the grind limit; and back the loop off briefly so a flapping upstream isn't hit
            # again next second.
            self._error_backoff_until = time.monotonic() + self._ERROR_BACKOFF_SECONDS
        else:
            self._detect_grind(working_task_id, active)
        self._emit("done", "step complete", conversation=conversation_id)

        # Durable flight recorder — one row per tick, so how he's doing is
        # reviewable over time even though the live Mind feed is in-memory.
        # A step you stopped is its own outcome. Not an error — nothing went wrong — and not
        # "step complete", which would be a lie about work that was cut off partway. It gets
        # a row either way: /api/activity counts rows, and a stopped step with no row is
        # indistinguishable from a step that never happened, which is exactly the ambiguity
        # a Stop button exists to remove.
        if cancelled:
            did = f" after {', '.join(dict.fromkeys(tools_used))}" if tools_used else ""
            outcome = f"stopped{did}"
        elif error_msg:
            outcome = f"error: {error_msg}"
        else:
            outcome = final_text.strip()[:280] or "step complete"
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

    def _why_idle(self, project: int | None = None, claimed: set[int] | None = None) -> tuple[str, str]:
        """Why there is nothing to do, and whether you need telling.

        Returns the status line and, when you are the reason, what to say. Deliberately reads
        two different things: tasks whose status is `waiting` (he asked you something) and
        tasks held by the roadmap (their milestone waits on one that is not finished). Both
        mean "he cannot proceed without you"; neither shows up as work he can do.

        Scoped to the session that ran out of work, for the same reason the tick is: a
        session that finished its project should say so, not report on questions outstanding
        somewhere else entirely.
        """
        held_by = claimed or set()
        asked = [
            task
            for task in repo.tasks.list_tasks(AGENT_DB_PATH)
            if task["status"] == "waiting" and self._in_scope(task, project, held_by)
        ]
        held = [
            task
            for task in repo.tasks.waiting_on_the_roadmap(AGENT_DB_PATH)
            if self._in_scope(task, project, held_by)
        ]
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
        # Ready to work, and shut out by a closed project. `active_tasks` drops everything
        # under a done or paused project, so a project that completed itself and then gained
        # a task showed two `todo` items on the board while every tick said "caught up —
        # resting". Nothing in this function had a branch for it, so the most confusing state
        # available produced the most reassuring sentence available.
        shut = self._shut_out(project, held_by)
        if shut:
            names = ", ".join(sorted({one["project"] for one in shut}))
            return (
                f"nothing I can pick up — {len(shut)} task"
                f"{'' if len(shut) == 1 else 's'} in a closed project",
                f"I have {len(shut)} task{'' if len(shut) == 1 else 's'} ready to work, but "
                f"{names} is not active, so I am not allowed to touch them: "
                + "; ".join(f"“{one['goal']}” (#{one['id']})" for one in shut[:3])
                + ("…" if len(shut) > 3 else "")
                + ". Reopen the project and I will start.",
            )
        # A session hired for one project has finished it. Worth saying which, because the
        # session staying put is now deliberate: it used to wander to whatever project it
        # touched next, which read as him changing the subject on his own. Being told "caught
        # up" by a session you pointed at one thing, while another project has work waiting,
        # is confusing in a way naming the project fixes in one sentence.
        if project:
            elsewhere = sum(
                1 for task in repo.tasks.active_tasks(AGENT_DB_PATH) if self._project_of(task) != project
            )
            here = self._project_name(project)
            if elsewhere:
                return (
                    f"done with {here} — resting",
                    f"Nothing left that I can do on {here}, and this session is on {here} — so "
                    f"I am not picking up the {elsewhere} task{'' if elsewhere == 1 else 's'} "
                    "waiting on other projects. Point this session somewhere else, or start a "
                    "new one for them.",
                )
            return f"done with {here} — resting", ""
        return "caught up — resting", ""

    def _project_name(self, project_id: int) -> str:
        try:
            row = repo.projects.get_project(AGENT_DB_PATH, int(project_id))
            return str((row or {}).get("name") or f"project #{project_id}")
        except Exception:
            return f"project #{project_id}"

    def _shut_out(self, project: int | None, held_by: set[int]) -> list[dict]:
        """Tasks that are ready to work and whose project will not let them run."""
        try:
            closed = {
                int(row["id"]): (row.get("name") or f"project #{row['id']}")
                for row in repo.projects.list_projects(AGENT_DB_PATH)
                if (row.get("status") or "") in ("done", "paused")
            }
            if not closed:
                return []
            return [
                {"id": task["id"], "goal": task["goal"], "project": closed[int(task["project_id"])]}
                for task in repo.tasks.list_tasks(AGENT_DB_PATH)
                if task.get("status") in ("todo", "doing")
                and task.get("project_id")
                and int(task["project_id"]) in closed
                and self._in_scope(task, project, held_by)
            ]
        except Exception:
            # Explaining why he is idle must never be the thing that stops him being idle
            # gracefully. No explanation is worse than a wrong one but better than a crash.
            return []

    #: How long to leave between saying "you are the blocker". Said once per stretch, not once
    #: per tick: a tick can fire every thirty seconds, and the same true sentence repeated
    #: twenty times is indistinguishable from a fault.
    _BLOCKER_QUIET_SECONDS = 6 * 60 * 60

    #: How long to hold off after a provider error before the loop takes another step.
    _ERROR_BACKOFF_SECONDS = 15.0

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

    @staticmethod
    def _focus_progress_of(detail: dict | None) -> int:
        """Countable forward motion on a task: checklist items ticked plus deliverables filed.

        The same notion of "moved forward" the stall detector's ADVANCE_TOOLS encodes, but read
        off the task's STATE rather than off which tools were named — so a tick that called
        update_task and changed nothing counts as the standstill it was.
        """
        if not detail:
            return 0
        checked = sum(1 for c in (detail.get("checklist") or []) if c.get("done"))
        return checked + len(detail.get("deliverables") or [])

    def _detect_grind(self, working_task_id: int | None, active: list[dict]) -> None:
        """Set aside a task worked for too many ticks with no net progress.

        The third stall signal, and the one that would have caught the 23x Prisma loop: it
        depends on neither wording (which the loop varied) nor tool shape (it "used update_task"
        each time), only on whether an item got ticked or a deliverable filed. A tick that
        genuinely advances resets the count; a run that doesn't crosses focus_grind_limit and
        hands the task back through the same _give_up path stall-giveup uses.
        """
        if working_task_id is None:
            return
        progress = self._focus_progress_of(repo.tasks.task_detail(AGENT_DB_PATH, working_task_id))
        if working_task_id != self._focus_id:
            self._focus_id, self._focus_progress, self._focus_grind = working_task_id, progress, 0
            return
        if progress > self._focus_progress:
            self._focus_progress, self._focus_grind = progress, 0
            return
        self._focus_grind += 1
        if self._focus_grind >= tuning.value("focus_grind_limit"):
            given_up = _give_up(active)
            if given_up:
                self._emit("breakout", f"set aside — {self._focus_grind} steps, no progress: {given_up}")
            self._focus_id, self._focus_grind = None, 0

    def _task_moved_on(self, task_id: int) -> bool:
        """Has the task he is working stopped being his to work?

        Throttled rather than checked on every event: it is one indexed read, but a tick fires
        events continuously and there is no reason to ask twice in the same second. A person
        blocking a task will wait two seconds for him to notice; nobody will notice the delay.

        Answers False on any failure. Interrupting real work because a status lookup hiccupped
        is a far worse outcome than finishing a step that should have stopped.
        """
        now = time.monotonic()
        if now - self._last_freshness_check < 2.0:
            return False
        self._last_freshness_check = now
        try:
            row = repo.tasks.task_detail(AGENT_DB_PATH, task_id)
        except Exception:
            return False
        if not row:
            return True  # deleted under him
        return str(row.get("status") or "") not in ("todo", "doing")

    @staticmethod
    def _project_of(row: dict | None) -> int | None:
        """The project a task or milestone belongs to, if it says."""
        try:
            value = (row or {}).get("project_id")
            return int(value) if value else None
        except (TypeError, ValueError):
            return None

    def _project_memory(self, project: int | None) -> str:
        """`.kith/memory.md` for the project this tick is working on, or nothing."""
        if not project:
            return ""
        try:
            from kith.services import project_memory

            row = repo.projects.get_project(AGENT_DB_PATH, project)
            if not row or not row.get("directory"):
                return ""
            return project_memory.block(row["directory"], row.get("name") or "")
        except Exception:
            return ""

    def _emit(
        self,
        kind: str,
        text: str,
        tokens: dict | None = None,
        tool: str | None = None,
        args: dict | None = None,
        conversation: str = "",
    ) -> None:
        """Push one line onto the live Mind feed.

        ``tokens`` rides alongside the text rather than being formatted into it, so the
        interface can show a per-request count as a quiet figure on the line instead of
        another sentence in the stream. Feed items are a flat {kind, text, at} shape and
        older readers ignore a key they don't know, so this stays additive.

        ``conversation`` is which session the line belongs to, and it is what lets the Mind
        panel show *this* session's work instead of everything at once. Absent on the global
        lines — a status change, a step run with nobody working — which the panel treats as
        belonging to whatever you are looking at, because they do.
        """
        item = {"kind": kind, "text": text, "at": _now()}
        if tokens:
            item["tokens"] = tokens
        if tool:
            item["tool"] = tool
        if args:
            item["args"] = args
        if conversation:
            item["conversation"] = conversation
        self._buffer.append(item)
        with self._state_lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(item)


# One runner for the process; created here, started only when asked.
runner = AutonomyRunner()
