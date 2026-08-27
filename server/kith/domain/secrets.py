"""Noticing that a command is carrying a credential in it.

This exists because of one conversation. Working against a live Odoo instance over XML-RPC,
he ran 167 shell commands and **136 of them had the API key written into the command text** —
because each one was a fresh `python3 - <<'PY'` heredoc that re-declared the connection from
scratch. Every one of those went into the transcript on disk, so a key that should have been
typed once is now recorded 136 times.

The fix is not to catch the key. It is to write the script *once* and import it, which is what
the `shell` tool now says. This is the backstop for when that advice is not taken, and it
points at the same remedy rather than merely objecting.

**Shape, not name.** The first version looked for `api_key=`, `token=`, `secret=` and found 4
of the 136 — because he had written `k='9a98…'`. A one-letter variable holding a forty-character
key is still a key, so what gets recognised is the *literal*: long, opaque, mixed letters and
digits, high entropy.

**Tuned against 219 real transcripts.** An earlier version accepted anything quoted and long,
and fired on `l10n_sa_edi_…` and `payment_…` — Odoo module names, not secrets. Requiring an
unbroken run of letters and digits, with no underscore or hyphen, removes those: an identifier
almost always has a separator and a credential almost never does. That leaves 2 transcripts of
219 flagged, both genuinely carrying the same Odoo key, and nothing else.

The known prefixes are matched directly because they are unambiguous and often shorter than
the entropy rule would accept on its own.
"""

from __future__ import annotations

import math
import re
from collections import Counter

#: Formats that say what they are. Cheap, certain, and worth checking first — several are
#: shorter or less random-looking than the general rule below would accept.
KNOWN = re.compile(
    r"\b("
    r"sk-[A-Za-z0-9_-]{20,}"  # OpenAI and friends
    r"|ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack
    r"|AKIA[0-9A-Z]{16}"  # AWS access key id
    r")"
)

#: A quoted literal that is one unbroken run of letters and digits. The absence of `_` and `-`
#: is what separates a key from an identifier — see the note above about `l10n_sa_edi_…`.
#:
#: The backreference matters: without it the two quotes need not be the same character, so a run
#: opened with `'` and closed with `"` counted as a quoted literal when it is no such thing.
_LITERAL = re.compile(r"""(['"])([A-Za-z0-9]{32,80})\1""")

#: Shannon entropy below which a string is too regular to be a key. Measured against the real
#: corpus: a forty-character hex key sits near 3.6, and the identifiers that were being caught
#: sat below 3.2.
MIN_ENTROPY = 3.2


def _entropy(text: str) -> float:
    counts = Counter(text)
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def looks_like_a_credential(literal: str) -> bool:
    """Whether one bare literal is opaque enough to be a secret.

    All digits or all letters is a number or a word. Both, at length, with no separator and no
    structure, is the shape of something generated rather than written.
    """
    if not (re.search(r"\d", literal) and re.search(r"[a-zA-Z]", literal)):
        return False
    return _entropy(literal) >= MIN_ENTROPY


def carries_a_secret(command: str) -> bool:
    """Whether this command has a credential written into it."""
    if KNOWN.search(command):
        return True
    return any(looks_like_a_credential(found.group(2)) for found in _LITERAL.finditer(command))


#: What to say. Names the remedy rather than only the problem — the point is not that a key is
#: bad, it is that retyping it into every command is what puts it in the record every time.
ADVICE = (
    "[this command has a credential written into it, so it goes into the record as written. "
    "Put the connection and the key in a file under .kith/scratch/ once, and run that file "
    "afterwards — then it is typed once instead of once per command.]"
)
