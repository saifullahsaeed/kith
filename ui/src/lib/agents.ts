import { create } from "zustand";

import type { AgentNode, TurnSender } from "@/lib/backend";

/**
 * Agents, as the interface reads them: the tree under a chat, flattened for drawing, and the words
 * for what each one is doing and for who sent a message.
 *
 * An agent is a whole conversation that another conversation started — its handler — and it talks
 * to nobody but that handler. The person talks only to the chat they opened, the *conductor*. So
 * everything the window says about agents is said *about* a tree it cannot type into: a sidebar row
 * that folds out, a dialog from the title bar, a banner where an agent's composer would be. All
 * three read these helpers, which is what keeps them saying the same thing about the same agent.
 */

/** Which chat's agent tree the title bar's dialog is showing, if any. A store rather than state
 *  because the pill opens it and the window draws it once, in `AgentTreeHost`. */
export const useAgentTree = create<{
  root: string;
  show: (root: string) => void;
  close: () => void;
}>((set) => ({
  root: "",
  show: (root) => set({ root }),
  close: () => set({ root: "" }),
}));

/** One agent in a tree drawn as a list, with how deep it sits. */
export interface AgentRow {
  node: AgentNode;
  /** 0 for the conductor's own agents, 1 for theirs, and so on. */
  depth: number;
}

/**
 * The tree as rows, parents before their children, in the order the server sent them (oldest
 * first). Closed agents are left out unless asked for — and their subtrees with them, because
 * closing an agent closes everything under it, so a closed branch is closed all the way down.
 */
export function flattenAgents(nodes: readonly AgentNode[], closed = false, depth = 0): AgentRow[] {
  const out: AgentRow[] = [];
  for (const node of nodes) {
    if (!closed && node.state === "closed") continue;
    out.push({ node, depth });
    out.push(...flattenAgents(node.agents ?? [], closed, depth + 1));
  }
  return out;
}

/** The tree as the map draws it: closed agents (and so their subtrees) left out unless asked for. */
export function prunedAgents(nodes: readonly AgentNode[], closed = false): AgentNode[] {
  return nodes
    .filter((node) => closed || node.state !== "closed")
    .map((node) => ({ ...node, agents: prunedAgents(node.agents ?? [], closed) }));
}

/** One agent anywhere in a tree, by id. */
export function findAgent(nodes: readonly AgentNode[], id: string): AgentNode | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    const below = findAgent(node.agents ?? [], id);
    if (below) return below;
  }
  return null;
}

/**
 * How long an agent has been at it, as the map shows it: from when it was started to now while it
 * is busy, and to the last thing it did once it is not. Wall time, not turn time — "23m" means it
 * has been going for 23 minutes, which is the question a glance at the map is asking.
 */
export function agentTook(
  node: Pick<AgentNode, "createdAt" | "updatedAt" | "state" | "working" | "asking" | "queued">,
  now: number,
): string {
  const started = Date.parse(node.createdAt);
  if (Number.isNaN(started)) return "";
  const ended = busyLook(agentLook(node)) ? now : Date.parse(node.updatedAt);
  return durationWord(((Number.isNaN(ended) ? now : ended) - started) / 1000);
}

/** Still at it, as far as a clock is concerned: running, queued to run, or parked on someone else's work. */
function busyLook(look: AgentLook): boolean {
  return look === "live" || look === "queued" || look === "asking" || look === "waiting";
}

/** `23m 26s`, `1h 4m` — the map's way of saying a duration. */
function durationWord(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/**
 * How long a whole tree has been at it: from its first agent's start to now while any agent is
 * still busy, and to the last thing any of them did once none is. Closed agents count — they were
 * part of the work — which is also why a tree of nothing but closed agents still has an age.
 */
export function treeTook(nodes: readonly AgentNode[], now: number): string {
  let started = Infinity;
  let ended = -Infinity;
  let busy = false;
  for (const { node } of flattenAgents(nodes, true)) {
    const born = Date.parse(node.createdAt);
    const last = Date.parse(node.updatedAt);
    if (!Number.isNaN(born)) started = Math.min(started, born);
    if (!Number.isNaN(last)) ended = Math.max(ended, last);
    if (busyLook(agentLook(node))) busy = true;
  }
  if (!Number.isFinite(started)) return "";
  return durationWord(((busy || !Number.isFinite(ended) ? now : ended) - started) / 1000);
}

/** A tree at a glance — the Activity tab's card. */
export interface AgentSummary {
  /** Agents in each state, anywhere in the tree. `idle` is not a state an agent is ever in, so it
   *  is not counted. */
  working: number;
  /** Waiting for a turn slot on the server — busy, but not running yet. */
  queued: number;
  waiting: number;
  asking: number;
  done: number;
  stopped: number;
  closed: number;
  /** Tokens the agents have processed between them — the conductor's own are not in it. */
  tokens: number;
  /** Who is parked on a question to its handler, oldest first. */
  askingNames: string[];
}

export function summarizeAgents(nodes: readonly AgentNode[]): AgentSummary {
  const out: AgentSummary = {
    working: 0,
    queued: 0,
    waiting: 0,
    asking: 0,
    done: 0,
    stopped: 0,
    closed: 0,
    tokens: 0,
    askingNames: [],
  };
  for (const { node } of flattenAgents(nodes, true)) {
    out.tokens += node.tokens || 0;
    const look = agentLook(node);
    if (look === "live") out.working += 1;
    else if (look === "queued") out.queued += 1;
    else if (look === "asking") {
      out.asking += 1;
      out.askingNames.push(node.name);
    } else if (look === "waiting") out.waiting += 1;
    else if (look === "done") out.done += 1;
    else if (look === "stopped") out.stopped += 1;
    else if (look === "closed") out.closed += 1;
  }
  return out;
}

/** `446512` → `446.5k`, `1234567` → `1.2M`: a token count at a glance. */
export function tokensWord(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}

/** Open, busy and closed agents anywhere in a tree. */
export function countAgents(nodes: readonly AgentNode[]): {
  open: number;
  working: number;
  closed: number;
} {
  let open = 0;
  let working = 0;
  let closed = 0;
  for (const { node } of flattenAgents(nodes, true)) {
    if (node.state === "closed") {
      closed += 1;
      continue;
    }
    open += 1;
    if (agentLook(node) === "live" || agentLook(node) === "asking") working += 1;
  }
  return { open, working, closed };
}

/**
 * What an agent is doing, in the one word a dot can carry.
 *
 * `live` is a turn running — or about to: an agent whose state says working with no turn yet has
 * mail on its way in, and calling it idle for the second before its turn starts would make a
 * freshly started agent blink. `queued` is the same agent when the server says the wait is for a
 * turn slot rather than a second — a big tree runs a few agents at a time, and the rest would
 * otherwise all look busy at once. `asking` is parked on a question to its handler; it is still
 * busy, but on its handler rather than on you, which is why it is not amber.
 */
export type AgentLook = "live" | "queued" | "asking" | "waiting" | "done" | "stopped" | "closed" | "idle";

export function agentLook(node: Pick<AgentNode, "state" | "working" | "asking" | "queued">): AgentLook {
  if (node.state === "closed") return "closed";
  if (node.working) return "live";
  if (node.asking) return "asking";
  if (node.queued) return "queued";
  if (node.state === "working") return "live";
  if (node.state === "waiting") return "waiting";
  if (node.state === "stopped") return "stopped";
  if (node.state === "done") return "done";
  return "idle";
}

/** The same, as words — for a tooltip, a dialog row, a screen reader. */
export const AGENT_WORD: Record<AgentLook, string> = {
  live: "working",
  queued: "queued for a turn slot",
  asking: "asking its handler",
  waiting: "waiting on its own agents",
  done: "done",
  stopped: "stopped",
  closed: "closed",
  idle: "idle",
};

/** How each state looks as a dot, wherever an agent is listed. Green is a turn running, as
 *  everywhere; teal is agents' own colour, for one parked on its handler or waiting on agents of
 *  its own; the ends of a life are grey, a stop drawn hollow because it can be resumed and a close
 *  faint because it cannot. Never amber: nothing an agent waits on is you. */
export const AGENT_DOT: Record<AgentLook, string> = {
  live: "bg-roam animate-[kith-breathe_2.4s_ease-in-out_infinite]",
  // Hollow green: next to run, not running. Still, because nothing is happening in it yet.
  queued: "border border-roam",
  asking: "bg-teal-500 dark:bg-teal-400 animate-[kith-breathe_2.4s_ease-in-out_infinite]",
  waiting: "bg-teal-500/70 dark:bg-teal-400/70",
  done: "bg-muted-foreground/60",
  stopped: "border border-muted-foreground/70",
  closed: "bg-muted-foreground/25",
  idle: "bg-muted-foreground/35",
};

/** How far one level of a tree steps in, in pixels — the dialog and the sidebar alike. */
export const AGENT_INDENT = 14;

/**
 * What a message from another session is called, in three pieces so the name can be a link.
 *
 * The server's own heading is written for the model ("Question from your agent “api” — it is
 * waiting for your answer. Reply with `message_agents`…"), which is the right thing to tell him
 * and the wrong thing to show you. This is the same fact said for a reader: who, and what kind.
 */
export function senderLabel(from: TurnSender): { before: string; name: string; after: string } {
  switch (from.kind) {
    case "task":
      return { before: "Task from ", name: from.name, after: "" };
    case "report":
      return { before: "Report from ", name: from.name, after: "" };
    case "question":
      return { before: "", name: from.name, after: " is asking" };
    case "note":
      return { before: "Note from ", name: from.name, after: "" };
    // Kith itself, after a restart: no one to link to, and "from Kith" says less than this does.
    case "resume":
      return { before: "Kith restarted", name: "", after: "" };
    default:
      return { before: "From ", name: from.name, after: "" };
  }
}

/** Where a message from another session carries its sender — see `StoredTurn.from`. A key in
 *  `metadata.custom`, the one place assistant-ui keeps anything of ours on a user or system
 *  message; `toThreadMessages` writes it and the thread's message components read it. */
export const SENT_BY = "kithFrom";

/** Read `SENT_BY` back off a message's custom metadata, or undefined when it is not there. */
export function sentBy(custom: Record<string, unknown> | undefined): TurnSender | undefined {
  const from = custom?.[SENT_BY] as Partial<TurnSender> | undefined;
  return from && typeof from === "object" && typeof from.kind === "string"
    ? { id: String(from.id ?? ""), name: String(from.name ?? ""), kind: from.kind }
    : undefined;
}

/** The server's bracketed heading, alone on the first line — see `senderLabel`. */
const HEADING = /^\s*\[[^\n]*\][ \t]*(?:\r?\n|$)/;

/** A mailed message without the heading its label now says, and without the blank lines under
 *  it. Untouched when it has none: only the first line is ever taken, and only if it is one. */
export function withoutHeading(text: string): string {
  const found = HEADING.exec(text);
  if (!found) return text;
  return text.slice(found[0].length).replace(/^(?:[ \t]*\r?\n)+/, "");
}
