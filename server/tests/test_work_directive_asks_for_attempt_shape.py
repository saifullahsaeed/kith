"""The step a working tick records must be shaped attempt -> outcome -> next step.

The baton the next tick reads (its journal line) recorded "what's still missing" — pure state —
so the next tick re-derived the same gap and repeated. The WORK directive must ask instead for
what was tried, what happened (especially failures), and the one next step.
"""

from kith.autonomy import directives


def test_work_directive_asks_to_record_attempt_outcome_next():
    d = directives.WORK.lower()
    assert "tried" in d
    assert "happened" in d or "outcome" in d or "result" in d
    assert "next" in d
