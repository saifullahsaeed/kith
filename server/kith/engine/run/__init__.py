"""Running things, and still being able to see them afterwards.

Background processes and the test runner. Both are about *execution* rather than structure,
which is why they sit here rather than in `code/` next door — they share that folder's history
and almost none of its concerns.

**These report; they do not decide.** `finished_since_last_look()` returns what finished,
grouped by conversation, and says nothing about whether that is worth a turn. It used to take
a `resume` and call `scheduler._continue` itself, which made the scheduler call down here and
this call back up into a private function of the scheduler — a round trip held together by two
deferred imports, and the last thing tying these packages to `services/`.

That shape also could not be tested honestly, which is the more useful lesson. The covering
test patched `_continue`, so when `_continue` grew a third argument the suite went on passing
and the real call would have raised `TypeError` on the first background task to finish. A
returned value cannot drift from its caller that way.
"""

from __future__ import annotations
