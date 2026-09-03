import {
  Gauge,
  Inbox,
  LayoutGrid,
  ListChecks,
  type LucideIcon,
  MessageSquare,
  MessagesSquare,
  Settings,
} from "lucide-react";

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
};

/** Narrowest a pane may be: the widest minimum among the tabs it holds.
 *
 * The widest rather than the active one's, deliberately. A pane whose minimum changed as you
 * clicked between its tabs would resize the whole layout under you on a tab click, which reads
 * as the app rearranging itself for no reason. */
export function paneMinWidth(tabs: TabRef[]): number {
  if (!tabs.length) return 240;
  return Math.max(...tabs.map((tab) => SURFACES[tab.surface].minWidth));
}

/** Shortest a pane is worth being. One number for every surface: unlike width, no surface here
 *  becomes unreadable at a particular height — it just gets less of itself. */
export const MIN_HEIGHT = 140;

/** The label on a tab. A chat is titled by its conversation, which the caller knows and this
 *  module deliberately does not. */
export function tabTitle(ref: TabRef, chatTitle?: string): string {
  if (ref.surface === "chat") return chatTitle?.trim() || "New chat";
  return SURFACES[ref.surface].title;
}
