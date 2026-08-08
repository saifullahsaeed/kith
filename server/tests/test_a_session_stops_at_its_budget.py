"""A working session that spends past its ceiling stops itself, once, with a durable note.

This is the seatbelt: the process-wide meter in agent_loop counts everything but is keyed to
nothing, so a single "keep working" session could run through tens of millions of tokens with
no cumulative ceiling. Here the runner keeps a per-session tally and rests the session — using
the same set_working(False) baton that rest()/idle already use — the moment it crosses the cap.

Every charge below passes `cost_usd=0.0`, which is what a provider that reports no price
looks like, and which is the only case the *token* ceiling still governs. The ceiling that
matters is money — see `test_the_budget_is_money.py` — because charging the whole prompt
including cached re-reads ran the meter four times too fast and stopped a session that had
spent twenty-six cents.

The token cap's floor is 100k (a single heavy tick can be ~700k), so these tests work in
hundreds of thousands, not the toy numbers the clamp would swallow.
"""
