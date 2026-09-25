/**
 * When he puts something in front of you, open it.
 *
 * The other half of `show_canvas` (once `show_board`). He could make designs and never show one — asked to, he went
 * looking for the word "board" with `find`, `git log` and two plugin reads, because the surface
 * that draws them belongs to the interface and nothing told him it could be opened.
 *
 * The event carries no payload (`kernel/changes` never does), so this is the refetch that
 * follows the ping: ask what he is showing, open it, and take it. Taken immediately, because the
 * state means "he just asked" and not "this is on screen" — leaving it set would reopen the board
 * every time the app relaunched, and a board that reappears on its own is worse than one that
 * never appeared.
 */
import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchShowing, takeShowing } from "@/lib/backend/brain";
import { useBoardOverlay } from "@/lib/design/board-overlay";
import { keys } from "@/lib/query-keys";

export function useShowing(): void {
  const cache = useQueryClient();
  const show = useBoardOverlay((state) => state.show);

  const { data } = useQuery({
    queryKey: keys.showing(),
    queryFn: fetchShowing,
    // Only ever refetched by a `showing` change — polling for this would be a timer, and the
    // whole point of the event stream is that nothing owns one.
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!data || data.kind !== "board") return;
    show({ taskId: data.taskId, projectId: null, title: data.note || undefined });
    void takeShowing().then(() => cache.setQueryData(keys.showing(), null));
  }, [data, show, cache]);
}
