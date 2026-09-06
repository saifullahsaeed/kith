import { create } from "zustand";

import { SURFACES } from "./surfaces";
import {
  activateTab,
  closeTab,
  dockTab,
  ids,
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
/* 2: tabs gained a permanent `uid`, so a stored v1 tab has none and every React key would be
 * undefined. `withUid` could fill them in on read, but the default layout is a correct answer
 * and a layout is a convenience — a migration for one field is more code to be wrong. */
const VERSION = 2;

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
    return (
      typeof node.id === "string" &&
      Array.isArray(node.tabs) &&
      typeof node.active === "number" &&
      (node.tabs as unknown[]).every(
        (tab) =>
          !!tab &&
          typeof tab === "object" &&
          /* The surface has to be one this build knows, not merely a string.
           *
           * Every deref now goes through `surfaceFor`, which never returns undefined, so this
           * is no longer about crashing — it is about not accepting a tab nothing can draw.
           *
           * **A plugin tab is checked structurally, not against what is installed.** Asking
           * "is this plugin here" discards the *whole tree* when the answer is no: every
           * split, every size, and every other tab in the window. So uninstalling one plugin
           * used to reset the layout. "Is it installed" is a render-time question now, where
           * the answer can be a placeholder pane with a Close button — one tab, not the
           * arrangement. */
          ((tab as TabRef).surface === "plugin"
            ? typeof (tab as TabRef).plugin === "string" &&
              typeof (tab as TabRef).view === "string" &&
              !!(tab as TabRef).plugin &&
              !!(tab as TabRef).view
            : (tab as TabRef).surface in SURFACES),
      )
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
    if (!looksLikeLayout(held.tree)) return null;
    /* Duplicate ids are checked here as well as prevented at the source.
     *
     * `nextId` mixes a per-load token in so a new id cannot look like a stored one, which is the
     * actual fix. This is the second lock: a tree with two panes sharing an id throws inside the
     * panel library during render, which takes the window down rather than one pane, and the
     * only recovery from a *stored* one is clearing site data. Cheap to check, and the default
     * layout is a fine answer. */
    const seen = ids(held.tree);
    if (new Set(seen).size !== seen.length) return null;
    return held.tree;
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
  /** Pane ids, most recently focused first.
   *
   * Exists so that "when a split cannot honour every child's minimum, the least-recently
   * focused pane gives way" has something to mean. Without an order the only available answer
   * is position, and collapsing the leftmost pane because it is leftmost is arbitrary in a way
   * you feel immediately. */
  order: string[];
  open: (ref: TabRef) => void;
  close: (key: string) => void;
  dock: (key: string, paneId: string, edge: Edge) => void;
  activate: (paneId: string, index: number) => void;
  /** Re-key a tab in place — a new chat learning its conversation id. */
  rename: (key: string, ref: TabRef) => void;
  focus: (paneId: string) => void;
  resize: (splitId: string, sizes: number[]) => void;
  /** Put the layout back to the default. Reachable from an empty pane, which is exactly where
   *  somebody who has closed everything is standing. */
  reset: () => void;
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
    const alive = new Set(panesNow.map((one) => one.id));
    const wanted = focused ?? get().focused;
    const now = alive.has(wanted) ? wanted : firstPaneId(tree);
    set({
      tree,
      // A focused pane that has just been closed would leave new tabs opening into nothing.
      focused: now,
      // Closed panes drop out; panes that appeared (a split) join at the back, so a pane you
      // have never looked at is the first to give way when the window runs out of room.
      order: [
        now,
        ...get().order.filter((id) => id !== now && alive.has(id)),
        ...panesNow.map((one) => one.id).filter((id) => id !== now && !get().order.includes(id)),
      ],
    });
  };

  return {
    tree: initial,
    focused: firstPaneId(initial),
    order: panes(initial).map((one) => one.id),

    open: (ref) => {
      const { tree, focused } = get();
      const result = openTab(tree, ref, paneFor(tree, ref.surface, focused));
      commit(result.tree, result.paneId);
    },

    close: (key) => commit(closeTab(get().tree, key)),

    dock: (key, paneId, edge) => commit(dockTab(get().tree, key, paneId, edge)),

    activate: (paneId, index) => commit(activateTab(get().tree, paneId, index), paneId),

    rename: (key, ref) => commit(renameTab(get().tree, key, ref)),

    focus: (paneId) =>
      set((was) => ({
        focused: paneId,
        order: [paneId, ...was.order.filter((id) => id !== paneId)],
      })),

    resize: (splitId, sizes) => commit(resizeSplit(get().tree, splitId, sizes)),

    reset: () => {
      const tree = defaultLayout();
      commit(tree, firstPaneId(tree));
    },

  };
});

/** For tests and for "reset layout" from outside a component. */
export const layoutKey = KEY;
export const layoutVersion = VERSION;
export { tabKey };
