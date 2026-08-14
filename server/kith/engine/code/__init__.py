"""Reading code as structure rather than as text.

Two tiers, deliberately separate, and the separation is the design rather than a detail of it.

**Structure** — `outline` and `repomap` — parses. No server, no project, no setup; it works on
a single file in an empty folder. It ships with the application: tree-sitter is one shared
object of about 4.2 MB covering 32 extensions across 19 language families, which is 2.8% of the
released dmg. Because it is always present there is no availability branch anywhere, no
fallback path, and no tool that sometimes exists.

**Meaning** — the `lsp` package — resolves who calls this, where it is defined, what breaks if
it is renamed. That genuinely needs a language server: a real process of a hundred-odd
megabytes, and not ours. None is shipped. They are looked for on the machine, project first,
so the answer comes from the version the project itself pins rather than whatever is global.

Keeping the tiers apart is what lets the cheap half always be available. A design where
structure came from the language server too would mean no outline at all on a machine with
nothing installed, which is the machine most people are on.

The rules
---------

New analyses land here, and these are the four things that keep that from becoming a pile.
They are prose rather than a `Protocol` on purpose: the last round of layering work removed 65
upward imports and needed no framework to do it, and a contract nobody can read is a contract
nobody keeps.

1. **An analysis speaks `Symbol`.** `outline` produces them, `repomap` is built on them, and
   the LSP manager asks `outline` which family a file belongs to. That shared vocabulary is
   already how this package works; an analysis needing a different one is not an analysis of
   this kind and belongs somewhere else.

2. **An analysis states its tier.** Structure always answers. Meaning may answer `Unavailable`
   — and that is a *returned value* naming the server and how to install it, never an empty
   result and never an exception path. An analysis that returns "no references found" when the
   real answer is "nothing could look" is worse than one that refuses.

3. **No language server is ever shipped.** Adding a language means adding a `Candidate` with
   its install command to the table in `lsp/manager.py`, in a reviewed commit. Vendoring a
   server, or adding one to `requirements.txt`, grows the download by roughly the size of the
   whole application per language.

4. **An analysis is not automatically a tool.** A tool schema is sent on every round of every
   turn, whether or not the model is looking at code. This package is a library; `tools/` is a
   curated adapter over it, and analyses are expected to outnumber tools.
"""

from __future__ import annotations
