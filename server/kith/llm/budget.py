"""How much room is left in the context window, and when to make some.

Before this, nothing watched the total. A conversation that outgrew the model's window got
an HTTP 400 and the turn ended with `Cloud model returned 400: …` — no compaction, no retry
with less, no chance to save state. The window was fetched when the model was chosen and
never consulted again.

**Estimation comes off the wire, not off a ruler.** No tokenizer is installed, and adding
one is the wrong answer: a per-model BPE is a large dependency that would still be wrong for
whichever provider you switch to next. But the provider reports `prompt_tokens` on every
single round, which is the truth. So each round's report calibrates a characters-per-token
ratio, and the only thing ever *estimated* is the delta appended since — typically a percent
or two of the prompt.

Measured on this repo's own transcripts, a fixed ratio is not good enough to build a
threshold on: comparing transcript bytes to billed prompt tokens gives a median of 0.17
chars per token, because the prompt is dominated by a persona and tool schemas that never
appear in a transcript at all. Anything anchored to a constant is guessing.

**Pictures are not their bytes.** A message carrying an image part is a 600KB data URI that a
provider counts as a few hundred tokens. Measuring it by length would put the estimate out by
three orders of magnitude in the direction that matters, so an image part is costed flat.
This is the same mistake that cost 2.8 million tokens when base64 reached a text field, seen
from the other side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Where to start before any round has reported. `caching._CHARS_PER_TOKEN` is the repo's
#: existing figure for prose and JSON, and it is only ever the seed — the first real
#: measurement replaces it.
SEED_CHARS_PER_TOKEN = 3.7

#: What the first request of the last turn on each model actually cost, as a ratio — the
#: starting point for the next turn on that model, so the seed is only used when nothing has
#: been measured since the server started.
#:
#: **The first reading of every turn was a guess.** A `ContextBudget` is born per turn and the
#: first wire measurement arrives only after the first request, so the reading the person sees
#: when a turn starts was costed at 3.7 in all 353 turns recorded Sep 13-27 — and in every
#: single-round turn, which is most chat, the *only* reading was. Against what each of those
#: first requests was actually billed: median error 3.4%, p90 43.6%. Starting from this model's
#: last first-round ratio instead: median 0.7%, p90 19.3% — the remaining tail being a model
#: whose real ratio sits below `MIN_RATIO` and a first turn after switching models.
#:
#: The *first* round's ratio, not the latest. A turn's first request is persona, tools and the
#: history, which is what the next turn's first request is too; its fortieth is mostly tool
#: output, which tokenizes differently. Carrying the latest instead measured worse (median 1.0%,
#: p90 22.0%) for exactly that reason.
#:
#: Per model because the tokenizer is the model's. Process-wide rather than per conversation
#: because a new conversation's first turn deserves the measurement too — and the anchor that
#: `ContextBudget` extrapolates from stays per turn, which is the part concurrency would break.
_FIRST_ROUND: dict[str, float] = {}

#: The band a calibrated ratio is held to. Outside it, something is wrong with the arithmetic
#: rather than with the model — a ratio of 20 would silently disable the guard, and one of
#: 0.5 would make it fire on every round of every turn.
#:
#: **The floor was 2.0, and a real model sits below it.** `openai/gpt-6.1-sol-pro` bills about
#: 1.4 characters a token — 3.3x what OpenRouter's own count makes of the same request, where
#: plain Sol bills 0.89x. Clamped at 2.0, the meter said 237k on a request billed at 349k, so
#: nothing folded before 272k, the line past which OpenAI doubles every rate: flex input went
#: from $1 to $2 a million, and each round cost $0.23 to say a sentence (2026-10-01).
MIN_RATIO, MAX_RATIO = 1.0, 6.0

#: What one image part costs, regardless of how many bytes its data URI is. A page render at
#: high detail is roughly this; being out by a factor of two here is immaterial next to being
#: out by a factor of a thousand, which is what measuring the base64 would do.
IMAGE_TOKENS = 800


def _sent(key: str) -> bool:
    """Whether a message key reaches the provider. ``_present`` is the one underscore key that does:
    it is an earlier turn's present-state block, riding on the history message it was sent with
    until the prompt is assembled and it goes back out beside it (see `turn.prompt._assemble`)."""
    return not key.startswith("_") or key == "_present"


def message_chars(message: dict[str, Any]) -> int:
    """The size of one message, with image parts costed rather than measured.

    Returns characters, so it composes with the ratio. An image contributes its flat token
    cost converted back into characters, which keeps one unit throughout.

    Keys starting with ``_`` are left out, because they are not sent: `openai_compat._to_openai`
    rebuilds every message from role and content, and the underscore is how this codebase marks
    bookkeeping that rides on a message for the loop's own use. Counting them measured something
    that never reached a provider — harmless while they were flags, and badly wrong once one of
    them carried text. `history._tool_result_message` keeps the whole result a pointer stands in
    for under ``_whole``; counted, every pointer would have cost the meter exactly what it saved
    the request, and calibrated the ratio against characters nobody was billed for.
    """
    content = message.get("content")
    if not isinstance(content, list):
        return len(json.dumps({key: value for key, value in message.items() if _sent(key)}))
    total = 0
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            total += int(IMAGE_TOKENS * SEED_CHARS_PER_TOKEN)
        else:
            total += len(json.dumps(part))
    # The envelope — role, tool_call_id — is small but real.
    return total + 40


def conversation_chars(convo: list[dict[str, Any]], schemas: list[dict] | None = None) -> int:
    """Everything a request will carry: the messages and the tool block."""
    total = sum(message_chars(message) for message in convo)
    if schemas:
        total += len(json.dumps(schemas))
    return total


@dataclass
class ContextBudget:
    """What the last round actually cost, and what the next one probably will.

    One instance per turn, held as a local in `stream_agent`. Deliberately not process-wide:
    two concurrent conversations have different prompts and
    would each drag the other's calibration around with no lock and no way to notice. The one
    thing that does outlive the turn is where its ratio *starts* — see `_FIRST_ROUND` — and
    that is only ever a starting point: this turn's first measurement replaces it.
    """

    #: The model's context window, or 0 when nobody knows — Ollama, a custom endpoint, a
    #: model chosen before the window was recorded. Zero disables every check below rather
    #: than guessing a number: acting on a made-up window would trim a conversation that had
    #: plenty of room.
    window: int = 0
    #: Room to leave for the answer. `num_predict` is -1 (no limit) on a default install, so
    #: the caller resolves this to something real before handing it over.
    reserve: int = 4_096
    #: Which model the turn is talking to: where its starting ratio is looked up and its first
    #: measurement is remembered. Empty remembers nothing and starts from the seed, which is what
    #: a caller with no model — a test, a script — should get.
    model: str = ""
    #: Left at 0 to mean "whatever this model last measured, else the seed" — resolved in
    #: `__post_init__`. A caller that passes a ratio gets exactly that ratio.
    chars_per_token: float = 0.0
    #: The last figure the provider reported, and the size of what produced it.
    last_prompt_tokens: int = 0
    last_chars: int = 0
    #: How much one more round has grown the prompt, biggest seen so far. Measured rather
    #: than assumed: a round that reads a file grows by kilobytes, one that ticks a checklist
    #: by a hundred bytes, and picking a constant would be wrong in both directions.
    biggest_growth: int = 0
    _measurements: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.chars_per_token <= 0:
            self.chars_per_token = (
                _FIRST_ROUND.get(self.model, SEED_CHARS_PER_TOKEN) if self.model else SEED_CHARS_PER_TOKEN
            )

    @property
    def known(self) -> bool:
        return self.window > 0

    def observe(self, prompt_tokens: int, chars: int) -> None:
        """Record what the provider charged for a request of this size."""
        if prompt_tokens <= 0 or chars <= 0:
            return
        ratio = chars / prompt_tokens
        self.chars_per_token = min(MAX_RATIO, max(MIN_RATIO, ratio))
        if self.model and not self._measurements:
            _FIRST_ROUND[self.model] = self.chars_per_token
        if self.last_chars:
            self.biggest_growth = max(self.biggest_growth, max(0, chars - self.last_chars))
        self.last_prompt_tokens = prompt_tokens
        self.last_chars = chars
        self._measurements += 1

    def projected(self, chars: int) -> int:
        """Tokens the next request will carry, at its current size.

        Anchored on the last measurement and extrapolating only the difference, so the error
        is on the delta rather than on the whole prompt. Before any measurement there is
        nothing to anchor to and the whole thing is an estimate, which is why the first round
        of a turn is never acted on.
        """
        if not self._measurements:
            return int(chars / self.chars_per_token)
        return self.last_prompt_tokens + int((chars - self.last_chars) / self.chars_per_token)

    def headroom(self, chars: int) -> int:
        """Tokens left before the next round would not fit. Meaningless with no window."""
        if not self.known:
            return 0
        return self.window - self.projected(chars) - self.reserve - self.biggest_growth

    def is_tight(self, chars: int) -> bool:
        """Would one more round of this turn risk not fitting?

        Absolute rather than a fraction of the window. A bare percentage is wrong at both
        ends: 85% of a 32k model leaves 4,800 tokens, which is less than a default answer,
        while 85% of a million leaves 150,000 and trims a conversation with room to spare.
        What matters is whether the prompt, the answer and one more round still fit.

        Never fires before the first measurement, because there is nothing to anchor to and
        acting on a pure estimate is how a turn gets trimmed on round one for no reason.
        """
        if not self.known or not self._measurements:
            return False
        return self.headroom(chars) <= 0


def looks_like_overflow(status: int, body: str) -> bool:
    """Is this 400 the window, rather than anything else?

    On the provider's own words, the way `_refuses_reasoning` reads a refusal. There is no
    status code for it — every provider spells it differently in prose — and guessing wrong
    in the permissive direction would make the loop retry a request that failed for some
    other reason, hard-compacting a conversation to fix a problem it does not have.
    """
    if status != 400:
        return False
    text = (body or "").lower()
    return any(
        phrase in text
        for phrase in (
            "context length",
            "context_length",
            "context window",
            "maximum context",
            "too many tokens",
            "prompt is too long",
            "reduce the length",
            "exceeds the maximum",
            "input length and `max_tokens` exceed",
        )
    )
