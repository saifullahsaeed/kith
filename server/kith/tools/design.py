"""The design system, as something Kith can read and change rather than only obey.

Before this, a system was a file he could resolve by guessing at paths and edit by writing raw
JSON with `write_file`. Which meant in practice that he did neither: the record is a run of
screens drawn against whatever palette the renderer happened to hand him, with no idea whether
the product had an inventory of parts or a house style, because nothing ever put either in front
of him.

**Why a tool and not a line in the skill.** The same reason `delegate_subtask` went unused until
the shape changed: a capability he has to remember to look for is a capability he does not use.
`read_design_system` answers with the inventory *listed*, so composing from it is the path of
least resistance and inventing a button from raw divs is the effortful one. That ordering is the
whole mechanism — see the persona work on plural tools for the receipts.
"""

from __future__ import annotations

from pathlib import Path

from kith.services import design_system as systems
from kith.tools.params import BOOL, INT, LIST_STR, STR
from kith.tools.registry import tool


def _project(path: Path, args: dict) -> int | None:
    """Which project's system.

    In order: the argument, then the folder the work is actually happening in, then the
    conversation's binding.

    The middle one is there because of what the first use outside a bound conversation did.
    Drawing a screen for one product while the conversation was bound to another would have
    written that product's palette into the other project's system — the folder said one thing,
    the binding said another, and only the binding was being asked. A design belongs to the
    folder it is drawn in; the binding is a guess about which folder that is.
    """
    given = args.get("project")
    if given not in (None, ""):
        try:
            return int(given)
        except (TypeError, ValueError):
            return None

    from kith.infra import workspace as sandbox
    from kith.services import project_binding

    try:
        by_folder = systems.project_of_folder(sandbox.base_dir())
    except Exception:
        by_folder = None
    return by_folder or project_binding.bound_project(path)


@tool(
    "read_design_system",
    "The design system this project's screens are drawn in: its colours, type scale, spacing "
    "scale, the inventory of components already built for it, and the conventions written down "
    "for how screens here are put together. Read this before drawing a screen — composing from "
    "the inventory is what makes the fourth screen look like the first one.",
    {
        "project": {
            **INT,
            "description": "Which project's system. Leave it out for the one this conversation "
            "is working in.",
        }
    },
)
def read_design_system(path: Path, args: dict):
    system = systems.resolve(_project(path, args))

    if not system.found:
        return {
            "system": "none",
            "note": (
                "There is no design system for this project yet, so screens fall back to Kith's "
                "own nine colours and a 4/8/12/16/24/32/48/64 spacing scale. If you are about to "
                "draw more than one screen for this product, write one first with "
                "`update_design_system` — the palette, the type roles, and the conventions. That "
                "is what stops screen four from being a different product from screen one."
            ),
            "space": list(systems.DEFAULT_SPACE),
        }

    out = system.as_dict()
    # Which layer answered, in words. The path alone leaves the one question a person authoring
    # a system actually has — is this mine, or the thing I fell through to — unanswered.
    out["where"] = {
        "project": "this project's own system.",
        "workspace": "NOT this project's own — inherited from his workspace. If this product "
        "should look like itself rather than like everything else he owns, write it one.",
        "skill": "NOT this project's own — a brand installed as a skill, shared with whatever "
        "it was authored for.",
    }.get(system.scope, "")
    # Listed rather than counted. A count is something he reads past; the names and what each is
    # for are something he composes from.
    #
    # A part that is a file is listed by what a screen needs to *use* it — its props, whether it
    # takes content, the tag to write — and not by its markup. He composes with the tag; he reads
    # the file only when he is changing the part, and leaving the markup out keeps this answer
    # the same size however big the inventory grows.
    out["components"] = [_listed(part) for part in system.components.values()]
    if not system.components:
        out["components_note"] = (
            "This system has no components yet. When a part shows up on a second screen — a "
            "button, a card, a field, a table row — add it here with `update_design_system` "
            'rather than drawing it again. Screens then write `<k-use name="…">` instead of '
            "a copy, so changing the part changes every screen."
        )
    elif any(not part.path for part in system.components.values()):
        out["components_note"] = (
            "Some parts are still inline snippets (they have `html` and no `path`). Those can only "
            "be copied, and copies drift. Re-save each one with `update_design_system` and it "
            "becomes a file that screens reference with `<k-use>`."
        )
    if not system.conventions:
        out["conventions_note"] = (
            "Nothing is written down about how screens are built for this product. If he "
            "corrects the same thing twice — density, shadows, where the primary action goes — "
            "that is a convention, and it belongs in here so the next screen starts from it."
        )
    if system.shared:
        out["shared_note"] = (
            f"This system lives in an installed skill ({system.path}) and is shared with whatever "
            "it was authored for. Editing it here would change that too, so an update writes the "
            "project its own copy, seeded from this one."
        )
    return out


def _listed(part: systems.Component) -> dict:
    """One part, as somebody composing a screen needs it."""
    if not part.path:
        return part.as_dict()
    props = " ".join(f'{name}="…"' for name in part.props)
    tag = f'<k-use name="{part.name}"{" " + props if props else ""}>'
    return {
        "name": part.name,
        "use": part.use,
        "props": part.props,
        "slot": part.slot,
        "write": tag + ("…content…</k-use>" if part.slot else "</k-use>"),
        "path": str(part.path),
    }


@tool(
    "update_design_system",
    "Create or change this project's design system — add a colour, a type role, a component to "
    "the inventory, or write down a convention. Merges by default, so adding one thing does not "
    "drop the rest. Use it when a value or a part turns out to be the product's rather than one "
    "screen's: a colour reached for three times, a card that appears on a second screen, a "
    "correction he has now made twice.",
    {
        "name": {**STR, "description": "What the system is called. The product's name, usually."},
        "colors": {
            "type": "object",
            "description": 'Token name to colour, e.g. {"ink": "#1a1a1a", "accent": "#2f6f4f"}. '
            "Becomes var(--ds-ink) and var(--ds-accent) in a screen.",
        },
        "text": {
            "type": "object",
            "description": 'Type roles, e.g. {"hero": {"size": 44, "weight": 700}, '
            '"body": {"size": 16}}. Becomes var(--ds-text-hero-size).',
        },
        "radius": {"type": "object", "description": 'Named radii, e.g. {"card": 12}.'},
        "fonts": {
            "type": "object",
            "description": 'Font roles, e.g. {"display": "Fraunces, Georgia, serif"}.',
        },
        "space": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "The spacing scale the checker measures against. Ascending.",
        },
        "components": {
            "type": "object",
            "description": 'The inventory. Each entry is {"html": "<the markup>", "use": '
            '"one line on when to reach for it"}, keyed by a name like "button.primary" or '
            '"card.metric". Each is saved as its own file, components/<name>.html, and screens '
            'use it with <k-use name="card.metric" label="Revenue" value="$12k"></k-use>. In the '
            "markup, {{label}} is a hole the use fills from its attribute of that name, and "
            "<k-slot></k-slot> is where content written inside the <k-use> goes. One root "
            "element; a <style> block is allowed and travels with the part. Dialect HTML — flex, "
            "tokens, data-k names. Pass null as a value to remove one.",
        },
        "conventions": {
            **STR,
            "description": "Markdown. How screens are built for this product — density, what the "
            "brand does and does not do, decisions already made. Short: it is read every time a "
            "screen is drawn. Replaces the file wholesale, so send the whole thing.",
        },
        "project": {**INT, "description": "Which project. Leave it out for the bound one."},
        "replace": {
            **BOOL,
            "description": "Replace each section given instead of merging into it. Rarely what "
            "you want — use it to delete in bulk.",
        },
    },
)
def update_design_system(path: Path, args: dict):
    try:
        system = systems.save(
            _project(path, args),
            name=args.get("name"),
            colors=args.get("colors"),
            text=args.get("text"),
            radius=args.get("radius"),
            fonts=args.get("fonts"),
            space=args.get("space"),
            components=args.get("components"),
            conventions=args.get("conventions"),
            replace=bool(args.get("replace")),
        )
    except systems.SystemError as exc:
        return {"error": str(exc)}

    return {
        "wrote": str(system.path),
        "name": system.name,
        "colors": sorted(system.colors),
        "components": [
            _listed(system.components[name])
            for name in sorted(args.get("components") or {})
            if name in system.components
        ]
        or sorted(system.components),
        "conventions": bool(system.conventions),
        "note": "Screens in this project resolve this now. Re-run the design checker on anything "
        "already drawn — a screen written against the old palette may now be off the scale.",
    }


@tool(
    "find_icons",
    "Icons for a screen, as finished inline SVG to paste where the icon goes. Ask for several "
    'at once by what each is for — ["search", "calendar", "patients", "inbox", "more"] '
    "— and each comes back as the best match plus a few alternatives by name. One consistent set "
    "(Lucide: 24px grid, one stroke weight), coloured by the surrounding text, so an icon in a "
    "button or a nav item takes the button's colour. Use these instead of Unicode symbols "
    "(⌂ ▦ ☰ ✚) or hand-written path data, which render in whatever font the machine has and "
    "never match each other.",
    {
        "names": {
            **LIST_STR,
            "description": "What each icon is for, in plain words or as an exact icon name: "
            '"search", "user plus", "chevron-down", "bell".',
        },
        "size": {
            **INT,
            "description": "Pixels. 16 in dense rows and inline with small text, 20 in buttons "
            "and nav (the default), 24 for a tab bar. Keep one size per context.",
        },
    },
)
def find_icons(path: Path, args: dict):
    from kith.domain.tooling import many
    from kith.services import icons

    wanted = [one.strip() for one in many(args, "names", "name") if one and one.strip()]
    if not wanted:
        return {"error": 'Say what each icon is for — names: ["home", "calendar"].'}
    if not icons.icons():
        return {"error": "The icon set is not installed — designing-a-screen/icons/lucide.json is missing."}

    try:
        size = int(args.get("size") or icons.DEFAULT_SIZE)
    except (TypeError, ValueError):
        size = icons.DEFAULT_SIZE
    size = max(8, min(size, 64))

    found = []
    for query in wanted[:40]:
        names = icons.search(query)
        if not names:
            found.append({"for": query, "icon": None, "note": "Nothing in the set — try a plainer word."})
            continue
        found.append(
            {
                "for": query,
                "icon": names[0],
                "svg": icons.markup(names[0], size),
                "alternatives": names[1:],
            }
        )
    return {
        "icons": found,
        "note": "Paste each svg where the icon goes; it takes `color` from its parent. For an "
        "alternative, ask again with its exact name.",
    }
