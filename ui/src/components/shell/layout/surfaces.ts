import {
  Check,
  Circle,
  Gauge,
  Inbox,
  LayoutGrid,
  ListChecks,
  type LucideIcon,
  MessageSquare,
  MessagesSquare,
  Palette,
  Pencil,
  Plus,
  Puzzle,
  RefreshCw,
  Scroll,
  Settings,
  Square,
  Trash2,
  X,
} from "lucide-react";

import { indexSettled, pluginSurface } from "@/lib/plugin-index";

import type { SurfaceId, TabRef } from "./tree";

/**
 * What each surface is called, what it is drawn with, and how narrow it may get.
 *
 * Metadata only — no components. The layout module must not import the things it lays out, or
 * every surface's dependencies end up in the entry chunk again and the tree can no longer be
 * tested without rendering the app. `Workspace` supplies the actual elements through a `render`
 * callback, because that is where the props those components need already live.
 *
 * **`minWidth` is where the three yielding constants went.** `CHAT_FLOOR`, `WORK_YIELDS_BELOW`
 * and `HISTORY_YIELDS_BELOW` encoded the same intent — do not squeeze prose to three words a
 * line — as a hand-tuned order in which two specific panels gave way to a third. As a per
 * surface minimum it is one number in one place, enforced by the panel library, and it keeps
 * being true when there are five panes instead of three.
 */
export type Surface = {
  title: string;
  icon: LucideIcon;
  /** Narrowest this surface is worth showing, in pixels. */
  minWidth: number;
};

export const SURFACES: Record<SurfaceId, Surface> = {
  // 560 was `CHAT_FLOOR`, measured rather than chosen: below it a paragraph wraps to three
  // words a line and the thread stops being readable prose.
  chat: { title: "Chat", icon: MessageSquare, minWidth: 560 },
  // 256 was `HISTORY_WIDTH`, the width a conversation title needs before it starts eliding
  // mid-word.
  conversations: { title: "Conversations", icon: MessagesSquare, minWidth: 240 },
  // 320 was `WORK_MIN`.
  work: { title: "Work", icon: ListChecks, minWidth: 320 },
  /* The Control Panel, whole.
   *
   * Its eight sections — Projects, Roadmap, Journal, Memory, Files, Sources, Schedules,
   * Overview — are tabs *inside* it, sharing its routing and its data. Making each a dockable
   * surface of its own is a refactor of the Control Panel rather than of the layout, and doing
   * it under cover of this change would mean rewriting the board while also replacing the
   * shell. One surface here, and the split is its own piece of work. */
  board: { title: "Board", icon: LayoutGrid, minWidth: 380 },
  settings: { title: "Settings", icon: Settings, minWidth: 420 },
  inbox: { title: "Inbox", icon: Inbox, minWidth: 320 },
  context: { title: "Context", icon: Gauge, minWidth: 420 },
  /* The placeholder every plugin surface resolves through when its own declaration is not
   * (yet) known. A real static key, which is what keeps the four `SURFACES[...]` derefs safe by
   * construction rather than by a guard someone could forget — see `SurfaceId` in `tree.ts`. */
  plugin: { title: "Plugin", icon: Puzzle, minWidth: 320 },
};

/** What a surface is called and drawn with, for any tab including a plugin's.
 *
 * Every deref of `SURFACES` goes through this. A plugin tab has three answers rather than two,
 * and the third is the point: `loading` is the window between module import — when the layout
 * rehydrates from local storage — and the first `GET /api/plugins`. A tab that says "this plugin
 * is not installed" for 200ms and then works is worse than one that says nothing for 200ms.
 */
export function surfaceFor(ref: TabRef): Surface & { standing: "known" | "loading" | "absent" } {
  if (ref.surface !== "plugin") return { ...SURFACES[ref.surface], standing: "known" };
  const declared = pluginSurface(ref.plugin, ref.view);
  if (declared) {
    return {
      title: declared.title,
      icon: ICONS[declared.icon] ?? Puzzle,
      minWidth: declared.minWidth,
      standing: "known",
    };
  }
  return {
    ...SURFACES.plugin,
    title: ref.view || SURFACES.plugin.title,
    standing: indexSettled() ? "absent" : "loading",
  };
}

/** The lucide components a plugin may name, keyed by the same names the server's sprite uses.
 *
 * A plugin names an icon and never supplies one, so this is a lookup and not a loader. Kept in
 * step with `services/plugins/icons.py` by hand — a name in one and not the other renders the
 * fallback, which is visible and harmless, where accepting arbitrary markup would not be. */
const ICONS: Record<string, LucideIcon> = {
  puzzle: Puzzle,
  check: Check,
  plus: Plus,
  x: X,
  pencil: Pencil,
  trash: Trash2,
  circle: Circle,
  square: Square,
  "list-checks": ListChecks,
  scroll: Scroll,
  palette: Palette,
  gauge: Gauge,
  inbox: Inbox,
  "layout-grid": LayoutGrid,
  refresh: RefreshCw,
};

/** Narrowest a pane may be: the widest minimum among the tabs it holds.
 *
 * The widest rather than the active one's, deliberately. A pane whose minimum changed as you
 * clicked between its tabs would resize the whole layout under you on a tab click, which reads
 * as the app rearranging itself for no reason. */
export function paneMinWidth(tabs: TabRef[]): number {
  if (!tabs.length) return 240;
  return Math.max(...tabs.map((tab) => surfaceFor(tab).minWidth));
}

/** Shortest a pane is worth being. One number for every surface: unlike width, no surface here
 *  becomes unreadable at a particular height — it just gets less of itself. */
export const MIN_HEIGHT = 140;

/** The label on a tab. A chat is titled by its conversation, which the caller knows and this
 *  module deliberately does not.
 *
 * **"New chat" only for a chat that genuinely has no conversation.** It used to be the fallback
 * for any missing title, so after a reload — when no title is known yet — three open chats read
 * "New chat", "New chat" and one real name. Two tabs claiming to be a new chat, neither of them
 * one, and no way to tell which conversation either was. A chat with an id says so instead,
 * until its title arrives. */
export function tabTitle(ref: TabRef, chatTitle?: string): string {
  if (ref.surface !== "chat") return surfaceFor(ref).title;
  const given = chatTitle?.trim();
  if (given) return given;
  return ref.conversationId ? "Chat" : "New chat";
}
