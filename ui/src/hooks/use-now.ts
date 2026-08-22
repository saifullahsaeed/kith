/**
 * Re-render on a clock, without asking the server anything.
 *
 * The distinction this exists to make: **a clock is not a poll.** "Started 12 minutes ago" goes
 * stale on its own, with nothing on the server having changed and nothing to refetch — so a timer
 * is genuinely the right mechanism for it. "Has a task moved?" is not that, and every interval in
 * this app that looked like the first was actually the second.
 *
 * Keeping them apart is what makes the remaining timers legible. Two intervals were left in the
 * interface after the change channel took over, and both of them are this: one for how long a
 * background process has been running, one for the relative times on the control panel.
 *
 * ```ts
 * const now = useNow(30_000);   // re-renders every half minute
 * ```
 */

import { useEffect, useState } from "react";

export function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(timer);
  }, [everyMs]);
  return now;
}

/**
 * How long ago, in the shortest form that is still true.
 *
 * Matches the server's `_ago` — see `engine/run/processes.py` — because the two used to be one
 * string rendered there, and a panel that phrased the same duration differently from the tool
 * output beside it would read as two different facts.
 */
export function ago(since: number, now: number): string {
  const seconds = Math.max(0, Math.floor(now / 1_000 - since));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}m`;
  const minutes = Math.floor((seconds % 3_600) / 60);
  return `${Math.floor(seconds / 3_600)}h${String(minutes).padStart(2, "0")}m`;
}
