import { create } from "zustand";

import { choosePane, isPlacementMode, type Pin, type PlacementMode } from "./place";
import { SURFACES } from "./surfaces";
import {
  activateTab,
  BOUND,
  clearPlace,
  closePane as closePaneIn,
  closeTab,
  dedupePlaces,
  dockTab,
  findTab,
  flipSplit as flipSplitIn,
  ids,
  moveTab,
  nextPlace,
  openAtSlot,
  openBeside,
  openTab,
  orderPinned,
  pane,
  paneWithPlace,
  renameTab,
  panes,
  resizeSplit,
  rotateSplit as rotateSplitIn,
  slotFor,
  stampPlace,
  tabKey,
  TAKEOVERS,
  withoutSurfaces,
  type Edge,
  type Node,
  type SurfaceId,
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
 * and a layout is a convenience — a migration for one field is more code to be wrong.
 *
 * 3: `conversations` and `inbox` stopped being surfaces — the list is the window's own left
 * rail, and alerts slide over the room the way `Layer.Panel` always said they did. So a stored
 * v2 tree can hold a tab whose surface is no longer a key of `SURFACES` — which is invariant 6,
 * and which `looksLikeLayout` does not check. Same call as above: discard and hand back the
 * default rather than walk the tree stripping tabs out of it.
 *
 * 4: `work`, `context` and plugin surfaces name the conversation they are about, so `tabKey`
 * answers differently for the same stored ref. A v3 tree is structurally fine and would load —
 * it would just hold unbound tabs whose keys no longer match what the app now mints, which is
 * the quiet kind of wrong. The default is one pane now; there is very little to lose. */
const VERSION = 4;

/** What the app opens as, and what "reset layout" restores.
 *
 * One pane, full width. The three standing columns are gone in three steps: the conversation
 * list became the window's rail, alerts slide over the room, and Work now opens as a tab in the
 * pane you are looking at rather than claiming a column of its own — see `DEFAULT_PLACEMENT`.
 *
 * What is left is the thing the window is for: one surface with the whole width to read in. The
 * tree is still a tree and splitting is still a drag to a pane's edge; it is only no longer
 * something the app does to you before you ask. */
export function defaultLayout(): Node {
  return pane([{ surface: "chat", conversationId: "" }]);
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
    /* Retired surfaces come out here rather than failing the tree.
     *
     * Board and Settings were tabs until they became route-driven takeovers, so every layout
     * stored before that holds one. Returning null for those would mean the default arrangement
     * — the whole thing thrown away over two tabs nothing can draw. See `TAKEOVERS`. */
    return withoutSurfaces(held.tree, TAKEOVERS);
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

/* Where each surface prefers to open, as set from a tab's menu — its own storage, not the
 * layout's.
 *
 * The layout blob is versioned, and a version bump throws every stored arrangement away: the
 * placement is one preference and the layout is an arrangement, and losing the second to ship
 * the first is exactly the trade this module exists to refuse. Separate key, no version — the
 * reader validates every entry against what this build knows and ignores the rest, which is the
 * same structural-check move the tree gets on read. */
const PLACEMENTS_KEY = "kith-layout-placements";

export function readPlacements(): Partial<Record<SurfaceId, PlacementMode>> {
  try {
    const raw = localStorage.getItem(PLACEMENTS_KEY);
    if (!raw) return {};
    const held = JSON.parse(raw) as Record<string, unknown>;
    const out: Partial<Record<SurfaceId, PlacementMode>> = {};
    for (const [surface, mode] of Object.entries(held)) {
      if (surface in SURFACES && isPlacementMode(mode)) out[surface as SurfaceId] = mode;
    }
    return out;
  } catch {
    /* not JSON, private mode, no storage — defaults are a correct answer */
    return {};
  }
}

function writePlacements(placements: Partial<Record<SurfaceId, PlacementMode>>): void {
  try {
    localStorage.setItem(PLACEMENTS_KEY, JSON.stringify(placements));
  } catch {
    /* storage full or unavailable — the preference is a convenience, not the work */
  }
}

/* Pinned tabs and the places they live in — its own key, for the same reason placements have
 * one. A pin is a preference about where one thing belongs; the layout blob is an arrangement,
 * and a version bump to the arrangement must not be able to destroy the preference.
 *
 * Unversioned and validated per entry: junk, a tab key this build cannot parse, or a slot with
 * a direction that is not a direction is dropped and the rest are kept — the same move the tree
 * gets from `looksLikeLayout`, applied one pin at a time so one bad entry is not a lost set. */
const PINS_KEY = "kith-layout-pins";

function isPin(value: unknown): value is Pin {
  if (!value || typeof value !== "object") return false;
  const held = value as Partial<Pin> & { slot?: Partial<Pin["slot"]> };
  if (typeof held.place !== "string" || !held.place) return false;
  const slot = held.slot;
  if (!slot || typeof slot !== "object") return false;
  return (
    (slot.direction === "row" || slot.direction === "column") &&
    typeof slot.index === "number" &&
    Number.isFinite(slot.index) &&
    slot.index >= 0 &&
    typeof slot.size === "number" &&
    Number.isFinite(slot.size)
  );
}

export function readPins(): Record<string, Pin> {
  try {
    const raw = localStorage.getItem(PINS_KEY);
    if (!raw) return {};
    const held = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, Pin> = {};
    for (const [key, pin] of Object.entries(held)) {
      if (key && isPin(pin)) out[key] = pin;
    }
    return out;
  } catch {
    /* not JSON, private mode, no storage — no pins is a correct answer */
    return {};
  }
}

function writePins(pins: Record<string, Pin>): void {
  try {
    localStorage.setItem(PINS_KEY, JSON.stringify(pins));
  } catch {
    /* storage full or unavailable — the pin is a convenience, not the work */
  }
}

/* Named arrangements you switch between by hand.
 *
 * **This is not the thing the windowed-shell design turned down.** That rejected a layout *per
 * project or per conversation*: "a layout you have to rebuild every time you switch projects is
 * worse than not having one — the arranging becomes the work." The objection is to the app
 * choosing an arrangement for you on every context switch, and it still stands. A named layout
 * you pick deliberately is the opposite of that — arranging done once and recalled — and
 * nothing here binds a layout to a project, a conversation, or anything else. Adding such a
 * binding would re-create exactly what was refused.
 *
 * Own key and unversioned, validated per entry: one unreadable saved layout must not cost you
 * the others, and none of them may cost you the live one. */
/**
 * Surfaces attached *to a tab* rather than beside it, keyed by the host tab's key.
 *
 * A Work panel opened from a chat is about that chat and belongs with it — as a companion in
 * the tab's own column, not as a sibling in the pane's strip. Binding the surface to the
 * conversation made that possible; this is what makes it visible. Two chats side by side each
 * carry their own Work, and neither shows in the other's strip.
 *
 * Keyed by `tabKey`, which for a chat is `chat:<conversation>` — so companions are really per
 * conversation. Close the chat and reopen it a day later and its column comes back, which is
 * the behaviour you would expect from something that is *about* that conversation.
 *
 * Its own storage key rather than a field on the tree: the tree is versioned and gets discarded
 * whenever its shape changes, and losing which panels you keep open beside a chat because the
 * *layout* format moved is a bad trade. Validated per entry like pins — one unparseable host
 * drops that host, not the set.
 */
const COMPANIONS_KEY = "kith-companions";

function isTabRef(value: unknown): value is TabRef {
  if (!value || typeof value !== "object") return false;
  const held = value as Partial<TabRef>;
  return typeof held.surface === "string" && held.surface in SURFACES;
}

export function readCompanions(): Record<string, TabRef[]> {
  try {
    const raw = localStorage.getItem(COMPANIONS_KEY);
    if (!raw) return {};
    const held = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, TabRef[]> = {};
    for (const [host, list] of Object.entries(held)) {
      if (!host || !Array.isArray(list)) continue;
      const kept = list.filter(isTabRef);
      if (kept.length) out[host] = kept;
    }
    return out;
  } catch {
    /* not JSON, private mode, no storage — no companions is a correct answer */
    return {};
  }
}

function writeCompanions(companions: Record<string, TabRef[]>): void {
  try {
    localStorage.setItem(COMPANIONS_KEY, JSON.stringify(companions));
  } catch {
    /* storage full or unavailable — a companion is a convenience, not the work */
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
  /** Open a surface as a tab. Where it goes is the placement policy (place.ts); opts.paneId is
   *  the caller's overrule, honoured while the pane exists — the hook for a future plugin
   *  open_surface delivery that knows which pane the plugin lives in. */
  open: (ref: TabRef, opts?: { paneId?: string }) => void;
  close: (key: string) => void;
  dock: (key: string, paneId: string, edge: Edge) => void;
  activate: (paneId: string, index: number) => void;
  /** Move a tab to a final position in a pane's strip — a reorder inside its pane, or into
   *  another pane's strip where the drop was aimed. The strip is where tab order lives; without
   *  this a drop on it could only append. */
  move: (key: string, paneId: string, index: number) => void;
  /** Where each surface prefers to open, as set from its tab's menu. Per surface rather than
   *  per pane, because the preference is about the kind of thing — "settings goes in the wide
   *  pane" — and it must survive panes, which are closed and rebuilt, but not outlive a
   *  decision to change it. */
  placements: Partial<Record<SurfaceId, PlacementMode>>;
  setPlacement: (surface: SurfaceId, mode: PlacementMode) => void;
  /** Pinned tabs, by `tabKey`. Global rather than per saved layout: a pin names a place, and a
   *  layout either has that place or the pin falls through to the per-surface policy. One set,
   *  surviving a reset, is both less state and the more useful answer. */
  pins: Record<string, Pin>;
  /** Pin the tab to the pane it is in now, minting that pane a place if it has none. Idempotent:
   *  pinning an already-pinned tab does not re-record its slot, so a pin keeps saying where the
   *  tab was *pinned*, not wherever it was last dragged. */
  pin: (key: string) => void;
  /** Drop a pin, and the place with it once nothing is pinned there. */
  unpin: (key: string) => void;
  /** Which pane is filling the window, if any.
   *
   * Live state, never stored — the same treatment `widths` gets and for the same reason: it is
   * a fact about right now, and a layout that came back from storage already zoomed would look
   * like a layout that had lost two of its panes. Cleared by `commit` when its pane dies.
   *
   * Zoom does not touch the tree. Nothing is resized and nothing is moved, so leaving it
   * restores the arrangement exactly rather than approximately. */
  zoomed: string | null;
  /** Fill the window with a pane, or stop. Passing the pane already zoomed is how you leave. */
  zoom: (paneId: string | null) => void;
  /** Close a pane and everything in it. The last pane stays, emptied — see `closePane`. */
  closePane: (paneId: string) => void;
  /** Turn a split's columns into rows, or back. */
  flipSplit: (splitId: string) => void;
  /** Reverse a split's children — "put that column on the other side". */
  rotateSplit: (splitId: string) => void;
  /** How wide each pane is, measured, in px. Live state — never stored, and pruned by commit.
   *  Placement reads it so an open stops landing in a pane the yielding rule has already
   *  collapsed to a rail; nothing else may care, which is why it is not persisted. */
  widths: Record<string, number>;
  trackWidth: (paneId: string, width: number) => void;
  /** Re-key a tab in place — a new chat learning its conversation id. */
  rename: (key: string, ref: TabRef) => void;
  focus: (paneId: string) => void;
  resize: (splitId: string, sizes: number[]) => void;
  /** Surfaces attached to a tab, keyed by that tab's key — see `COMPANIONS_KEY`. */
  companions: Record<string, TabRef[]>;
  /** Attach a surface to a tab's own column. Idempotent: attaching one that is already there
   *  does nothing, so the button that opens it can be pressed twice without stacking two. */
  attach: (hostKey: string, ref: TabRef) => void;
  /** Take one back out of the column. */
  detach: (hostKey: string, key: string) => void;

  /** Put the layout back to the default. Reachable from an empty pane, which is exactly where
   *  somebody who has closed everything is standing — and, since the saved-arrangements menu
   *  went, the only way back. */
  reset: () => void;
};

/** The conversation in front of you, or "" — the focused pane's active tab when that is a chat.
 *
 * Deliberately *only* the focused pane's active tab, with no fallback to "the first chat
 * anywhere". A companion column is a place things are put; guessing a host when none is in
 * front of you is how a panel ends up attached to a conversation you were not looking at. With
 * no answer here a bound surface opens as a tab, which is the honest one.
 *
 * Read as the *fallback* in `open`, not as the answer: a ref that names its own conversation has
 * already said which chat it is about, and this is only asked when it has not. Being the sole
 * answer is what made the Context button open a stray tab whenever focus had moved into the
 * column beside the chat — see the comment at its one call site. */
function focusedChat(tree: Node, focused: string): string {
  const pane = panes(tree).find((one) => one.id === focused);
  const active = pane?.tabs[pane.active];
  return active?.surface === "chat" ? (active.conversationId ?? "") : "";
}

function firstPaneId(tree: Node): string {
  return panes(tree)[0]?.id ?? "";
}

export const useLayout = create<LayoutState>((set, get) => {
  const initial = readStored() ?? defaultLayout();

  /** Every mutation goes through here, so persistence cannot be forgotten by one of them.
   *
   * Also the one place that sees both the tree and the pins, which is why invariants 7 and 8
   * are normalised here rather than clamped inside `moveTab`: `tree.ts` is pure and knows
   * nothing of preferences, and enforcing the pinned prefix there would thread a predicate
   * through `moveTab`, `dockTab` and every caller of both.
   *
   * `pinsNext` is for the two actions that change the pins and the tree together — without it
   * they would have to `set` twice and the normalisation would run against the old pins. */
  const commit = (tree: Node, focused?: string, pinsNext?: Record<string, Pin>) => {
    const pins = pinsNext ?? get().pins;
    const held = (key: string) => key in pins;
    const normalised = orderPinned(dedupePlaces(tree, held), held);
    writeStored(normalised);
    const panesNow = panes(normalised);
    const alive = new Set(panesNow.map((one) => one.id));
    const wanted = focused ?? get().focused;
    const now = alive.has(wanted) ? wanted : firstPaneId(normalised);
    // Measured widths of panes that are gone are gone with them: the map never answers for
    // a pane that no longer exists, and never grows without bound.
    const widths: Record<string, number> = {};
    for (const [id, width] of Object.entries(get().widths)) {
      if (alive.has(id)) widths[id] = width;
    }
    set({
      tree: normalised,
      widths,
      ...(pinsNext ? { pins: pinsNext } : {}),
      // A zoom on a pane that has just been closed would render nothing at all — the one state
      // `LayoutView` cannot draw its way out of.
      zoomed: alive.has(get().zoomed ?? "") ? get().zoomed : null,
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
    placements: readPlacements(),
    pins: readPins(),
    companions: readCompanions(),
    zoomed: null,
    widths: {},

    /* The placement policy decides; the caller may overrule it by naming a pane. Beside is a
     * split, and the pane it makes is where focus goes — an open you asked for in a new pane
     * should not leave you staring at the old one. */
    open: (ref, opts) => {
      /* Not everything is a tab. Board and Settings cover the window and are opened by the route;
       * putting one in the tree is what made them flash a tab on the way to taking the screen.
       * Refused here rather than trusted to callers, because a drag and a deep link are callers
       * too. See `TAKEOVERS`. */
      if (TAKEOVERS.has(ref.surface)) return;

      const { tree, focused, widths, placements, pins } = get();

      /* A surface that is about a conversation joins that chat's column, not the tab strip.
       *
       * Routed here rather than at the four call sites that open one — the plugins tab, both
       * ways into a plugin from a tool result, and `open_surface` when the model asks for it —
       * because "where a bound surface goes" is one policy and four copies of it drift.
       *
       * Two separate things happen, and keeping them separate matters. *Stamping* the
       * conversation onto the ref is about identity: a panel that arrived naming only a plugin
       * and a view still has to know which chat it is showing, or it falls back to the focused
       * one and we are back to the inference this replaced. *Attaching* is about placement, and
       * a caller naming a pane overrules it — that is what naming a pane means, and the plugin
       * delivery path relies on it. Conflating the two left a `paneId` open unbound. */
      /* Which conversation this surface is *about* — the ref's answer first, and the focused
       * chat only when the ref has none.
       *
       * That order is the fix for a panel that opened in the wrong place, and the cause was two
       * policies for one question. `workspace.tsx` computes the chat a surface is about with a
       * deliberately *sticky* rule — a click outside a chat leaves the answer where it was, so
       * that the Context button still works while you are in the panel beside the chat.
       * `focusedChat` below has the opposite rule, equally deliberately: it refuses to guess a
       * host when none is in front of you. Both are right about their own question and they
       * disagree exactly when focus is on a companion — the caller asked for "context for c-1",
       * this read "no chat is focused", and the panel opened as a stray tab somewhere else.
       *
       * So the caller wins when it has said something. `focusedChat` keeps its rule and becomes
       * what it always should have been: the fallback for a ref that did not say. */
      const focusedOn = focusedChat(tree, focused);
      const about = (BOUND.has(ref.surface) ? (ref.conversationId ?? "") : "") || focusedOn;
      const bound: TabRef =
        BOUND.has(ref.surface) && about && !ref.conversationId
          ? ({ ...ref, conversationId: about } as TabRef)
          : ref;

      /* And into that chat's column only if that chat is actually open. A column is rendered
       * inside its host's body (see `workspace.renderSurface`), so attaching to a tab that is
       * not there stores a panel nothing will ever draw — asked for, accepted, and invisible.
       * A tab is the honest answer in that case, which is what it already was for a ref with no
       * conversation at all. */
      const host = tabKey({ surface: "chat", conversationId: about });
      const hosted =
        !!about && panes(tree).some((one) => one.tabs.some((tab) => tabKey(tab) === host));

      if (!opts?.paneId && hosted && BOUND.has(bound.surface)) {
        get().attach(host, bound);
        return;
      }

      const chosen = choosePane(tree, bound, {
        focused,
        paneId: opts?.paneId,
        widths,
        placements,
        pins,
      });
      /* A pin whose place no pane carries any more: rebuild the place, do not improvise. Checked
       * before `beside`, because the two are different answers to different questions and a
       * pinned tab never wants "split whatever I am looking at". */
      if (chosen.raise) {
        const made = openAtSlot(tree, bound, chosen.raise.slot, chosen.raise.place);
        commit(made.tree, made.paneId);
        return;
      }
      if (chosen.beside) {
        const made = openBeside(tree, bound, focused);
        commit(made.tree, made.paneId);
        return;
      }
      const result = openTab(tree, bound, chosen.paneId ?? undefined);
      commit(result.tree, result.paneId);
    },

    close: (key) => commit(closeTab(get().tree, key)),

    dock: (key, paneId, edge) => commit(dockTab(get().tree, key, paneId, edge)),

    // The pane the tab moved into is where focus goes, whether it is a reorder — already
    // focused by the mousedown that started the drag — or a move into a neighbour.
    move: (key, paneId, index) => commit(moveTab(get().tree, key, paneId, index), paneId),

    activate: (paneId, index) => commit(activateTab(get().tree, paneId, index), paneId),

    /* A rename carries the pin across with the tab.
     *
     * Same reason `renameTab` carries `uid` across rather than closing and reopening: it is the
     * same tab. A draft chat pinned before its first turn came back would otherwise lose its
     * pin the moment the server named it, which is the one moment nobody is watching the strip.
     * A refused rename (`renameTab` returns the tree it was given) leaves the pin alone. */
    rename: (key, ref) => {
      const { tree, pins } = get();
      const next = renameTab(tree, key, ref);
      const pin = pins[key];
      if (next === tree || !pin) {
        commit(next);
        return;
      }
      const moved = { ...pins };
      delete moved[key];
      moved[tabKey(ref)] = pin;
      writePins(moved);
      commit(next, undefined, moved);
    },

    pin: (key) => {
      const { tree, pins } = get();
      if (pins[key]) return;
      const found = findTab(tree, key);
      if (!found) return;
      const slot = slotFor(tree, found.pane.id);
      if (!slot) return;
      // A pane that already has a place keeps it, so pinning a second tab into the right column
      // pins it to the same place rather than renaming the column out from under the first.
      const place = found.pane.place ?? nextPlace();
      const next = { ...pins, [key]: { place, slot } };
      writePins(next);
      commit(stampPlace(tree, found.pane.id, place), found.pane.id, next);
    },

    unpin: (key) => {
      const { tree, pins } = get();
      const pin = pins[key];
      if (!pin) return;
      const next = { ...pins };
      delete next[key];
      writePins(next);
      /* The place goes when the last pin on it goes — asked of the pins and not of the pane's
       * tabs, because a pinned tab that has been *closed* still has a pin and still needs its
       * place to come home to. */
      const stillWanted = Object.values(next).some((one) => one.place === pin.place);
      const home = paneWithPlace(tree, pin.place);
      commit(home && !stillWanted ? clearPlace(tree, home.id) : tree, undefined, next);
    },

    /* Companions do not touch the tree, so neither of these goes through `commit`.
     *
     * That is the point of them: a panel in a tab's own column is not a tab, has no pane, and
     * cannot be dragged into a split — so none of the invariants `commit` exists to hold can be
     * broken by attaching one. It is a list beside a tab, stored under its own key. */
    attach: (hostKey, ref) => {
      if (!hostKey) return;
      const { companions } = get();
      const held = companions[hostKey] ?? [];
      // Idempotent, so the control that opens a panel is also safe to press twice.
      if (held.some((one) => tabKey(one) === tabKey(ref))) return;
      const next = { ...companions, [hostKey]: [...held, ref] };
      writeCompanions(next);
      set({ companions: next });
    },

    detach: (hostKey, key) => {
      const { companions } = get();
      const held = companions[hostKey];
      if (!held) return;
      const kept = held.filter((one) => tabKey(one) !== key);
      const next = { ...companions };
      // An empty column is no column: drop the host entirely rather than storing an empty list
      // that would be indistinguishable from one on every later read.
      if (kept.length) next[hostKey] = kept;
      else delete next[hostKey];
      writeCompanions(next);
      set({ companions: next });
    },

    focus: (paneId) =>
      set((was) => ({
        focused: paneId,
        order: [paneId, ...was.order.filter((id) => id !== paneId)],
      })),

    resize: (splitId, sizes) => commit(resizeSplit(get().tree, splitId, sizes)),

    zoom: (paneId) =>
      set((was) => ({
        zoomed: paneId === null || was.zoomed === paneId ? null : paneId,
        // Zooming a pane is looking at it, so it becomes the focused one — otherwise the next
        // tab you open lands in a pane you cannot currently see.
        ...(paneId && was.zoomed !== paneId
          ? { focused: paneId, order: [paneId, ...was.order.filter((id) => id !== paneId)] }
          : {}),
      })),

    closePane: (paneId) => commit(closePaneIn(get().tree, paneId)),

    flipSplit: (splitId) => commit(flipSplitIn(get().tree, splitId)),

    rotateSplit: (splitId) => commit(rotateSplitIn(get().tree, splitId)),

    setPlacement: (surface, mode) => {
      const placements = { ...get().placements, [surface]: mode };
      writePlacements(placements);
      set({ placements });
    },

    trackWidth: (paneId, width) => {
      const rounded = Math.round(width);
      /* The observer fires on subpixel jitter as well as on real layout, and every set is a
       * pass over every subscriber — so whole pixels and real changes only. Zero is a pane
       * measured before its first layout; recording it would make placement believe nothing
       * fits anywhere, and every open after that would be a guess. */
      const widths = get().widths;
      if (rounded <= 0 || widths[paneId] === rounded) return;
      set({ widths: { ...widths, [paneId]: rounded } });
    },

    reset: () => {
      const tree = defaultLayout();
      commit(tree, firstPaneId(tree));
    },

  };
});

/** For tests and for "reset layout" from outside a component. */
export const layoutKey = KEY;
export const layoutVersion = VERSION;
export const placementsKey = PLACEMENTS_KEY;
export const pinsKey = PINS_KEY;
export { tabKey };
