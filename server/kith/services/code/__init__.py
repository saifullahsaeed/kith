"""Reading code as structure rather than as text.

Two layers, deliberately separate. `outline` and `repomap` parse — no server, no project, no
setup, and they work on a single file in an empty folder. The `lsp` package next door
resolves *meaning* — who calls this, where is it defined, what breaks if I rename it — and
that genuinely does need a language server, so it is allowed to be absent.

Keeping them apart is what lets the cheap half always be available. A design where structure
came from the language server too would mean no outline at all on a machine with nothing
installed, which is the machine most people are on.
"""

from __future__ import annotations
