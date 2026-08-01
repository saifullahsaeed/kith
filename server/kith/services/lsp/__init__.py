"""Language servers: what the code *means*, as opposed to what shape it is.

`services/code` parses and always works. This resolves — who calls this, where does it come
from, what breaks if I rename it — and needs a real language server, which is frequently not
installed. That asymmetry is the whole design: the cheap half is unconditional, this half is
offered only when something can actually answer.
"""

from __future__ import annotations

from kith.services.lsp.manager import Unavailable, manager

__all__ = ["Unavailable", "manager"]
