"""The live tool-output window ships lean.

240k chars (~65k tokens) re-sent every round was the direct driver of ~700k-token ticks. With
the baton improved, a smaller window is safe — and a session-token cap now backstops it either
way. The knob stays, so a genuinely large-context user can raise it.
"""

from kith.services import tuning


def test_live_tool_chars_default_is_lean():
    tuning.reset(["live_tool_chars"])
    assert tuning.value("live_tool_chars") == 80_000
