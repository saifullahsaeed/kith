import { create } from "zustand";
import type { QueryClient } from "@tanstack/react-query";

import { findAgent } from "@/lib/agents";
import { fetchAgentTree, type AgentTree, type ConversationDetail } from "@/lib/backend";
import { keys } from "@/lib/query-keys";

/**
 * Agents opened *inside* their conductor's tab, rather than as tabs of their own.
 *
 * Opening an agent used to open a top-level tab for it. A conductor that started six agents and had
 * each looked at once left seven tabs in the title bar, and every tab you have visited keeps its
 * `ChatPane` mounted — a runtime, a page of transcript, its queries and, while a turn runs, a live
 * stream each. The bar became a mess and the window got slower with every agent you checked on.
 *
 * So an agent is a *sub-view* of the tab its tree hangs from: the conductor's tab grows a slim strip
 * (the conductor, then each agent opened in it) and shows one of them at a time. Only the shown one
 * is mounted — see `workspace.surfaceBody` — which is the performance half of this.
 *
 * Keyed by the root conversation, the same id the conductor's tab is keyed on, and pruned when that
 * tab goes (`keep`), so a closed tab takes its sub-views with it the way it takes its draft.
 *
 * **Persisted**, unlike drafts. A draft holds `File` objects and is keyed on a tab `uid` that only
 * lives inside the layout blob; this holds two conversation ids per tab, both of which mean the same
 * thing after a reload as before it. Every read and write is wrapped, so a window without storage
 * simply opens every tab on its conductor.
 */

export interface AgentViews {
  /** The agents opened in this tab, in the order they were first opened — the strip's order. */
  open: string[];
  /** What the tab shows: one of `open`, or the root itself for the conductor. */
  shown: string;
}

const KEY = "kith-agent-views";

/** The stored views, validated per tab: one bad entry costs that tab its strip, not every tab. */
export function readViews(): Record<string, AgentViews> {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const held = JSON.parse(raw) as Record<string, unknown> | null;
    const out: Record<string, AgentViews> = {};
    for (const [root, value] of Object.entries(held ?? {})) {
      const entry = value as Partial<AgentViews> | null;
      if (!root || !entry || !Array.isArray(entry.open)) continue;
      const open = [...new Set(entry.open.filter((id): id is string => typeof id === "string" && !!id && id !== root))];
      if (!open.length) continue;
      const shown = typeof entry.shown === "string" && open.includes(entry.shown) ? entry.shown : root;
      out[root] = { open, shown };
    }
    return out;
  } catch {
    /* not JSON, private mode, no storage — every tab on its conductor is a correct answer */
    return {};
  }
}

function writeViews(views: Record<string, AgentViews>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(views));
  } catch {
    /* storage full or unavailable — which agent a tab was showing is a convenience, not the work */
  }
}

export const useAgentViews = create<{
  /** root conversation -> its tab's sub-views. Absent for a tab no agent has been opened in. */
  views: Record<string, AgentViews>;
  /** Show `id` in `root`'s tab, adding it to the strip if it is not there. `id === root` is back to
   *  the conductor. */
  show: (root: string, id: string) => void;
  /** Add `id` to the strip without changing what is shown — an agent's old tab folded in. */
  fold: (root: string, id: string) => void;
  /** Take `id` off the strip. If it was showing, its left-hand neighbour shows instead — the agent
   *  opened before it, or the conductor. */
  close: (root: string, id: string) => void;
  /** Drop the views of every tab not in `roots`. */
  keep: (roots: ReadonlySet<string>) => void;
}>((set, get) => {
  const commit = (views: Record<string, AgentViews>) => {
    writeViews(views);
    set({ views });
  };
  const add = (root: string, id: string, select: boolean) => {
    if (!root || !id || id === root) return;
    const { views } = get();
    const was = views[root];
    const open = was?.open.includes(id) ? was.open : [...(was?.open ?? []), id];
    const shown = select ? id : (was?.shown ?? root);
    if (was && open === was.open && shown === was.shown) return;
    commit({ ...views, [root]: { open, shown } });
  };
  return {
    views: readViews(),
    show: (root, id) => {
      if (!root) return;
      if (id && id !== root) return add(root, id, true);
      const { views } = get();
      const was = views[root];
      if (!was || was.shown === root) return;
      commit({ ...views, [root]: { ...was, shown: root } });
    },
    fold: (root, id) => add(root, id, false),
    close: (root, id) => {
      const { views } = get();
      const was = views[root];
      const at = was ? was.open.indexOf(id) : -1;
      if (!was || at < 0) return;
      const open = was.open.filter((one) => one !== id);
      const next = { ...views };
      if (open.length) {
        next[root] = { open, shown: was.shown === id ? (open[at - 1] ?? root) : was.shown };
      } else {
        delete next[root];
      }
      commit(next);
    },
    keep: (roots) => {
      const { views } = get();
      const gone = Object.keys(views).filter((root) => !roots.has(root));
      if (!gone.length) return;
      const next = { ...views };
      for (const root of gone) delete next[root];
      commit(next);
    },
  };
});

/** What `root`'s tab is showing: an agent, or the root itself. */
export function shownIn(views: Record<string, AgentViews>, root: string): string {
  return views[root]?.shown || root;
}

/**
 * The conductor at the top of `id`'s tree, if this window already knows it — `""` if it does not.
 *
 * Asked before anything is fetched, because almost every open is of a chat the person talks to, and
 * waiting a round trip to learn that a sidebar row is its own conductor would put a delay on every
 * click that had none. Four things already in the cache can say: its own tree (which names the root
 * outright), the conversation's own record (no handler means it is a root), any conversations
 * listing (which only ever holds the person's chats), and any tree it appears *in* — which is how
 * an agent clicked in the sidebar's fold, the map or the strip opens without a request, since each
 * of those drew it from its conductor's tree. An agent's record alone cannot say — its handler may
 * itself be an agent — so an agent in none of these falls through to the server.
 */
export function knownConductor(cache: QueryClient, id: string): string {
  if (!id) return "";
  const tree = cache.getQueryData<AgentTree>(keys.agentTree(id));
  if (tree?.root) return tree.root;
  const own = cache.getQueryData<ConversationDetail>(keys.conversation(id));
  if (own && !own.parentId) return id;
  for (const [, data] of cache.getQueriesData<unknown>({ queryKey: ["conversations"] })) {
    // Most listings cache the rows; one caches the whole response. Either shape, nothing else.
    const rows = Array.isArray(data) ? data : (data as { conversations?: unknown } | undefined)?.conversations;
    if (!Array.isArray(rows)) continue;
    if (rows.some((row: { id?: string; parentId?: string | null }) => row?.id === id && !row.parentId)) return id;
  }
  for (const [, held] of cache.getQueriesData<AgentTree>({ queryKey: ["agentTree"] })) {
    if (held?.root && findAgent(held.agents ?? [], id)) return held.root;
  }
  return "";
}

/** The conductor at the top of `id`'s tree — from the cache when it can say, the server when not.
 *  A server that cannot answer gets `id` back: opened as a chat of its own, which is what every
 *  conversation was before agents. */
export async function conductorOf(cache: QueryClient, id: string): Promise<string> {
  const known = knownConductor(cache, id);
  if (known) return known;
  try {
    const tree = await cache.fetchQuery({ queryKey: keys.agentTree(id), queryFn: () => fetchAgentTree(id) });
    return tree.root || id;
  } catch {
    return id;
  }
}

/** The newest open asked for. One window, one answer to "what did they click last". */
let latest = 0;

/**
 * Open a conversation where it lives: its conductor's tab, focused (or opened) by `focus`, showing
 * `id` — the conductor itself when `id` is one, an agent as a sub-view when it is not.
 *
 * Usually synchronous (see `knownConductor`); when the tree has to be asked for, a later open
 * supersedes this one, so a slow answer about an agent cannot land on top of a chat clicked after
 * it. `supersedeOpens` is the same for an open that never goes through here — a new chat.
 */
export function openInConductor(cache: QueryClient, id: string, focus: (root: string) => void): Promise<void> {
  const ticket = ++latest;
  const land = (root: string) => {
    if (ticket !== latest) return;
    focus(root);
    if (id) useAgentViews.getState().show(root, id);
  };
  const known = knownConductor(cache, id);
  if (!id || known) {
    land(known || id);
    return Promise.resolve();
  }
  return conductorOf(cache, id).then(land);
}

/** Forget any open still waiting on the server: something else has been put in front since. */
export function supersedeOpens(): void {
  latest += 1;
}
