import { useEffect, useRef } from "react";

/**
 * Run something when the server says a kind of thing changed.
 *
 * The app had one push channel — the activity feed — and eleven `setInterval`s asking for everything
 * else on their own clocks, between 1.2 and 20 seconds. So a new task appeared when the working-task
 * card next polled, a background task when *that* one did, the board when the control panel got round
 * to it: one reload showed you one of them and a second showed you another.
 *
 * The event carries no data on purpose. It says "tasks changed"; you refetch through the endpoint you
 * already use. A missed event therefore costs a stale second, not a wrong screen — and there is one
 * shape here however many consumers appear.
 *
 * One `EventSource` per subscriber rather than one shared: SSE over HTTP/2 multiplexes on a single
 * connection, and the browser reconnects each of them on its own with backoff. Sharing one would mean
 * writing that reconnection logic here, badly.
 *
 * ```ts
 * useChanges(["task", "turn"], load);          // any conversation
 * useChanges("process", load, conversationId); // this conversation's only
 * ```
 */
export type ChangeKind =
  | "turn"
  | "task"
  | "project"
  | "message"
  | "process"
  | "conversation"
  | "workspace";

interface Change {
  kind: ChangeKind;
  conversation: string;
  at: string;
}

export function useChanges(
  kinds: ChangeKind | ChangeKind[],
  onChange: () => void,
  /** Only fire for events from this conversation. Events with no conversation always fire — they are
   *  about the machine rather than about one chat, and a widget that ignored those would miss the
   *  board changing from the control panel. */
  conversation?: string,
) {
  // Held in a ref so a new callback identity does not tear down and rebuild the connection every
  // render — which, on a component that refetches in its own callback, is a reconnect loop.
  const latest = useRef(onChange);
  latest.current = onChange;
  const wanted = Array.isArray(kinds) ? kinds.join(",") : kinds;

  useEffect(() => {
    const want = new Set(wanted.split(","));
    // A bare path, and the route is exempt from the token: an EventSource cannot send headers, and
    // putting the token in the query string would print it into the request log on every reconnect.
    // Gated on same-origin server-side instead — the same trade `/api/activity/stream` already made,
    // and this one carries even less: the name of what changed, never a value.
    const source = new EventSource("/api/changes");
    source.onmessage = (event) => {
      try {
        const change = JSON.parse(event.data) as Change;
        if (!want.has(change.kind)) return;
        if (conversation && change.conversation && change.conversation !== conversation) return;
        latest.current();
      } catch {
        /* a malformed line is not worth breaking the stream over */
      }
    };
    // No `onerror` handler: EventSource reconnects on its own, and a handler that closed the source
    // would turn one dropped connection into a window that never updates again.
    return () => source.close();
  }, [wanted, conversation]);
}
