/**
 * What is in the window, itemised — asked for, rather than streamed.
 *
 * The meter's categories say how much: "code he has read, 831k". They cannot say what of, and
 * that is the part you can act on — 831k of files he needed once and 831k of one file read sixty
 * times are the same figure and completely different problems.
 *
 * Its own request rather than more fields on the `context` event every turn already streams: that
 * event is persisted into the transcript (167 of them on one real conversation here), so carrying
 * a couple of thousand items on each would grow every stored turn forever to serve a screen that
 * is open for a few seconds a month. Fetched when the screen opens and not held anywhere.
 */

import type { ContextLine } from "@/lib/backend/types";

/** One tool call's contribution, and how many times it was the same call. */
export interface ContextItem {
  /** The `ContextLine.key` this belongs under, so it can be shown inside its category. */
  key: string;
  tool: string;
  /** What the call was about — a path, a pattern, a skill's name. Empty when the call's own
   *  arguments do not say, in which case `wasted` is always 0: the server will not claim a
   *  repeat it cannot prove. */
  subject: string;
  calls: number;
  tokens: number;
  /** What could be dropped without losing anything — every copy but the newest. */
  wasted: number;
}

export interface ContextDetail {
  /** False on a conversation no turn has measured yet. The difference between "nothing is in the
   *  window" and "nothing has looked", which a 0% chart would silently conflate. */
  reading: boolean;
  window: number;
  used: number;
  free: number;
  share: number;
  folded: boolean;
  lines: ContextLine[];

  /* ── Below here is the conversation, not the window, and the two must not be mixed. ──
   *
   * A reading measures the request a turn actually sent: the conversation *after* the fold, with
   * old turns replaced by a brief and their tool results gone with them. The items are the whole
   * transcript. On one real conversation those are 556k and 2.36M tokens — the window is a
   * quarter of the history — so `wasted` shown as a share of `used` read "121% of the window",
   * which is how this got noticed.
   *
   * Still the actionable half: a file read fifty-one times is a habit that will refill the window
   * whether or not those copies survived the last fold. */

  items: ContextItem[];
  /** Every item's waste, totalled. The one figure here that is a decision rather than a fact. */
  wasted: number;
  /** What the items add up to — the denominator `wasted` is a share of. Never `used`. */
  itemsTotal: number;

  /** The prompt itself, message by message. */
  sent: Sent;
  /** What the harness told the last turn, mid-turn — the landing nudge, a dead round, an empty
   *  one, the budget running out.
   *
   *  Reported beside `sent` rather than inside it, and that is not a layout choice. `sent` is the
   *  prompt a turn would build from the transcript *now*; a directive belongs to one round of one
   *  turn that has already happened, and the transcript deliberately does not replay one. Without
   *  this the screen was quietly missing up to four messages per turn that really were sent. */
  directives: string[];
  /** What the provider billed for the last round, or null when there has not been one. */
  lastRound: LastRound | null;
}

/** One message of the prompt, as the list shows it. No `text` — see `fetchContextMessage`. */
export interface SentMessage {
  /** 1-based, matching what the screen prints beside the row. */
  index: number;
  role: string;
  /** Which tool produced it, for a `tool` message. "tool" is a role, not an answer. */
  tool: string;
  chars: number;
  tokens: number;
  preview: string;
  /** Against the prompt the last turn sent. `rewritten` is the live block, which is neither —
   *  it is rebuilt every turn, which is why the tail of a prompt is never cached. */
  change: "kept" | "added" | "rewritten";
  live: boolean;
  /** The tool calls this message carries, with the arguments they were made with. Empty on
   *  everything that is not an assistant turn making a call. A call is a real message in the
   *  prompt, so the command inside it is part of what gets sent and has to be visible. */
  calls: ToolCall[];
}

export interface ToolCall {
  name: string;
  /** The arguments on one line — the command itself when there is only one. */
  args: string;
}

/** A message the last turn carried that the next one will not — what a fold or trim removed. */
export interface DroppedMessage {
  role: string;
  tool: string;
  tokens: number;
  preview: string;
}

export interface RoleTotal {
  role: string;
  tokens: number;
  count: number;
  share: number;
}

export interface Sent {
  /** A fold is owed before the next real turn sends. The preview never pays for one, so on such
   *  a conversation this is the prompt that would go if it did not — worth saying rather than
   *  papering over, on a screen whose whole point is that its numbers are the real ones. */
  foldPending: boolean;
  tokens: number;
  messages: SentMessage[];
  byRole: RoleTotal[];

  /* ── against the prompt the last turn sent ──
   *
   * The comparison is the part that actually explains context management. A total says the
   * prompt is large; the difference says one question added an assistant message, a tool call
   * and a tool result, and that all three are in every request from now on.
   *
   * `previousLiveTokens` is carved out of the arithmetic on both sides: the live block is
   * rewritten rather than added, so counting it as either makes `before + added = after` fail by
   * a few thousand tokens for a reason nobody can find. */

  /** False on a first turn — there is no previous prompt, and "+100%" against one that never
   *  happened would be inventing the comparison rather than making it. */
  hasPrevious: boolean;
  previousTokens: number;
  previousLiveTokens: number;
  addedTokens: number;
  dropped: DroppedMessage[];
  droppedTokens: number;
}

export interface LastRound {
  model: string;
  provider: string;
  promptTokens: number;
  responseTokens: number;
  cachedTokens: number;
  cacheWriteTokens: number;
  costUsd: number;
}

const NOTHING: ContextDetail = {
  reading: false,
  window: 0,
  used: 0,
  free: 0,
  share: 0,
  folded: false,
  lines: [],
  items: [],
  wasted: 0,
  itemsTotal: 0,
  sent: {
    foldPending: false,
    tokens: 0,
    messages: [],
    byRole: [],
    hasPrevious: false,
    previousTokens: 0,
    previousLiveTokens: 0,
    addedTokens: 0,
    dropped: [],
    droppedTokens: 0,
  },
  directives: [],
  lastRound: null,
};

/** One message in full. Its own request so the list can stay small: the transcript behind a long
 *  conversation runs to millions of tokens, and the screen shows one message at a time. */
export async function fetchContextMessage(
  conversationId: string,
  index: number,
): Promise<{ index: number; role: string; tool: string; text: string }> {
  const empty = { index, role: "", tool: "", text: "" };
  if (!conversationId) return empty;
  const response = await fetch(`/api/chat/${conversationId}/context/message/${index}`).catch(
    () => null,
  );
  if (!response || !response.ok) return empty;
  return (await response.json().catch(() => empty)) ?? empty;
}

export async function fetchContextDetail(conversationId: string): Promise<ContextDetail> {
  if (!conversationId) return NOTHING;
  const response = await fetch(`/api/chat/${conversationId}/context`).catch(() => null);
  if (!response || !response.ok) return NOTHING;
  const body = (await response.json().catch(() => null)) as Partial<ContextDetail> | null;
  return body ? { ...NOTHING, ...body } : NOTHING;
}
