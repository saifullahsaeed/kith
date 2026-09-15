# Pinned tabs that know where they live, and the verbs a tiling layout was missing

**Status:** approved 2026-09-08. Four tranches, built in order.

## Why

The windowed shell landed with the arrangement persisted and every *individual* placement
decision delegated to one per-surface preference. That is the right default and it is not
enough for two things people actually do.

**A tab has no memory of its own.** `placements` in `store.ts` is keyed by `SurfaceId` — the
preference is about a *kind* of thing ("settings goes in the wide pane"), which is why it
survives panes coming and going. But it cannot say "this one lives *there*". Close the Work
panel out of the right column and reopen it and you get whatever `own` decides, which is a
pane of its own beside wherever you were looking. The arrangement you built is restored on
restart and then quietly eroded by every close and reopen inside a session.

**The layout has no verbs.** There is no way to focus a pane, walk its tabs, throw a tab into
the pane next door, close a pane in one action, temporarily blow one up to fill the window, or
flip a split from columns to rows. Everything is drag, click, or the tab context menu. The
renderer has **zero** layout shortcuts: fifteen-odd components each attach their own `window`
keydown listener and not one of them is about the layout.

Both are the same shape of gap — the arrangement is a value the app persists faithfully and
gives you almost no way to *operate*.

## What a "place" is, and why it is not a pane id

This is the whole load-bearing decision, so it goes first.

A pin has to name somewhere. The obvious candidate — the pane's id — is the one thing that
cannot be used. `nextId` in `tree.ts` mixes a per-page-load token into every id it mints, and
the comment beside it records why in blood: the counter is module state starting at zero, a
stored layout comes back holding the ids it was *saved* with, so the first pane created after
a reload was `pane-1`, which the restored tree already contained. `react-resizable-panels`
refuses duplicate panel ids by throwing during render, below `LayoutView`, and because
`commit` writes before it sets, the colliding tree was already in `localStorage` — "Try again"
and "Reload" both restored the same broken layout and recovery meant clearing site data by
hand.

So ids are addresses, not identity. Worse for our purpose, a pane *dies* the moment its last
tab closes: `replacePane` returning `null` removes it and collapses the split above it. A pin
pointing at a pane id would break in exactly the case a person would notice — close the pinned
tab, reopen it, land somewhere else.

**`PaneNode` gains an optional `place?: string`.** An opaque id, minted the first time
something is pinned into that pane, and **never re-minted**. Node `id`s are re-minted freely
(and must be, on any load); `place` rides through untouched. That is the same split the tab
model already makes and already documents: `uid` is *this tab* for React to key on, `tabKey` is
*what the tab shows*. Here `id` addresses a node and `place` says which place it is.

An optional field needs **no `VERSION` bump.** `looksLikeLayout` asks only whether a pane has a
string `id`, an array `tabs` and a numeric `active`, and whether each tab's surface is one this
build knows. A v2 tree written before places existed reads as a v2 tree with no places, which
is correct. This matters more than it looks: the version comment records that a bump throws
every stored arrangement away, and losing everyone's layout to ship a preference is precisely
the trade `store.ts` exists to refuse.

### Pins

Their own storage key, `kith-layout-pins`, no version, every entry validated against what the
build knows and the rest ignored. Same move `kith-layout-placements` already makes, for the
same reason: the layout blob is versioned and a preference must not be destroyed by a bump to
the arrangement.

```ts
type Slot = {
  /** The root split's direction at pin time — used only when the root is a bare pane and a
   *  split has to be created to replay the slot. */
  direction: "row" | "column";
  /** The pane's index among the ROOT split's children at pin time. A nested pane reports the
   *  index of its outermost ancestor that is a direct child of the root. */
  index: number;
  /** That child's size fraction within the root split. */
  size: number;
};

type Pin = { place: string; slot: Slot };

pins: Record<string /* tabKey */, Pin>;
```

`index` rather than an edge, because the default layout's middle column is the chat and "left
or right" cannot describe it. Replay inserts at `min(index, children.length)`, so a slot
recorded in a three-column row lands back in the middle of a two-column one instead of
resolving to a lie.

Pins are keyed by `tabKey`, which means a specific conversation can be pinned
(`chat:20260803-…`) as well as a surface. A draft chat's key changes when the server names it,
so `rename` moves the pin across with the tab — the same reason `renameTab` carries `uid`
across rather than closing and reopening.

**Pins are global, not per saved layout.** A pin names a place, and a layout either has that
place or does not; a pin that cannot be honoured falls through to the existing policy rather
than being lost. One set of pins, surviving a reset, is both less state and the more useful
answer.

### Places do not duplicate

Checked against every operation in `tree.ts` rather than assumed:

- `dockTab` splitting a pane builds a **new** pane for the moved tab and keeps the original
  pane object as the other child, so `place` stays with the pane that has the pinned tabs.
- `replacePane` collapsing a one-child split returns that child unchanged.
- `closeTab`, `moveTab` and `activateTab` spread the existing pane.

So no existing op can produce two panes carrying one place. A normalisation pass in `commit`
stays anyway, as defence against a loaded or hand-edited tree: the pane holding the most
pinned tabs keeps the place, ties broken by render order. Cheap, and the failure it prevents is
"which of these two is the right column".

## Tranche 1 — places, pins, placement, protection

### The new rung, and one old wart

`choosePane` grows two rungs, and they go *below* the caller's overrule rather than above it: a
caller that names a live pane still wins, because `open_surface` from a plugin knows things no
policy can. The order becomes:

1. A caller that names a live pane. (unchanged)
2. **Pinned:** the pane carrying `pins[tabKey(ref)].place`, if one exists.
3. **Pinned, homeless:** raise a pane at the recorded slot and stamp it with the place.
4. `beside`, `focused`, the section rule, and every fallback below them — unchanged.

Rung 3 is carried as an optional `raise?: { slot, place }` on the existing
`PlacementChoice` rather than turning that type into a discriminated union. The union is the
tidier type and it would rewrite every assertion in `place.test.ts`, which reads
`toEqual({ paneId, beside })` twenty-odd times; an added optional field leaves all of them
passing and still makes the new case impossible to confuse with the old ones.

**A pin ignores the width-fit check.** The `fits` test exists so a heuristic does not open a
560px chat into a pane the yielding rule has railed to 36px. A pin is not a heuristic; it is an
instruction, and honouring it into a narrow pane is the answer the person asked for. The rail
is one click from coming back.

One addition while in there, which fixes something already wrong: **prefer an existing empty
pane that fits, before raising a new one.** Today an empty pane holds no tabs, therefore holds
nothing of the new tab's kind, so `own` raises a *fresh* pane beside it — open a chat with an
empty pane on screen and you get a fourth column next to a blank third. Tranche 4 makes this
acute, since a saved layout deliberately comes back with a blank chat pane, but it is a bug
now.

### Strip order

**Invariant 7: pinned tabs occupy a prefix of `tabs`.** Enforced in the *array*, not at render,
because the whole drag layer — `insertIndexAt`, `landingIndex`, the caret, and `caretAt`
reading `getBoundingClientRect` off `[data-tab]` — assumes array order is visual order. Sorting
at render would put the caret arithmetic and the tree's own indices in different frames of
reference, which is the class of bug that only shows up on the third tab.

Held by a normalisation pass in `commit`, not by clamping inside `moveTab`. `tree.ts` is pure
and knows nothing of the store, so a `moveTab` that enforced this would need a
`pinned: (key) => boolean` predicate threaded through it and through `dockTab` and every
caller. `commit` already normalises — it prunes dead pane widths and rebuilds the focus order —
and it is the one place that sees both the tree and the pins. So `tree.ts` exports a pure
`orderPinned(root, isPinned)` and `commit` runs it on every mutation. A drop that aims an
unpinned tab in front of a pinned one snaps back, which is the wanted behaviour and cheaper
than refusing the drop.

### Protection

- The strip's X is **hidden** on a pinned tab. That is the accidental close; the deliberate one
  belongs in the menu.
- Its context menu grows `Unpin` and keeps `Close`, so closing a pinned tab is one explicit
  choice and the pin survives it — reopen and it goes home.
- "Close the others" skips pinned tabs.
- Closing a whole pane (tranche 3) closes pinned tabs too. It is a deliberate action and the
  pins outlive it.

### Store

```ts
pins: Record<string, Pin>;
pin: (key: string) => void;    // mints a place on the tab's pane if it has none
unpin: (key: string) => void;  // drops the pane's place when nothing is pinned there
```

`unpin` clearing an orphaned `place` matters: a place with nothing pinned to it is a name
nobody can reach, and it would be saved into every layout thereafter.

### Done when

- A pinned tab closed and reopened lands in the same pane. ✔
- A pinned tab whose pane has been closed re-raises it at the recorded slot. ✔
- An unpinned tab's placement is byte-identical to today, asserted by a regression test. ✔
- A stored v2 layout written before this ships still loads. ✔
- `tsc -b` clean, `oxlint` clean over `shell/layout/`, 486 tests passing (400 before). ✔

## Tranche 2 — keyboard and zoom

### `layout/keys.ts`

A pure `commandFor(event): Command | null` and one `window` listener mounted by `LayoutView`.
One listener rather than a seventh ad-hoc one, and a pure function so the binding table is a
test rather than a thing you verify by pressing keys.

Guards: bail on `event.defaultPrevented`, and bail when the target is an `input`, `textarea` or
`[contenteditable]`. Uniformly — a digit that focuses a pane while you are typing a message is
worse than a shortcut you have to click out of first.

The taken combos, enumerated rather than guessed. In the renderer: `Cmd+F` (`session-bar.tsx`,
find in thread), `Cmd+S` (`persona-tab.tsx`), `Cmd+K` and `Cmd+R` (`control-panel/index.tsx`).
In the main process, `hardening.ts` installs an application menu of standard roles —
`appMenu`, `fileMenu`, `editMenu`, `viewMenu`, `windowMenu` — which owns `Cmd+W` (Close
Window), `Cmd+0` / `Cmd+±` (page zoom), `Cmd+R` (reload, so that one is *already* double-bound),
`Ctrl+Cmd+F` (fullscreen) and `Cmd+M` (minimize). Menu accelerators are dispatched in the main
process and a renderer `keydown` cannot cancel them, so none of those are available to a
listener no matter what it does.

| Binding | Command |
|---|---|
| `Cmd+1` … `Cmd+9` | focus the nth pane in render order |
| `Cmd+Alt+←` / `→` | previous / next tab in the focused pane |
| `Cmd+Shift+←` / `→` | throw the focused pane's active tab into the neighbouring pane |
| `Cmd+Alt+M` | zoom the focused pane |
| `Esc` | leave zoom, when zoomed |
| `Cmd+Alt+P` | pin / unpin the active tab |
| `Cmd+Alt+W` | close the focused pane |

### Zoom

`zoomed: string | null` in the store, **not persisted** — the same treatment `widths` gets, and
for the same reason: it is a fact about right now. `commit` clears it when the pane is gone,
alongside the width pruning it already does.

`LayoutView` short-circuits: if `zoomed` names a live pane, render that pane alone. No tree
mutation and no size written, so leaving zoom restores the arrangement exactly rather than
approximately. A restore glyph appears at the end of the zoomed pane's strip — an exit that
exists only on the keyboard is a trap for whoever got there from the context menu.

## Tranche 3 — restructuring a pane

Three tree ops, all pure, all tested against the invariants:

- `closePane(root, paneId)` — closes every tab in it at once. Invariant 5 still holds: the last
  pane stays, empty.
- `flipSplit(root, splitId)` — row ↔ column, sizes carried across.
- `rotateSplit(root, splitId)` — reverse the children, sizes reversed with them.

Reached by right-clicking the strip's empty space, which is the filler `div` that already
exists there to carry the bottom rule. Plus `Zoom this pane`, so the zoom has a discoverable
home.

## Tranche 4 — saved named layouts

Own key, `kith-layouts`, `{ name: string; tree: Node }[]`, each entry validated with
`looksLikeLayout` on read and ignored if it fails.

**Saving** takes the live tree and strips chat tabs. Everything else is kept: panes, sizes,
places, non-chat tabs. A pane that held only chats comes back as an empty pane, which is the
intended affordance — a place ready to hold chats — and is why tranche 1's empty-pane rung
exists. What a named layout captures is a workspace *shape*, not a bookmark: a "reviewing"
layout that reopens the same three conversations is archaeology within a month.

**Loading** deep-re-mints every node `id` and preserves every `place`. Non-negotiable, for the
reason the whole "what is a place" section is about: a saved layout loaded twice, or loaded
next to a live tree that shares its ids, is the unrecoverable duplicate-id crash. Pinned tabs
whose place exists in the loaded layout open into it; the rest fall through to policy.

**The tension with the original design, stated rather than skirted.** The windowed shell spec
rejected multiple layouts: "One layout rather than one per project or per conversation. A
layout you have to rebuild every time you switch projects is worse than not having one — the
arranging becomes the work." That argument is about the app *choosing* a layout for you on
every context switch, which is still rejected. A named layout you switch deliberately is the
opposite: it is arranging done once and recalled. There is no automatic binding of a layout to
a project, a conversation or anything else, and adding one would re-create the thing that was
turned down.

UI is a small menu in the app header — Save current as…, the list, Delete — not a surface of
its own.

## Rejected, with reasons

- **Pin to a tree path** (`[0]`, `[1,0]`). No tree change, and stale after the first dock:
  inserting a pane at the root shifts every recorded index, silently, so every pin then points
  one column off. Brittle exactly where the app is most interactive, and meaningless across a
  saved layout of a different shape.
- **Pin beside a neighbour** (`{ anchorTabKey, edge }`). Also needs no tree change, and anchors
  are durable identity. But pins chain — pin A beside B, B beside A — and closing the anchor
  orphans everything pinned through it. "Beside Conversations" is also a worse model than "the
  left column": it describes a relationship the person did not make.
- **Pins inside each saved layout.** More faithful per layout, and it means a second copy of the
  same state per layout, pins lost with the layout that held them, and pins destroyed by
  `reset`. A pin is a preference about a kind of place, which is the same argument that put
  `placements` in its own key.
- **`Cmd+W` for close-tab.** Needs a `File` menu item with the accelerator, `Close Window`
  moved to `Cmd+Shift+W`, and IPC into the renderer. Worth doing and worth proposing on its own
  — smuggling a main-process menu change into a layout feature is how the menu ends up
  undocumented.
- **A `VERSION` bump for `place`.** Unnecessary, since `looksLikeLayout` accepts a pane without
  it, and actively harmful: a bump discards every stored arrangement.
- **Sorting pinned tabs at render.** Would leave the tree's indices and the drag layer's
  measured rects in different orders. See invariant 7.

## Testing

All vitest, no new dependency.

- `place.test.ts` — a pin honoured into an existing place; a homeless pin raising at its slot;
  a slot index clamped into a smaller root; a pin honoured into a pane too narrow to "fit"; the
  empty-pane rung; and a regression asserting unpinned placement is unchanged for all four
  modes.
- `tree.test.ts` — invariant 7 under `moveTab`, `dockTab`, `closeTab`, pin and unpin; no
  operation duplicates a place; `closePane`, `flipSplit`, `rotateSplit` against invariants 1–3;
  id reminting preserves `place` and produces no duplicate ids.
- `store.test.ts` — pins round-trip; junk and unknown entries ignored; a pin carried across
  `rename`; `zoomed` cleared when its pane dies; an orphaned `place` dropped by `unpin`; a saved
  layout round-trip; a v2 tree with no places still loads.
- `keys.test.ts` — the binding table as a table, plus both guards, plus an assertion that
  nothing in it collides with the four taken renderer combos or the menu's accelerators.
- `layout-view.test.tsx` — a pinned tab renders no close button; "close the others" leaves it;
  a zoomed pane renders alone and its restore glyph works.

## What the build needed that the design did not foresee

**`event.code`, not `event.key`, for every letter and digit.** With Option held, macOS puts the
*composed* character in `event.key` — Option+M is `"µ"`, Option+P is `"π"`, Option+W is `"∑"` —
so `key.toLowerCase() === "m"` never matches, on the one platform this ships as a desktop build
for, with no error anywhere to find. `code` names the physical key and is untouched by
modifiers or keyboard layout. Every existing shortcut in this app compares `key`, which is fine
for `Cmd+F` and `Cmd+S` and would have been a silent dead shortcut for all three of the
Cmd+Alt ones here.

**`Cmd+Ctrl+←/→` became `Cmd+Shift+←/→`.** "Primary" in this app means `metaKey || ctrlKey`,
which every other handler in the renderer already assumes — so a binding that *also* wanted
Ctrl as a secondary modifier could not be told apart from the primary on a machine without a
Command key. Shift is unambiguous under the same rule, and `Cmd+Shift+←/→` is only taken inside
text, where the editable guard has already bailed.

**Invariant 8, written down.** "A place names at most one pane" was in the design as a dedupe
pass; it belongs in `tree.ts`'s invariant list beside the other seven, and `isSound` in
`tree.test.ts` now checks it on every tree any test produces — which is how the property gets
asserted forty times instead of once.

**`commit` takes the pins it should normalise against.** `pin` and `unpin` change the pins *and*
the tree in one gesture. Without a `pinsNext` parameter they would have to `set` the pins, then
commit, and the normalisation inside `commit` would run against the pins as they were a moment
ago — putting the tab it had just pinned at the back of the strip.

**Zoom focuses what it zooms.** Not in the design, and wrong without it: the focused pane is
where the next tab opens, so zooming pane B while pane A is focused left new tabs landing in a
pane that was not on screen.

**`readLayouts` rejects an entry with duplicate ids inside it.** `remintIds` on load is the fix
for ids *colliding*; this is the earlier lock, and it is the same one `readStored` already
applies to the live tree. A saved entry that is internally broken should never be offered for
loading, because the crash it causes has already been established as unrecoverable without
clearing site data.

**An emptied last pane keeps its place.** `closePane` on the only pane leaves it on screen
(invariant 5). Dropping its `place` at the same time would strand every pin pointing at it in
the one situation where the pane is demonstrably still there.

**The layouts menu had to opt out of the window drag.** The header is `.window-drag-region`, so
a `pointerdown` reaching it moves the window rather than opening a menu. `header-controls.tsx`
records having fixed this once already for `Picker`; the new menu is built on the same shape
rather than a second one, `stopPropagation` included.

## Not doing

Floating or overlapping windows, and popping a pane out into its own Electron
`BrowserWindow` — both still rejected, for the reason the original design gave: on a laptop
screen, arranging becomes the work.

Automatically selecting a layout per project or per conversation. See tranche 4.

A user-facing name for a place. Places are opaque ids; the only named thing is a saved layout.
Naming panes is a second vocabulary to teach for no gain over "pin it and it stays there".
