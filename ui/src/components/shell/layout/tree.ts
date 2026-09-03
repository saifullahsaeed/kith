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
 */

/** Everything that can be a tab. `chat` is the only one that repeats. */
export type SurfaceId =
  | "chat"
  | "conversations"
  | "work"
  | "board"
  | "settings"
  | "inbox"
  | "context";

export type TabRef =
  | { surface: "chat"; conversationId: string }
  | { surface: Exclude<SurfaceId, "chat">; conversationId?: undefined };

export type PaneNode = { kind: "pane"; id: string; tabs: TabRef[]; active: number };
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
export function tabKey(ref: TabRef): string {
  return ref.surface === "chat" ? `chat:${ref.conversationId}` : ref.surface;
}

let counter = 0;
/** Ids are for React keys and for addressing a node in an operation, not for persistence
 *  identity — a reloaded layout keeps whatever ids it was saved with. */
export function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-${counter}`;
}

export function pane(tabs: TabRef[] = [], active = 0, id = nextId("pane")): PaneNode {
  return { kind: "pane", id, tabs, active: clampActive(tabs, active) };
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
    tabs: [...one.tabs, ref],
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
    // Invariant 3: closing the active tab focuses the one that took its place, or the last.
    return { ...one, tabs, active: clampActive(tabs, Math.min(found.index, tabs.length - 1)) };
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

/** Is this tab anywhere in the layout? */
export function hasTab(root: Node, key: string): boolean {
  return findTab(root, key) !== null;
}
