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
    | "retrying"
    /** A sub-agent's own work — see `errand` below. */
    | "errand";
  text: string;
  at: string;
  tokens?: { round: number; uncached: number; cached: number; out: number };
  tool?: string;
  args?: Record<string, string>;
  /** Present on `errand` lines: which sub-agent this is, and where it has got to.
   *  `running` opens one, `step` is a tool call inside it, `done` closes it. The id is what
   *  keeps three scouts sent in the same round from reading as one confused list. */
  errand?: { id: string; state: "running" | "step" | "done"; objective: string };
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

/**
 * The last lines of the feed — the snapshot a window opens with.
 *
 * `openActivityStream` was here, and it opened an `EventSource` of its own on
 * `/api/activity/stream`. That stream is gone: the feed comes down `/api/events` as `activity`
 * events now, one connection for the whole app, with an id on every line. See
 * `lib/backend/events.ts` and `hooks/use-activity.ts`.
 */
export interface ActivityBacklog {
  activity: ActivityItem[];
  /** The event-log position this snapshot was taken at. Everything the stream delivers above it is
   *  new; everything at or below it is already in `activity`. See `hooks/use-activity.ts`. */
  at: number;
}

export async function fetchRecentActivity(): Promise<ActivityBacklog> {
  const response = await fetch("/api/activity/recent");
  if (!response.ok) throw new Error(`/api/activity/recent returned ${response.status}`);
  const body = (await response.json()) as Partial<ActivityBacklog>;
  return { activity: body.activity ?? [], at: body.at ?? 0 };
}
