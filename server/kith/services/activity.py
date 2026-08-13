"""The live feed of what he is doing, and what a session has spent doing it.

Both of these lived inside one class that also contained a loop choosing work on
its own. Only the loop was the idea being abandoned. The feed is not: chat publishes every
turn through it, and a conversation was once the one kind of work that left no trace here.

Nothing in this module runs on its own. It is written to, read from, and subscribed to.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from datetime import UTC, datetime

from kith.infra.db import repositories as repo
from kith.services import tuning
from kith.settings import AGENT_DB_PATH

#: How much of an argument survives into a feed line.
_ARG_CHARS = 160


def _now() -> str:
    return datetime.now(UTC).isoformat()


def short_args(arguments: dict) -> dict:
    """Arguments trimmed for display, as data rather than as a sentence.

    Values only — the interface decides which argument is the subject of which verb, because
    that is a presentation question and it changes when the wording changes.
    """
    trimmed: dict[str, str] = {}
    for key, value in (arguments or {}).items():
        text = value if isinstance(value, str) else str(value)
        text = " ".join(text.split())
        trimmed[key] = text[: _ARG_CHARS - 1] + "…" if len(text) > _ARG_CHARS else text
    return trimmed


def describe_call(name: str, arguments: dict) -> str:
    if not arguments:
        return f"{name}()"
    parts = []
    for key, value in arguments.items():
        text = value if isinstance(value, str) else str(value)
        if len(text) > 40:
            text = text[:40] + "…"
        parts.append(f"{key}={text}")
    return f"{name}({', '.join(parts)})"


class Feed:
    """One feed for the process: a ring buffer of recent lines and a set of subscribers."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._buffer: deque[dict] = deque(maxlen=100)

        # Running token tally so cloud spend is visible at a glance. `_in` is every token he
        # was shown, which counts a cached prefix again on every round; `_uncached` is what a
        # provider actually had to read. On a warm cache the two differ by more than 10x over
        # a turn, so the second is the honest one.
        self._tokens_in = 0
        self._tokens_out = 0
        self._tokens_uncached = 0
        # Dollars, as the provider billed them. Every token count here is a proxy for this,
        # and OpenRouter returns it on every call — we simply were not reading it.
        self._cost_usd = 0.0
        # Per-session meter, keyed by conversation_id. The tallies above are process-wide and
        # keyed to nothing, so they cannot bound one runaway session; this can. In-memory: a
        # restart mid-run forgets it, a bounded gap rather than the all-nighter it exists to
        # stop.
        self._session_tokens: dict[str, int] = {}
        # And what it has actually cost, as the provider billed it. The cap is enforced on
        # this; tokens are only the fallback for a provider that reports no price.
        self._session_cost: dict[str, float] = {}
        # Sessions already stopped for budget, so a late charge can't post the note twice.
        self._session_capped: set[str] = set()

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

    def publish(
        self,
        kind: str,
        text: str,
        *,
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
        lines — a status change — which the panel treats as belonging to whatever you are
        looking at, because they do.

        Keyword-only beyond the text, where this used to take ``**fields``: a mistyped key
        used to be accepted and silently dropped, which on a feed looks exactly like a line
        that was never published.
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

    # -- what it cost ------------------------------------------------------- #

    def observe(self, uncached_in: int, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        """Add one request's cost to the process-wide tally."""
        self._tokens_in += max(0, int(tokens_in))
        self._tokens_out += max(0, int(tokens_out))
        self._tokens_uncached += max(0, int(uncached_in))
        self._cost_usd += max(0.0, float(cost_usd))

    def charge_session(self, conversation_id: str, uncached_in: int, cost_usd: float) -> bool:
        """Add this turn's spend to the session's meter. True once it is past its budget.

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

        Returns rather than acts. It used to enforce the cap by resting the session — telling
        it to stop keeping going on its own — and there is no keeping going any more. What is
        left to stop is the turn, which the caller owns.
        """
        if not conversation_id or conversation_id in self._session_capped:
            return False

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
                return False
            reached = f"${spent:,.2f}, past its budget of ${cap_cents / 100:,.2f}"
            short = f"budget reached: ${spent:,.2f} this session — stopping"
        else:
            if read < token_cap:
                return False
            reached = f"{read:,} tokens read, past its budget of {token_cap:,}"
            short = f"budget reached: {read:,} tokens this session — stopping"

        self._session_capped.add(conversation_id)
        try:
            repo.messages.add_message(
                AGENT_DB_PATH,
                f"I stopped this session — it ran through {reached}. I've stopped it so it "
                "can't keep spending while you're away. Say the word if you want me to carry "
                "on, or raise the session budget in settings.",
                kind="stuck",
                link="/messages",
            )
            self.publish("done", short, conversation=conversation_id)
        except Exception:
            pass
        return True

    def status(self) -> dict:
        return {
            "tokensIn": self._tokens_in,
            "tokensOut": self._tokens_out,
            "tokensUncached": self._tokens_uncached,
            "costUsd": round(self._cost_usd, 6),
        }


#: One feed for the process.
feed = Feed()
