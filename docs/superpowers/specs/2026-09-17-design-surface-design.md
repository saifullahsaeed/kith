# A design surface — screens he can write and you can move

*2026-09-17*

## Why

The loop this is for is **mockup → correction → code**. He drafts a screen, you look at it and
find it wrong in the way that is faster to fix by hand than to describe, you move it, and the
corrected thing is what he then implements. Today the middle step does not exist. An ```html
fence renders — `html-canvas.tsx` has done that since August — but the frame is opaque by
construction, so the only thing you can do to a design is read it and type a paragraph about
what is wrong with it. Describing a 12px gap in prose is worse than dragging it, and it is worse
for him too: "tighten the spacing a bit" is a sentence he has to guess at, and `gap: 12px →
16px` is not.

So: a surface where his design is editable, and where your edit comes back to him as a diff
rather than as a description of a diff.

## The decision everything else falls out of: the document is HTML

Three formats were considered — a bespoke Figma-style node tree, an embedded whiteboard engine
(tldraw/Excalidraw) with its own document JSON, and HTML. HTML wins, and the reasons are about
the model, not the editor.

**He does not have to compute layout.** A node tree makes him emit `x/y/width/height` for every
element. Absolute geometry is the single thing a language model is worst at, because it is the
part a person would *see* rather than derive. `display: flex; gap: 12px` hands the arithmetic to
the browser: he states intent, the renderer resolves position. Every coordinate a node tree would
make him invent is a coordinate he can get wrong and cannot check.

**He can change a design without rewriting it.** A design is text, so `edit_files` already works
on it — the same diff primitive he uses on code, with no new verb to learn. In a JSON tree a
small change is either a full-document rewrite (expensive, and rewrite pressure is exactly where
he drops fields — measured, in the flash-vs-pro head-to-head) or a patch language he has to be
told about and will therefore not use.

**Your edits come back as intent.** Drag something and the diff he reads is `padding: 16px →
24px`. He can infer *why*. The same drag in a shape-based document is a delta on an opaque id,
which tells him something moved and nothing about what it meant. That alone disqualifies the
embedded-whiteboard option for this purpose.

**Priors and cost.** HTML/CSS is the densest-represented UI language in any model's training; a
bespoke schema is zero-shot every time. And the same mockup runs roughly 5–8× the tokens as a
node tree, on a per-tick budget that has been audited twice.

The counter-argument is real and is accepted: arbitrary CSS does not round-trip through drag
handles. Grid, media queries and pseudo-elements render but are not handle-editable. The answer
is a constrained dialect (Layer 0) and a source view for everything outside it, not a different
format.

## Layer 0 — the dialect

Left alone he writes `<div style="...">` with invented hex codes and magic numbers, and drifts
visually from one turn to the next. The dialect is the constraint that makes his output both
consistent *and* editable:

* **Layout is flex, not coordinates.** Rows, columns, `gap`, `padding`, `align`/`justify`. No
  `position: absolute` except inside a frame explicitly marked `data-k-absolute`, which exists
  for the genuine overlay cases (a badge on an avatar, a dropdown) and nothing else.
* **Colour is a token, not a literal.** `var(--kith-accent)`, not `#f1b65a`. The tokens are the
  ones `groundwork()` already injects, derived from `canvasTokens()` — so a design is
  automatically in the app's palette and follows a theme switch. This is the half that buys
  consistency across turns, and it is also what lets the inspector offer a dropdown where a hex
  code could only offer a text field.
* **Every element carries a stable id.** `data-k="hero.title"`. Written by him where he names
  things, minted by the parser where he did not. Patches address these, so an edit survives him
  rewriting a neighbouring section.

The dialect is taught by a skill (Layer 5), not enforced by a validator. Output outside it still
renders; it is simply less editable, and the layout report says so.

## Layer 1 — a design is a file, attached as an ordinary deliverable

**No migration. No new tool. No new enum.** This is the layer where the feature most easily dies
and the design is chosen accordingly — `delegate_subtask` shipped complete and went unused
because it required wanting a new shape, and the HTML canvas worked precisely because ```html was
already what he writes.

A design is an HTML file in his workspace, attached to a task as a `kind: "file"` deliverable.
Both halves are things he does constantly and neither is new. `deliverables.kind` is a bare TEXT
column with no CHECK constraint, so nothing in the schema needs touching.

The editor is offered by **detection, not declaration**: a file deliverable whose path ends
`.html` and whose content passes the existing `looksRenderable` gets an "Open in design editor"
action. That mirrors `canvas.ts`, which already prefers judging his output over demanding a
schema of it, and it means the surface appears for work he did before this feature existed.

Because it is a file, versioning is free where it matters: a design inside a project repo is
covered by `changes` and `commit`, which he already drives. A design in his bare sandbox has
in-editor undo and nothing else — a known limit, below.

## Layer 2 — `ui/src/lib/design/parse.ts`, the boundary that replaces the seal

`html-canvas.tsx` contains his HTML by giving it an opaque origin. The design editor cannot do
that — you cannot put resize handles on a document you are forbidden to touch — so it renders in
trusted React instead, and the containment moves from the origin to a whitelist. **This is a
security boundary and gets the same treatment `canvas.ts` got, including its own test file.**

Parse produces a validated node tree, and everything not on a list is dropped rather than
escaped:

* Tags: a layout-and-content set (`div`, `span`, `p`, `h1`–`h6`, `img`, `svg` and its shape
  children, `button`, `input`, `ul`/`ol`/`li`, `table` and friends). `script`, `iframe`,
  `object`, `embed`, `link`, `form` are dropped entirely.
* `<style>` is **kept and sanitised**, not dropped. He writes stylesheets — dropping them would
  mean the editor only opens for output he does not naturally produce, which is the failure mode
  this whole design is arranged to avoid. Its declarations go through the same property
  whitelist; selectors are restricted to element/class/id and `@media` blocks; `@import` and
  everything else at-rule is dropped, and the whole sheet is scoped to the design root at render.
  **The inspector never writes into it** — edits are inline styles, which win on specificity.
  That keeps direct manipulation unambiguous: changing one node's padding changes one node, not
  every node sharing its class.
* Attributes: `data-k*` (the id and `data-k-absolute`), `class`, `style`, `src`/`href` restricted to `data:` and relative
  workspace paths, `alt`, `type`, `value`, `placeholder`. Anything matching `on*` is dropped —
  not sanitised, dropped.
* CSS: a property whitelist (box model, flex, typography, colour, border, radius, shadow,
  opacity, transform). Values are re-serialised from parsed primitives, so `url(...)`,
  `expression(...)`, `@import` and anything with an escape sequence in it do not survive the
  round trip. `!important` is stripped.

The parse runs on his output, which is influenced by pages he read. Its tests are adversarial by
construction — the file to copy the shape of is `canvas.test.ts`.

`html-canvas.tsx` is **not** modified. Everything that is not a design keeps the seal it has.

## Layer 3 — `ui/src/lib/design/edit.ts`, the editable model

Geometry is measured, never stored: the tree renders, the DOM lays it out, and
`getBoundingClientRect` gives the handles their positions. Nothing in the document holds an x or
a y, which is the whole point of Layer 0.

Edits are **patches against `data-k` ids**, applied to the HTML source text rather than to a
parallel model:

```
{ id: "hero.title", set: { "margin-bottom": "24px" } }
{ id: "hero.cta", move: { parent: "hero.actions", index: 0 } }
```

A patch writes the smallest possible change into the source — a style declaration replaced in
place, a node moved between two parents — so the diff he reads afterwards is the edit, not a
reformat of his file. Serialisation preserves his indentation and attribute order for untouched
nodes; a whole-document reprint would drown one changed value in a thousand-line diff and make
the "readable intent" argument false.

## Layer 4 — the editor, autolayout-first

The interaction model follows from the dialect. If he never writes absolute coordinates, a plain
move tool is wrong: dragging must express the same things flex expresses.

* **Drag = reorder and reparent.** Dropping between two children inserts; dropping into a frame
  reparents. Insertion points are shown as a line, Figma-style. Free x/y only inside a
  `data-k-absolute` frame.
* **Handles adjust padding and gap**, not width and height, on a flex container. Width/height
  handles appear on leaves and on explicitly-sized frames.
* **Inspector**: spacing, alignment, typography, colour, radius, border. Colour is a swatch list
  of the `--kith-*` tokens plus a custom field that warns it will not follow a theme.
* **Layers panel** is the tree, named by `data-k`.
* **Source view** toggles to the HTML, editable directly, which is the escape hatch for
  everything the dialect does not cover.

Placement: a full-height view opened from the deliverable row in `task-detail.tsx`, at
`/tasks/:id/design/:deliverable`. Not a drawer — the inspector and layers panel need the width,
and `task-detail.tsx` is already 818 lines and should not absorb this.

Saving writes the file through **`PUT /api/workspace/file`** — the one server-side addition.
`workspace.py` has list, read, raw, open, rename, folder and delete, but no write; it gets one,
gated by the same `wanted()` path resolution the others use so a design cannot be saved outside
the workspace.

## Layer 5 — `designing-a-screen`, the skill

Modelled on `drawing-a-canvas`, which is the precedent for teaching him a surface exists rather
than hoping he infers it. It covers: when a screen is worth mocking up at all (most answers are
still prose), the dialect, the token list, how ids are named, and the fact that a person can move
what he made and that he should re-read the file before continuing rather than assuming his own
last version is current.

That last point is the one with teeth. A design he wrote three turns ago may have been edited by
hand since, and his default assumption — that the file says what he last wrote — is wrong for
exactly this kind of file.

## Layer 6 — the layout report

He can see: `computer.py:75` hands an image read by `read_file` to him as a picture, and
`check_code` already tells him to look at a screenshot rather than trust that the build passed.
So the feedback loop exists. What vision does not give him is the half he will otherwise
self-certify past — a thing 3px off the grid looks fine.

So the skill ships `scripts/check_design.py` (the shape `drawing-a-canvas/scripts/check_canvas.py`
already has): render, then report as **text** — computed boxes, overflow and clipping, colours
that are not tokens, spacing values off the scale, contrast failures, elements outside the
editable dialect. Vision catches "this looks wrong". The report catches "this is wrong".

## Testing

* `design/parse.test.ts` — adversarial, mirroring `canvas.test.ts`: script tags, `on*` handlers,
  `javascript:` hrefs, `url()` in styles, escape sequences, malformed nesting. A test asserting
  the whitelist is a whitelist, so an edit that opens it fails here rather than in a month.
* `design/edit.test.ts` — patch round-tripping: a style change touches one line; a reparent moves
  one node; untouched nodes come back byte-identical.
* A component test for the editor's drop-target arithmetic (insert before/after/into).
* `server/tests/` — the new write route rejects paths outside the workspace, mirroring whatever
  `test_*workspace*` already asserts for rename and delete.

## Known limits, stated rather than discovered

* **Only flex round-trips.** Grid, media queries, pseudo-elements and animation render but are
  not handle-editable. Source view is the answer; the report names them.
* **No version history outside a repo.** A design in a project gets git. A design in the bare
  sandbox gets in-editor undo and nothing more.
* **Single writer.** If he edits the file mid-session while the editor is open, last write wins.
  The editor reloads on focus and warns when the file changed underneath it; it does not merge.
  (This is the `two-writers-one-worktree` problem in miniature and gets the same honest answer.)
* **No components or variants.** A design is one screen. Shared pieces are copy-paste until
  there is evidence the loop needs more.
* **The seal is gone for designs.** Containment is the whitelist, and a whitelist is only as
  good as its tests. That trade is made deliberately here and nowhere else — `html-canvas.tsx`
  keeps the opaque origin for everything that is not a design.

## Build order

Each step ends somewhere demonstrable.

1. `parse.ts` + its adversarial tests. Nothing renders yet; the boundary exists first.
2. `PUT /api/workspace/file` + server tests.
3. Read-only render of a design in the new route, opened from a deliverable row. At this point a
   design is visible and nothing can be changed.
4. Selection, layers panel, inspector — reading properties, writing them through `edit.ts`.
5. Drag: reorder, reparent, padding/gap handles.
6. Source view and save.
7. The `designing-a-screen` skill and `check_design.py`.
