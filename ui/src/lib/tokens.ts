/**
 * What a model request actually cost, in tokens.
 *
 * The prompt token count a provider reports is not a measure of work: it counts the
 * whole prompt, and with prompt caching most of that is a re-read of bytes the provider
 * already holds. A measured chat turn showed 8,034 prompt tokens of which 8,016 were
 * cache hits — so the honest figure for that request is 122, not 8,138. Displaying the
 * raw number would make every reply look sixty times more expensive than it was.
 *
 * `uncached` is the prompt with the cache hits removed, computed once on the server
 * (`agent_loop.measured`) so nothing here has to know how a provider spells its usage
 * fields. This module exists so the two places that show the number — a chat round and
 * a turn — cannot drift apart on what it means.
 */

export interface Usage {
  /** Prompt tokens the provider actually had to read, cache hits already removed. */
  uncached: number;
  /** Prompt tokens served from its cache. Not billed as work; shown for context only. */
  cached: number;
  /** Tokens generated. */
  out: number;
}

export function formatTokens(count: number): string {
  return count.toLocaleString();
}

/** "74.9k", "1.05M" — for a spot where the exact figure matters less than seeing it move.
 *
 * The context meter used to show only a rounded percentage, and on a 1M+-token window that
 * is too coarse to be worth looking at turn to turn: 74,887 -> 78,308 tokens is a real 3,421-
 * token jump, and both round to "7%". The percentage was correct and the conversation was
 * genuinely growing; it just looked frozen, because a whole percentage point of a million-
 * token window is tens of thousands of tokens. This is what actually moves every turn. */
export function formatCompact(count: number): string {
  if (count < 1_000) return String(count);
  if (count < 1_000_000) return `${(count / 1_000).toFixed(1)}k`;
  return `${(count / 1_000_000).toFixed(2)}M`;
}

