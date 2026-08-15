"""Checking that an edit did what it meant to, by looking at what is left.

Two incidents in this repository, both shipped, neither visible to a passing suite:

* A rewrite truncated a module at `data_dir()` and took everything after it with it, including
  `tuning.routing()`. Nothing failed until something asked for a routing decision.
* `sandbox.docker_available()` was deleted along with the container it asked about, and one
  call site was missed. For weeks the model was handed
  ``searx: AttributeError: module … has no attribute 'docker_available'`` instead of a
  readable message.

Both are the same shape: **a definition stopped existing and nobody noticed at the moment it
happened.** A test suite is bad at this, because the code that would have caught it is
sometimes the code that was deleted. A parser is good at it, because the question is purely
structural — which names were defined before this write, and which are defined after.

**Only the losses are reported.** An edit that adds definitions is doing its job and saying so
costs a line on every write for no information. An edit that *removes* one is either intended,
in which case a single line confirming it is cheap, or it is the accident above, in which case
it is the most valuable sentence in the turn. Reporting in one direction only is what keeps
this affordable enough to run on every write rather than being a tool he has to remember.

This says what changed; it does not judge. Deciding whether losing `_old_helper` matters is
the caller's business, and `tools/computer.py` simply reports it — a rule here about which
deletions are acceptable would be this module guessing at intent.
"""

from __future__ import annotations

from pathlib import Path

from kith.engine.code import excerpt, outline


def definitions(source: bytes, language: str) -> list[str]:
    """Every definition in `source`, fully qualified, in the order they appear.

    Qualified rather than bare so that removing `Thing.method` while a `method` survives on
    another class is a loss rather than a wash — which is the sort of near-miss this exists to
    catch. `excerpt.qualify` already rebuilds the dotted path from the outline's depth column,
    so this is that function put to a second use rather than a second implementation of it.
    """
    return [name for name, _ in excerpt.qualify(outline.of_source(source, language))]


def lost(before: bytes, after: bytes, language: str) -> list[str]:
    """Definitions that existed before the write and do not exist after it.

    Counted, so that a file which had two `run` methods and now has one reports a loss. A set
    difference would call that unchanged, and "the duplicate I meant to keep" is exactly the
    kind of thing a rewrite drops.
    """
    was = definitions(before, language)
    now = definitions(after, language)
    remaining = list(now)
    gone: list[str] = []
    for name in was:
        if name in remaining:
            remaining.remove(name)
        else:
            gone.append(name)
    return gone


def readable(path: str | Path) -> tuple[bytes, str] | None:
    """The bytes and grammar of a file worth watching, or None to skip it.

    None for anything that is not source, is too big, is binary, or does not exist yet — all
    of which are ordinary rather than a problem, and none of which should make a write fail.
    A new file has nothing to lose, and a Markdown file has no definitions to lose.
    """
    try:
        return outline.source_of(path)
    except outline.OutlineError:
        return None
    except OSError:
        return None
