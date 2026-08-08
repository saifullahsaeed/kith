import { useCallback, useEffect, useState } from "react";

import {
  fetchActivityStatus,
  openActivityStream,
  type ActivityItem,
  type ActivityStatus,
} from "@/lib/backend/activity";

const MAX_ACTIVITY = 200;

/** The live feed of what he is doing, and what is waiting on you.
 *
 * This was `useAutonomy`, and it exposed controls — start a session working, rest one, force
 * a step, cancel one. There is nothing to control now: work happens in a turn you started.
 * What it still does is watch.
 */
export function useActivity() {
  const [status, setStatus] = useState<ActivityStatus | null>(null);
  const [activity, setActivity] = useState<ActivityItem[]>([]);

  const refresh = useCallback(() => {
    fetchActivityStatus()
      .then(setStatus)
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const close = openActivityStream((item) => {
      // A turn ending can change what is waiting on you — a task moved to `review`, a plan
      // filed for approval — so re-read rather than infer it from the line.
      if (item.kind === "status" || item.kind === "done") refresh();
      setActivity((prev) => [...prev, item].slice(-MAX_ACTIVITY));
    });
    return close;
  }, [refresh]);

  return { status, activity, refresh };
}
