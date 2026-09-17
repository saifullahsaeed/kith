#!/usr/bin/env python3
"""What is wrong with this screen that looking at it will not tell you.

You can see a screenshot, and you should take one. But vision answers "does this look right",
and the half it never answers is "is this right" — a heading 3px off the grid, a grey that fails
contrast by a tenth, a card two pixels wider than the column it sits in. Those read as fine and
are not, and they are exactly the defects that survive a self-review and reach the person.

So this renders the file the way the editor renders it and reports the measurable half:

    editable   nodes the design editor cannot give handles to, and why
    tokens     colours written as literals instead of var(--kith-…)
    spacing    padding/gap/margin values that are off the 4px scale
    contrast   text that does not meet 4.5:1 against what is behind it
    overflow   anything wider than what contains it
    height     what the screen comes to, at a narrow and a wide column
    shot       two PNGs — wide and narrow — that you can open with read_file

It writes the pictures because it already has the page open. There is no screenshot tool in
Kith: `read_file` will *show* you a PNG, but nothing on the machine produces one — the window's
own capture needs a display surface and there is none in a headless turn, which is why the
honest answer to "look at it" used to be standing up a web server and opening a browser panel.
This has a loaded headless Chromium with the design rendered in it, and was throwing that away.

Both widths, because one picture is the thing this script already tells you a single render
never shows: a row that fits at 1200 and breaks at 720 looks perfect in one screenshot.

Run it on the file before you attach it, and again after you change it:

    python3 check_design.py screens/home.html

Exits non-zero if anything in the first five is non-empty, so it chains with && while you
iterate. Height is reported, never judged — a long page is a decision, not a defect.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

#: Where the pictures go.
#:
#: Not beside the design, which is the obvious place and the wrong one: designs live in a project
#: or in `screens/`, both of which are committed, and a PNG per check would put a stream of
#: screenshots into somebody's history. `.kith/scratch` is what this workspace already uses for
#: things that are worth reading once.
SHOTS = Path(os.environ.get("KITH_SCRATCH") or Path.home() / "Kith" / ".kith" / "scratch" / "shots")

#: The two column widths a design is looked at in: the editor with both panels, and without.
WIDE = 1200
NARROW = 720

#: How far off the grid a value may be before it is worth saying. Four, because that is the scale
#: the dialect asks for; a 6px gap among 8s and 12s is the kind of thing nobody sees and everybody
#: feels.
STEP = 4

#: The palette the editor defines on the design root. Injected here for the reason
#: `check_canvas.py` gives: run bare, every `var(--kith-…)` resolves to nothing, every colour
#: silently becomes its initial value, and a design that will look right in the app reports here
#: as black text on a transparent ground.
_TOKENS = """:root{
  --kith-bg:#1a1713; --kith-line:#25221e; --kith-text:#eae5dd; --kith-dim:#a19a92;
  --kith-accent:#f1b65a; --kith-accent-soft:#352918; --kith-second:#5dd786;
  --kith-second-soft:#1d2e1f; --kith-muted:#26221f;
}
html{background:var(--kith-bg)} body{margin:0;color:var(--kith-text)}"""

#: A literal colour in the source. Hex or an rgb()/hsl() call — the two ways a token gets
#: bypassed. Inside a `var(...)` fallback is still a literal, and still stops following the theme.
_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")

#: A line that is *defining* a token rather than bypassing one.
#:
#: This is the whole difference between a design that carries its own system and a design that
#: gave up on systems. `--brand-accent: #59C5C7` is a hex in source and is the correct, intended
#: way to declare a palette; flagging it made the only legal escape from Kith's nine into a
#: checker failure, which is the loop this script was part of causing.
_DECLARES_TOKEN = re.compile(r"--[A-Za-z0-9_-]+\s*:")

#: Where a design system is looked for, in order. A file written *for screens* outranks one
#: adapted *into* them, so `design-system.json` is tried before `brand/profile.json`.
#:
#: The same order the server's /api/design-system route uses, so the picture this script renders
#: and the picture the editor renders are the same design. If they disagree, one of them is
#: lying about the brand.
_SYSTEM_FILES = (
    "design-system.json",
    "brand/design-system.json",
    "design/system.json",
    "brand/profile.json",
)

#: Skills hold brands too — the owner's lives in one, authored for decks long before screens.
#: `KITH_SKILLS_DIR` is how the app names that folder; the default is where a normal install puts
#: it. `KITH_DESIGN_SYSTEM` short-circuits everything for a one-off.
_SKILLS_DIR = os.environ.get("KITH_SKILLS_DIR") or ""


def _with_tokens(source: str, system: dict | None = None) -> str:
    """The design with the palette in front of it, as the editor would define it.

    Both palettes: Kith's nine, which every design written before systems existed refers to, and
    the resolved system's own `--ds-*`. Without the second, a correctly-branded screen renders
    with every colour at its initial value and the contrast half of this report describes a
    design nobody will ever see.
    """
    tokens = _TOKENS
    if system and system.get("colors"):
        declared = [f"--ds-{name}:{value};" for name, value in system["colors"].items()]

        # Not only the colours: a design referring to --ds-text-hero-size in a render that only
        # declared colours gets the inherited size, so the picture shows a hero at body size and
        # the report describes a screen nobody will see.
        for name, value in (system.get("radius") or {}).items():
            declared.append(f"--ds-radius-{name}:{_px(value)};")
        for role, value in (system.get("fonts") or {}).items():
            family = value.get("family") if isinstance(value, dict) else value
            stack = (value.get("stack") if isinstance(value, dict) else "") or "system-ui, sans-serif"
            if family:
                declared.append(f'--ds-font-{role}:"{family}", {stack};')
        for role, spec in (system.get("text") or {}).items():
            if not isinstance(spec, dict):
                continue
            if spec.get("size") is not None:
                declared.append(f"--ds-text-{role}-size:{_px(spec['size'])};")
            if spec.get("weight"):
                declared.append(f"--ds-text-{role}-weight:{spec['weight']};")
            if spec.get("tracking"):
                declared.append(f"--ds-text-{role}-tracking:{spec['tracking']};")

        ground = system["colors"].get("surface") or system["colors"].get("bg")
        page = f"html{{background:{ground}}}" if ground else ""
        tokens = tokens + "\n:root{" + " ".join(declared) + "}\n" + page
    style = f"<style>{tokens}</style>"
    lowered = source.lower()
    for marker in ("<head>", "<html>", "<!doctype html>"):
        at = lowered.find(marker)
        if at != -1:
            cut = at + len(marker)
            return source[:cut] + style + source[cut:]
    return style + source


def _px(value) -> str:
    """A number is pixels; anything already carrying a unit is left alone."""
    if isinstance(value, (int, float)):
        return f"{round(value)}px"
    return str(value)


def _system(start: Path) -> dict:
    """The design system this screen is drawn in, or Kith's own.

    Looked for next to the design and then upwards, because a system belongs to a project and a
    screen sits inside one. Both shapes are read: the written `design-system.json` and the brand
    profile that existed before this surface did, whose colours are bare hex and whose type scale
    is in points.
    """
    import json

    named = os.environ.get("KITH_DESIGN_SYSTEM")
    if named and Path(named).is_file():
        found = _read_system(Path(named))
        if found:
            return found

    here = start.resolve().parent
    places = [here, *list(here.parents)[:6]]

    # Then every installed skill, for a brand that was authored for something else.
    for root in filter(None, [_SKILLS_DIR, str(Path.home() / "Kith" / ".kith" / "skills")]):
        try:
            places.extend(sorted(p for p in Path(root).iterdir() if p.is_dir()))
        except OSError:
            pass

    for folder in places:
        for name in _SYSTEM_FILES:
            found = _read_system(folder / name)
            if found:
                return found
    return {
        "name": "Kith",
        "file": "",
        "colors": {},
        "text": {},
        "radius": {},
        "fonts": {},
        "space": [4, 8, 12, 16, 24, 32, 48, 64],
    }


def _read_system(candidate: Path) -> dict | None:
    """One candidate file, read — or None when it is not a system."""
    import json

    if not candidate.is_file():
        return None
    try:
        doc = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    colours = doc.get("colors") or (doc.get("tokens") or {}).get("colors")
    if not isinstance(colours, dict) or not colours:
        return None
    space = doc.get("space")
    return {
        "name": doc.get("name") or candidate.stem,
        "file": str(candidate),
        "colors": {
            str(k): (v if str(v).startswith("#") else f"#{v}")
            for k, v in colours.items()
            if re.fullmatch(r"[\w-]{1,40}", str(k))
        },
        "text": doc.get("text") or {},
        "radius": doc.get("radius") or doc.get("radii_px") or {},
        "fonts": doc.get("fonts") or (doc.get("tokens") or {}).get("fonts") or {},
        "space": [int(n) for n in space if isinstance(n, (int, float))]
        if isinstance(space, list) and space
        else [4, 8, 12, 16, 24, 32, 48, 64],
    }


def _literal_colours(source: str) -> list[str]:
    """Colours written as themselves, which is how a design stops matching everything else.

    Read from the source rather than from computed style, deliberately: by the time the browser
    has resolved it, `var(--kith-accent)` and `#f1b65a` are the same string, and the difference
    between them — whether it follows a theme — is exactly what is being asked about.
    """
    found: list[str] = []
    for line, text in enumerate(source.splitlines(), 1):
        # A line that declares a token is defining the system, not bypassing it.
        if _DECLARES_TOKEN.search(text):
            continue
        for hit in _LITERAL.finditer(text):
            # A gradient or a shadow can legitimately need a literal; a bare colour rarely can.
            found.append(f"line {line}: {hit.group(0)}")
    return found


try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - the message is the whole point
    sys.exit("playwright is not installed here: pip install playwright && playwright install chromium")


#: Everything measured inside the page, in one pass, because a round trip per node is slow enough
#: to change how often this gets run.
_MEASURE = """
() => {
  const out = { editable: [], spacing: [], contrast: [], overflow: [] };
  const name = (el) => el.getAttribute('data-k') || el.tagName.toLowerCase();

  const channel = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  const luminance = (rgb) =>
    0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
  const parse = (value) => {
    const m = /rgba?\\(([^)]+)\\)/.exec(value || '');
    if (!m) return null;
    const parts = m[1].split(',').map((n) => parseFloat(n));
    return parts.length >= 3 && (parts[3] === undefined || parts[3] > 0.05) ? parts : null;
  };
  // What is actually behind this text, which is the nearest ancestor that painted something.
  const ground = (el) => {
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg) return bg;
    }
    return parse(getComputedStyle(document.documentElement).backgroundColor) || [26, 23, 19];
  };

  for (const el of document.body.querySelectorAll('*')) {
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (!box.width && !box.height) continue;

    // Editable: the dialect is flex and tokens. Anything else renders and cannot be dragged.
    if (style.position === 'absolute' && !el.closest('[data-k-absolute]')) {
      out.editable.push(name(el) + ': position:absolute outside a data-k-absolute frame');
    }
    if (style.display.includes('grid')) {
      out.editable.push(name(el) + ': display:grid — renders, but has no handles');
    }

    /* Spacing: off the scale by enough to be felt rather than seen.
     *
     * Read from what he *wrote*, not from what the browser computed, and that distinction is the
     * whole check. `margin-top: auto` is a perfectly good instruction that computes to whatever
     * space was left — 309px on one of these — and reporting that as "off the 4px scale" is a
     * defect the tool invented. Computed values also carry the user-agent's own margins on
     * headings and paragraphs, which he never chose and cannot be blamed for.
     *
     * The inline declarations are exactly the editable surface, so checking those is checking
     * the thing the dialect actually asks him to get right.
     *
     * `gap` is also one declaration the cascade reports as three, so it is read once. */
    const wrote = (property) => el.style.getPropertyValue(property).trim();
    const spacing = [
      ['padding-top', wrote('padding-top') || wrote('padding')],
      ['padding-left', wrote('padding-left')],
      ['margin-top', wrote('margin-top') || wrote('margin')],
      ['margin-left', wrote('margin-left')],
      ['gap', wrote('gap') || wrote('row-gap')],
    ];

    for (const [property, raw] of spacing) {
      if (!raw || raw === 'auto') continue;
      // A shorthand such as `16px 24px` is several values; every one of them counts.
      for (const piece of raw.split(/\\s+/)) {
        if (!/px$/.test(piece)) continue;
        const px = parseFloat(piece);
        if (!px || Number.isNaN(px)) continue;
        // Against the system's own scale, not a fixed four. A system with a 5px step is not
        // wrong, and telling him every value in it is wrong was the checker enforcing Kith's
        // taste on somebody else's brand.
        if (!SCALE.includes(px)) out.spacing.push(name(el) + ': ' + property + ' ' + px + 'px');
      }
    }

    // Contrast, for the nodes that actually carry text of their own.
    const own = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.nodeValue.trim());
    if (own) {
      const fg = parse(style.color);
      const bg = ground(el);
      if (fg && bg) {
        const a = luminance(fg) + 0.05;
        const b = luminance(bg) + 0.05;
        const ratio = a > b ? a / b : b / a;
        const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontWeight) >= 700 && parseFloat(style.fontSize) >= 18.66);
        if (ratio < (large ? 3 : 4.5)) {
          out.contrast.push(name(el) + ': ' + ratio.toFixed(2) + ':1');
        }
      }
    }

    // Overflow: wider than what holds it, which is how a mockup lies about fitting.
    const parent = el.parentElement;
    if (parent && parent !== document.body) {
      const holder = parent.getBoundingClientRect();
      if (box.right > holder.right + 1 || box.left < holder.left - 1) {
        out.overflow.push(name(el) + ' escapes ' + name(parent));
      }
    }
  }

  out.editable = [...new Set(out.editable)];
  out.spacing = [...new Set(out.spacing)];
  out.overflow = [...new Set(out.overflow)];
  return out;
}
"""


#: How tall the design is, which is not how tall the window is.
#:
#: `body.scrollHeight` is the larger of the content and the viewport, so a short screen in a
#: 900px window reports 900 at every width — which reads as "identical at both widths" and is the
#: opposite of what it means.
_HEIGHT = """
() => {
  let bottom = 0;
  for (const el of document.body.children) {
    const box = el.getBoundingClientRect();
    if (box.bottom > bottom) bottom = box.bottom;
  }
  return Math.ceil(bottom + window.scrollY);
}
"""


def check(path: Path) -> int:
    source = path.read_text(encoding="utf-8", errors="replace")
    system = _system(path)
    literals = _literal_colours(source)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": WIDE, "height": 900})
        # Offline, because the editor is. A design that needs the network is a design that will
        # arrive with holes in it.
        page.context.route(
            "**/*",
            lambda route: (
                route.abort() if route.request.url.startswith(("http://", "https://")) else route.continue_()
            ),
        )
        staged = Path(tempfile.mkdtemp()) / path.name
        staged.write_text(_with_tokens(source, system))
        page.goto(staged.resolve().as_uri())
        page.wait_for_timeout(250)

        scale = system.get("space") or [STEP]
        measure = _MEASURE.replace("SCALE", repr(scale))
        found = page.evaluate(measure)
        wide = page.evaluate(_HEIGHT)
        shots = []
        try:
            SHOTS.mkdir(parents=True, exist_ok=True)
            wide_shot = SHOTS / f"{path.stem}-{WIDE}.png"
            page.screenshot(path=str(wide_shot), full_page=True)
            shots.append(wide_shot)
        except Exception:
            # A picture that could not be written is not a reason to lose the measurements.
            pass

        page.set_viewport_size({"width": NARROW, "height": 900})
        page.wait_for_timeout(200)
        narrow_found = page.evaluate(measure)
        narrow = page.evaluate(_HEIGHT)
        try:
            narrow_shot = SHOTS / f"{path.stem}-{NARROW}.png"
            page.screenshot(path=str(narrow_shot), full_page=True)
            shots.append(narrow_shot)
        except Exception:
            pass
        browser.close()

    # Overflow at the narrow width is the one that matters most and the one a single render
    # never shows: a row that fits at 1200 and breaks at 720 looks perfect in a screenshot.
    overflow = sorted({*found["overflow"], *(f"{one} (at {NARROW}px)" for one in narrow_found["overflow"])})

    def report(label: str, items: list[str], limit: int = 6) -> None:
        if not items:
            print(f"{label:<9}ok")
            return
        head = ", ".join(items[:limit])
        tail = f" … and {len(items) - limit} more" if len(items) > limit else ""
        print(f"{label:<9}{len(items)} — {head}{tail}")

    print(f"system   {system['name']}" + (f" ({system['file']})" if system["file"] else " — no system file found"))
    report("editable", found["editable"])
    report("tokens", literals)
    report("spacing", found["spacing"])
    report("contrast", found["contrast"])
    report("overflow", overflow)
    print(f"height   {wide}px at {WIDE}, {narrow}px at {NARROW}")
    if shots:
        # Named so they can be opened directly: `read_file` shows a PNG as a picture.
        print("shot     " + "  ".join(str(one) for one in shots))
    else:
        print("shot     none — could not write the pictures")

    return 1 if any([found["editable"], literals, found["spacing"], found["contrast"], overflow]) else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1])
    if not target.is_file():
        sys.exit(f"no such file: {target}")
    raise SystemExit(check(target))
