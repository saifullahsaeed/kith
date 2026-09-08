import { panes, type Node, type SurfaceId, type TabRef } from "./tree";

import { surfaceFor } from "./surfaces";

/**
 * Where a newly opened view goes.
 *
 * `paneFor` answered exactly one question — "is a pane already showing this kind?" — and then
 * fell to "wherever you last clicked", which is a guess wearing a rule. It could not say where
 * a surface *prefers* to open, it could not be told, and it happily opened a 420px-minimum
 * Settings into the 240px sidebar you happened to be clicking in, or a chat into a pane the
 * yielding rule had already collapsed to a rail — technically open, practically invisible. So
 * the placement lived up to nothing and the person re-arranged after every open.
 *
 * This is the policy `paneFor` was reaching for, as one pure function so the order of its
 * answers is a thing you can read and test rather than reconstruct from the store:
 *
 * 1. A caller that names a pane gets that pane, while it exists. `open`'s callers know things
 *    no policy can — the plugin `open_surface` delivery, when it lands, should not have to
 *    re-derive "the pane the plugin already lives in" from the tree.
 * 2. `beside`, when the surface has been told "always a new pane": a pane of its own, split
 *    off where you are, every time.
 * 3. `focused`, when told "always here": the pane you are looking at, grouping be damned. Some
 *    surfaces are visits, not collections.
 * 4. `own` and `grouped` together run the section rule — the default — in two flavours. Both
 *    open beside a pane already keeping this kind (the chats with the chats, a plugin beside
 *    its own plugin), and both prefer a pane wide enough to show what it would take. `own`,
 *    the default, answers "no such pane" by raising one — a section of the surface's own, the
 *    way conversations and work each hold their column in the arrangement the app opens as.
 *    `grouped` answers "no such pane" by absorbing into where you are, which is the older
 *    habit and the right one for surfaces you would rather not spend a column on.
 * 5. Whatever is left of the older habit for `grouped`: the focused pane, fitting or not, then
 *    the unfit pane of its own kind, then the first pane. Predictability beats cleverness —
 *    "where I am looking" is an answer you can argue with, "wherever was widest" is not.
 *
 * Widths are *measured* ones, live in the store, never stored: a pane's real width is what the
 * yielding rule left it with, not what any saved size says. A pane with no measurement yet is
 * given the benefit of the doubt — placement runs before layout has had its turn, and a wrong
 * first open corrects itself on the next one, where a pane treated as zero never opens at all.
 */

/** How a surface prefers to open, as set from its tab's menu.
 *
 * `own` is the default and the section rule: with its kind while a pane keeps it, a pane of
 * its own the moment none does. The other three are the overrides a person reaches for when
 * the rule is wrong for one surface — absorb into the room (`grouped`), follow the eye
 * (`focused`), or always claim a fresh pane (`beside`). */
export type PlacementMode = "own" | "grouped" | "focused" | "beside";

export const PLACEMENT_MODES: readonly PlacementMode[] = [
  "own",
  "grouped",
  "focused",
  "beside",
];

export const DEFAULT_PLACEMENT: PlacementMode = "own";

/** Read a placement out of storage. Junk from a build that named a mode differently, or a
 *  surface since renamed, is ignored rather than trusted — the same rule the tree's structural
 *  check applies to a stored layout. */
export function isPlacementMode(value: unknown): value is PlacementMode {
  return typeof value === "string" && (PLACEMENT_MODES as readonly string[]).includes(value);
}

/** The mode a surface opens with, defaults applied. */
export function modeFor(
  placements: Partial<Record<SurfaceId, PlacementMode>>,
  surface: SurfaceId,
): PlacementMode {
  return placements[surface] ?? DEFAULT_PLACEMENT;
}

/** What an open should do. `paneId` null with `beside` false means "no pane anywhere" —
 *  the empty tree, which `openTab` turns into a first pane rather than a shrug. */
export type PlacementChoice = { paneId: string | null; beside: boolean };

export type PlacementContext = {
  /** The pane a click last landed in — the answer of last resort, and of `focused`. */
  focused?: string;
  /** Where the caller insists, honoured while the pane still exists. */
  paneId?: string;
  /** Measured pane widths in px, keyed by pane id. Live state, never persisted. */
  widths: Record<string, number>;
  /** Per-surface placement, as set from a tab's menu; everything unspecified is `own`. */
  placements: Partial<Record<SurfaceId, PlacementMode>>;
};

/** Do two tabs belong in the same strip? The question the section rule asks of every pane.
 *
 * For everything but plugins this is "same surface", and chats are the reason the answer is a
 * function and not an equality: every conversation is its own tab and they all belong together.
 * A plugin is keyed by *which* plugin before which view — two views of one plugin are two faces
 * of one thing and share a pane, where two different plugins are two different kinds that
 * happen to share a union member. `paneFor` grouped them all together, so a sketchpad opened
 * while a browser was open went into the browser's pane. */
function sameKind(ref: TabRef, tab: TabRef): boolean {
  if (ref.surface !== tab.surface) return false;
  if (ref.surface !== "plugin") return true;
  return ref.plugin === tab.plugin;
}

/** Choose the pane a new tab opens into, by the waterfall in the module comment. */
export function choosePane(root: Node, ref: TabRef, ctx: PlacementContext): PlacementChoice {
  const all = panes(root);
  const alive = (id: string | undefined): id is string =>
    !!id && all.some((one) => one.id === id);

  if (alive(ctx.paneId)) return { paneId: ctx.paneId, beside: false };

  const mode = modeFor(ctx.placements, ref.surface);
  if (mode === "beside") return { paneId: null, beside: true };
  if (mode === "focused" && alive(ctx.focused)) return { paneId: ctx.focused, beside: false };

  const wantFit = surfaceFor(ref).minWidth;
  const fits = (id: string): boolean => {
    const measured = ctx.widths[id];
    return measured === undefined || measured >= wantFit;
  };

  /* With its kind — the focused one first, so "open a chat" from a chat pane keeps using it
   * rather than jumping to the first chat pane in render order, then any other. Unfit panes
   * are skipped here and re-read below: between "with its kind" and "visible", visible wins,
   * which is the lesson the railed chat pane taught the first version of this. */
  const grouped = all.filter((one) => one.tabs.some((tab) => sameKind(ref, tab)));
  const preferred = grouped.filter((one) => one.id === ctx.focused).concat(grouped);
  const fitting = preferred.find((one) => fits(one.id));
  if (fitting) return { paneId: fitting.id, beside: false };

  /* The section rule, and the rung `own` exists for: the kind has no pane that shows it, so
   * it gets one — not absorbed into whatever you were clicking, which is how a conversation
   * used to open into a 240px sidebar and a Work panel into the chat. The pane it raises goes
   * beside where you are, so "open the board" from the chat is still one gesture. */
  if (mode === "own") return { paneId: null, beside: true };

  if (alive(ctx.focused)) return { paneId: ctx.focused, beside: false };
  if (preferred[0]) return { paneId: preferred[0].id, beside: false };

  return { paneId: all[0]?.id ?? null, beside: false };
}
