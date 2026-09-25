#!/usr/bin/env python3
"""What is wrong with this screen that looking at it will not tell you.

You can see a screenshot, and you should take one. But vision answers "does this look right",
and the half it never answers is "is this right" — a heading 3px off the grid, a grey that fails
contrast by a tenth, a card two pixels wider than the column it sits in. Those read as fine and
are not, and they are exactly the defects that survive a self-review and reach the person.

So this renders the file the way the editor renders it and reports the measurable half:

    editable   nodes the design editor cannot give handles to, and why
    tokens     colours written as literals instead of var(--ds-…)
    spacing    padding/gap/margin values that are off the 4px scale
    contrast   text that does not meet 4.5:1 against what is behind it
    parts      components used from the project's inventory, and any named that do not exist
    overflow   anything wider than what contains it
    viewport   vh/vw lengths, which measure a window the design is not in
    frame      the size the design declared with data-k-frame, and what it rendered at
    height     what the screen comes to, at a narrow and a wide column
    shot       two PNGs — wide and narrow — that you can open with read_file
    next       on a clean run, the part this script cannot check: go and look

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
#: The size a design gets when it does not say. 1200 because that is what every design on this
#: machine was implicitly authored against before `data-k-frame` existed; changing it would
#: resize work nobody touched.
WIDE = 1200
NARROW = 720

#: The named sizes, kept identical to `PRESETS` in `ui/src/lib/design/frame.ts`. A design that
#: renders at one size here and another in the editor is the failure this whole file exists to
#: prevent, one level up.
_PRESETS = {
    "desktop": (1440, 900),
    "laptop": (1280, 800),
    "tablet": (834, 1112),
    "mobile": (390, 844),
}

#: Below this a design is a phone and the narrow pass is the same render twice.
_NARROW_WORTH_DOING_ABOVE = 600


def _frame(source: str) -> tuple[int, int, str]:
    """How big the screen is, as the design declared it.

    Without this the checker rendered everything at 1200 and reported a second pass at 720 —
    which for a screen designed as a phone is a desktop render and a slightly narrower desktop
    render, neither of them the thing being designed.
    """
    hit = re.search(r"""data-k-frame\s*=\s*["']([^"']{1,40})["']""", source, re.I)
    declared = (hit.group(1).strip().lower() if hit else "")
    if declared in _PRESETS:
        return (*_PRESETS[declared], declared)
    exact = re.fullmatch(r"(\d{2,4})\s*[x\u00d7]\s*(\d{2,4})", declared)
    if exact:
        width, height = int(exact.group(1)), int(exact.group(2))
        if 240 <= width <= 4000 and 240 <= height <= 4000:
            return width, height, "custom"
    return WIDE, 800, "default"

#: How far off the grid a value may be before it is worth saying. Four, because that is the scale
#: the dialect asks for; a 6px gap among 8s and 12s is the kind of thing nobody sees and everybody
#: feels.
STEP = 4

#: The palette the editor defines on the design root. Injected here because,
#: run bare, every `var(--kith-…)` resolves to nothing, every colour
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
#:
#: `(?<!&)` is the whole of a bug worth remembering. An HTML numeric entity is a `#` followed
#: by digits — `&#9906;` is ⌕, `&#10003;` is ✓ — so every icon written the way HTML has always
#: written them was reported as a hardcoded colour. The design was correct, the report said it
#: was not, and the workaround it drove was to stop using entities: a real defect invented by
#: the thing that exists to find them. A hex colour is never preceded by an ampersand.
_LITERAL = re.compile(r"(?<!&)#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")

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
#: The same order `kith.services.design_system.NAMES` uses, so the picture this script renders
#: and the picture the editor renders are the same design. If they disagree, one of them is
#: lying about the brand — which is why `test_three_readers_agree_on_one_system.py` feeds the
#: same fixture to this script and to the service and asserts they resolve identically.
#:
#: This script keeps its own reader rather than importing the service, and that is deliberate:
#: it is handed to the model as a path and run with whatever `python3` is on PATH, so a
#: dependency on the `kith` package would make it fail in exactly the situation it exists for.
#: The duplication is real; the test is what keeps it from drifting.
_SYSTEM_FILES = (
    ".kith/design/system.json",
    "design/system.json",
    "design-system.json",
    "brand/design-system.json",
    "brand/profile.json",
)

#: Skills hold brands too — the owner's lives in one, authored for decks long before screens.
#: `KITH_SKILLS_DIR` is how the app names that folder; the default is where a normal install puts
#: it. `KITH_DESIGN_SYSTEM` short-circuits everything for a one-off.
_SKILLS_DIR = os.environ.get("KITH_SKILLS_DIR") or ""


def _font_stack(value) -> str:
    """One font role, as a CSS value.

    This used to be `f'"{family}", {stack}'` unconditionally, which is right for exactly one
    of the two shapes a system can be written in and wrong for the other. A `brand/profile.json`
    names a bare family and a separate fallback stack, so quoting is correct. A
    `design-system.json` gives the whole stack in one string — `ui-rounded, "SF Pro Rounded",
    system-ui, sans-serif` — and wrapping *that* in quotes produces
    `"ui-rounded, "SF Pro Rounded", ..."`, which is not a font value at all.

    The damage was invisible and total. A malformed custom property makes any `font:` shorthand
    referring to it an invalid declaration, and the browser drops the *whole* declaration — so
    a 44px/600 heading came out at inherited size and weight, in the screenshot the model was
    told to judge the design by. Every screen using `var(--ds-font-*)` in a shorthand has been
    rendered with its type scale silently discarded.
    """
    if isinstance(value, dict):
        family = str(value.get("family") or "").strip()
        stack = str(value.get("stack") or "").strip()
        if not family:
            return stack
        # A bare family name needs quoting; one that already carries quotes or commas is a
        # stack somebody wrote and must be passed through as written.
        quoted = family if ('"' in family or "'" in family or "," in family) else f'"{family}"'
        return f"{quoted}, {stack}" if stack else quoted
    text = str(value or "").strip()
    if not text:
        return ""
    return text if ('"' in text or "'" in text or "," in text) else f'"{text}", system-ui, sans-serif'


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
            emitted = _font_stack(value)
            if emitted:
                declared.append(f"--ds-font-{role}:{emitted};")
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
        "components": {},
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
        "components": doc.get("components") if isinstance(doc.get("components"), dict) else {},
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


def _playwright():
    """Imported when a page is about to be opened, not when this module is.

    It used to be a module-level import guarded by `sys.exit`, which made the whole script
    unimportable anywhere playwright is not installed — including the test suite, whose venv
    does not have it, and which is the one place that can check this script's idea of the
    design system against the service's. A rendering dependency should not decide whether the
    pure functions above can be read.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - the message is the whole point
        sys.exit("playwright is not installed here: pip install playwright && playwright install chromium")
    return sync_playwright


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


#: `100vh` in a design is not the design's height — it is the height of whatever window the
#: editor happens to be in. This script renders into a 900px page and gets one answer; the board
#: renders into a frame inside the app and gets another; neither is the screen. It is the single
#: cause of a mockup that is right at the top and then a thousand pixels of nothing.
#: Symbols standing in for icons, found in the rendered text rather than the source, so a
#: glyph in a comment or a `content:` string a sheet never applies does not count.
#:
#: The ranges are where the stand-ins actually came from — Misc Technical (⌂ ⌕ ⌘), Geometric
#: Shapes (▦ ▾ ▶), Misc Symbols (☰ ★ ♧), Dingbats (✓ ✚ ✕), the arrows block and emoji. An arrow
#: only counts when it is the whole of its text: "See all →" is punctuation, a lone ↕ in a
#: table header is an icon. Box drawing is left alone; it is how people draw rules in comments.
_GLYPHS = r"""
() => {
  const icon = /[\u2300-\u23FF\u25A0-\u25FF\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF]|[\u{1F300}-\u{1FAFF}]/u;
  const arrow = /^[\u2190-\u21FF\s]+$/;
  const found = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    const parent = node.parentElement;
    if (!parent || parent.closest("style, script, svg")) continue;
    const text = node.nodeValue.trim();
    if (!text) continue;
    const hit = text.match(icon) ?? (arrow.test(text) ? [text.replace(/\s+/g, "")] : null);
    if (!hit) continue;
    const named = parent.closest("[data-k]");
    const where = named ? named.getAttribute("data-k") : parent.tagName.toLowerCase();
    found.push(`${hit[0]} (${where})`);
  }
  return found;
}
"""

_VIEWPORT = re.compile(r"\b\d[\d.]*(vh|vw|vmin|vmax|dvh|svh|lvh)\b")


def _viewport_units(source: str) -> list[str]:
    """Lengths measured against a window the design is not in."""
    found = []
    for number, line in enumerate(source.splitlines(), start=1):
        for hit in _VIEWPORT.finditer(line):
            found.append(f"line {number}: {hit.group(0)}")
    return found


def _parts(source: str, inventory: dict) -> tuple[list[str], list[str]]:
    """Which of the project's components this screen is built out of, and which it invents.

    A `data-k-component="button.primary"` says this block is an instance of a part the product
    already has. Naming one that is not in the system is the interesting case and the reason
    this is a failure rather than a note: it is either a typo, or a part somebody meant to add
    to the inventory and did not, and both end with the next screen drawing its own version.

    What this deliberately does not do is complain about a screen with no components in it at
    all. A one-off does not owe the inventory anything, and a checker that nagged about it would
    be making an aesthetic judgement, which is the one thing this script is not for.
    """
    used = sorted(set(re.findall(r'data-k-component\s*=\s*["\']([^"\']{1,60})["\']', source)))
    unknown = [name for name in used if name not in inventory]
    return used, unknown


def check(path: Path) -> int:
    source = path.read_text(encoding="utf-8", errors="replace")
    wide_px, frame_h, frame_name = _frame(source)
    # A phone has no second, narrower pass to do — it is already the narrow one.
    narrow_px = NARROW if wide_px > _NARROW_WORTH_DOING_ABOVE else 0
    system = _system(path)
    literals = _literal_colours(source)
    used_parts, unknown_parts = _parts(source, system.get("components") or {})
    viewport = _viewport_units(source)

    with _playwright()() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": wide_px, "height": frame_h})
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
        glyphs = sorted(set(page.evaluate(_GLYPHS)))
        shots = []
        try:
            SHOTS.mkdir(parents=True, exist_ok=True)
            wide_shot = SHOTS / f"{path.stem}-{wide_px}.png"
            page.screenshot(path=str(wide_shot), full_page=True)
            shots.append(wide_shot)
        except Exception:
            # A picture that could not be written is not a reason to lose the measurements.
            pass

        # A design already narrower than a phone has no second pass to do: rendering it twice
        # produces the same picture under a second name and invites looking at the wrong one.
        narrow_found = {"editable": [], "spacing": [], "contrast": [], "overflow": []}
        narrow = wide
        if narrow_px:
            page.set_viewport_size({"width": narrow_px, "height": frame_h})
            page.wait_for_timeout(200)
            narrow_found = page.evaluate(measure)
            narrow = page.evaluate(_HEIGHT)
            try:
                narrow_shot = SHOTS / f"{path.stem}-{narrow_px}.png"
                page.screenshot(path=str(narrow_shot), full_page=True)
                shots.append(narrow_shot)
            except Exception:
                pass
        browser.close()

    # Overflow at the narrow width is the one that matters most and the one a single render
    # never shows: a row that fits at 1200 and breaks at 720 looks perfect in a screenshot.
    overflow = sorted(
        {*found["overflow"], *(f"{one} (at {narrow_px}px)" for one in narrow_found["overflow"] if narrow_px)}
    )

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
    if unknown_parts:
        print(f"parts    {len(unknown_parts)} not in the system — {', '.join(unknown_parts)}")
    elif used_parts:
        print(f"parts    {len(used_parts)} from the system — {', '.join(used_parts)}")
    elif system.get("components"):
        # The inventory exists and this screen used none of it. Said, not failed: a screen can
        # legitimately be the one that is not made of the usual parts.
        print(f"parts    none used; {len(system['components'])} available — {', '.join(sorted(system['components']))}")
    report("overflow", overflow)
    report("viewport", viewport)
    if glyphs:
        # Said with the fix, because the fix is one call: the reason these are here at all is
        # that there was nothing else to reach for.
        report("icons", glyphs)
        print("         symbols standing in for icons — get real ones with find_icons")
    else:
        print(f"{'icons':<9}ok")
    print(f"frame    {frame_name} · {wide_px}\u00d7{frame_h}" + ("" if frame_name != "default" else " (not declared — say `data-k-frame` on <body>)"))
    if narrow_px:
        print(f"height   {wide}px at {wide_px}, {narrow}px at {narrow_px}")
    else:
        print(f"height   {wide}px at {wide_px}")
    if shots:
        # Named so they can be opened directly: `read_file` shows a PNG as a picture.
        print("shot     " + "  ".join(str(one) for one in shots))
    else:
        print("shot     none — could not write the pictures")

    failing = any(
        [found["editable"], literals, found["spacing"], found["contrast"], overflow, unknown_parts, viewport, glyphs]
    )
    if not failing:
        # The last line of a clean run, because a clean run is the moment the mistake gets made.
        # Everything above is hygiene — on the grid, on the palette, above 4.5:1 — and none of it
        # is an opinion about whether the screen is any good. This is the only thing here with a
        # green light attached, so if it is left as the last word it becomes the goal, and what
        # gets shipped is a forgettable screen that measures perfectly. Say what is still owed.
        print(
            "next     nothing measurable is wrong, which is not the same as right. "
            "Open the shots above and name what your eye hits first, whether that is what you "
            "intended, and the one thing you will change."
        )
    return 1 if failing else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1])
    if not target.is_file():
        sys.exit(f"no such file: {target}")
    raise SystemExit(check(target))
