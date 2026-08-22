/**
 * The join between the stream and the cache. Mounted once; the rest of the app just uses queries.
 *
 * This replaces `useChanges`, which every widget called for itself and which handed each one a
 * callback to refetch with. That shape is why three events in a turn meant three rounds of every
 * subscriber's own fetch: the channel was shared but the *response* to it was not. Here there is
 * one listener for the process, it invalidates by key, and the cache decides what to actually ask
 * for — deduping identical requests and coalescing a burst into one refetch per key.
 *
 * Two things are exported because two genuinely different things listen:
 *
 * * `useLiveUpdates` — the invalidation, once, at the root. Nothing else should call it.
 * * `useServerEvent` — for the handful of consumers that are not a query at all. Rejoining a
 *   turn's stream is an *action*, not data: there is nothing to refetch, there is something to do.
 *   Making those fake a query would be worse than admitting they are not one.
 *
 * ## Coalescing
 *
 * Events are batched on a short timer rather than acted on one at a time. A turn that files three
 * tasks publishes three `task` events inside a few milliseconds, and without this the board is
 * invalidated three times — React Query would dedupe the in-flight request, but only after three
 * rounds of invalidation have already re-rendered every observer. The delay is small enough to be
 * imperceptible and large enough to cover a burst.
 *
 * ## Resync
 *
 * A `resync` means the server cannot say what was missed — the cursor is older than its log, or the
 * process restarted, or a frame was lost and the ids no longer line up. There is exactly one
 * correct response and it is the blunt one: treat everything as stale. That is what makes losing an
 * event survivable, and therefore what made the eleven timers removable.
 */

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  subscribe,
  type Change,
  type ChangeKind,
  type ServerEvent,
} from "@/lib/backend/events";
import { STALE_ON } from "@/lib/query-keys";

/** How long to gather events before acting on them. One frame's worth, near enough. */
const COALESCE_MS = 50;

/**
 * Keep the cache honest. Call once, above everything that queries.
 *
 * Deliberately returns nothing: the point is that no component has to know this exists. A widget
 * declares what it needs with `useQuery` and is re-rendered when the server says that thing moved.
 */
export function useLiveUpdates(): void {
  const cache = useQueryClient();

  useEffect(() => {
    const kinds = new Set<ChangeKind>();
    let everything = false;
    let timer: number | null = null;

    const flush = () => {
      timer = null;
      if (everything) {
        everything = false;
        kinds.clear();
        // Not `resetQueries`: reset throws the data away and every screen flashes back to a
        // spinner. Invalidating refetches under what is already on screen, which for a reconnect
        // is exactly right — the data is probably still correct and we simply cannot promise it.
        void cache.invalidateQueries();
        return;
      }
      const stale = [...kinds];
      kinds.clear();
      for (const kind of stale) {
        for (const prefix of STALE_ON[kind] ?? []) {
          void cache.invalidateQueries({ queryKey: prefix });
        }
      }
    };

    const stop = subscribe((event: ServerEvent) => {
      if (event.type === "resync") everything = true;
      else if (event.type === "changed") kinds.add((event.data as Change).kind);
      else return; // `activity` is consumed by the feed itself; it invalidates nothing.

      if (timer === null) timer = window.setTimeout(flush, COALESCE_MS);
    });

    return () => {
      stop();
      // The pending batch goes with the subscription. Left behind, it would fire from a closure
      // whose `kinds` set nothing is reading any more — harmless today and exactly the kind of
      // thing that stops being harmless when this hook is mounted somewhere conditional.
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [cache]);
}

/**
 * Run something when the server says a kind of thing changed.
 *
 * For the cases that are not data — rejoining a live turn, mainly. Everything that *is* data should
 * be a `useQuery` instead, so that it dedupes and coalesces with every other reader of the same
 * thing rather than firing its own fetch on every event.
 *
 * ```ts
 * useServerEvent("turn", rejoin, conversationId);
 * ```
 */
export function useServerEvent(
  kinds: ChangeKind | ChangeKind[],
  onChange: () => void,
  /** Only fire for events from this conversation. Events with no conversation always fire — they
   *  are about the machine rather than about one chat, and a consumer that ignored those would
   *  miss anything done from the control panel. */
  conversation?: string,
): void {
  // Held in a ref so a new callback identity does not tear the subscription down and build it
  // again on every render — which, on a component that refetches inside its own callback, is a
  // reconnect loop.
  const latest = useRef(onChange);
  latest.current = onChange;
  const wanted = Array.isArray(kinds) ? kinds.join(",") : kinds;

  useEffect(() => {
    const want = new Set(wanted.split(","));
    return subscribe((event) => {
      // A resync means we cannot know whether this fired. Running the action is the safe half:
      // every consumer here is idempotent by construction (rejoining a stream you are already on
      // is a no-op), and not running it means missing the event that mattered.
      if (event.type === "resync") {
        latest.current();
        return;
      }
      if (event.type !== "changed") return;
      const change = event.data as Change;
      if (!want.has(change.kind)) return;
      if (conversation && change.conversation && change.conversation !== conversation) return;
      latest.current();
    });
  }, [wanted, conversation]);
}
