# A windowed shell, and a chat that opens instantly

**Status:** approved 2026-09-04. Three tranches, built in order.

## Why

Two things asked for together, and the second cannot be built well without the first.

**The shell.** Kith's layout is three fixed slots — Conversations (256px, overlays when
tight), Chat (560px floor), Work (320–720, resizable) — plus five surfaces that take over the
whole screen instead of sharing it: Control Panel, Settings, File Viewer, Inbox, Context
Detail. The yielding between the three is hand-rolled against three pixel constants in
`ui/src/components/shell/workspace.tsx`, a 54KB file that also owns routing, the assistant
runtime, timeline windowing and scroll anchoring. Wanted instead: tiling panes you dock by
dragging a tab to an edge, with chats as tabs so several conversations can be open at once.

**The lag.** Opening a conversation hangs the app. Measured on the largest real conversation
(`20260803-110800342-734b7a`):

| | |
|---|---|
| Turns in the timeline | 492 (12,957 parts) |
| JSON the server sends | **22.83 MB** |
| Server time to build it | 121 ms |
| What is actually rendered | last 40 turns — 901 parts, **1.06 MB** |

95% of the payload is downloaded, `JSON.parse`d and held in React state to render nothing.
`WINDOW = 40` is applied client-side *after* the whole thing has arrived, and
`setTimeline(detail.timeline)` keeps all 492 turns in state so `loadEarlier` can slice
locally. The server is not the problem — 121 ms to build. The wire and the browser are.

Someone has cut this once already: the endpoint's own comment records removing `messages` and
`entries` to save 25.6 MB of a 48.1 MB response. `timeline` itself was left whole.

Tabs multiply it. Three chat tabs on the current path is ~68 MB held in the renderer. So the
chat-open fix is not an improvement bundled alongside the feature; it is load-bearing for it.

## Tranche 1 — the chat-open path

No layout change. One new dependency. Measurable before and after.

### Server

`conversations.timeline()` stays exactly as it is — `services/checkpoints.py` needs every turn
to map a checkpoint onto a turn index, and quietly windowing underneath it would silently
misalign restores.

A new `conversations.timeline_window(id, *, turns, before)` calls it and slices:

```python
{"turns": [...], "total": 492, "start": 452, "hasMore": True}
```

`start` is the index of the window's first turn in the whole list, so the previous page is
`before=start`. Building all and slicing is deliberate: 121 ms of server time to save 21 MB of
wire is a trade with no downside worth the complexity of reading the file backwards, and turn
boundaries are computed by the same forward pass anyway.

`GET /api/conversations/<id>` takes `?turns=` and `?before=`. **The default is windowed** at 40,
not whole. `turns=0` asks for everything. A default that ships 22 MB is the bug; leaving it in
place for compatibility would let the next caller re-create it, and there is exactly one
caller.

### Client

- `fetchConversation(id, {turns, before})`.
- `openConversation` switches `conversationId` and shows a skeleton **before** awaiting
  anything. Today it awaits the whole payload before changing any state, which is why the
  click does nothing at all — a missing state, not a missing spinner.
- The full timeline stops living in React state. What is held is the loaded window, its
  `start`, and the total.
- `loadEarlier` fetches the previous page and prepends, instead of re-slicing a 492-turn array.
- Code-split the screens that are not the chat out of the 2.37 MB entry chunk.

### Virtualization: deliberately not done, and why

The plan said virtualize the thread viewport with `@tanstack/react-virtual`. On reading
`@assistant-ui/react` it turns out the only supported way in is
`ThreadPrimitive.Unstable_MessageById` with `unstable_useThreadMessageIds` — the library
documents this as "the shape needed to drive a virtualized list" and marks it
`@deprecated Unstable / Experimental - may change in any release`.

That is a bad trade *now*, because the thing it would fix is already capped. The window mounts
40 turns, which the existing comments record as the deliberate ceiling — a 473-turn thread was
~547 KB of prose to parse before anything appeared, and 40 turns is roughly a twelfth of that.
The measured hang was the 22.83 MB payload and the 2.37 MB entry chunk, and both are fixed.
Putting the chat's central render path on an API the library says may change in any release, to
speed up something that is no longer the bottleneck, buys a small win and an ongoing liability.

Revisit if the window is ever raised above 40, or if the API stabilises. No dependency added.

### Dependencies

None. `@tanstack/react-virtual` was planned and is not needed — see above.

### Done when

- The endpoint returns a bounded payload at the default, asserted by a test. ✔
- The client never asks for the whole conversation, asserted by a test. ✔
- `./check` green. ✔
- The measurement above is re-run and reported. ✔ **22.83 MB → 1.06 MB, 21.5×, 95.3% less.**
  Entry chunk **2,374 kB → 1,326 kB**, gzip 739 → 428 kB.

## Tranche 2 — the tiling shell

Chat stays single. Surfaces stop taking over.

### The layout tree

In `zustand` (already a dependency):

```ts
type Node =
  | { kind: "split"; direction: "row" | "column"; children: Node[]; sizes: number[] }
  | { kind: "pane"; tabs: TabRef[]; active: number }

type TabRef =
  | { surface: "chat"; conversationId: string }   // the only one that repeats
  | { surface: "work" | "roadmap" | "files" | "journal" | "memories"
             | "projects" | "conversations" | "settings" }
```

`split` maps 1:1 onto `PanelGroup` / `Panel` / `PanelResizeHandle`. A tree rather than a flat
list because "drag a tab to an edge and it splits there" *is* inserting a split node at that
position — a flat list cannot nest, and nesting is what makes the fourth pane fit rather than
squeeze. Every surface but chat is a singleton: opening one twice focuses the existing tab.

### The surfaces

The five takeovers stop taking over. The Control Panel's internal tabs — Work, Projects,
Roadmap, Journal, Memories, Files, Sources, Schedules — become dockable surfaces in their own
right. The URL still names what you are looking at; **layout is not in the URL**. `pathForTask`
and friends focus-or-open within the current layout instead of replacing the screen, so deep
links keep working and dock instead of taking over.

### The yielding constants go

`CHAT_FLOOR`, `WORK_YIELDS_BELOW`, `HISTORY_YIELDS_BELOW` and the overlay behaviour are
deleted. Their intent — do not squeeze prose to three words a line — becomes a per-surface
`minWidth` the library enforces, plus one rule: when a split cannot honour every child's
minimum, the least-recently-focused pane collapses to a tab rail rather than every pane
getting narrower.

### Persistence

One layout, versioned, in `localStorage`. Not per-project and not per-conversation: a layout
you must rebuild on every project switch is worse than none. A reset action, and an unreadable
or newer-versioned layout falls back to the default rather than white-screening.

### Dependency

`react-resizable-panels@^4.12.3` — React 18/19 peer. **Its real bundle delta is measured with
`./check ui` before it is committed to**; 553 KB unpacked is ESM + CJS + maps, not the shipped
cost, and an earlier "~10 KB" estimate in conversation was unfounded. If the measured delta
does not justify it, hand-rolling stays on the table.

Explicitly rejected, with reasons: **dockview** (declares no React peer range at all — not a
bet worth making on React 19), **flexlayout-react** (0.x, owns its styling, 1.8 MB),
**rc-dock** (alpha), **react-mosaic-component** (tiling only, no tabs), **virtua** (25× the
size of react-virtual, carries Angular/Vue/Svelte/Solid peers), **@dnd-kit** (1.07 MB to avoid
~150 lines of pointer events against drop rects whose geometry we already own).

## Tranche 3 — chat tabs

### ChatSession

Today `workspace.tsx` holds one `conversationId`, one runtime and one `resumed` array as
component state. Each open chat tab needs its own: runtime, paginated window, scroll anchor.
Created on open, disposed on close, held in a zustand map keyed by conversation id.

This is why the shell is not a docking framework's job. A chat tab is not a passive panel; it
owns a live runtime.

### Background tabs — narrower than specified

`/api/events` is a single process-wide `EventSource` that invalidates by key — already
consolidated down from two sources and eleven timers. So a background chat tab learns its turn
finished without holding a connection of its own; it shows a dot on its tab. Only visible tabs
mount a thread, and a background tab that has not been looked at drops its window and refetches
on focus.

That, plus tranche 1, is what stops N tabs costing N × 22 MB.

**As built, an inactive tab unmounts entirely** rather than staying mounted and subscribed. A
pane renders only its active tab, so a background chat holds no runtime, no window and no
listeners at all — better for memory than what was specified, and it makes switching a cache
read rather than a fetch. What it costs is the dot: a background tab cannot show that its turn
finished, because nothing of it is running to notice. Worth adding, and it wants a
subscription that outlives the component rather than a mounted-but-hidden pane.

### Breaking up workspace.tsx

`shell/layout/` (tree, store, renderer, drag-to-dock), `chat/session.ts` (ChatSession), and a
`workspace.tsx` that mounts the layout and nothing else. Not speculative: per-tab sessions are
impossible while the session is component state in one 54 KB file.

## Testing

- **vitest** over the layout tree: split, dock, close, collapse-to-rail, persistence round
  trip, version fallback.
- **vitest** over ChatSession: open, background, focus, dispose.
- **pytest** over the endpoint: bounded payload at the default, `before` pagination returns the
  adjacent page with no gap and no overlap, `turns=0` still returns everything for anything
  that needs it.
- **vitest** that opening a conversation never places the full timeline in state.
- **playwright** (already a devDependency): open the 492-turn conversation, assert the thread
  paints.

## What tranche 3 actually needed that the design did not foresee

Splitting one runtime into one per pane broke everything *outside* a pane that had been reading
it, and both failures were the same shape: a hook that throws during render, taking its whole
subtree down.

`DropZone` listens on the window — it must, because without a `preventDefault` a dropped file
stays a navigation to a `file://` URL and the desktop shell opens it in Preview. It called
`useComposerRuntime()` from above every provider. The focused chat publishes its composer into
`lib/active-composer` instead, read at drop time, so the drop is still swallowed when no chat is
open and simply attaches to nothing.

The Work panel's context meter read `thread.messages` and `thread.isRunning` off the runtime.
It publishes through `lib/focused-chat` now — two facts, not a handle on the runtime, because
handing out the runtime would let anything outside a pane reach into a conversation it is not
in, which is the coupling the split was for.

One more the browser found: a new tab opened into whichever pane had focus, and clicking a
conversation focuses the *sidebar*, so the chat landed in a 240px column beside the list it came
from. `paneFor` groups a new tab with a pane already holding its kind, which is what makes the
second conversation open as a tab beside the first.

## Not doing

Floating overlapping windows, and popping panes out into separate Electron `BrowserWindow`s.
Both were considered and set aside: on a laptop screen, arranging becomes the work. Tiling was
chosen precisely because the app decides how things fit.
