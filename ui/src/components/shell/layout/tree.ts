/**
 * The layout, as a tree, and the handful of operations that move it.
 *
 * Pure functions over a plain value — no React, no store, no DOM. Everything that can be got
 * wrong about a tiling layout is in here (a split left holding one child, a tab closed out of
 * the pane that was focused, sizes that no longer sum), and none of it is worth debugging
 * through a drag gesture. The store and the renderer are thin on purpose.
 *
 * **A tree rather than a list of panes.** "Drag a tab to an edge and it splits there" *is*
 * inserting a split node at that position, and a flat list cannot express the nesting that
 * produces — which is what makes the fourth pane fit instead of squeezing the other three.
 *
 * **Invariants, held by every operation here and asserted in the tests:**
 *
 * 1. A split has two or more children. One child is not a split, it is that child, and a split
 *    that keeps a single child is how you end up with a divider that has nothing on one side.
 * 2. `sizes` has one entry per child and sums to 100.
 * 3. Every pane's `active` points at a tab that exists, or the pane has no tabs.
 * 4. A tab key appears at most once in the whole tree. Chat tabs are keyed by conversation, so
 *    two chats are two tabs and the same chat opened twice is one.
 * 5. The root is always a node. An empty layout is an empty pane, never `null` — a renderer
 *    that has to handle "no tree" grows a second empty state that is only reachable by bug.
 * 6. Every tab's `surface` is a key of `SURFACES`. Unstated while the union was one-to-one with
 *    that record; written down now that `"plugin"` covers many surfaces, because a rule enforced
 *    only by a union type stops being enforced the moment the union opens.
 * 7. Pinned tabs occupy a prefix of every pane's `tabs`. Held by `orderPinned`, which the store
 *    runs from `commit` — this module knows nothing of which tabs are pinned, so the rule
 *    arrives as a predicate rather than being clamped inside `moveTab`. Enforced in the array
 *    and not at render, because the drag layer measures `[data-tab]` rects and assumes array
 *    order is visual order.
 * 8. A place names at most one pane. No operation here can break this — see `dedupePlaces` for
 *    why, and for what happens to a tree that arrived already broken.
 */

/** Everything that can be a tab. `chat` is the only one that repeats. */
export type SurfaceId =
  | "chat"
  | "work"
  | "board"
  | "settings"
  | "context"
  /* Every plugin surface, under one member.
   *
   * Not `SurfaceId = string`, and not a member per plugin. Widening to `string` produces **zero**
   * compile errors at the four unguarded `SURFACES[...]` derefs — `tsconfig.app.json` has no
   * `strict` — and all four sit in `LayoutView`'s own render tree, above every per-surface
   * `ErrorBoundary`, so an unknown id there takes the whole window rather than one pane. A member
   * per plugin fails `looksLikeLayout` for an uninstalled one, which propagates to the root and
   * silently replaces the person's entire arrangement with the default.
   *
   * One static member keeps every deref safe by construction, and which plugin a tab shows lives
   * in the fields below instead. */
  | "plugin";

/** A tab, and the two different identities it has.
 *
 * `uid` is *this tab*, minted once and never changed — including by the rename that gives a
 * draft chat its conversation. It is what React keys on, and that is the whole reason it
 * exists: keying on `conversationId` meant the pane unmounted and remounted the moment the
 * first turn named the chat, which is mid-reply, discarding the streaming runtime and blanking
 * to a skeleton. The comment beside that key claimed the opposite was true.
 *
 * `tabKey(ref)` is *what the tab shows*, which is how "is this conversation already open"
 * is answered and why every surface but chat is a singleton. Two identities because they answer
 * two questions, and a draft chat is exactly the case where they diverge. */
export type TabRef =
  | {
      surface: "chat";
      conversationId: string;
      plugin?: undefined;
      view?: undefined;
      instance?: undefined;
      uid?: string;
    }
  | {
      surface: "plugin";
      /** The plugin's id, which is also its folder name and its tool namespace. */
      plugin: string;
      /** Which of its surfaces. */
      view: string;
      /** Set only for a surface declaring `instances: "many"`. */
      instance?: string;
      /** Which conversation this surface is about — see `BOUND`. */
      conversationId?: string;
      uid?: string;
    }
  | {
      surface: Exclude<SurfaceId, "chat" | "plugin">;
      /** Which conversation this surface is about — see `BOUND`. Unset on `board` and
       *  `settings`, which are about the app rather than about a chat. */
      conversationId?: string;
      plugin?: undefined;
      view?: undefined;
      instance?: undefined;
      uid?: string;
    };

export type PaneNode = {
  kind: "pane";
  id: string;
  tabs: TabRef[];
  active: number;
  /** Which *place* this pane is, if anything has been pinned into it — durable identity, where
   *  `id` is only an address.
   *
   * `id` cannot carry this. `nextId` mixes a per-load token in precisely so a new id can never
   * look like a stored one, and a pane dies the moment its last tab closes. A pin pointing at
   * an id would break in the one case somebody would notice: close the pinned tab, reopen it,
   * land somewhere else. So a place is minted once, never re-minted, and survives the reminting
   * that a loaded layout does to every `id` in the tree.
   *
   * Optional, which is what keeps `VERSION` at 2: `looksLikeLayout` asks a pane for `id`,
   * `tabs` and `active`, so a tree stored before places existed reads as a tree with no
   * places — correct, and a version bump would have thrown every stored arrangement away. */
  place?: string;
};
export type SplitNode = {
  kind: "split";
  id: string;
  direction: "row" | "column";
  children: Node[];
  sizes: number[];
};
export type Node = PaneNode | SplitNode;

/** Which side of a pane a tab was dropped on. `center` means "into its tab strip". */
export type Edge = "left" | "right" | "top" | "bottom" | "center";

/** A tab's identity, and the reason a chat can repeat while nothing else can.
 *
 * Keyed by conversation rather than by position, so a tab survives being moved, and so opening
 * a conversation that is already open focuses it instead of making a second copy of a live
 * runtime. */
/**
 * The surfaces that are *about* a conversation rather than about the app.
 *
 * Every one of these already took a `conversationId` — they just took whichever chat happened
 * to be focused, worked out in `Workspace` and handed down. That inference is what let a click
 * inside the Work pane re-elect a different conversation and point the Fold button at it; the
 * fix at the time made the inference sticky, which narrowed the bug without removing what
 * caused it. Naming the conversation on the tab removes it: a Work tab is about the chat it was
 * opened from, for as long as it is open, whatever you click next.
 *
 * `board` and `settings` are deliberately absent — they are about Kith, not about a chat.
 */
export const BOUND: ReadonlySet<SurfaceId> = new Set(["work", "context", "plugin"]);

/**
 * The surfaces that are not panes at all, and cannot be put in this tree.
 *
 * Board and Settings cover the window — `fixed inset-0 z-30`, both of them — because their own
 * layouts do not fit a pane: at the widths their `minWidth` allows, their content columns come up
 * short and each shortfall becomes a horizontal scrollbar rather than a squeeze. That was decided
 * and written down (see `pane-surfaces.test.tsx`), but they were left registered as tabs anyway,
 * so the model said "pane" while the paint said "window".
 *
 * You could see the disagreement. Opening one added a tab, the pane drew a strip and a
 * pane-scoped loading veil for as long as the lazy chunk took, and *then* the thing covered the
 * screen — a tab flashing into existence and vanishing, every time. They also sat in the strip
 * and held a pane that nothing could ever render into.
 *
 * So the route opens them now and the tree never sees them. Kept in `SurfaceId` and `SURFACES`
 * deliberately: that vocabulary is what `surfaceFor` and the layout's own tests are written
 * against, and narrowing a union to say something the store already enforces would be a hundred
 * edits to the layout engine for no behaviour. `open` refuses them, and `withoutSurfaces` takes
 * them out of anything that arrives holding one.
 */
export const TAKEOVERS: ReadonlySet<SurfaceId> = new Set(["board", "settings"]);

export function tabKey(ref: TabRef): string {
  if (ref.surface === "chat") return `chat:${ref.conversationId}`;
  /* `@<conversation>` for a bound surface, which is what makes Work-for-this-chat and
   * Work-for-that-chat two tabs rather than one tab that changes its mind. Invariant 4 is
   * unchanged and now does more: the same surface for the same conversation is still a
   * singleton, so asking for it twice focuses the one already open. */
  const bound =
    BOUND.has(ref.surface) && ref.conversationId ? `@${ref.conversationId}` : "";
  /* `plugin:<id>/<view>` — the namespace prefix and the plugin/view boundary use different
   * separators, so neither is ambiguous, and a plugin id contains neither `/` nor `#`.
   *
   * Collision with a built-in is impossible because no built-in id contains a colon, and
   * collision between plugins is impossible because an id *is* a folder name. `#<instance>` is
   * what lets a surface declaring `instances: "many"` be open more than once while everything
   * else stays a singleton by construction — which is invariant 4, unchanged. */
  if (ref.surface === "plugin") {
    const base = `plugin:${ref.plugin}/${ref.view}`;
    return (ref.instance ? `${base}#${ref.instance}` : base) + bound;
  }
  return ref.surface + bound;
}

let counter = 0;
/** A token unique to this page load, mixed into every id this session mints.
 *
 * **Without it the app crashes on the second run.** The counter is module state and starts at
 * zero on every load, while a stored layout comes back holding the ids it was *saved* with — so
 * the first pane created after a reload was `pane-1`, which the restored tree already contained.
 * `react-resizable-panels` refuses duplicate panel ids by throwing during render, below
 * `LayoutView`, so it unwound to the top-level boundary and the whole window became the crash
 * panel; and because `commit` writes before it sets, the colliding tree was already in
 * `localStorage`, so "Try again" and "Reload" both restored the same broken layout. Recovery
 * meant clearing site data by hand.
 *
 * Non-sibling collisions were quieter and worse: `replacePane` runs its change on every id
 * match, so one `activate` set two panes active and one drop put the same tab in two places. */
const session = Math.random().toString(36).slice(2, 8);

/** Ids address a node in an operation and key it in React. They are not persistence identity —
 *  a reloaded layout keeps whatever ids it was saved with, which is exactly why a new one must
 *  never be able to look like an old one. */
export function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-${session}-${counter}`;
}

/** Every node id in the tree, for the uniqueness check a stored layout has to pass. */
export function ids(node: Node): string[] {
  return node.kind === "pane" ? [node.id] : [node.id, ...node.children.flatMap(ids)];
}

export function pane(
  tabs: TabRef[] = [],
  active = 0,
  id = nextId("pane"),
  place?: string,
): PaneNode {
  const made: PaneNode = { kind: "pane", id, tabs: tabs.map(withUid), active: clampActive(tabs, active) };
  // Set rather than always present, so a pane with no place serialises without the key and a
  // stored tree round-trips byte-identical to one written before places existed.
  return place ? { ...made, place } : made;
}

/** Give a tab its permanent identity if it does not have one — a layout stored before `uid`
 *  existed, or a caller building a ref by hand. */
export function withUid(ref: TabRef): TabRef {
  return ref.uid ? ref : { ...ref, uid: nextId("tab") };
}

export function split(direction: "row" | "column", children: Node[], sizes?: number[]): SplitNode {
  return {
    kind: "split",
    id: nextId("split"),
    direction,
    children,
    sizes: sizes ?? evenSizes(children.length),
  };
}

function evenSizes(count: number): number[] {
  if (count <= 0) return [];
  const each = 100 / count;
  // The last one absorbs the rounding so the total is exactly 100 rather than 99.99999.
  return Array.from({ length: count }, (_, i) =>
    i === count - 1 ? 100 - each * (count - 1) : each,
  );
}

function clampActive(tabs: TabRef[], active: number): number {
  if (!tabs.length) return 0;
  return Math.max(0, Math.min(active, tabs.length - 1));
}

/** Every pane in the tree, in render order. */
export function panes(node: Node): PaneNode[] {
  return node.kind === "pane" ? [node] : node.children.flatMap(panes);
}

/** Where a tab is, or null. */
export function findTab(node: Node, key: string): { pane: PaneNode; index: number } | null {
  for (const one of panes(node)) {
    const index = one.tabs.findIndex((tab) => tabKey(tab) === key);
    if (index >= 0) return { pane: one, index };
  }
  return null;
}

/** Rebuild the tree with one pane replaced by whatever `change` returns.
 *
 * Returning `null` removes the pane, and removing a pane is where the invariants earn their
 * keep: the split above it loses a child, and a split with one child left has to *become* that
 * child rather than stay a split with a divider against nothing. */
function replacePane(node: Node, id: string, change: (found: PaneNode) => Node | null): Node | null {
  if (node.kind === "pane") return node.id === id ? change(node) : node;

  const children: Node[] = [];
  const sizes: number[] = [];
  node.children.forEach((child, index) => {
    const next = replacePane(child, id, change);
    if (next === null) return;
    children.push(next);
    sizes.push(node.sizes[index] ?? 0);
  });

  if (children.length === 0) return null;
  // Invariant 1. Also the reason a deeply nested close does not leave a chain of one-child
  // splits behind it: this collapses on the way back up, at every level.
  if (children.length === 1) return children[0];
  if (children.length === node.children.length) return { ...node, children, sizes };
  // A child went away, so the rest share its space in proportion to what they already had.
  return { ...node, children, sizes: normalise(sizes) };
}

/** Scale to sum 100, or fall back to even when there is nothing to scale. */
function normalise(sizes: number[]): number[] {
  const total = sizes.reduce((sum, one) => sum + one, 0);
  if (!sizes.length) return [];
  if (total <= 0) return evenSizes(sizes.length);
  const scaled = sizes.map((one) => (one / total) * 100);
  const head = scaled.slice(0, -1);
  return [...head, 100 - head.reduce((sum, one) => sum + one, 0)];
}

/** Open a tab, or focus it if it is already somewhere.
 *
 * `into` is the pane to put it in when it is genuinely new — the focused one, normally. A
 * missing or unknown pane falls back to the first, because "open the roadmap" should never be
 * answered with nothing happening. */
export function openTab(
  root: Node,
  ref: TabRef,
  into?: string,
): { tree: Node; paneId: string; focused: boolean } {
  const key = tabKey(ref);
  const found = findTab(root, key);
  if (found) {
    // Invariant 4: already here, so focus rather than duplicate. This is what makes every
    // surface but chat a singleton without a second rule saying so.
    const tree = replacePane(root, found.pane.id, (one) => ({ ...one, active: found.index }));
    return { tree: tree ?? root, paneId: found.pane.id, focused: true };
  }
  const all = panes(root);
  const target = all.find((one) => one.id === into) ?? all[0];
  if (!target) {
    const made = pane([ref]);
    return { tree: made, paneId: made.id, focused: false };
  }
  const tree = replacePane(root, target.id, (one) => ({
    ...one,
    tabs: [...one.tabs, withUid(ref)],
    active: one.tabs.length,
  }));
  return { tree: tree ?? root, paneId: target.id, focused: false };
}

/** Close a tab wherever it is. An emptied pane goes with it, unless it is the only pane. */
export function closeTab(root: Node, key: string): Node {
  const found = findTab(root, key);
  if (!found) return root;
  const onlyPane = panes(root).length === 1;
  const tree = replacePane(root, found.pane.id, (one) => {
    const tabs = one.tabs.filter((tab) => tabKey(tab) !== key);
    // Invariant 5: the last pane stays, empty. Something has to be on screen, and an empty
    // pane with an "open something" state is a better answer than a blank window.
    if (!tabs.length && !onlyPane) return null;
    /* Invariant 3, and only when it has to move.
     *
     * This recomputed `active` from the *closed* tab's index unconditionally, so closing a
     * background tab changed what the pane was showing — click the X on a tab you are not
     * reading and the pane switches to something else. `dockTab` inherits the same call, so
     * dragging a background tab out yanked the pane's view along with it.
     *
     * Which tab you are reading is identity, not position: it is found again by `uid` after
     * the removal, and only a close of the active tab has to choose a new one. */
    const wasActive = one.tabs[one.active];
    const stillThere = tabs.findIndex((tab) => tab.uid === wasActive?.uid);
    const active =
      stillThere >= 0 ? stillThere : clampActive(tabs, Math.min(found.index, tabs.length - 1));
    return { ...one, tabs, active };
  });
  return tree ?? pane();
}

/** Move a tab onto a pane — into its tab strip, or splitting it on one side.
 *
 * Dropping a tab back where it already is returns the tree unchanged rather than doing the
 * removal and the insertion, which would renumber `active` and make a no-op drag look like a
 * reorder. */
export function dockTab(root: Node, key: string, targetPaneId: string, edge: Edge): Node {
  const found = findTab(root, key);
  if (!found) return root;
  const ref = found.pane.tabs[found.index];

  if (edge === "center" && found.pane.id === targetPaneId) return root;
  // A pane holding one tab, dropped on its own edge, would split against itself: the source
  // pane empties and is removed, and the target it was splitting no longer exists.
  if (found.pane.id === targetPaneId && found.pane.tabs.length === 1) return root;

  const without = closeTab(root, key);
  const stillThere = panes(without).some((one) => one.id === targetPaneId);
  if (!stillThere) {
    // The target was the source and it went away with the last tab. Nothing to dock onto, so
    // the move is a no-op rather than a lost tab.
    return root;
  }

  if (edge === "center") {
    return openTab(without, ref, targetPaneId).tree;
  }

  const direction = edge === "left" || edge === "right" ? "row" : "column";
  const before = edge === "left" || edge === "top";
  const tree = replacePane(without, targetPaneId, (one) => {
    const made = pane([ref]);
    return split(direction, before ? [made, one] : [one, made]);
  });
  return tree ?? without;
}

/** Move a tab to a final position in a pane's strip — a reorder, or a move into another pane.
 *
 * One operation for the two things a drop on a tab strip can mean. Dropping on the *pane* was
 * already docking; dropping on the *strip* used to be a no-op on its own pane and an append on
 * every other, which is why tabs could not be sorted: the strip is where the order lives, and
 * it accepted nothing but "go to the end".
 *
 * `to` is where the tab should end up counting the strip as it should look *after* the move,
 * clamped into range — not "insert before the tab now at index `to`", which is what the caret
 * shows. Removing the dragged tab shifts every later tab left by one, so the caller translates
 * the caret (`landingIndex` in `drag.ts`); an operation that took the caret position
 * directly would drag every later drop one slot toward the front, exactly as far as it is
 * subtle.
 *
 * A move that ends where it started returns the tree unchanged, for the same reason a no-op
 * dock does: a drag there and back must not renumber `active` and look like a reorder. */
export function moveTab(root: Node, key: string, paneId: string, to: number): Node {
  const found = findTab(root, key);
  if (!found) return root;

  if (found.pane.id === paneId) {
    const from = found.index;
    const at = Math.max(0, Math.min(to, found.pane.tabs.length - 1));
    if (at === from) return root;
    return (
      replacePane(root, paneId, (one) => {
        const tabs = one.tabs.slice();
        const [moved] = tabs.splice(from, 1);
        tabs.splice(at, 0, moved);
        /* The pane keeps showing what it was showing. The active tab is found again by `uid`
         * after the move — its index changed, its identity did not — the rule `closeTab`
         * already paid for: a background tab dragged across the strip must not change what the
         * pane is showing, and dragging the active one must not take focus somewhere else. */
        const wasActive = one.tabs[one.active];
        const active = tabs.findIndex((tab) => tab.uid === wasActive?.uid);
        return { ...one, tabs, active: active >= 0 ? active : clampActive(tabs, one.active) };
      }) ?? root
    );
  }

  /* Another pane's strip. Close it out of where it is — which may collapse its pane — then put
   * it where the pointer was rather than at the end, which is the whole point of aiming. The
   * moved tab activates, as it does when it docks or opens: you aimed it at that pane. */
  const without = closeTab(root, key);
  const stillThere = panes(without).some((one) => one.id === paneId);
  if (!stillThere) {
    // The target was the source and it went away with the last tab. Nothing to move onto, so
    // the move is a no-op rather than a lost tab — the same answer `dockTab` gives.
    return root;
  }
  const ref = found.pane.tabs[found.index];
  return (
    replacePane(without, paneId, (one) => {
      const at = Math.max(0, Math.min(to, one.tabs.length));
      const tabs = one.tabs.slice();
      tabs.splice(at, 0, withUid(ref));
      return { ...one, tabs, active: at };
    }) ?? without
  );
}

/** Open a tab in a pane of its own, split off an anchor.
 *
 * The placement answer when the person has *asked* for a new pane — the `beside` placement —
 * and never the app's own initiative: an open that rearranges the layout on its own is the app
 * working against the arrangement already on screen. A missing or unknown anchor falls back to
 * the first pane, and a tree with no panes at all becomes the pane — "open it" must never be
 * answered with nothing happening, the same rule `openTab` holds. */
export function openBeside(
  root: Node,
  ref: TabRef,
  anchorId: string,
  edge: Exclude<Edge, "center"> = "right",
): { tree: Node; paneId: string } {
  // Built outside the closure so the caller can have its id: the pane is what focus moves to.
  const made = pane([ref]);
  const all = panes(root);
  const anchor = all.find((one) => one.id === anchorId) ?? all[0];
  if (!anchor) return { tree: made, paneId: made.id };
  const direction = edge === "left" || edge === "right" ? "row" : "column";
  const before = edge === "left" || edge === "top";
  const tree = replacePane(root, anchor.id, (one) =>
    split(direction, before ? [made, one] : [one, made]),
  );
  return { tree: tree ?? made, paneId: made.id };
}

/** Focus a tab within its pane. */
export function activateTab(root: Node, paneId: string, index: number): Node {
  return replacePane(root, paneId, (one) => ({ ...one, active: clampActive(one.tabs, index) })) ?? root;
}

/** Record a drag of a divider. Sizes come from the panel library already summing to 100. */
export function resizeSplit(root: Node, splitId: string, sizes: number[]): Node {
  const walk = (node: Node): Node => {
    if (node.kind === "pane") return node;
    if (node.id === splitId && sizes.length === node.children.length) {
      return { ...node, sizes: normalise(sizes) };
    }
    return { ...node, children: node.children.map(walk) };
  };
  return walk(root);
}

/** Give a tab a new identity in place, keeping its pane and its position.
 *
 * Exists for one case, and it is the case that makes chat tabs possible at all: a new chat has
 * no conversation until its first turn comes back, so its tab opens keyed on nothing and is
 * re-keyed the moment the server names it. Closing and reopening would work, and would move the
 * tab to the end of the strip and steal focus from wherever you had gone in the meantime — mid
 * first reply, which is exactly when you are watching.
 *
 * A rename onto a key that already exists is refused rather than merged. Two tabs claiming one
 * conversation is invariant 4 broken, and the honest outcome — the caller finds out its rename
 * did nothing and closes the draft — is better than a tree with a duplicate in it.
 */
export function renameTab(root: Node, key: string, ref: TabRef): Node {
  const found = findTab(root, key);
  if (!found) return root;
  const wanted = tabKey(ref);
  if (wanted === key) return root;
  if (findTab(root, wanted)) return root;
  return (
    replacePane(root, found.pane.id, (one) => ({
      ...one,
      // The `uid` rides across, which is the point: the tab is the same tab, so React keeps
      // the pane — and the runtime streaming inside it — rather than rebuilding both.
      tabs: one.tabs.map((tab, index) =>
        index === found.index ? { ...ref, uid: tab.uid ?? nextId("tab") } : tab,
      ),
    })) ?? root
  );
}

/** Is this tab anywhere in the layout? */
export function hasTab(root: Node, key: string): boolean {
  return findTab(root, key) !== null;
}

/* ── Places ──────────────────────────────────────────────────────────────────────────────────
 *
 * A pane's durable identity, and the machinery a pin needs to find or rebuild one. Everything
 * here is as pure as the rest of this module: the store owns which tabs are pinned, and passes
 * that in as a predicate rather than letting the tree know about preferences.
 */

/** Where a pane sat, described well enough to put one back there.
 *
 * The pane's index among the *root* split's children, not an edge. "Left or right" cannot
 * describe the default layout's middle column, which is the chat — the thing most likely to be
 * pinned. A nested pane reports the index of its outermost ancestor that is a direct child of
 * the root, because that is the column it visually belongs to.
 *
 * `direction` is only consulted when the root is a bare pane and a split has to be created to
 * replay the slot at all. When the root is already a split, the slot is replayed into it as it
 * is: matching a recorded direction against a root that has since been flipped would mean
 * rebuilding the arrangement around one reopened tab. */
export type Slot = { direction: "row" | "column"; index: number; size: number };

/** Mint a place. Opaque, and minted through `nextId` so it carries the same per-load token that
 *  makes a fresh id unable to collide with a stored one. */
export function nextPlace(): string {
  return nextId("place");
}

/** The pane carrying a place, or null. The question a pin asks first. */
export function paneWithPlace(root: Node, place: string): PaneNode | null {
  return panes(root).find((one) => one.place === place) ?? null;
}

/** Rebuild the tree with `fn` applied to every pane. */
function mapPanes(node: Node, fn: (one: PaneNode) => PaneNode): Node {
  if (node.kind === "pane") return fn(node);
  return { ...node, children: node.children.map((child) => mapPanes(child, fn)) };
}

/** Give a pane a place, taking it off any pane that already had it.
 *
 * Removing it elsewhere is the whole point: a place names one pane, and two panes claiming it
 * is "which of these is the right column" with no answer. */
export function stampPlace(root: Node, paneId: string, place: string): Node {
  return mapPanes(root, (one) => {
    if (one.id === paneId) return { ...one, place };
    if (one.place !== place) return one;
    const { place: _gone, ...rest } = one;
    return rest;
  });
}

/** Take a pane's place away. Called when the last pinned tab leaves it — a place nothing is
 *  pinned to is a name nobody can reach, and it would be saved into every layout thereafter. */
export function clearPlace(root: Node, paneId: string): Node {
  return mapPanes(root, (one) => {
    if (one.id !== paneId || one.place === undefined) return one;
    const { place: _gone, ...rest } = one;
    return rest;
  });
}

/** Describe where a pane sits, for a pin to record. Null when the pane is not in this tree. */
export function slotFor(root: Node, paneId: string): Slot | null {
  if (root.kind === "pane") {
    return root.id === paneId ? { direction: "row", index: 0, size: 100 } : null;
  }
  const index = root.children.findIndex((child) => panes(child).some((one) => one.id === paneId));
  if (index < 0) return null;
  return { direction: root.direction, index, size: root.sizes[index] ?? 100 / root.children.length };
}

/** How much of a split one child may claim when a slot is replayed. A slot recorded at 100 —
 *  the whole window, because the root was a single pane — must not come back as a pane with no
 *  room left for the one it split. */
function slotShare(size: number): number {
  return Math.max(10, Math.min(90, size));
}

/** Open a tab in a pane of its own, at a recorded slot, stamped with a place.
 *
 * The answer to "this tab is pinned somewhere that no longer exists". Unlike `openBeside`, which
 * splits whatever you are looking at, this puts the pane back where the pin says it was — which
 * is the difference between honouring a pin and merely noticing one. */
export function openAtSlot(
  root: Node,
  ref: TabRef,
  slot: Slot,
  place: string,
): { tree: Node; paneId: string } {
  const made = pane([ref], 0, nextId("pane"), place);
  const share = slotShare(slot.size);

  if (root.kind === "pane") {
    const others = 100 - share;
    const children = slot.index <= 0 ? [made, root] : [root, made];
    const sizes = slot.index <= 0 ? [share, others] : [others, share];
    return { tree: { ...split(slot.direction, children, sizes) }, paneId: made.id };
  }

  const at = Math.max(0, Math.min(slot.index, root.children.length));
  const children = root.children.slice();
  children.splice(at, 0, made);

  /* The new pane takes its recorded share and the existing children give it up in proportion
   * to what they already had — not `normalise` over the lot, which would scale the recorded
   * share down along with everything else and land the pane at a width the pin never said. */
  const held = root.sizes.reduce((sum, one) => sum + one, 0) || 100;
  const scale = (100 - share) / held;
  const sizes = root.sizes.map((one) => one * scale);
  sizes.splice(at, 0, share);

  return {
    tree: { ...root, children, sizes: normalise(sizes) },
    paneId: made.id,
  };
}

/**
 * Every tab of these surfaces, gone — for a tree that arrived holding one.
 *
 * Stored layouts on disk predate `TAKEOVERS`, and every one of them may hold a Board or Settings
 * tab. Rejecting such a tree is not an option: `readStored` answers null for anything it does not
 * like, and null means the default, so one retired surface would throw away the whole
 * arrangement. Strip the tabs, keep the panes and the sizes.
 *
 * An emptied pane is left empty rather than collapsed. Invariant 5 allows one, `EmptyPane` draws
 * it with a way out, and collapsing here would silently reshape a layout the person arranged —
 * a bigger change than the one that was asked for.
 */
export function withoutSurfaces(root: Node, surfaces: ReadonlySet<SurfaceId>): Node {
  return mapPanes(root, (one) => {
    const tabs = one.tabs.filter((tab) => !surfaces.has(tab.surface));
    if (tabs.length === one.tabs.length) return one;
    const wasActive = one.tabs[one.active];
    const active = tabs.findIndex((tab) => tab.uid === wasActive?.uid);
    return { ...one, tabs, active: active >= 0 ? active : clampActive(tabs, 0) };
  });
}

/** Invariant 7: pinned tabs occupy a prefix of every pane's `tabs`.
 *
 * A stable partition, so the relative order inside each group is whatever the person dragged it
 * into. Run from the store's `commit` rather than clamped inside `moveTab`: this module knows
 * nothing about preferences, and threading a predicate through `moveTab`, `dockTab` and every
 * caller to enforce it there would spread one rule across five signatures.
 *
 * The pane keeps showing what it was showing — active is found again by `uid`, the rule
 * `closeTab` and `moveTab` already pay for. */
export function orderPinned(root: Node, isPinned: (key: string) => boolean): Node {
  return mapPanes(root, (one) => {
    const held = one.tabs.filter((tab) => isPinned(tabKey(tab)));
    if (!held.length || held.length === one.tabs.length) return one;
    const free = one.tabs.filter((tab) => !isPinned(tabKey(tab)));
    const tabs = [...held, ...free];
    if (tabs.every((tab, index) => tab === one.tabs[index])) return one;
    const wasActive = one.tabs[one.active];
    const active = tabs.findIndex((tab) => tab.uid === wasActive?.uid);
    return { ...one, tabs, active: active >= 0 ? active : clampActive(tabs, one.active) };
  });
}


/** One pane per place, enforced on a tree that came from outside.
 *
 * No operation in this module can duplicate a place — `dockTab` splits by building a *new* pane
 * and keeping the original object, `replacePane` collapsing a one-child split returns that child
 * untouched, and everything else spreads the pane it found. This is defence against a tree that
 * did not come from those operations: hand-edited storage, or a saved layout stamped twice.
 *
 * The pane holding the most pinned tabs keeps the place, ties broken by render order. */
export function dedupePlaces(root: Node, isPinned: (key: string) => boolean): Node {
  const all = panes(root);
  const winners = new Map<string, string>();
  const contested = new Set<string>();
  for (const one of all) {
    if (!one.place) continue;
    const held = winners.get(one.place);
    if (held === undefined) {
      winners.set(one.place, one.id);
      continue;
    }
    contested.add(one.place);
    const mine = one.tabs.filter((tab) => isPinned(tabKey(tab))).length;
    const theirs =
      all.find((other) => other.id === held)?.tabs.filter((tab) => isPinned(tabKey(tab))).length ??
      0;
    if (mine > theirs) winners.set(one.place, one.id);
  }
  if (!contested.size) return root;
  return mapPanes(root, (one) => {
    if (!one.place || !contested.has(one.place)) return one;
    if (winners.get(one.place) === one.id) return one;
    const { place: _gone, ...rest } = one;
    return rest;
  });
}

/* ── Restructuring ───────────────────────────────────────────────────────────────────────────
 *
 * The verbs a tiling layout is expected to have and this one did not: close a pane rather than
 * its tabs one at a time, turn columns into rows, swap two of them round.
 */

/** Close a pane and everything in it.
 *
 * Invariant 5 still holds: the last pane stays, emptied, because something has to be on screen
 * and an empty pane that says what to do is a better answer than a blank window. It keeps its
 * `place` when it does — pins point at that place, and a pane emptied is not a place abandoned.
 *
 * Pinned tabs go with the rest. Closing a pane is a deliberate act aimed at the pane, not a
 * mis-click on a 22px target, and the pins survive it: each one comes home the moment its tab
 * is opened again. */
export function closePane(root: Node, paneId: string): Node {
  const all = panes(root);
  const found = all.find((one) => one.id === paneId);
  if (!found) return root;
  if (all.length === 1) return pane([], 0, found.id, found.place);
  return replacePane(root, paneId, () => null) ?? pane();
}

/** Turn a split's columns into rows, or back. Sizes ride across: the proportions were chosen,
 *  the axis was not. */
export function flipSplit(root: Node, splitId: string): Node {
  const walk = (node: Node): Node => {
    if (node.kind === "pane") return node;
    const children = node.children.map(walk);
    if (node.id !== splitId) return { ...node, children };
    return { ...node, direction: node.direction === "row" ? "column" : "row", children };
  };
  return walk(root);
}

/** Reverse a split's children, sizes with them — the "put that column on the other side" move,
 *  which is otherwise three drags and a resize. */
export function rotateSplit(root: Node, splitId: string): Node {
  const walk = (node: Node): Node => {
    if (node.kind === "pane") return node;
    const children = node.children.map(walk);
    if (node.id !== splitId) return { ...node, children };
    return { ...node, children: children.slice().reverse(), sizes: node.sizes.slice().reverse() };
  };
  return walk(root);
}

/** The split a pane hangs from, or null when the pane *is* the root. What "flip this pane's
 *  split" has to resolve before it can do anything. */
export function splitAbove(root: Node, paneId: string): SplitNode | null {
  if (root.kind === "pane") return null;
  for (const child of root.children) {
    if (child.kind === "pane" && child.id === paneId) return root;
  }
  for (const child of root.children) {
    const deeper = splitAbove(child, paneId);
    if (deeper) return deeper;
  }
  return null;
}

/** The pane `delta` steps from this one in render order, clamped rather than wrapped.
 *
 * Clamped, because "throw this tab one pane to the right" arriving at the far left instead is
 * the kind of surprise that makes people stop using a shortcut. Null when there is nowhere to
 * go, so the caller does nothing rather than something arbitrary. */
export function paneBeside(root: Node, paneId: string, delta: number): PaneNode | null {
  const all = panes(root);
  const at = all.findIndex((one) => one.id === paneId);
  if (at < 0) return null;
  const to = at + delta;
  if (to < 0 || to >= all.length || to === at) return null;
  return all[to];
}

