import { create } from "zustand";

import {
  activateTab,
  closeTab,
  dockTab,
  hasTab,
  openTab,
  pane,
  paneFor,
  renameTab,
  panes,
  resizeSplit,
  split,
  tabKey,
  type Edge,
  type Node,
  type TabRef,
} from "./tree";

/**
 * The layout, kept once for the app, and remembered between runs.
 *
 * One layout rather than one per project or per conversation. A layout you have to rebuild
 * every time you switch projects is worse than not having one — the arranging becomes the work,
 * which is the whole reason tiling was chosen over floating windows.
 */

const KEY = "kith-layout";

/** Bumped when the shape changes in a way an older stored layout cannot be read as.
 *
 * A stored layout is the one piece of state here that outlives the code that wrote it, and the
 * failure it can cause is the worst kind: the app does not start. So an unreadable layout, a
 * newer one, or one that fails its own invariants is *silently* replaced with the default. The
 * arrangement is worth remembering; it is not worth a white screen. */
const VERSION = 1;

/** What the app opens as, and what "reset layout" restores.
 *
 * The same three columns the fixed layout had, so the first run after this ships looks like the
 * last run before it — the windowing is something you discover by dragging, not something the
 * app rearranges around you on upgrade. */
export function defaultLayout(): Node {
  return split(
    "row",
    [pane([{ surface: "conversations" }]), pane([{ surface: "chat", conversationId: "" }]), pane([{ surface: "work" }])],
    [18, 56, 26],
  );
}

/** Is this actually a layout? Cheap structural check, run on anything read from storage.
 *
 * Deliberately not a schema library. The question is "can the renderer walk this without
 * throwing", and the honest way to answer it is to try to walk it. */
export function looksLikeLayout(value: unknown): value is Node {
  if (!value || typeof value !== "object") return false;
  const node = value as Partial<Node> & Record<string, unknown>;
  if (node.kind === "pane") {
    return typeof node.id === "string" && Array.isArray(node.tabs) && typeof node.active === "number"
      && (node.tabs as unknown[]).every(
        (tab) => !!tab && typeof tab === "object" && typeof (tab as TabRef).surface === "string",
      );
  }
  if (node.kind === "split") {
    const children = node.children as unknown;
    const sizes = node.sizes as unknown;
    return (
      typeof node.id === "string" &&
      (node.direction === "row" || node.direction === "column") &&
      Array.isArray(children) &&
      children.length >= 2 &&
      Array.isArray(sizes) &&
      sizes.length === children.length &&
      (sizes as unknown[]).every((n) => typeof n === "number") &&
      (children as unknown[]).every(looksLikeLayout)
    );
  }
  return false;
}

export function readStored(): Node | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const held = JSON.parse(raw) as { version?: unknown; tree?: unknown };
    // A newer version is not an error and not something to guess at. The default is a correct
    // layout; a half-understood one is not.
    if (held.version !== VERSION) return null;
    return looksLikeLayout(held.tree) ? held.tree : null;
  } catch {
    // Private mode, no storage, or something that is not JSON. All three mean the same thing.
    return null;
  }
}

function writeStored(tree: Node): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ version: VERSION, tree }));
  } catch {
    /* storage full or unavailable — the layout is a convenience, not the work */
  }
}

export type LayoutState = {
  tree: Node;
  /** The pane a new tab lands in. Follows what you last clicked, so "open the roadmap" puts it
   *  where you are looking rather than always in the first column. */
  focused: string;
  open: (ref: TabRef) => void;
  close: (key: string) => void;
  dock: (key: string, paneId: string, edge: Edge) => void;
  activate: (paneId: string, index: number) => void;
  /** Re-key a tab in place — a new chat learning its conversation id. */
  rename: (key: string, ref: TabRef) => void;
  focus: (paneId: string) => void;
  resize: (splitId: string, sizes: number[]) => void;
  reset: () => void;
  has: (key: string) => boolean;
};

function firstPaneId(tree: Node): string {
  return panes(tree)[0]?.id ?? "";
}

export const useLayout = create<LayoutState>((set, get) => {
  const initial = readStored() ?? defaultLayout();

  /** Every mutation goes through here, so persistence cannot be forgotten by one of them. */
  const commit = (tree: Node, focused?: string) => {
    writeStored(tree);
    const panesNow = panes(tree);
    const wanted = focused ?? get().focused;
    set({
      tree,
      // A focused pane that has just been closed would leave new tabs opening into nothing.
      focused: panesNow.some((one) => one.id === wanted) ? wanted : firstPaneId(tree),
    });
  };

  return {
    tree: initial,
    focused: firstPaneId(initial),

    open: (ref) => {
      const { tree, focused } = get();
      const result = openTab(tree, ref, paneFor(tree, ref.surface, focused));
      commit(result.tree, result.paneId);
    },

    close: (key) => commit(closeTab(get().tree, key)),

    dock: (key, paneId, edge) => commit(dockTab(get().tree, key, paneId, edge)),

    activate: (paneId, index) => commit(activateTab(get().tree, paneId, index), paneId),

    rename: (key, ref) => commit(renameTab(get().tree, key, ref)),

    focus: (paneId) => set({ focused: paneId }),

    resize: (splitId, sizes) => commit(resizeSplit(get().tree, splitId, sizes)),

    reset: () => {
      const tree = defaultLayout();
      commit(tree, firstPaneId(tree));
    },

    has: (key) => hasTab(get().tree, key),
  };
});

/** For tests and for "reset layout" from outside a component. */
export const layoutKey = KEY;
export const layoutVersion = VERSION;
export { tabKey };
