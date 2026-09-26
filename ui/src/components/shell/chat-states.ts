import type { ActivityItem } from "@/lib/backend/activity";
import type { TabState } from "@/components/shell/layout/layout-view";

/**
 * What each open chat is doing, in the one word its tab has room for.
 *
 * Three facts arrive from three places and none of them knows about the others: which chats have a
 * turn running (`/api/chat/live`), which are blocked on a question (`waiting` on the conversation
 * list), and which have a workflow going (the workflow board, with the live feed laid over it).
 * This module ranks them, so the tab strip, the rail and the presence pill cannot disagree about
 * what a chat is doing — they all read the same answer.
 *
 * Pure on purpose. The ranking is the part that is easy to get subtly wrong (a finished workflow
 * resurrected by an older feed line, a permission pinned on the wrong chat), and a function of
 * plain data is one that can be tested without mounting the window.
 */

/** A workflow the board or the feed says is still going, as the tab needs it. */
export interface RunningWorkflow {
  run: string;
  conversation: string;
  name: string;
  /** The phase it is in, as the script named it ("Survey"). Empty before the first phase line. */
  phase: string;
}

/** A run as `GET /api/workflows` lists it — only the fields this module reads. */
export interface BoardRunLite {
  run: string;
  conversation?: string;
  name?: string;
  phase?: string;
  state: string;
}

/** Every run on the server, for every conversation. Empty on any failure: a tab without its
 *  workflow word is a smaller loss than a window that will not draw. */
export async function fetchAllWorkflows(): Promise<BoardRunLite[]> {
  try {
    const response = await fetch("/api/workflows");
    if (!response.ok) return [];
    const body = (await response.json()) as { runs?: BoardRunLite[] } | null;
    return body?.runs ?? [];
  } catch {
    return [];
  }
}

const ENDED = new Set(["finished", "failed", "stopped"]);

/**
 * The running workflow of each conversation, keyed by conversation id.
 *
 * The board is the record and the feed is the heartbeat — the same split `workflows.tsx` makes for
 * the Work panel. The board survives the feed's ring rolling over; the feed carries a run that
 * started a moment ago, before the board has been re-read. Either one saying a run has ended is
 * final: the feed's lines are oldest first and a stale `started` must not bring a finished run
 * back to life.
 */
export function runningWorkflows(
  board: BoardRunLite[],
  lines: ActivityItem[],
): Map<string, RunningWorkflow> {
  const runs = new Map<string, RunningWorkflow & { ended: boolean }>();
  for (const one of board) {
    runs.set(one.run, {
      run: one.run,
      conversation: one.conversation ?? "",
      name: one.name ?? "workflow",
      phase: one.phase ?? "",
      ended: ENDED.has(one.state),
    });
  }
  for (const line of lines) {
    const mark = line.workflow;
    if (!mark) continue;
    const found = runs.get(mark.run) ?? {
      run: mark.run,
      conversation: line.conversation ?? "",
      name: mark.name ?? "workflow",
      phase: "",
      ended: false,
    };
    if (!found.conversation && line.conversation) found.conversation = line.conversation;
    if (mark.state === "started" && mark.name) found.name = mark.name;
    else if (mark.state === "phase") found.phase = line.text;
    else if (ENDED.has(mark.state)) found.ended = true;
    runs.set(mark.run, found);
  }
  const byChat = new Map<string, RunningWorkflow>();
  for (const { ended, ...run } of runs.values()) {
    if (ended || !run.conversation) continue;
    byChat.set(run.conversation, run);
  }
  return byChat;
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
  workflows: ReadonlyMap<string, RunningWorkflow>;
}

/**
 * One chat's state, ranked: what needs you, then what is running, then what runs in the
 * background.
 *
 * Needing you outranks working because it is the only one of the three that stops without you.
 * A live turn outranks its chat's workflow because the turn is the thing happening in front of
 * you; the workflow is still on the Activity tab.
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
  const flow = facts.workflows.get(id);
  if (flow) {
    return {
      tone: "workflow",
      tag: flow.phase || "Workflow",
      said: `Workflow ${flow.name}${flow.phase ? ` — ${flow.phase}` : ""} running`,
    };
  }
  return undefined;
}

/** What the presence pill in the title bar says. */
export type Presence =
  | { kind: "waiting"; chats: number; target: string }
  | { kind: "working"; since: number }
  | { kind: "workflow"; count: number; target: string }
  | { kind: "here" };

/**
 * Kith's state in one pill, led by the chat in front of you.
 *
 * The chat you are looking at speaks first — if it is asking, he is waiting on you; if its turn is
 * live, he is working, with the clock. Otherwise the window-wide facts: workflows carrying on in
 * the background, then anything elsewhere that needs you. `target` is the chat the pill would take
 * you to, which is empty when that is where you already are.
 */
export function presenceFor(
  current: string,
  facts: ChatFacts,
  since: number | undefined,
): Presence {
  const needing = new Set(facts.waiting);
  if (facts.asking) needing.add(facts.asking);
  const here = current ? chatState(current, facts) : undefined;
  if (here?.tone === "needs") return { kind: "waiting", chats: needing.size, target: "" };
  if (here?.tone === "working") return { kind: "working", since: since ?? Date.now() };
  if (facts.workflows.size) {
    const mine = current && facts.workflows.has(current);
    const first = facts.workflows.keys().next().value ?? "";
    return { kind: "workflow", count: facts.workflows.size, target: mine ? "" : first };
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
