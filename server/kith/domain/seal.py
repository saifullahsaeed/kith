"""What a sealed frame may do — the one statement of it.

A plugin's surface is an opaque document Kith did not write, mounted in an iframe with no
same-origin, carrying a policy that denies it everything it does not need. `default-src 'none'`
is the whole of it — connect, frame, worker, form and object all inherit that denial, so a sealed
document cannot fetch, cannot open a socket and cannot embed anything. The exceptions are what a
drawing needs and none of them leave the page: inline style, inline script, and data-URI images,
fonts and media.

`'unsafe-eval'` is deliberately absent. There is no network to load a library from, so nothing
legitimate needs it, and leaving it out means the one construct that turns arbitrary text into
running code stays unavailable.

**Why this is a module rather than a constant in the route that serves surfaces.** It began as a
constant in the route that served the ```html canvas (since retired), and by the time plugins
wanted it there were already two copies — that one and a `<meta>` the interface baked into the
document — and they had drifted: the route's carried ``form-action 'none'`` and
``base-uri 'none'`` and the interface's carried neither, with nothing anywhere failing.

Here in `domain/` because it is structure with no I/O in it, and because it must be importable
from `services/plugins/documents.py` (rank 4) as well as any route.

The interface still states the `sandbox` half, in `ui/src/lib/seal.ts`;
`tests/test_the_seal_is_stated_once.py` reads it and asserts it agrees.
"""

from __future__ import annotations

#: What the frame is allowed to do, as the `sandbox` attribute.
#:
#: `allow-scripts` and nothing else. Crucially **not** `allow-same-origin`: with both, the frame
#: shares the app's origin and can reach the backend with the session it already trusts, which is
#: the entire thing being prevented. The omissions matter too — no `allow-popups`, so it cannot
#: open a window; no `allow-top-navigation`, so it cannot replace the app; no `allow-forms`, so
#: there is nothing to submit; no `allow-modals`, so `alert` does nothing rather than freezing
#: the thread it was called from.
FRAME_SANDBOX = "allow-scripts"

#: The directives, in order. A tuple rather than a joined string so a test can compare sets
#: without parsing, and so the order — which is the byte sequence a `<meta>` carries — is stated
#: once rather than implied by two `join` calls.
DIRECTIVES = (
    "default-src 'none'",
    "style-src 'unsafe-inline'",
    "script-src 'unsafe-inline'",
    "img-src data: blob:",
    "font-src data:",
    "media-src data: blob:",
    "form-action 'none'",
    "base-uri 'none'",
)

POLICY = "; ".join(DIRECTIVES)

#: A ceiling on one sealed document.
#:
#: Set for the retired canvas, whose pages ran 4-6 KB, and small enough that a runaway reply cannot park a large object in memory.
#:
#: **Inherited by plugin surfaces and not yet measured for them.** A plugin document is built by
#: inlining its own assets, so it is a different distribution entirely: a surface with a font and
#: a few images could approach this legitimately where a drawing never would. Marked here rather
#: than quietly re-used, and it is one of three provisional numbers the plugin work owes a
#: measurement.
MAX_BYTES = 2_000_000
