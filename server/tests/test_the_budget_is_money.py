"""A session's budget is what it cost, not how many tokens went past.

A real working session was stopped with:

    I stopped this session — it ran through 6,544,155 tokens, past its budget of 5,000,000.

It had spent about twenty-six cents. The meter charged `tokens_in`, the whole prompt with
cached re-reads included, on the reasoning that it was the number a person reacts to. It is
— and that is why it was the wrong one to enforce: at a 78% cache hit it runs more than four
times faster than the work being done, against a ceiling written in the same units.

The provider reports cost on every call and it was already being summed for the dashboard.
It simply was not the thing being enforced.
"""

from __future__ import annotations
