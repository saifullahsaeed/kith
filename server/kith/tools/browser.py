"""The Browser tab: a real browser in one of Kith's panes, driven by the person and by him.

It is not `browse_page`. That reads a page in a browser nobody sees and forgets it; this is the
page the person has in front of them, with the logins they made in it, and it stays where it was
left — so a login they complete is a login his next call is on the far side of.

## One tool, not nine

It was a plugin, and a plugin's commands are separate tools: nine schemas, about 3,900 characters
on every request of every turn, whether or not the browser was touched. One tool with the act as
its first argument carries the same vocabulary for about a third of that — the argument that
made `plugin_state` one tool and folded `look` and "look at one thing" into one verb. What each act
is *for* lives in the `driving-a-browser` skill, which costs nothing until it is read.

## What the shell does and what this does

Everything that touches the page happens in the desktop app (`desktop/src/web/web-views.ts`),
reached over its loopback service. This decides which conversation's browser a call is for,
keeps a screenshot as a file rather than as bytes in the transcript, and trims each answer to
what the act says — a tool result is read on every later round of the turn, so the eleven status
fields the shell reports about every page are paid for eleven times over if they are not.
"""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

from kith.kernel import session_context
from kith.tools.params import INT, STR
from kith.tools.registry import tool

ACTS = (
    "open",
    "read",
    "click",
    "type",
    "press",
    "scroll",
    "look",
    "resize",
    "back",
    "tabs",
    "switch",
    "close",
    "show",
)
KEYS = ("Enter", "Tab", "Escape", "Backspace", "ArrowDown", "ArrowUp")
#: The named sizes. The desktop side holds the pixels behind them (`VIEWPORT_PRESETS`), and its
#: test holds that table against this tuple, so the two cannot offer different sizes.
PRESETS = ("reset", "phone", "phone-landscape", "tablet", "tablet-landscape", "laptop", "desktop", "square")

#: The pseudo-plugin a `show` is filed under in the host-call queue. Not a valid plugin id — ids
#: are lower-case letters, digits and hyphens — so no installed plugin can ever answer as it.
HOST = "@kith"

#: How many screenshots to keep. Oldest go first; what was just taken is what he is about to read.
MAX_SHOTS = 32
#: The most page text an answer carries. The shell cuts at the same length and says so; this is
#: the server not taking that on trust, because a tool result is re-read on every later round.
MAX_TEXT_CHARS = 8_200

#: Which fields each act's answer keeps, beyond the page's `url`, `title` and any `failure`.
KEEP = {
    "open": (),
    "read": ("text",),
    "click": ("clicked", "options"),
    "type": ("into",),
    "press": ("did",),
    "scroll": ("moved",),
    "look": ("shot", "selector", "options", "width", "height"),
    "resize": ("width", "height", "scale"),
    "back": (),
    "tabs": ("tabs",),
    "switch": ("tabs",),
    "close": ("tabs",),
}


@tool(
    "browser",
    "The Browser tab: a real browser the person also uses — you drive the same pages, their "
    "logins included. `act`: open (url; new_tab keeps this page), read (its text; cheap, prefer "
    "it), click (text or selector), type (text, into), press (key), scroll (by), look (a "
    "screenshot path; text or selector crops), resize (preset, or width/height/ratio), back, "
    "tabs, switch (tab), close (tab, else the front one), show (put it on screen). Acts are on "
    "the tab in front. The driving-a-browser skill has the rest.",
    {
        "act": {"type": "string", "enum": list(ACTS)},
        "url": {**STR, "maxLength": 1000, "description": "open: the address."},
        "new_tab": {"type": "boolean", "description": "open: in a new tab, in front."},
        "tab": {**STR, "maxLength": 8, "description": 'switch, close: its id, "t2".'},
        "text": {
            **STR,
            "maxLength": 1000,
            "description": "click, look: the words on it. type: what to type.",
        },
        "selector": {**STR, "maxLength": 200, "description": "click, look: CSS, when text will not do."},
        "into": {
            **STR,
            "maxLength": 200,
            "description": "type: the field's label, placeholder or selector; replaces what is in it.",
        },
        "key": {"type": "string", "enum": list(KEYS)},
        "by": {"type": "number", "description": "scroll: pixels, negative is up. Default 600."},
        "preset": {"type": "string", "enum": list(PRESETS)},
        "width": {**INT, "minimum": 200, "maximum": 3840},
        "height": {**INT, "minimum": 200, "maximum": 2160},
        "ratio": {**STR, "description": 'resize: "16:9", "9:16"…'},
    },
    required=("act",),
)
def browser(path: Path, args: dict):
    act = str(args.get("act") or "")
    if act not in ACTS:
        return {"error": f"{act or 'nothing'} is not something the browser does. It can: {', '.join(ACTS)}."}
    wanted = _arguments(act, args)
    if isinstance(wanted, str):
        return {"error": wanted}
    if act == "show":
        return _show()

    from kith.infra import renderer

    answer = renderer.browse(act, session_context.current(), args=wanted)
    if answer is None:
        return {"error": "No window is open, so nothing could do that. Carry on and say what you skipped."}
    if answer.get("error"):
        return {"error": str(answer["error"])}
    if isinstance(answer.get("text"), str):
        answer["text"] = answer["text"][:MAX_TEXT_CHARS]
    return _trimmed(act, answer)


def _arguments(act: str, args: dict) -> dict | str:
    """What the shell needs for this act, or the sentence saying what is missing."""
    text = str(args.get("text") or "")
    selector = str(args.get("selector") or "")
    if act == "open":
        url = str(args.get("url") or "").strip()
        if not url:
            return "open needs a url."
        return {"url": url, "new_tab": args.get("new_tab") is True}
    if act == "switch":
        tab = str(args.get("tab") or "").strip()
        return {"tab": tab} if tab else 'switch needs the tab, by its id — "t2". `tabs` lists them.'
    if act == "close":
        return {"tab": str(args.get("tab") or "").strip()}
    if act == "click":
        if not text and not selector:
            return "click needs the text on it, or a selector."
        return {"text": text, "selector": selector}
    if act == "type":
        if "text" not in args:
            return "type needs the text to type."
        return {"text": text, "into": str(args.get("into") or "")}
    if act == "press":
        key = str(args.get("key") or "")
        return {"key": key} if key in KEYS else f"press needs a key: one of {', '.join(KEYS)}."
    if act == "scroll":
        by = args.get("by")
        return {"by": by if isinstance(by, int | float) else 600}
    if act == "look":
        return {"text": text, "selector": selector}
    if act == "resize":
        return {
            key: args[key]
            for key in ("preset", "width", "height", "ratio")
            if args.get(key) not in (None, "")
        }
    return {}


def _show() -> dict:
    """Put the Browser tab on screen, by asking the window — the layout is the renderer's to change.

    Through the same queue a plugin's host effects use, because it already knows how to refuse
    when nobody is watching and how to give up on a window that went away, and a second channel
    for "open a tab" would have to learn both again.
    """
    if session_context.unattended():
        return {"error": "Nobody is here and no window is open, so nothing could do that."}
    from kith.services.plugins import calls

    answer = calls.ask(
        calls.new(session_context.current(), HOST, "show", "browser", {}, timeout_ms=8_000, kind="host")
    )
    if answer.get("ok") is False:
        return {"error": str(answer.get("error") or "The window did not open the tab.")}
    return {"opened": True}


def _trimmed(act: str, answer: dict) -> dict:
    """The page and what the act says, with the rest of the shell's status left out.

    Which tab the answer is about is said only when there is more than one — with a single tab it
    is a field that is always the same, paid for on every call. The tab list is said by the acts
    that are about tabs, one line each, the front one marked.
    """
    out: dict = {}
    tabs = answer.get("tabs")
    if not isinstance(tabs, list):
        tabs = []
    front = str(answer.get("tab") or "")
    if len(tabs) > 1:
        out["tab"] = front
    if tabs or act in ("tabs", "close"):
        answer = {
            **answer,
            "tabs": [
                f"{one.get('id')}{' (in front)' if one.get('id') == front else ''}: "
                f"{one.get('title') or 'untitled'} — {one.get('url') or 'blank'}"
                for one in tabs
                if isinstance(one, dict)
            ]
            or ["no tabs are open"],
        }
    if act == "look" and isinstance(answer.get("png"), str) and answer["png"]:
        answer = {**answer, "shot": _keep(answer["png"])}
    if isinstance(answer.get("options"), list):
        # A list, flattened, because return fields are primitives — and the list is the useful
        # half of a miss, the thing that saves a screenshot to find out what *is* there.
        answer = {**answer, "options": ", ".join(str(one) for one in answer["options"] if one)}
    for key in ("url", "title", *KEEP.get(act, ()), "failure"):
        value = answer.get(key)
        if value in (None, "", []):
            continue
        if key == "scale" and value == 1:
            continue
        out[key] = value
    return out


def _keep(png: str) -> str:
    """Write a screenshot where he can `read_file` it, and return the path.

    A path rather than the bytes because a path costs about fifty tokens and the picture costs
    thousands, and whether it is worth reading is his decision to make after seeing the answer —
    which is the whole reason `look` is cheap until it is read.
    """
    folder = _shots()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"browser-{secrets.token_hex(4)}.png"
    target.write_bytes(base64.b64decode(png))
    held = sorted(folder.glob("browser-*.png"), key=lambda one: one.stat().st_mtime)
    for stale in held[:-MAX_SHOTS]:
        try:
            stale.unlink()
        except OSError:
            pass
    return str(target)


def _shots() -> Path:
    """Beside Kith's own records, not in his workspace — a screenshot is not work product."""
    from kith.infra import workspace

    return workspace.internal() / "browser"
