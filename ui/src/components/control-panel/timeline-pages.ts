/** The panel's one timeline query: the lifetime as pages, newest first, loaded as far back as
 * anything on screen has asked for.
 *
 * It was one request for the newest 500 events, and Lifetime ended wherever the 500th fell — late
 * August, on a mind that started on the 1st. Now `GET /api/brain/timeline?page=1` answers a page
 * and whether there is more, and each older page is asked for strictly before the last event of
 * the one above it. Today reads the same cache (it only wants the newest things, which page one
 * always is), so the panel makes one request for both screens, not one each.
 *
 * Invalidated by the `["timeline"]` prefix in `query-keys.ts`; react-query then refetches every
 * loaded page in order, each from the refreshed cursor of the page above, so a refresh never
 * leaves a gap or a repeat at a page edge. */
import { useCallback, useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

import { fetchTimelinePage } from "@/lib/backend/brain";
import type { TimelineEvent, TimelinePage } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";
import { msOf } from "./day-format";
import { chipTotals, joinPages } from "./lifetime-fold";
import type { LifeFilter } from "./lifetime-fold";

/** What Lifetime needs to page: everything here is about the whole loaded history, unfiltered. */
export interface Paging {
  /** Each chip's count over all of history, or `null` when the server sent none. */
  totals: Record<LifeFilter, number> | null;
  /** When the first thing ever happened (ms), from the server; `null` if it did not say. */
  oldest: number | null;
  /** The oldest event loaded so far (ms), or `null` with nothing loaded. */
  reached: number | null;
  hasMore: boolean;
  loadingMore: boolean;
  /** The last older page failed. Nothing asks again by itself until you do. */
  failed: boolean;
  /** Ask for the next older page — a no-op while one is on its way or there is none. */
  loadMore: () => void;
}

/** The cursor for the page after `last`: its oldest event's time, or none at the end. */
export function nextCursor(last: TimelinePage): string | undefined {
  return last.has_more ? last.events[last.events.length - 1]?.at : undefined;
}

export function useTimelinePages() {
  const query = useInfiniteQuery({
    queryKey: keys.timeline(),
    queryFn: ({ pageParam }) => fetchTimelinePage({ before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });
  const pages = query.data?.pages;
  const events: TimelineEvent[] = useMemo(() => joinPages(pages ?? []), [pages]);

  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = query;
  /* `cancelRefetch: false`, because the default is the opposite of what three callers want: a
     second call while a page is on its way would abandon it and ask again, and a scroll fires
     dozens of those before the first answer is back. */
  const loadMore = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage({ cancelRefetch: false });
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const paging: Paging = useMemo(() => {
    const first = pages?.[0];
    const last = events[events.length - 1];
    return {
      // Page one is refetched first on every refresh, so its totals are the current ones.
      totals: chipTotals(first),
      oldest: first?.oldest ? msOf(first.oldest) : null,
      reached: last ? msOf(last.at) : null,
      hasMore: hasNextPage,
      loadingMore: isFetchingNextPage,
      failed: isFetchNextPageError,
      loadMore,
    };
  }, [pages, events, hasNextPage, isFetchingNextPage, isFetchNextPageError, loadMore]);

  return { query, events, paging };
}
