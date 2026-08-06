# Making a large conversation cheap to open, switch away from, and scroll

Written 2026-08-06. Measured against conversation `20260803-110800342-734b7a`: 19,394
transcript lines, 473 turns, 12,724 parts, 6,385 tool calls, ~1.8M characters of text.

## What is actually slow, measured

| | |
|---|---|
| Server `timeline()` | 124 ms — **not** the bottleneck |
| Detail payload | **48.1 MB** (timeline 22.5 · entries 25.0 · messages 0.5) |
| Of that, unused by the open path | **25.6 MB (53%)** |
| Messages mounted at once, unvirtualized | 473, holding 12,724 parts |

Dev mode (`./run dev`) multiplies render cost — development React plus `StrictMode`
double-rendering — but it is **not** the cause. This was slow before any of it.

## Four causes, and they are separate

1. **53% of the payload is dead weight.** `read_conversation` returns the conversation three
   times: `timeline` (used), `messages` (flattened prose) and `entries` (the entire raw
   transcript). The client's own `ConversationDetail` type does not even declare `entries`,
   and `openConversation` reads only `detail.timeline`. The browser downloads and
   `JSON.parse`s 25.6 MB for nothing.

2. **Switching conversations remounts everything.** `setThreadKey(n => n + 1)` bumps a `key`
   on `AssistantRuntimeProvider`, so every switch unmounts all 12,724 part components and
   their effects before building the next set. This is why switching is slow in *both*
   directions. It exists only because `useLocalRuntime` accepts `initialMessages` once.

3. **Nothing is virtualized, and `content-visibility` does not substitute for it.** This is the
   important correction to an earlier reading of this problem. `content-visibility: auto` skips
   *layout and paint* for off-screen messages. It does **not** stop React mounting them, and it
   does not stop `MarkdownText` parsing and syntax-highlighting them. So all ~547 KB of prose
   across 1,031 text parts is parsed on open whether or not one character is visible. The
   optimisation is aimed at a cost that was never the bottleneck — which is why the app is slow
   despite it being there, and why only real virtualization (not mounting off-screen messages at
   all) addresses mount cost.

   Note what is *already* cheap, so it is not re-optimised by mistake: the 6,385 tool calls are
   grouped into collapsibles whose `CollapsibleContent` (Radix) unmounts its children when
   closed, so a collapsed run costs one trigger row, not one card per call. The expensive parts
   are the text parts, not the tool parts.

4. **`contain-intrinsic-size: auto 200px` makes the scroll land wrong.** Until a message is
   laid out the browser assumes 200px, so with 473 messages the initial `scrollHeight` is a
   ~95,000px guess against a much larger real height. "Scroll to bottom" scrolls to the bottom
   of that fiction, which lands near the top. `thread.tsx` already anticipated this and added
   `autoScroll` to re-correct; at this length the gap is too large to close. **This fixes
   itself** once fewer messages are mounted, because then the height is real.

## Order of work

**Revised 2026-08-06, after measuring.** An earlier version of this plan put
`useExternalStoreRuntime` first, because that was the option chosen when the question was framed
as "how far should lazy loading go". The measurements do not support that ordering: the runtime
swap removes the *remount on switch* and unblocks prepending, but it does **nothing** for mount
cost, which is what "opening a large chat takes ages" actually is. It is also the riskiest step
and the one that turns the latent `toolCallId` collision into a live bug. So it is now last.

Steps 1 and 2 are small, carry almost no risk, and address payload, mount cost, unmount cost and
the scroll-position bug together. Do them and re-measure before committing to 3 or 4.

Each step is independently shippable and independently verifiable.

### 1. Stop sending what nothing reads  (two lines, ~26 MB, no client change)

In `kith/api/routes/conversations.py::read_conversation`, drop `entries` and `messages` from
the response.

- **First**, `grep` for other consumers of `/api/conversations/<id>` — only the workspace path
  was verified. The desktop shell and any script may read them.
- `messageCount` must stay; the listing's `messages` count is a different field and the
  comment there explains why the two were confused before.
- Verify: `curl` the endpoint and check the payload drops to ~22.5 MB, and that opening a
  conversation still renders.

### 2. Window the initial render  (a slice and a button — no server or runtime change)

Hand the runtime only the last ~40 turns from `toThreadMessages`, with an explicit "load earlier"
that widens the window. This is the highest-value structural step per unit of risk: it cuts mount
cost, cuts unmount cost (fewer components to tear down on a `threadKey` bump), and fixes the
scroll landing, because the mounted height stops being a 200px-per-message fiction.

Re-measure after this. It may be enough, and steps 3 and 4 are considerably more work.

### 3. Server-side windowing  (`limit` / `before`)

Add query params to `read_conversation` and slice in `conversations.timeline()`.

- `limit` = turns to return, counting **back from the end** (default: current behaviour, all).
- `before` = return the `limit` turns immediately preceding this turn index.
- Return the total turn count so the client knows whether more exist.
- Turn indices must stay stable and absolute, because checkpoints match on them
  (`checkpoints.for_conversation` returns `turnIndex` compared against `s.message.index`).
  A window must not renumber turns, or "Restore files to here" silently targets the wrong one.
- Verify: window of 40 returns turns 434–473 with `turnIndex` still 434–473, not 0–39.

### 4. Virtualize the message list  (the only real fix for mount cost)

- The blocker is that `ThreadPrimitive.Messages` owns iteration. Check whether the installed
  `@assistant-ui/react` exposes a virtualized variant or `unstable_useThreadMessageIds`
  (it is exported) to drive a custom windowed list.
- Once mounted count is bounded, revisit `contain-intrinsic-size` — with virtualization the
  estimate matters less, and it may be removable, which also simplifies `scrollToRange` in
  `lib/thread-find.ts` (it currently double-scrolls a frame apart purely to correct for it).

### 5. `useLocalRuntime` → `useExternalStoreRuntime`  (last: highest risk, no mount-cost win)

This is the risky step. It removes cause 2 and unblocks scroll-up loading.

- Workspace owns the message array as state; switching conversations becomes `setState`, not a
  key bump. Delete `threadKey`.
- Prepending older turns becomes an array operation.
- **The live streaming path must keep working through this.** `createBackendAdapter` is a
  `ChatModelAdapter` for the local runtime; the external store needs its own `onNew` and the
  streaming updates routed into the store.
- Two known landmines, both already documented in the code:
  - **`toolCallId` uniqueness.** `toThreadMessages` uses a conversation-wide counter
    (`call-${n}`) and the live path uses `live-${tool.id}`, which restarts at `c1` every turn.
    `@assistant-ui/core`'s `ToolInvocationTracker` keys a **thread-global** map on
    `toolCallId` and *is* wired into the external-store runtime (it is not used by the local
    runtime). So duplicates that are currently harmless become real: one message's tool result
    lands on another's entry. Give the live path a monotonic counter in the adapter closure
    before this step, not after.
  - `USAGE_PART` data parts and the `ContextMeter` read `s.thread.messages`; check they still
    resolve under the external store.
- Verify: switch between two long conversations and confirm no full unmount (React DevTools),
  streaming still renders, tool cards still pair with their results.

## Do not regress these

- **Turn indices are load-bearing** for checkpoint restore (step 2).
- `scrollToRange` must keep `behavior: "instant"` — the viewport sets `scroll-smooth`, and
  animating to a distant match lays out every message in between. That was a real freeze.
- Find-in-chat (`lib/thread-find.ts`) reads the rendered DOM under
  `[data-slot="aui_message-group"]`. **Virtualization breaks it**: unmounted messages have no
  text nodes, so matches outside the window vanish. Find will have to search the message data
  and map to DOM only for what is mounted — which reintroduces the markdown-offset problem
  that reading the DOM was chosen to avoid (`**check**point` is one word on screen, two runs
  in the source). Budget for this in step 4; it is not incidental.

## Next: sidebar and the new-chat transition

Raised 2026-08-06, after steps 1 and 2 shipped. Not started.

### Check this first, before building anything

"New chat feels slow" is almost certainly not `newConversation` — it only resets state. It is the
`threadKey` bump tearing down every mounted message and its effects before an empty thread
renders. Step 2 cut that from 473 messages to 40, so **re-measure before adding a loading
state**: a spinner over an action that is now fast makes a fast action look slow, and it hides
the real fix rather than being one.

If it is still slow, the cause is the remount itself and the fix is step 5
(`useExternalStoreRuntime`), where switching a conversation becomes a state update instead of a
remount. A loading affordance is then optional polish, not a remedy.

### Group the session list by project

`history-panel.tsx` lists conversations flat, newest first, clamped to 500 with search as the way
past that.

- The data is already there: `ConversationSummary.projectId`, and `fetchBrain()` gives project
  names and status (`session-bar.tsx` already pairs them this way).
- Group by `projectId`, with an "No project" group for unbound sessions — that is a real
  category, not an empty state, and the same reasoning `session-bar.tsx` documents applies.
- A closed project should say so in its group header, for the reason `session-bar.tsx` gives:
  being bound to a closed project is *why* nothing is happening in those sessions.
- Keep search working across groups. It currently searches transcripts server-side
  (`conversations.search`), which is one hit per conversation by design; grouping must not make
  that read as "one hit per group".
- Watch the cost: that search already re-reads every transcript per query (29 MB at present, ~100
  ms). Grouping does not worsen it, but it is the next thing to bite as transcripts grow.

### Sidebar polish

Deliberately vague here because it needs someone looking at it, not a spec written blind. Known
concrete items:

- The list is clamped to 500 and says "Showing the {MAX} most recent" — with grouping that line
  needs to make sense per group or move.
- `kith-fade-bottom` is already used to stop the list being cut mid-row; check it still lands
  correctly with group headers in the scroll region.
