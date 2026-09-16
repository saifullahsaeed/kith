"""The icons a plugin may name, as Kith's own bytes.

A plugin names an icon and never supplies one. That is the whole rule, and it is what keeps a
surface from being the one place in the app where the iconography is somebody else's: the name
is looked up here, the path data is ours, and an unknown name falls back to `puzzle` rather than
rendering a missing-image box.

Lucide's own path data, since that is what the rest of the interface draws with — so an icon
inside a sealed frame is pixel-identical to the same icon in the chrome around it. Stroke
geometry only; the frame's stylesheet supplies `stroke: currentColor` and the widths, which is
why these carry no presentation attributes of their own.

Deliberately a short list. Every entry is bytes in every document that names it, and a plugin
that needs a picture of something specific can inline its own `data:` URI — this exists for the
dozen shapes that make a panel read as part of the app.
"""

from __future__ import annotations

#: name -> the contents of its `<symbol>`, on a 24x24 grid.
ICONS: dict[str, str] = {
    "puzzle": (
        '<path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68'
        "l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0"
        "-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1"
        " 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414"
        " 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474"
        '-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z"/>'
    ),
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "pencil": (
        '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a'
        '.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    ),
    "circle": '<circle cx="12" cy="12" r="10"/>',
    "square": '<rect width="18" height="18" x="3" y="3" rx="2"/>',
    "list-checks": (
        '<path d="M3 17 5 19 9 15"/><path d="M3 7 5 9 9 5"/><path d="M13 6h8"/>'
        '<path d="M13 12h8"/><path d="M13 18h8"/>'
    ),
    "scroll": (
        '<path d="M19 17V5a2 2 0 0 0-2-2H4"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11'
        'a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/>'
    ),
    "palette": (
        '<path d="M12 22a1 1 0 0 1 0-20 10 9 0 0 1 10 9 5 5 0 0 1-5 5h-2.25a1.75 1.75 0 0 0-1.4'
        ' 2.8l.3.4a1.75 1.75 0 0 1-1.4 2.8z"/><circle cx="13.5" cy="6.5" r=".5"/>'
        '<circle cx="17.5" cy="10.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/>'
        '<circle cx="8.5" cy="7.5" r=".5"/>'
    ),
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "inbox": (
        '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/>'
        '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0'
        ' 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
    ),
    "layout-grid": (
        '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3"'
        ' rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/>'
        '<rect width="7" height="7" x="3" y="14" rx="1"/>'
    ),
    "refresh": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>'
    ),
}

FALLBACK = "puzzle"


def named(names) -> dict[str, str]:
    """The subset a plugin asked for, with anything unknown resolved to the fallback.

    Only what was named, because every symbol is bytes in every document that carries it.
    """
    wanted = {str(name).strip() for name in names if str(name).strip()}
    out: dict[str, str] = {}
    for name in wanted:
        # **Aliased, not substituted.** A document renders one `<symbol id="icon-{name}">` per
        # entry and a surface references `#icon-{name}` by the name it wrote in its manifest, so
        # emitting the puzzle glyph under the id `icon-puzzle` answered a question nobody asked:
        # the frame still pointed at `#icon-widget`, still found nothing, and drew an empty box.
        # The documented fallback only applies if it is reachable by the name it is standing in
        # for.
        out[name] = ICONS.get(name, ICONS[FALLBACK])
    return out
