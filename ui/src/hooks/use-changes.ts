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
 * **One connection for the whole app, however many widgets are watching.** This used to build an
 * `EventSource` per subscriber, on the reasoning that SSE over HTTP/2 multiplexes onto a single
 * connection. It does — but there is no HTTP/2 here. Kith is served by Werkzeug, which speaks
 * HTTP/1.1 only, and Chromium will not negotiate h2 without TLS. So each subscriber took a whole TCP
 * connection out of a pool that Chromium caps at six per origin, and seven call sites plus the
 * activity feed plus the turn's own `POST /api/chat` do not fit in six.
 *
 * What that cost: measured on 2026-08-14, the renderer held exactly six sockets and had issued no
 * request of any kind for four minutes while a turn sat parked on an `ask`. Every fetch was queued
 * in Chromium behind a full pool — the question card, the health check, and on Cmd-R the document
 * itself, which is why reloading a frozen window did not help either. It surfaced on `ask` because
 * an ordinary turn's stream closes in seconds, so the shortage was transient; a parked turn holds
 * its socket for the whole fifteen-minute deadline. Stop looked like the cure because aborting a
 * fetch is client-side: it frees a slot, and the queued poll goes out at last.
 *
 * Sharing costs no reconnection logic, which was the other half of the old reasoning: one
 * `EventSource` reconnects itself exactly as seven did. `subscribe` hands out callbacks against the
 * one socket and closes it with the last of them.
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
  | "workspace";

interface Change {
  kind: ChangeKind;
  conversation: string;
  at: string;
}

type Listener = (change: Change) => void;

/** The one socket, built on the first subscriber and closed with the last. */
let shared: EventSource | null = null;
const listeners = new Set<Listener>();

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  if (shared === null) {
    // A bare path, and the route is exempt from the token: an EventSource cannot send headers, and
    // putting the token in the query string would print it into the request log on every reconnect.
    // Gated on same-origin server-side instead — the same trade `/api/activity/stream` already made,
    // and this one carries even less: the name of what changed, never a value.
    shared = new EventSource("/api/changes");
    shared.onmessage = (event) => {
      let change: Change;
      try {
        change = JSON.parse(event.data) as Change;
      } catch {
        return; /* a malformed line is not worth breaking the stream over */
      }
      // Over a copy: a listener refetches, a refetch can mount or unmount a widget, and both would
      // otherwise be changing this set while it is being walked.
      for (const fire of [...listeners]) fire(change);
    };
    // No `onerror` handler: EventSource reconnects on its own, and a handler that closed the source
    // would turn one dropped connection into a window that never updates again.
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      shared?.close();
      shared = null;
    }
  };
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
    // Sharing a socket is not sharing a filter: each subscriber still hears only its own kinds.
    return subscribe((change) => {
      if (!want.has(change.kind)) return;
      if (conversation && change.conversation && change.conversation !== conversation) return;
      latest.current();
    });
  }, [wanted, conversation]);
}
