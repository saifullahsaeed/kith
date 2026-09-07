"""Building a plugin surface's document, and mounting it without widening the seal.

A plugin surface is a canvas with a longer life: an opaque document Kith did not write, in an
iframe with no same-origin, carrying `domain/seal.POLICY`. The seal is unchanged and that is
**checkable rather than argued** — no new sandbox token, no new CSP directive, no relaxation of
`default-src 'none'`, and `ui/src/lib/canvas.test.ts`'s eighteen assertions pass untouched.

That is possible because **nothing fetches**. Every relative `<script src>`, `<link rel=stylesheet>`
and `<img src>` that resolves inside the plugin folder is inlined here, server-side; images and
fonts become `data:` URIs, which the existing policy already permits. An absolute or remote href
is **refused at install with the href quoted**, not dropped silently at serve time — the seal has
no network, and the honest moment to say so is once, while a person is deciding, rather than as a
blank pane later.

It also means the question three readers flagged — what CSP `'self'` resolves to inside an
opaque-origin frame — does not have to be answered, because nothing loads.

**Two things Kith injects that a canvas does not get, and they are the difference between an
honest plugin tab and a second-class one.** A core-authored stylesheet carrying the whole token
set, and a core-authored inline SVG sprite of the icons the manifest declared. Both are Kith's
own bytes at build time, exactly as the CSP `<meta>` and the bridge script already are, and
neither needs a single change to the seal: `style-src 'unsafe-inline'` and `img-src data:` are
already permitted. Without them every plugin surface reads as foreign at a glance — measured on
the panel this design's dogfood rebuilds, which uses eleven icons and a dozen tokens against the
nine tokens and no icons a canvas is given.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path

from kith.domain.plugins import Plugin, PluginError, SurfaceDecl
from kith.domain.seal import MAX_BYTES, POLICY

#: How many mounts to hold. Four times the largest arrangement anyone has built.
#:
#: Unlike the canvas store's FIFO, eviction is **by identity, not by age**: a new mount of the
#: same `(plugin, view, instance, client)` replaces the previous one, which reclaims a stale
#: ticket on the next reload for free and cannot evict a live tab out from under itself.
MAX_MOUNTS = 32

#: Assets worth inlining, by extension. Anything else is left as-is and simply does not load,
#: which is why `asset_faults()` exists — the refusal happens at install, with the path named.
_TEXTUAL = {".js", ".mjs", ".css"}
_BINARY = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".woff", ".woff2", ".ttf", ".otf"}

_SCRIPT = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>\s*</script\s*>""", re.I)
_LINK = re.compile(r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*>""", re.I)
_HREF = re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""", re.I)
_IMG = re.compile(r"""(<img\b[^>]*\bsrc\s*=\s*["'])([^"']+)(["'])""", re.I)
_REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//|^data:|^blob:", re.I)
#: Whether the tag being inlined was a module. See `script` in `_inline`.
_MODULE = re.compile(r"""\btype\s*=\s*["']module["']""", re.I)


@dataclass
class Mount:
    """One live frame, and what it is allowed to be asked."""

    ticket: str
    plugin: str
    view: str
    instance: str = ""
    client: str = ""
    conversation: str = ""
    tab: str = ""
    ready: bool = False
    document: bytes = b""
    #: Consecutive timeouts. Three marks the mount unresponsive and the pane offers a reload.
    misses: int = 0
    at: float = field(default=0.0)

    def key(self) -> tuple[str, str, str, str]:
        return (self.plugin, self.view, self.instance, self.client)


_mounts: dict[str, Mount] = {}
_lock = threading.Lock()


def resolve_entry(plugin: Plugin, surface: SurfaceDecl) -> Path:
    """The surface's entry file, confirmed to be inside the plugin folder.

    **Resolve first, then confirm containment.** The other order lets `../` walk out — the
    sentence `api/spa.py` already wrote down, and it matters more here because this process can
    read everything the person can.
    """
    root = plugin.path.resolve()
    entry = (plugin.path / surface.entry).resolve()
    if entry != root and root not in entry.parents:
        raise PluginError(f"{surface.entry!r} is outside the plugin's folder.")
    if not entry.is_file():
        raise PluginError(f"{surface.entry!r} is not there.")
    return entry


def asset_faults(plugin: Plugin) -> list[str]:
    """Every reference a surface makes that could not be inlined, with the href quoted.

    Reported at install rather than at serve time. A plugin whose stylesheet lives on a CDN is
    a plugin that will render unstyled with nothing on screen explaining why, and finding that
    out while deciding whether to install it is strictly better than finding out afterwards.
    """
    faults: list[str] = []
    for surface in plugin.surfaces:
        try:
            entry = resolve_entry(plugin, surface)
        except PluginError as exc:
            faults.append(str(exc))
            continue
        html = entry.read_text(errors="replace")
        for href in _referenced(html):
            if _REMOTE.match(href):
                faults.append(
                    f"The {surface.id!r} surface loads {href!r}. A plugin surface has no network "
                    f"access, so that would silently never arrive — bundle it into the plugin."
                )
                continue
            target = (entry.parent / href).resolve()
            root = plugin.path.resolve()
            if root not in target.parents or not target.is_file():
                faults.append(f"The {surface.id!r} surface refers to {href!r}, which is not in the plugin.")
    return faults


def _referenced(html: str) -> list[str]:
    found = [match.group(1) for match in _SCRIPT.finditer(html)]
    for match in _LINK.finditer(html):
        href = _HREF.search(match.group(0))
        if href:
            found.append(href.group(1))
    found += [match.group(2) for match in _IMG.finditer(html)]
    return found


def sealed(plugin: Plugin, surface: SurfaceDecl, palette: dict, icons: dict[str, str]) -> bytes:
    """The whole document, inlined, with the policy and the bridge on the front.

    Order is the specification, and it is the order `lib/canvas.ts` established. The policy
    first, because a CSP `<meta>` governs only what follows it. Then the token stylesheet,
    because it is a floor rather than a rule — a surface that sets no colours still belongs on
    the screen — and anything the plugin writes comes later and wins. Then the bridge, ahead of
    the plugin's own script, because it installs `window.kith`: a page calling it from top-level
    code would find nothing there otherwise, and that reads as the feature being broken rather
    than mis-ordered.
    """
    entry = resolve_entry(plugin, surface)
    html = entry.read_text(errors="replace")
    html = _inline(html, entry.parent, plugin.path.resolve())

    head = (
        f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<style>{_groundwork(palette)}</style>"
        f"{_sprite(icons)}"
        f"<script>{_bridge(plugin.id, surface.id)}</script>"
    )
    if re.search(r"<head\b[^>]*>", html, re.I):
        html = re.sub(r"(<head\b[^>]*>)", r"\1" + head, html, count=1, flags=re.I)
    elif re.search(r"<html\b[^>]*>", html, re.I):
        html = re.sub(r"(<html\b[^>]*>)", r"\1<head>" + head + "</head>", html, count=1, flags=re.I)
    else:
        html = f"<head>{head}</head>{html}"
    document = (
        f"<!doctype html>{html}" if not html.lstrip().lower().startswith("<!doctype") else html
    ).encode()
    if len(document) > MAX_BYTES:
        raise PluginError(f"that surface builds to {len(document):,} bytes and the ceiling is {MAX_BYTES:,}.")
    return document


def _inline(html: str, base: Path, root: Path) -> str:
    """Replace every local reference with its content. Nothing is left to fetch.

    `root` is resolved here rather than trusted, because the containment check below compares it
    against a *resolved* target — so an unresolved root fails every comparison and silently drops
    every asset, producing an empty surface with no error anywhere. On macOS that is the ordinary
    case rather than an exotic one: `/var` is a symlink to `/private/var`, so any path under a
    temporary directory hits it. Caught by a test that passed `tempfile.mkdtemp()` straight in.
    """
    root = root.resolve()
    base = base.resolve()

    def read(href: str) -> tuple[str, bytes] | None:
        if _REMOTE.match(href):
            return None
        target = (base / href.split("?")[0].split("#")[0]).resolve()
        if root not in target.parents or not target.is_file():
            return None
        return target.suffix.lower(), target.read_bytes()

    def script(match: re.Match) -> str:
        got = read(match.group(1))
        if got is None or got[0] not in _TEXTUAL:
            return ""
        # `type="module"` survives the inlining, and that is not cosmetic.
        #
        # Most bundlers emit ES modules by default, so a plugin built with an ordinary Vite or
        # Rollup config produces a file with top-level `import`/`export` in it. Inlined as a
        # classic script that is a *syntax error* — the whole surface dies before its first line
        # runs, with nothing on screen and nothing in any log the person can reach. It is also
        # the failure a React plugin is most likely to hit, because that is the shape every
        # React tutorial produces.
        #
        # An inline module is still governed by `script-src 'unsafe-inline'`, so keeping the
        # attribute costs the seal nothing.
        kind = ' type="module"' if _MODULE.search(match.group(0)) else ""
        # No `</script` can survive inside an inline script, or it closes the element early.
        body = got[1].decode(errors="replace").replace("</script", "<\\/script")
        return f"<script{kind}>{body}</script>"

    def link(match: re.Match) -> str:
        href = _HREF.search(match.group(0))
        got = read(href.group(1)) if href else None
        if got is None:
            return ""
        return "<style>" + got[1].decode(errors="replace") + "</style>"

    def image(match: re.Match) -> str:
        got = read(match.group(2))
        if got is None or got[0] not in _BINARY:
            return match.group(0)
        kind = mimetypes.guess_type("x" + got[0])[0] or "application/octet-stream"
        return match.group(1) + f"data:{kind};base64,{base64.b64encode(got[1]).decode()}" + match.group(3)

    html = _SCRIPT.sub(script, html)
    html = _LINK.sub(link, html)
    return _IMG.sub(image, html)


def _groundwork(palette: dict) -> str:
    """The colours and the reset a surface starts from.

    A separate document cannot inherit `index.css`, so every token it might want arrives as
    text. Exposed as `--kith-*` custom properties *and* applied, because the useful case is a
    surface that quietly matches the app without having been told what the app looks like.
    """
    tokens = "\n".join(f"  --kith-{name}: {value};" for name, value in sorted(palette.items()))
    return f""":root {{
{tokens}
  color-scheme: light dark;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; height: 100%; }}
/* A surface's own root fills the frame.
 *
 * Measured in Chromium, because reasoning about it is how this went unnoticed: without this the
 * React example rendered its header at 45px and its graph at **zero** — thirteen nodes present
 * in the DOM, no space to draw them in, and a blank pane with nothing anywhere saying why. The
 * chain is ordinary CSS and ordinary CSS is the problem: `body` is 100% tall, a mounting div is
 * `height: auto`, and a percentage height inside an auto-height parent behaves as auto — so a
 * `flex: 1` child has nothing to grow into.
 *
 * Every full-height surface wants this and each one would hit it separately, which is what a
 * host stylesheet is for: the same argument as the colour tokens, which exist so a surface that
 * sets nothing still belongs on the screen. `:where()` keeps the specificity at zero, so a
 * plugin that wants a short, content-sized root just says so and wins. */
:where(body > div:only-of-type) {{ height: 100%; }}
body {{
  background: var(--kith-bg);
  color: var(--kith-text);
  font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
button {{
  font: inherit; color: inherit; cursor: pointer;
  background: var(--kith-muted);
  border: 1px solid var(--kith-line);
  border-radius: 6px; padding: 4px 9px;
}}
button:hover {{ border-color: var(--kith-accent); }}
input, select, textarea {{
  font: inherit; color: inherit;
  background: var(--kith-bg);
  border: 1px solid var(--kith-line);
  border-radius: 6px; padding: 4px 7px;
}}
a {{ color: var(--kith-accent); }}
.kith-icon {{ width: 1em; height: 1em; vertical-align: -0.125em; fill: none;
  stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
"""


def _sprite(icons: dict[str, str]) -> str:
    """An inline SVG sprite of the icons this plugin declared.

    Kith's own bytes, from the bundled set, looked up by name — a plugin names an icon and never
    supplies one. Rendered as `<symbol>`s so a surface writes
    `<svg class="kith-icon"><use href="#icon-check"/></svg>` and gets the app's own iconography
    rather than an emoji or a missing-image box.
    """
    if not icons:
        return ""
    symbols = "".join(
        f'<symbol id="icon-{name}" viewBox="0 0 24 24">{body}</symbol>'
        for name, body in sorted(icons.items())
    )
    return f'<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">{symbols}</svg>'


def _bridge(plugin: str, view: str) -> str:
    """`window.kith`, installed before the plugin's own code runs.

    Hand-written ES5 in a string, because it is concatenated into an untrusted document under
    `script-src 'unsafe-inline'` with no bundler anywhere near it.

    **Four members, and no verb that reaches outside this plugin's own store.** No `fetch`, no
    `navigate`, no `invoke`. Data arrives because the manifest declared `wants` and core pushes
    it; actions happen because the manifest declared a command and core drew the button. That is
    what keeps every privileged effect behind chrome Kith drew, and it is why a person clicking
    such a button can be treated as the authorisation.
    """
    return (
        "(function(){var H=[],S={},R=null,N=0,W={};"
        "function post(m){try{parent.postMessage(m,'*')}catch(e){}}"
        # `event.source !== parent` first, before the type switch. The canvas bridge checks type
        # first, which is harmless with one inbound message and not harmless with three.
        "window.addEventListener('message',function(e){"
        "if(e.source!==parent)return;var m=e.data;if(!m||m.kith!==1)return;"
        "if(m.type==='render'){S=m.state||{};R=m.host||{};H.forEach(function(f){try{f(S,R)}catch(x){}})}"
        "else if(m.type==='theme'){var t=m.tokens||{};for(var k in t){"
        "document.documentElement.style.setProperty('--kith-'+k,t[k])}}"
        "else if(m.type==='command'){var f=W[m.name];if(!f){post({kith:1,type:'result',call:m.call,"
        "ok:false,value:{}});return}"
        "try{var out=f(m.args||{});"
        "if(out&&typeof out.then==='function'){out.then(function(v){"
        "post({kith:1,type:'result',call:m.call,ok:true,value:v||{}})},function(){"
        "post({kith:1,type:'result',call:m.call,ok:false,value:{}})})}"
        "else{post({kith:1,type:'result',call:m.call,ok:true,value:out||{}})}}"
        "catch(x){post({kith:1,type:'result',call:m.call,ok:false,value:{}})}}"
        "});"
        "window.kith={"
        f"plugin:{plugin!r},view:{view!r},"
        "render:function(f){H.push(f);if(R!==null){try{f(S,R)}catch(x){}}},"
        "on:function(n,f){W[n]=f},"
        "state:{get:function(k){return S[k]},all:function(){return S},"
        "set:function(v,x){N++;post({kith:1,type:'state.set',id:'w'+N,values:v,expect:x})},"
        "drop:function(k){N++;post({kith:1,type:'state.drop',id:'d'+N,keys:k})}},"
        "size:function(px){post({kith:1,type:'size',px:px})}"
        "};"
        "post({kith:1,type:'ready',protocol:1,handles:[]});"
        "})();"
    ).replace("'", "'")


# --------------------------------------------------------------------------- #
# Mounts
# --------------------------------------------------------------------------- #


def mount(
    plugin: str, view: str, *, instance: str = "", client: str = "", conversation: str = "", tab: str = ""
) -> Mount:
    """Claim a ticket for one frame.

    A ticket rather than a stable path, because a frame's `src` cannot carry `X-Kith-Token` and
    adding a guessable path to `OPEN_GET_PREFIXES` would drop the unguessable-id property that
    exemption rests on. Twenty-four random bytes handed out over an authenticated POST, and what
    it buys is a document already on this machine's disk.

    Keyed on the renderer as well as the surface. Two windows on one backend are two mounts, so
    opening a second window cannot silently steal the first's frame identity and leave a person
    clicking a button in one and watching it act in the other.
    """
    import time

    entry = Mount(
        ticket=secrets.token_urlsafe(24),
        plugin=plugin,
        view=view,
        instance=instance,
        client=client,
        conversation=conversation,
        tab=tab,
        at=time.time(),
    )
    with _lock:
        for ticket, held in list(_mounts.items()):
            if held.key() == entry.key():
                _mounts.pop(ticket, None)
        _mounts[entry.ticket] = entry
        while len(_mounts) > MAX_MOUNTS:
            _mounts.pop(next(iter(_mounts)))
    return entry


def held(ticket: str) -> Mount | None:
    with _lock:
        return _mounts.get(ticket)


def unmount(ticket: str) -> None:
    with _lock:
        _mounts.pop(ticket, None)


def live(plugin: str, view: str, *, instance: str = "", conversation: str = "") -> list[Mount]:
    """Every mount that could answer for this surface.

    More than one means two windows, and the caller decides — `surface_ambiguous` naming them
    beats picking one, because picking is how a person ends up watching a command act somewhere
    they are not looking.
    """
    with _lock:
        found = [
            entry
            for entry in _mounts.values()
            if entry.plugin == plugin and entry.view == view and entry.ready
        ]
    if instance:
        found = [entry for entry in found if entry.instance == instance]
    if conversation:
        narrower = [entry for entry in found if entry.conversation == conversation]
        if narrower:
            found = narrower
    return found


def forget_plugin(plugin: str) -> None:
    """Drop every mount of one plugin — it is being disabled or removed."""
    with _lock:
        for ticket, entry in list(_mounts.items()):
            if entry.plugin == plugin:
                _mounts.pop(ticket, None)
