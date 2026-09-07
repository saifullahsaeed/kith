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
