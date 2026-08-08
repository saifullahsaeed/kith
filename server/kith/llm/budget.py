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

#: The band a calibrated ratio is held to. Outside it, something is wrong with the arithmetic
#: rather than with the model — a ratio of 20 would silently disable the guard, and one of
#: 0.5 would make it fire on every round of every turn.
MIN_RATIO, MAX_RATIO = 2.0, 6.0

#: What one image part costs, regardless of how many bytes its data URI is. A page render at
#: high detail is roughly this; being out by a factor of two here is immaterial next to being
#: out by a factor of a thousand, which is what measuring the base64 would do.
IMAGE_TOKENS = 800


def message_chars(message: dict[str, Any]) -> int:
    """The size of one message, with image parts costed rather than measured.

    Returns characters, so it composes with the ratio. An image contributes its flat token
    cost converted back into characters, which keeps one unit throughout.
    """
    content = message.get("content")
    if not isinstance(content, list):
        return len(json.dumps(message))
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
    would each drag the other's calibration around with no lock and no way to notice.
    """

    #: The model's context window, or 0 when nobody knows — Ollama, a custom endpoint, a
    #: model chosen before the window was recorded. Zero disables every check below rather
    #: than guessing a number: acting on a made-up window would trim a conversation that had
    #: plenty of room.
    window: int = 0
    #: Room to leave for the answer. `num_predict` is -1 (no limit) on a default install, so
    #: the caller resolves this to something real before handing it over.
    reserve: int = 4_096
    chars_per_token: float = SEED_CHARS_PER_TOKEN
    #: The last figure the provider reported, and the size of what produced it.
    last_prompt_tokens: int = 0
    last_chars: int = 0
    #: How much one more round has grown the prompt, biggest seen so far. Measured rather
    #: than assumed: a round that reads a file grows by kilobytes, one that ticks a checklist
    #: by a hundred bytes, and picking a constant would be wrong in both directions.
    biggest_growth: int = 0
    _measurements: int = field(default=0, repr=False)

    @property
    def known(self) -> bool:
        return self.window > 0

    def observe(self, prompt_tokens: int, chars: int) -> None:
        """Record what the provider charged for a request of this size."""
        if prompt_tokens <= 0 or chars <= 0:
            return
        ratio = chars / prompt_tokens
        self.chars_per_token = min(MAX_RATIO, max(MIN_RATIO, ratio))
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
