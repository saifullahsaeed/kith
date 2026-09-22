"""The icon set a design is drawn with, looked up by what the icon is for.

Before this there was no set. Across forty-three screens the record was zero images, a handful
of hand-drawn SVGs and seven hundred Unicode stand-ins: `⌂` for home, `▦` for a schedule, `☰`
for more, `♧` for a bell. Those render in whatever font the machine happens to have, at
whatever weight that font draws them, so no two sit on the same baseline or look like the same
family — which is most of why a screen read as a mockup rather than a product. Hand-writing path
data is not the answer either: it is the one part of a screen a language model reliably gets
wrong, because a path is coordinates and nothing about it can be checked by reading it.

So the icons are Lucide's, vendored beside the skill that draws with them (ISC, 24px grid, one
stroke weight across the whole set — the same family Kith's own interface is built from), and
this module hands them out as finished inline SVG. **Inline, not a new tag**, on purpose: the
checker renders the raw file and the board renders a whitelisted tree, and every design-surface
bug so far has lived in a gap between those two. A `<svg>` of `<path>`s is something both already
draw identically; a `data-k-icon="bell"` would have been a third renderer.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from kith import settings

#: Where the set lives inside the skill, installed or bundled.
_RELATIVE = Path("designing-a-screen") / "icons" / "lucide.json"

#: Sizes a design reaches for. Anything else is almost always an icon doing a heading's job.
DEFAULT_SIZE = 20
DEFAULT_STROKE = 1.75


def _file() -> Path | None:
    """The installed copy first, so a set he updated is the one he gets; the bundled one after,
    because seeding a new file into an existing install happens at the next start, not now."""
    from kith.services import skills as skills_service

    places: list[Path] = []
    try:
        places.append(skills_service.root() / _RELATIVE)
    except Exception:
        pass
    places.append(settings.BUNDLED_SKILLS_DIR / _RELATIVE)
    for place in places:
        if place.is_file():
            return place
    return None


@lru_cache(maxsize=1)
def _load(path: str) -> dict[str, dict]:
    return json.loads(Path(path).read_text(encoding="utf-8")).get("icons", {})


def icons() -> dict[str, dict]:
    found = _file()
    return _load(str(found)) if found else {}


#: What an interface calls a thing, where the set calls it something else.
#:
#: Not a thesaurus — the words that land in nearly every nav bar, toolbar and row action, and
#: that the tags alone rank wrongly. "home" is a tag on a dozen icons and `dock` sorts first; the
#: word a person means is `house`. Without this the first answer to the commonest request is the
#: wrong icon, and the first answer is the one that gets pasted.
ALIASES: dict[str, str] = {
    "home": "house",
    "more": "ellipsis",
    "more vertical": "ellipsis-vertical",
    "overflow": "ellipsis",
    "kebab": "ellipsis-vertical",
    "hamburger": "menu",
    "close": "x",
    "dismiss": "x",
    "cancel": "x",
    "add": "plus",
    "new": "plus",
    "create": "plus",
    "remove": "minus",
    "delete": "trash-2",
    "edit": "pencil",
    "rename": "pencil",
    "logout": "log-out",
    "sign out": "log-out",
    "login": "log-in",
    "sign in": "log-in",
    "back": "chevron-left",
    "forward": "chevron-right",
    "next": "chevron-right",
    "previous": "chevron-left",
    "expand": "chevron-down",
    "collapse": "chevron-up",
    "dropdown": "chevron-down",
    "notification": "bell",
    "alert": "triangle-alert",
    "warning": "triangle-alert",
    "error": "circle-alert",
    "info": "info",
    "help": "circle-question-mark",
    "success": "circle-check",
    "done": "check",
    "profile": "user-round",
    "account": "circle-user-round",
    "person": "user-round",
    "people": "users-round",
    "team": "users-round",
    "patient": "user-round",
    "contact": "contact-round",
    "schedule": "calendar-days",
    "appointment": "calendar-clock",
    "event": "calendar",
    "time": "clock",
    "message": "message-square",
    "chat": "message-circle",
    "comment": "message-square-text",
    "mail": "mail",
    "email": "mail",
    "send": "send",
    "attach": "paperclip",
    "attachment": "paperclip",
    "upload": "upload",
    "export": "download",
    "share": "share-2",
    "link": "link",
    "external": "external-link",
    "copy": "copy",
    "sort": "arrow-up-down",
    "filter": "list-filter",
    "refresh": "refresh-cw",
    "sync": "refresh-cw",
    "reload": "rotate-cw",
    "undo": "undo-2",
    "redo": "redo-2",
    "dashboard": "layout-dashboard",
    "analytics": "chart-no-axes-column",
    "chart": "chart-column",
    "report": "file-chart-column",
    "reports": "file-chart-column",
    "document": "file-text",
    "file": "file",
    "folder": "folder",
    "favorite": "star",
    "favourite": "star",
    "like": "heart",
    "view": "eye",
    "show": "eye",
    "hide": "eye-off",
    "visible": "eye",
    "lock": "lock",
    "security": "shield",
    "payment": "credit-card",
    "billing": "credit-card",
    "money": "banknote",
    "cart": "shopping-cart",
    "location": "map-pin",
    "address": "map-pin",
    "phone": "phone",
    "call": "phone",
    "camera": "camera",
    "photo": "image",
    "picture": "image",
    "gallery": "images",
    "play": "play",
    "pause": "pause",
    "stop": "square",
    "mute": "volume-x",
    "sound": "volume-2",
    "microphone": "mic",
    "language": "languages",
    "theme": "sun-moon",
    "dark mode": "moon",
    "light mode": "sun",
    "grid": "layout-grid",
    "list": "list",
    "drag": "grip-vertical",
    "handle": "grip-vertical",
    "print": "printer",
    "save": "save",
    "bookmark": "bookmark",
    "tag": "tag",
    "label": "tag",
    "history": "rotate-ccw-clock",
    "archive": "archive",
    "inbox": "inbox",
    "medical": "stethoscope",
    "doctor": "stethoscope",
    "health": "heart-pulse",
    "heart rate": "heart-pulse",
    "medication": "pill",
    "hospital": "hospital",
}


def _words(text: str) -> list[str]:
    return [one for one in re.split(r"[^a-z0-9]+", text.lower()) if one]


def _singular(word: str) -> str:
    """`patients` → `patient`, `notifications` → `notification`. Crude and enough: the set
    names things in the singular, and a nav label is usually a plural."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def search(query: str, limit: int = 6) -> list[str]:
    """Icon names for what `query` describes, best first.

    Names beat tags, and a whole-name match beats everything, so `"calendar"` is `calendar` and
    not the eleven icons tagged with it. A multi-word query is also tried as a name —
    `"user plus"` is `user-plus` — because that is how the set spells compound icons.
    """
    everything = icons()
    words = [_singular(one) for one in _words(query)]
    if not words:
        return []
    joined = "-".join(words)
    squashed = "".join(words)
    alias = ALIASES.get(" ".join(words)) or ALIASES.get(" ".join(_words(query)))
    scored: list[tuple[int, int, str]] = []
    for name, entry in everything.items():
        parts = name.split("-")
        tags = [tag.lower() for tag in entry.get("tags", [])]
        score = 0
        if name == alias:
            score = 2000
        elif name == joined or name.replace("-", "") == squashed:
            score = 1000
        elif name.startswith(joined + "-"):
            score = 500
        else:
            for word in words:
                if word in parts:
                    score += 60
                elif any(part.startswith(word) for part in parts):
                    score += 30
                if word in tags:
                    score += 20
                elif any(word in tag for tag in tags):
                    score += 5
            # Every word has to land somewhere, or "heart rate" finds every heart.
            if not all(word in name or any(word in tag for tag in tags) for word in words):
                score = 0
        if score:
            # Shorter names first among equals: `bell` before `bell-electric`.
            scored.append((-score, len(name), name))
    scored.sort()
    return [name for _, _, name in scored[:limit]]


def markup(name: str, size: int = DEFAULT_SIZE, stroke: float = DEFAULT_STROKE) -> str | None:
    """The icon as inline SVG, coloured by the text around it."""
    entry = icons().get(name)
    if entry is None:
        return None
    width = f"{stroke:g}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{entry["body"]}</svg>'
    )
