"""Whether he reasons before answering, and how hard — resolved into OpenRouter's
`reasoning` extension.

There was no test here at all before this, and the gap was live: only three of
OpenRouter's seven real effort levels ("low", "medium", "high") were recognised. Asking
for "xhigh" or "minimal" — both real, both in OpenRouter's own SDK types and docs —
silently fell through to the plain enabled/disabled switch instead of being sent, so the
choice was quietly overridden rather than honoured or rejected. Confirmed against
OpenRouter's SDK (generated from their OpenAPI spec, so it tracks the live API) rather
than guessed: `ReasoningEffort` and `ChatRequestReasoningEffort` both list the same seven
values this module now forwards.
"""

from __future__ import annotations

from kith.domain.chat import Config
from kith.llm.openai_compat import REASONING_EFFORTS, _reasoning_options


def _config(effort: str = "", think: bool = True) -> Config:
    return Config(model="x", num_ctx=0, num_predict=0, system="", think=think, effort=effort)


class TestEveryRealEffortLevelIsForwarded:
    def test_each_of_openrouters_seven_levels_is_sent_as_is(self):
        for level in REASONING_EFFORTS:
            assert _reasoning_options(_config(effort=level)) == {"reasoning": {"effort": level}}

    def test_it_is_case_and_whitespace_insensitive(self):
        """A setting round-tripped through storage or a stray edit shouldn't have to be exact."""
        assert _reasoning_options(_config(effort=" HIGH "))["reasoning"] == {"effort": "high"}


class TestBlankEffortLeavesItToTheThinkToggle:
    """Blank means "you decide" — the sensible amount of thinking is the provider's call,
    and the standing `think` setting is the only input left once effort says nothing."""

    def test_blank_with_think_on_enables_reasoning(self):
        assert _reasoning_options(_config(effort="", think=True)) == {"reasoning": {"enabled": True}}

    def test_blank_with_think_off_disables_reasoning(self):
        assert _reasoning_options(_config(effort="", think=False)) == {"reasoning": {"enabled": False}}

    def test_an_unrecognised_value_falls_back_to_the_toggle_rather_than_vanishing(self):
        """The regression this file exists to catch. OpenRouter's scale has grown before
        (xhigh, minimal and max all arrived after low/medium/high), and a future value this
        code does not yet know about must not be sent as literal text OpenRouter has never
        seen — but "unrecognised" still has to mean *something* rather than the setting
        just being dropped with no substitute at all. Falling back to the toggle is that
        substitute, not a silent no-op.
        """
        assert _reasoning_options(_config(effort="ludicrous", think=True)) == {"reasoning": {"enabled": True}}
