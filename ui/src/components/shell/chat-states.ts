import type { ConversationSummary } from "@/lib/backend";
import type { TabState } from "@/components/shell/layout/layout-view";

/**
 * What each open chat is doing, in the one word its tab has room for.
 *
 * Three facts arrive from three places and none of them knows about the others: which chats have a
 * turn running (`/api/chat/live`), which are blocked on a question (`waiting` on the conversation
 * list), and which have agents working under them (`agentsWorking` on the same list). This module
 * ranks them, so the tab strip, the rail and the presence pill cannot disagree about what a chat is
 * doing — they all read the same answer.
 *
 * Pure on purpose. The ranking is the part that is easy to get subtly wrong (a permission pinned on
 * the wrong chat, a pill pointing at a chat that is not running anything), and a function of plain
 * data is one that can be tested without mounting the window.
 */

/**
 * How many agents are busy under each of the person's chats, keyed by that chat's id.
 *
 * Only roots: the listing holds the person's chats and nothing else, and the count on each is for
 * its whole tree, however deep. That is the right grain for a tab and for the pill — "this chat has
 * two agents working somewhere under it" — and the tree itself is a click away.
 */
export function agentsWorking(chats: readonly ConversationSummary[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const chat of chats) if (chat.agentsWorking > 0) out.set(chat.id, chat.agentsWorking);
  return out;
}

/**
 * Which chat a pending permission request belongs to, when that can be said honestly.
 *
 * A request carries no conversation — the server raises it from inside whatever turn hit the
 * gate, and the gate *waits*, so that turn is still live. Among the live chats, the ones blocked on
 * a question are accounted for; if exactly one is left, the request is its. With two parked turns
 * there is no telling which asked, and a guess on the wrong tab is worse than no mark: the prompt
 * above every composer and the bell still say it.
 */
export function permissionHolder(
  pending: number,
  live: readonly string[],
  waiting: ReadonlySet<string>,
): string | null {
  if (!pending) return null;
  const parked = live.filter((id) => !waiting.has(id));
  return parked.length === 1 ? (parked[0] ?? null) : null;
}

export interface ChatFacts {
  live: ReadonlySet<string>;
  waiting: ReadonlySet<string>;
  /** The chat a pending permission is pinned on — see `permissionHolder`. */
  asking: string | null;
  /** Busy agents under each of the person's chats — see `agentsWorking`. */
  agents: ReadonlyMap<string, number>;
}

/**
 * One chat's state, ranked: what needs you, then what is running, then what runs in the
 * background.
 *
 * Needing you outranks working because it is the only one of the three that stops without you.
 * A live turn outranks its chat's agents because the turn is the thing happening in front of you,
 * and it is usually the conductor reading what they sent back.
 */
export function chatState(id: string, facts: ChatFacts): TabState | undefined {
  if (!id) return undefined;
  if (facts.waiting.has(id)) {
    return { tone: "needs", tag: "Asking", said: "He is asking you something" };
  }
  if (facts.asking === id) {
    return { tone: "needs", tag: "Needs yes", said: "He needs your yes to carry on" };
  }
  if (facts.live.has(id)) return { tone: "working", tag: "Working", said: "He is working here" };
  const busy = facts.agents.get(id) ?? 0;
  if (busy) {
    return { tone: "agents", tag: agentCount(busy), said: `${agentCount(busy)} working under this chat` };
  }
  return undefined;
}

/** What the presence pill in the title bar says. */
export type Presence =
  | { kind: "waiting"; chats: number; target: string }
  | { kind: "working"; since: number }
  /** Agents busy under a chat. `root` is the chat whose tree the pill opens; `target` is that chat
   *  when it is not the one in front of you, and empty when it is. */
  | { kind: "agents"; count: number; root: string; target: string }
  | { kind: "here" };

/**
 * Kith's state in one pill, led by the chat in front of you.
 *
 * The chat you are looking at speaks first — if it is asking, he is waiting on you; if its turn is
 * live, he is working, with the clock; if agents are busy anywhere in its tree, it says how many.
 * Otherwise the window-wide facts: agents carrying on under another chat, then anything elsewhere
 * that needs you. `target` is the chat the pill would take you to, which is empty when that is
 * where you already are.
 *
 * `root` is the person's chat at the top of the one in front of you — itself, unless you are
 * reading one of its agents, which the listing does not hold and so cannot be asked about.
 */
export function presenceFor(
  current: string,
  facts: ChatFacts,
  since: number | undefined,
  root: string = current,
): Presence {
  const needing = new Set(facts.waiting);
  if (facts.asking) needing.add(facts.asking);
  const here = current ? chatState(current, facts) : undefined;
  if (here?.tone === "needs") return { kind: "waiting", chats: needing.size, target: "" };
  if (here?.tone === "working") return { kind: "working", since: since ?? Date.now() };
  const mine = root ? (facts.agents.get(root) ?? 0) : 0;
  if (mine) return { kind: "agents", count: mine, root, target: "" };
  if (facts.agents.size) {
    // Elsewhere: every busy agent in the window, and the first chat they work under as where to go.
    let count = 0;
    for (const busy of facts.agents.values()) count += busy;
    const first = facts.agents.keys().next().value ?? "";
    return { kind: "agents", count, root: first, target: first };
  }
  if (needing.size) {
    const first = needing.values().next().value ?? "";
    return { kind: "waiting", chats: needing.size, target: first === current ? "" : first };
  }
  return { kind: "here" };
}

/** `1:08`, `12:40`, `1:02:03` — a turn's elapsed time, as the pill counts it. */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

/** "1 agent", "3 agents". */
export function agentCount(count: number): string {
  return `${count} agent${count === 1 ? "" : "s"}`;
}
