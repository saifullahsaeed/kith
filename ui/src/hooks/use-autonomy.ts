import { useCallback, useEffect, useRef, useState } from "react";

import {
  controlAutonomy,
  fetchAutonomyStatus,
  openActivityStream,
  type ActivityItem,
  type AutonomyStatus,
} from "@/lib/backend/autonomy";

const MAX_ACTIVITY = 200;

/** Tracks autonomy status and the live activity feed, and exposes controls. */
export function useAutonomy() {
  const [status, setStatus] = useState<AutonomyStatus | null>(null);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const statusRef = useRef<AutonomyStatus | null>(null);
  statusRef.current = status;

  const refresh = useCallback(() => {
    fetchAutonomyStatus()
      .then(setStatus)
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const close = openActivityStream((item) => {
      // Status changes (start/stop/tick boundaries) — re-pull the real status.
      if (item.kind === "status" || item.kind === "start" || item.kind === "done") {
        refresh();
      }
      setActivity((prev) => [...prev, item].slice(-MAX_ACTIVITY));
    });
    return close;
  }, [refresh]);

  const start = useCallback(async () => setStatus(await controlAutonomy("start")), []);
  const stop = useCallback(async () => setStatus(await controlAutonomy("stop")), []);
  const tick = useCallback(async () => {
    await controlAutonomy("tick");
    refresh();
  }, [refresh]);
  // Stops the step in flight and leaves roaming alone — so this is not `stop` with a
  // different name, and the two are never interchangeable.
  const cancel = useCallback(async () => setStatus(await controlAutonomy("cancel")), []);

  return { status, activity, start, stop, tick, cancel, refresh };
}
