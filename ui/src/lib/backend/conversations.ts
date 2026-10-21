/** Client for the conversation history. */

import type { ContextLedger } from "./types";

export interface ConversationSummary {
  id: string;
  title: string;
  sessionId: string;
  createdAt: string;
  updatedAt: string;
  messages: number;
  /** Where the conversation was left: the opening line of the last thing he said in it.
   *  Empty when he has not spoken yet, which is the only time `title` is worth showing —
   *  a history list is asked what came of something, and the title is what started it. */
  lastSaid: string;
  /** Absolute path to the JSONL transcript, so the settings page can reveal it. */
  transcript: string;
  /** What this session is working on. Null for a conversation that has not adopted a
   *  project — which is most of them, and is fine. It decides which project's memory he is
   *  shown here and which tasks he advances when this session is left working. */
  projectId: number | null;
  /** A turn is running in this conversation right now. Held in the server's memory rather than in
   *  a column, because it is what is happening this second — see `live_turns.live()`. */
  working: boolean;
  /** It is blocked on an answer from you. The more actionable of the two: working needs nothing
   *  from you, waiting needs only you — see `questions.waiting()`. */
  waiting: boolean;
  /** The conversation that started this one, when it is an agent — its *handler*. Null for a chat
   *  the person opened. The listing only ever returns those; an agent's own record, opened by id,
   *  is how a pane learns it is showing one and must not offer a composer. */
  parentId: string | null;
  /** What its handler calls it — `""` for the person's own chats. */
  agentName: string;
  /** Where an agent is in its life: `working`, `waiting` (on agents of its own), `done`, `stopped`
   *  or `closed`. `""` for a chat that is not an agent. */
  agentState: AgentState;
  /** Open agents anywhere under this conversation, and how many of those are busy this second.
   *  Counted by the server on the listing, so a sidebar row can say "3 agents · 2 working"
   *  without fetching the tree it summarises. Zero on a conversation opened by id. */
  agents: number;
  agentsWorking: number;
}

/** An agent's lifecycle, as `services/agents` writes it. `""` on a conversation that is not one. */
export type AgentState = "" | "working" | "waiting" | "done" | "stopped" | "closed";

/** One agent in a tree, with its own agents nested under it. Closed ones are included. */
export interface AgentNode {
  id: string;
  name: string;
  state: AgentState;
  /** A turn is live in it right now — the same fact `working` is on a chat. */
  working: boolean;
  /** It asked its handler something and is parked on the answer. Not you: an agent's questions
   *  go one link up the chain, and only the conductor's reach the person. */
  asking: boolean;
  /** Its turn is waiting for one of the server's background slots (`kernel/turns`): it has mail to
   *  read and no turn running yet. Optional because an older server never says. */
  queued?: boolean;
  model: string;
  /** The branch its worktree is on, or `""` when it shares its handler's folder. */
  branch: string;
  worktree: string;
  folder: string;
  lastSaid: string;
  createdAt: string;
  updatedAt: string;
  /** Running totals for the agent map: tokens processed (prompt and response, every round),
   *  dollars, seconds spent in turns, the context its last round carried, and that round's model. */
  tokens: number;
  costUsd: number;
  seconds: number;
  contextTokens: number;
  modelUsed: string;
  /** What it was started with — its first message from its handler. */
  task: string;
  agents: AgentNode[];
}

/** The session a tree is drawn from — the map's first node. */
export interface AgentTreeSelf {
  id: string;
  title: string;
  /** `"conductor"` for the person's chat; the agent's own name for an agent. */
  name: string;
  working: boolean;
  tokens: number;
  costUsd: number;
  seconds: number;
  contextTokens: number;
  modelUsed: string;
}

/** One link of the chain from the conductor down to a conversation. */
export interface AgentLink {
  id: string;
  /** `"conductor"` for the person's own chat at the top; the agent name below it. */
  name: string;
  state: AgentState;
}

export interface AgentTree {
  /** The conversation asked about, as the node its agents hang from. */
  self: AgentTreeSelf | null;
  /** The agents under the conversation asked about — not the whole tree unless that was the root. */
  agents: AgentNode[];
  /** The person's chat at the top of this conversation's tree: itself, when it is not an agent. */
  root: string;
  /** From the conductor down to the conversation asked about, inclusive. */
  chain: AgentLink[];
}

/** The agents under a conversation, nested, and the chain above it. */
export async function fetchAgentTree(id: string): Promise<AgentTree> {
  const response = await fetch(`/api/conversations/${encodeURIComponent(id)}/agents`);
  if (!response.ok) throw new Error(`could not read the agents (${response.status})`);
  const body = (await response.json()) as Partial<AgentTree> | null;
  return {
    self: body?.self ?? null,
    agents: body?.agents ?? [],
    root: body?.root ?? id,
    chain: body?.chain ?? [],
  };
}

/** Stop every agent under a conversation. They stay resumable: their handler can message them. */
export async function stopAgents(id: string): Promise<string[]> {
  const response = await fetch(`/api/conversations/${encodeURIComponent(id)}/agents/stop`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`could not stop the agents (${response.status})`);
  const body = (await response.json()) as { stopped?: string[] } | null;
  return body?.stopped ?? [];
}

/** One conversation, opened. Its metadata plus everything needed to render it back.
 *
 * `messages` is omitted from the summary and redeclared, because the two endpoints mean
 * different things by it: the listing gives a count, the detail gives the turns. That
 * collision was invisible while this type was written out inline — nothing extended the
 * summary, so nothing compared the two — and typing it honestly is what surfaced it. */
export interface ConversationDetail extends Omit<ConversationSummary, "messages"> {
  /** How many messages it holds — the number the listing calls `messages`. */
  messageCount: number;
  /** The turn's actual shape — reasoning, prose, calls with results — for rendering back.
   *
   *  **A page of it, not all of it.** This used to be the whole conversation: 22.83 MB on the
   *  largest real one, downloaded and parsed so that the last 40 turns — 1.06 MB — could be
   *  sliced out here and the rest held in state for a "load earlier" button. The window is the
   *  same 40 turns; it happens before the wire now. */
  timeline: StoredTurn[];
  /** Turns in the whole conversation, of which `timeline` is a page. */
  turnCount: number;
  /** Where this page starts in the whole — and what to pass as `before` for the page before
   *  it. Handed back rather than computed here, so the boundary has one definition. */
  windowStart: number;
  /** Whether anything precedes this page. */
  hasMore: boolean;
}

export async function fetchConversations(limit = 50): Promise<{
  conversations: ConversationSummary[];
  storage: { folder: string; files: number; bytes: number };
}> {
  const response = await fetch(`/api/conversations?limit=${limit}`);
  if (!response.ok) throw new Error(`/api/conversations returned ${response.status}`);
  return await response.json();
}

/** One part of a stored turn — the same shapes the live stream produces. */
export type StoredPart =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; id: string; name: string; arguments: Record<string, unknown>; result?: unknown }
  | {
      kind: "usage";
      uncached: number;
      cached: number;
      out: number;
      /** When the round landed. Absent on transcripts read by an older server. */
      at?: string;
      costUsd?: number;
      model?: string;
      provider?: string;
      serviceTier?: string;
    }
  // `baseline` is `{}` — not absent — on a turn recorded before this field existed; a
  // structurally real-but-empty reading, not a real one, which is why it's `Partial`.
  | {
      kind: "context";
      context: ContextLedger;
      baseline: Partial<ContextLedger>;
      folded: boolean;
      /** Rounds this turn had to send again. Absent on turns recorded before it was written. */
      retried?: number;
    };

export interface StoredTurn {
  /** `system` is a turn the harness started — a reminder firing, a background task finishing.
   *  It was recorded as `user` until it was noticed that this makes a scheduler's prose come back
   *  on reload as something the person said, editable. */
  role: "user" | "assistant" | "system";
  parts: StoredPart[];
  /** When the turn started, ISO. `timeline()` has always sent it; nothing read it until the
   *  footer showed a clock. `""` on a turn from before it was recorded. */
  at?: string;
  /**
   * Which turn wrote this, when the server knows.
   *
   * Only on assistant turns, and only on ones recorded since turns were given ids — the
   * transcript is append-only and every conversation on disk predates this. Absent means "no
   * stream can claim this", which is the safe reading: it renders as history, which is what a
   * turn nobody is streaming actually is.
   *
   * What it is for: a pane streaming a turn is *also* handed that turn's half-written copy by
   * `fetchConversation`, because the recorder writes as the turn runs. Matching this against the
   * `X-Kith-Turn` of the stream it owns is how it drops the copy rather than rendering both.
   */
  turn?: string;
  /**
   * Who sent this, when it was not the person.
   *
   * Agents talk to each other by mail, and mail lands in a transcript as an ordinary turn: from a
   * handler as `user` (the handler is that agent's person), from an agent below as `system`, and
   * after a restart from Kith itself. Without this the thread drew all of it as something *you*
   * said — editable, in your bubble — which in an agent's conversation is every message in it.
   * `kind` is `task` or `message` from a handler; `report`, `question` or `note` from an agent;
   * `resume` from Kith, whose `id` is `""`. The text still opens with the server's bracketed
   * heading; `toThreadMessages` takes it off, because the label drawn from this says it.
   */
  from?: TurnSender;
}

export interface TurnSender {
  id: string;
  name: string;
  kind: string;
}

/** One page of a conversation, newest last.
 *
 * `before` is the `windowStart` of a page you already hold, so paging reads as "what comes
 * before this" rather than an offset computed on both sides of the wire. Omit `turns` for the
 * server's page size, which is the same 40 the thread renders. */
export async function fetchConversation(
  id: string,
  page?: { turns?: number; before?: number },
): Promise<ConversationDetail> {
  const query = new URLSearchParams();
  if (page?.turns !== undefined) query.set("turns", String(page.turns));
  if (page?.before !== undefined) query.set("before", String(page.before));
  const suffix = query.size ? `?${query}` : "";
  const response = await fetch(`/api/conversations/${id}${suffix}`);
  if (!response.ok) throw new Error(`could not open that conversation (${response.status})`);
  return await response.json();
}

export async function renameConversation(id: string, title: string): Promise<void> {
  const response = await fetch(`/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw new Error(`could not rename (${response.status})`);
}

/** Point this session at a project, or pass null to unbind it. */
export async function setConversationProject(
  id: string,
  projectId: number | null,
): Promise<ConversationSummary> {
  const response = await fetch(`/api/conversations/${id}/project`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ projectId }),
  });
  if (!response.ok) throw new Error(`could not set the project (${response.status})`);
  return (await response.json()) as ConversationSummary;
}

/** Remove from the list. The transcript file stays unless `purge`. */
export async function deleteConversation(id: string, purge = false): Promise<void> {
  const response = await fetch(`/api/conversations/${id}?purge=${purge}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`could not remove (${response.status})`);
}

export interface TranscriptHit {
  conversationId: string;
  title: string;
  role: string;
  at: string;
  snippet: string;
}

/**
 * Find where something was said, across every transcript.
 *
 * Titles come from a conversation's first message, so without this a conversation is
 * findable by how it opened and by nothing else that happened in it.
 */
export async function searchConversations(query: string): Promise<TranscriptHit[]> {
  const response = await fetch(`/api/conversations/search?q=${encodeURIComponent(query)}`);
  if (!response.ok) return [];
  const body = (await response.json()) as { hits?: TranscriptHit[] };
  return body.hits ?? [];
}

/**
 * Which conversations have a turn running right now.
 *
 * A turn is live in the server's memory and nowhere else — the transcript only shows it once it is
 * over — so this is the one question only the server can answer, and the interface spent a long
 * time not asking it. `workspace.tsx` had `const working = false` with a comment saying the
 * presence orb and the ambient wash were waiting for one honest meaning of busy. This is it: a
 * session is busy if and only if a turn is live in it.
 *
 * Ids rather than a count, because "something is running" and "*that* conversation is running" are
 * different sentences and only the second tells you where to go.
 */
export async function fetchLiveTurns(): Promise<string[]> {
  const response = await fetch("/api/chat/live");
  if (!response.ok) return [];
  const body = (await response.json()) as { conversations?: string[] };
  return body.conversations ?? [];
}
