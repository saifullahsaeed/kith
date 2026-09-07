# Example plugins

A plugin is one folder with a `kith.plugin.json` in it. Install one from **Settings → Plugins**,
or point the API at it directly:

```sh
curl -s -X POST localhost:8611/api/plugins \
  -H "X-Kith-Token: $(cat ~/.kith/api-token)" \
  -H 'Content-Type: application/json' \
  -d "{\"path\": \"$PWD/sketchpad\"}"
```

Nothing is installed by default. A folder sitting here contributes nothing until somebody
installs it — the row in the database is what makes a plugin exist, not the folder.

## sketchpad

A tab he draws on. The shortest honest demonstration of the whole loop:

1. He calls `plugin__sketchpad__draw`, and one record lands in the plugin's store.
2. Core pushes the store into the sealed frame.
3. The page folds the record into its own list of shapes and writes the list back.
4. You drag a shape; that writes back too.
5. The next thing you say to him carries a digest line — `Sketchpad — title: Intake flow,
   shapes: 11, note: moved Intake` — so he knows what happened while he was not looking.

It contributes all four kinds of thing a plugin can: a **surface** (the tab), three
**commands** (two he can call, one drawn as a button), a **skill** telling him how to lay a
diagram out, and **state** with a digest. It bundles no MCP server, so it runs no program and
needs no boundary approved.

Read `ui/board.html` for what a surface actually looks like. The two things worth noticing:

- **`window.kith` is the only channel out**, and it has no verb that reaches past this plugin's
  own store. No fetch, no navigate, no open. Data arrives because core pushes it; actions
  happen because the manifest declared a command and core drew the button.
- **Nothing is fetched.** The frame has no network at all. Every relative script, stylesheet
  and image is inlined by the server before the document is served, and a reference to
  something remote is refused at install with the URL quoted — so a plugin whose stylesheet
  lives on a CDN finds out while somebody is deciding, rather than rendering unstyled later.

## Writing one

`docs/superpowers/specs/2026-09-06-plugin-host-design.md` is the contract. The short version:

| you want to | declare |
| --- | --- |
| a tab | `surfaces[]` with an `entry` html file |
| something he can call | `commands[]` with `model: true` |
| a button in the tab's chrome | `commands[]` with `present: {in: "toolbar"}` |
| something that survives the turn | `state`, and a `digest` if he should know it exists |
| to teach him how to use it | a folder under `skills/`, in the ordinary `SKILL.md` format |
| to run real code | one `server` — an MCP server, with the files it may see declared |

Two limits worth knowing before you start. A command with `model: true` costs prompt tokens on
**every round of every turn**, which is why it defaults to false and why the install screen
prices your plugin before anyone agrees to it. And a `params` shape is primitives only —
string, number, integer, boolean — in both directions; objects and arrays are how structure
gets smuggled into a turn, so a command that needs a record uses `does.collect` and lets the
surface do the folding.

## flowpad — the same thing, with React

`sketchpad` is hand-written DOM with no dependencies. `flowpad` is React + React Flow, and it
exists to prove the more useful claim: **a surface is an ordinary web page, so any library works
inside it.** Dragging, panning, zooming, edge routing, a minimap and keyboard handling come from
the library rather than from several hundred lines of hand-rolled SVG.

```sh
cd flowpad && npm install && npm run build
```

Then install `examples/plugins/flowpad` the same way. Ask him for a diagram:

```
Draw me the plugin install flow on the Flowpad — a node per step,
edges showing the order, decisions as `decision` nodes. Name it first.
```

Nodes are draggable, and a drag writes back — so the next thing you say carries what you moved.

### What it costs, measured

| | |
| --- | --- |
| `board.js` (React + React DOM + React Flow, minified) | 371 KB |
| `board.css` (React Flow's stylesheet + the example's) | 17 KB |
| The sealed document as served | **392 KB — 20% of the 2 MB ceiling** |
| Added to every request | ~466 tokens (its four command schemas) |

Two things worth separating there. The bundle is **browser** cost, paid on mount; the prompt cost
is the command schemas and nothing else. A React surface is not expensive in the way that
matters most, because the bytes never reach the model.

But `sketchpad` does its job in about 12 KB. Reach for a library when it earns its size — a real
graph does; a rectangle does not.

### The rules a bundle has to follow

**Nothing may be fetched.** The seal is `default-src 'none'`, so a CDN is not an option and
neither is code-splitting. Bundle to one JS file and one CSS file, both referenced relatively;
the host inlines them into the document before serving it, and a reference it cannot inline is
refused at install with the URL quoted. `flowpad/vite.config.ts` shows the config —
`inlineDynamicImports` plus fixed output names is the whole of it.

**`type="module"` is preserved**, so an ES-module bundle works. It has to be: most bundlers emit
modules by default, and a module inlined as a classic script is a syntax error that kills the
surface before its first line runs.

**Theme through `--kith-*`.** The host repaints those custom properties when the person flips the
theme, without remounting the frame. `flowpad/src/board.css` maps React Flow's own `--xy-*`
variables onto them, which is the whole of its theming.

**`window.kith` is the only channel out**, and it has no verb reaching past the plugin's own
store. `flowpad/src/kith.d.ts` is the shape, hand-written because a plugin is a folder someone
drops in — there is nothing to install.

## browser — a browser he drives, that you can reach into

The one that needs every mechanism at once, and the reason it is here.

```sh
cd browser && npm install && npx playwright install chromium
```

Install `examples/plugins/browser`, and when the review screen asks for
`PLAYWRIGHT_BROWSERS_PATH`, give it `~/Library/Caches/ms-playwright`.

```
Open news.ycombinator.com, read the front page, and tell me the three
most interesting titles and why. Open the Browser tab first so I can
watch.
```

### What it is made of, and why each piece has to be where it is

| | |
| --- | --- |
| **Chromium** | in the plugin's MCP **server** — a surface has `default-src 'none'`, so it could not load a page even if you asked it to |
| **The picture** | a PNG in the plugin's own storage; the *path* goes to him, and `read_file` routes it to `read_image` so he genuinely sees it |
| **The tab** | reads the path from the store, and the **host** pushes the bytes in as an asset, because a frame cannot fetch |
| **Your click** | on the picture → a coordinate in the store → his skill tells him to check before his next step |
| **`where`** | a `surface` command: he asks the tab what is on screen and waits for it |

That split is the whole design in one plugin: **the subprocess does, the frame shows, and
neither can do the other's job.**

### The bit worth trying

Ask him to sign in to something. He will get stuck, `ask` you, and stop. Click the button on
the screenshot; tell him to carry on. He reads the coordinate out of the store and continues.
Neither of you had to describe the page to the other.

### What it costs

| | |
| --- | --- |
| bundle (React, no graph library) | 192 KB |
| added to every request | ~200 tokens (two command schemas) |
| `read` on a page | a few hundred tokens |
| `read_file` on a screenshot | a few thousand |

The skill exists mostly to teach him that last row: **read the text, and reach for the picture
only when layout is the question.** A browser plugin that screenshots everything is a browser
plugin that empties a context window in six steps.

### The boundary it runs inside

Its manifest declares `reach.read: ["~/Library/Caches/ms-playwright"]` and nothing else, so the
install screen says in as many words that it will be able to read your Playwright cache — and
that the rest of your home folder is sealed from it. Verified: Chromium launches and screenshots
inside that profile, and the subprocess cannot read `~/.ssh` or list `$HOME`.

It has network access, and the review screen says so plainly: *it will be able to see those
files, and could send them anywhere.* For a browser that is the entire point, which is exactly
why the sentence is not conditional.
