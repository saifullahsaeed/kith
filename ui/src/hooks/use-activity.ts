import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchActivityStatus,
  fetchRecentActivity,
  type ActivityItem,
  type ActivityStatus,
} from "@/lib/backend/activity";
import { subscribe } from "@/lib/backend/events";
import { keys } from "@/lib/query-keys";

const MAX_ACTIVITY = 200;

/** The live feed of what he is doing, and what is waiting on you.
 *
 * This was `useAutonomy`, and it exposed controls — start a session working, rest one, force
 * a step, cancel one. There is nothing to control now: work happens in a turn you started.
 * What it still does is watch.
 *
 * **Snapshot, then subscribe, joined on a cursor.** The backlog comes from `GET /api/activity/
 * recent` and the stream carries what follows. That ordering fixes a real bug rather than being a
 * tidier way of writing the same thing: the old `/api/activity/stream` re-sent the whole recent
 * buffer to every new connection, with no id on the wire, so every reconnect duplicated the last
 * hundred lines into the feed.
 *
 * The join is the part worth care. The stream is *already running* when this mounts — in the
 * desktop shell it has been running since before the window existed — so lines arrive while the
 * snapshot request is in flight, and the two halves overlap. The snapshot therefore comes with the
 * log position it was taken at, and everything above that position is what the stream contributes.
 * No content matching, no "keep whichever arrived first": one number decides it.
 *
 * That number is also why the rendered list is derived rather than accumulated. An earlier version
 * copied the backlog into state in an effect and appended streamed lines to it, which broke twice
 * over — a line arriving before the snapshot resolved discarded the backlog permanently, and after
 * a resync the refetched backlog was often deeply equal to the last one, so React Query handed
 * back the same array reference and the effect that would have restored it never ran.
 */
export function useActivity() {
  const cache = useQueryClient();
  /** Lines from the stream, with the log position each arrived at. */
  const [streamed, setStreamed] = useState<{ at: number; item: ActivityItem }[]>([]);

  const { data: status = null } = useQuery({
    queryKey: keys.activityStatus(),
    queryFn: fetchActivityStatus,
  });

  const { data: snapshot } = useQuery({
    queryKey: keys.activityRecent(),
    queryFn: fetchRecentActivity,
    // The stream keeps this current afterwards, so re-asking on focus would fetch a backlog that
    // overlaps lines already on screen — which the cursor would then have to throw away again.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  useEffect(() => {
    return subscribe((event) => {
      if (event.type === "resync") {
        /* We cannot know what was missed. Both halves are rebuilt: the backlog is refetched, and
         * the streamed lines are dropped because the cursor they were filed under belongs to a
         * position the new snapshot will supersede. */
        setStreamed([]);
        void cache.invalidateQueries({ queryKey: keys.activityRecent() });
        return;
      }
      if (event.type !== "activity") return;
      const item = event.data as ActivityItem;
      // A turn ending can change what is waiting on you — a task moved to `review`, a plan filed
      // for approval — so the status is re-read rather than inferred from the line.
      if (item.kind === "status" || item.kind === "done") {
        void cache.invalidateQueries({ queryKey: keys.activityStatus() });
      }
      setStreamed((was) =>
        // Keyed by log position, so a replayed line after a reconnect replaces rather than repeats.
        was.some((one) => one.at === event.id)
          ? was
          : [...was, { at: event.id, item }].slice(-MAX_ACTIVITY),
      );
    });
  }, [cache]);

  const activity = useMemo(() => {
    const backlog = snapshot?.activity ?? [];
    const at = snapshot?.at ?? 0;
    // Everything the stream delivered above where the snapshot was taken. Below it is already in
    // the backlog, and showing it twice is the bug this whole seam exists to prevent.
    const after = streamed.filter((one) => one.at > at).map((one) => one.item);
    return [...backlog, ...after].slice(-MAX_ACTIVITY);
  }, [snapshot, streamed]);

  const refresh = () => void cache.invalidateQueries({ queryKey: keys.activityStatus() });

  return { status: status as ActivityStatus | null, activity, refresh };
}
