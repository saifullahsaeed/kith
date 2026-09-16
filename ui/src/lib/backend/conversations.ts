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
  | { kind: "usage"; uncached: number; cached: number; out: number }
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
