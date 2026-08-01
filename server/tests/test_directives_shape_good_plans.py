"""The instructions that shape a plan ask for done-conditions, invite review, and ban verb-tasks.

The warehouse project started wrong: vague verify-tasks with no checkable finish line, laid out
and run without the person ever seeing the plan. The chat directive now requires a done-condition
per task, frames a task as an outcome (not "verify X"), and says the plan back for review before
it runs on its own.
"""

from kith.api.routes import chat
from kith.autonomy import directives


def test_chat_directive_requires_done_conditions_and_invites_review():
    d = chat.CHAT_DIRECTIVE.lower()
    assert "done" in d
    assert "review" in d


def test_directives_discourage_verb_only_tasks():
    combined = (directives.WORK + " " + chat.CHAT_DIRECTIVE).lower()
    assert "verify" in combined
    assert "not a task" in combined or "done-condition" in combined
