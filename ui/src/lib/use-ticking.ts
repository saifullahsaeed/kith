import { useEffect, useState } from "react";

/**
 * The current time, re-read once a second while `active` and frozen otherwise.
 *
 * `hooks/use-now` always ticks, which is right for a panel with one clock in it. A thread has a
 * strip per turn and a card per workflow, and only the live ones have a clock worth moving — a
 * forty-turn conversation ticking forty timers to redraw nothing is the cost this avoids.
 */
export function useTicking(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}
