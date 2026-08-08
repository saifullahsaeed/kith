/** Client for the activity API — the live feed, and what is waiting on you.
 *
 * This was `autonomy.ts`, and most of it was controls: start a session working, rest one,
 * force a step, cancel the step in flight. Nothing runs unasked now, so there is nothing to
 * start and nothing to cancel. What is left is the half that was never about autonomy —
 * watching what he does, and being told when something wants your attention.
 */

export interface ActivityStatus {
  /** Everything he was shown, which counts a cached prefix again on every round. */
  tokensIn?: number;
  tokensOut?: number;
  /** The prompt side with cache hits removed — what a provider actually had to read. */
  tokensUncached?: number;
  costUsd?: number;
  /**
   * Work he has finished and handed over for checking — the `review` column.
   *
   * Optional because an older server does not send it, and the panel must not blank out over a
   * missing field: that exact assumption crashed the settings page once.
   */
  toReview?: { id: number; goal: string }[];
  /**
   * Plans drafted with the planning-a-task skill, waiting for a look before any
   * implementation starts — the `planning` column. Same optionality reasoning as `toReview`.
   */
  toApprove?: { id: number; goal: string }[];
}

export interface ActivityItem {
  /** Every kind the server emits, and no more. `reflect`, `curious` and `consolidate` were
   *  here until the scheduled inner life was removed; `start` and `breakout` went with the
   *  self-directed loop. */
  kind:
    | "reply"
    | "tool"
    | "thought"
    | "reminder"
    | "done"
    | "error"
    | "status"
    | "tokens"
    | "retrying";
  text: string;
  at: string;
  tokens?: { round: number; uncached: number; cached: number; out: number };
  tool?: string;
  args?: Record<string, string>;
  /** Which session this line belongs to. Absent on the genuinely global lines — a status
   *  change — which every session shows, because they are about the machine rather than
   *  about one piece of work. */
  conversation?: string;
}

export async function fetchActivityStatus(): Promise<ActivityStatus> {
  const response = await fetch("/api/activity/status");
  if (!response.ok) throw new Error(`/api/activity/status returned ${response.status}`);
  return (await response.json()) as ActivityStatus;
}

/** Subscribe to the server-sent activity stream. Returns an unsubscribe fn. */
export function openActivityStream(onItem: (item: ActivityItem) => void): () => void {
  const source = new EventSource("/api/activity/stream");
  source.onmessage = (event) => {
    try {
      onItem(JSON.parse(event.data) as ActivityItem);
    } catch {
      /* ignore malformed frames */
    }
  };
  return () => source.close();
}
