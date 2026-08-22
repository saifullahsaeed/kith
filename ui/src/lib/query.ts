/**
 * One cache for everything the server knows.
 *
 * Before this, server state lived in fifty-odd components as `useState` + `useEffect` + `fetch`,
 * each with its own loading flag, its own error handling and — in eleven of them — its own
 * `setInterval`. Nothing deduped: two widgets showing the same board made two requests, and a
 * change event ran every subscriber's refetch separately. Nothing coalesced: three events in a
 * turn meant three rounds of all of it. And because each widget decided for itself when to ask,
 * two halves of one screen could disagree about what the server had said.
 *
 * The defaults below are the whole policy, and they are chosen against the push channel rather
 * than in ignorance of it:
 *
 * **`staleTime` is a minute, not zero and not forever.** Freshness is pushed here — an event
 * invalidates, and the refetch is immediate — so a timer is not what keeps this current. But
 * `Infinity` would also switch off refetch-on-focus and refetch-on-reconnect, which are the floor
 * under a channel that can drop. A minute means a remount inside a minute is free, and coming back
 * to the window after being away always asks.
 *
 * **Focus and reconnect both refetch.** These replace the eleven timers, and they replace them
 * with something better: a timer asks when the clock says so, this asks when there is a reason to
 * think you might have missed something.
 *
 * **One retry.** The server is on loopback. A failure here is a restart or a real error, and the
 * first is over in well under a second while the second is not helped by asking four more times.
 */

import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      retry: 1,
      // A local server either answers or has gone away; waiting longer between two tries at a
      // socket on this machine is time spent showing a spinner for nothing.
      retryDelay: 400,
    },
  },
});
