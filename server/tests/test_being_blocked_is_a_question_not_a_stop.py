"""A turn that needs something has to say so, out loud, to the person who can give it.

Measured on turns 1264 and 1265, 2026-08-12. Both had the full 69-tool set — the ledger reads
`built_in_tools: 11,828`, `retried=0`, `folded=False`, no landing and no narrowing of any kind.
Nothing was taken away. Both used **zero tools** and wrote a status paragraph instead:

    "The only unchecked item is a fresh production-chat proof showing the new discovery -> read
     flow after the latest revision. ... I won't mark the final checkpoint until that production
     chat evidence exists in the logs."

He turned "I have not verified this" into "I am waiting for this", and waiting is not an action,
so the turn ended. His person answered "bitch find it then why are you yapping with me do you
need anything from me" — and the next turn found it in three tool calls and forty-one seconds,
concluding "I found and verified it. You needed nothing from me."

The log line he had called himself blocked on was timestamped 21:23:43. Turn 1264 ran at
21:54:13. The evidence had existed for thirty-one minutes. The two turns cost 409,337 prompt
tokens between them and produced nothing.

Two things in the prompt made that the sanctioned move, and this file guards both:

* `ask` is the only tool that can hold a turn open for an answer, and its description scoped it
  to design forks — "not for reassurance" — with nothing covering "I need something from you".
  So a genuinely blocked turn had no way to say so and fell back to prose.
* `CHAT_DIRECTIVE` said the same thing in its own words: "ASK WHEN IT WOULD CHANGE WHAT YOU
  BUILD". Being stuck does not change what you build, so it did not qualify.

Deliberately NOT fixed by forcing a turn to keep going. That was tried — `expect_durable` — and
it made him manufacture a file nobody wanted to satisfy the rule; see `routes/chat._turn`. The
rule here is narrower and true: a condition you can check is not a blocker.
"""

from __future__ import annotations

from kith.services.turn.prompt import CHAT_DIRECTIVE
from kith.tools import registry


def _ask_description() -> str:
    entry = registry.require("ask")
    assert entry is not None, "the ask tool has gone missing"
    return entry.description


class TestAskCoversBeingBlocked:
    def test_it_is_not_scoped_to_design_forks_alone(self):
        """A fork is two roads. Being blocked is no road, and it is the commoner case."""
        described = _ask_description().lower()
        assert "block" in described, (
            "`ask` describes only the fork case, so a turn that needs something from its person "
            "has no sanctioned way to say so and ends instead"
        )

    def test_it_says_what_the_alternative_actually_is(self):
        """ "Don't ask unnecessarily" reads as harmless. It is not — the fallback is stopping."""
        described = _ask_description().lower()
        assert "turn" in described and ("ending" in described or "end your turn" in described), (
            "the description has to name the real cost of not asking, which is the turn ending"
        )

    def test_it_still_refuses_to_be_a_reassurance_button(self):
        """The original scoping existed for a reason and must survive the widening."""
        described = _ask_description().lower()
        assert "reassurance" in described


class TestAStatedConditionIsTheNextAction:
    def test_the_directive_says_to_go_and_check(self):
        assert "CONDITION YOU STATE IS A CONDITION YOU CHECK" in CHAT_DIRECTIVE

    def test_the_directive_sanctions_asking_when_stuck(self):
        assert "BLOCKED" in CHAT_DIRECTIVE, (
            "point 3 covered only 'would change what you build', which being stuck is not"
        )

    def test_it_points_at_the_tool_rather_than_at_a_feeling(self):
        """ "Ask when blocked" with no named tool is advice. `ask` is a thing he can call."""
        assert "`ask`" in CHAT_DIRECTIVE
