/**
 * The one stream, and the two ways of reaching it.
 *
 * There were two `EventSource`s here — `/api/changes` and `/api/activity/stream` — and eleven
 * `setInterval`s beside them. The timers were not laziness: they were the safety net under a
 * channel that could silently drop, and the two channels dropped in opposite directions. One threw
 * its queue away on disconnect, so a laptop waking up lost everything and the window sat there
 * looking current. The other re-sent its whole backlog on every connect, with nothing on the wire
 * identifying a line, so a reconnect duplicated the last hundred into the feed.
 *
 * Both were the same missing thing: the events had no identity. With `id:` on every event and
 * `Last-Event-ID` on reconnect, a gap is either replayed or reported — and a channel you can resume
 * is what lets the timers go. See `server/kith/kernel/events.py` for the three ways a cursor can be
 * wrong and the answer to each.
 *
 * **Two transports, chosen by what exists rather than by a flag.** In the desktop app the stream is
 * held by the Electron main process and arrives over one receive-only IPC channel: it survives a
 * reload, sends the API token like every other call, and stays out of Chromium's
 * six-connections-per-origin pool. In a browser tab there is no shell, so this opens an
 * `EventSource` itself. The difference is ten lines and one `if`, and the alternative — one
 * transport, chosen everywhere — would mean either giving up the shell's advantages or giving up
 * being able to run the app in a tab.
 *
 * One subscription for the whole app however many widgets are watching. `subscribe` hands out
 * callbacks against it and tears it down with the last of them.
 */

/** What the server can say. `resync` is not data — it is "I cannot tell you what you missed". */
export type ServerEventType = "changed" | "activity" | "resync";

export interface ServerEvent {
  /** Position in the server's sequence. */
  id: number;
  /** Which run of the server that position belongs to. Empty if it did not say. */
  epoch: string;
  type: ServerEventType;
  data: unknown;
}

/** What can change, in the server's vocabulary. Mirrors `changes.KINDS` — the server has a test
 *  asserting every kind there is published by something, and `STALE_ON` covers every kind here. */
export type ChangeKind =
  | "turn"
  | "task"
  | "project"
  | "message"
  | "process"
  | "workspace"
  | "question"
  | "permission"
  | "schedule";

export interface Change {
  kind: ChangeKind;
  /** Which chat this belongs to, or "" when it is about the machine rather than one session. */
  conversation: string;
}

type Listener = (event: ServerEvent) => void;

/** The shell's channel, when there is a shell. Declared here because this is the only user. */
interface KithShell {
  onEvent(listener: (event: ServerEvent) => void): () => void;
}

declare global {
  interface Window {
    kith?: KithShell;
  }
}

const listeners = new Set<Listener>();
let close: (() => void) | null = null;

/** The last id we saw, so a gap is visible. */
let cursor = 0;
/** Which run of the server that id belongs to. A new one means everything we hold is suspect. */
let epoch = "";

/**
 * `<epoch>-<n>` as the server writes it. A bare number is read as no epoch, which is what the
 * server's own fallback expects.
 */
function readId(raw: string): { epoch: string; n: number } {
  const cut = raw.lastIndexOf("-");
  if (cut === -1) return { epoch: "", n: Number(raw) || 0 };
  return { epoch: raw.slice(0, cut), n: Number(raw.slice(cut + 1)) || 0 };
}

/**
 * Deliver to everyone, over a copy of the set.
 *
 * A listener invalidates queries, invalidating can mount or unmount a widget, and both would
 * otherwise be changing this set while it is being walked.
 */
function deliver(event: ServerEvent): void {
  /* Two ways to notice we are out of step, and both become the same thing.
   *
   * A **gap** in the numbers means something was lost between here and the server — a subscriber
   * queue that overflowed, a frame dropped in transit — and the client cannot know what. This is
   * why the server's queues may drop their oldest rather than block a write.
   *
   * A **new epoch** means the server restarted. The server refuses a cursor from another run, so
   * this is the belt to that braces: it also covers the desktop shell, where the main process
   * reconnects on our behalf and the page would otherwise carry on from state gathered before an
   * outage it never saw. */
  const restarted = epoch !== "" && event.epoch !== "" && event.epoch !== epoch;
  const gap = !restarted && cursor > 0 && event.id > cursor + 1;
  if (restarted || gap) {
    cursor = event.id;
    epoch = event.epoch;
    for (const fire of [...listeners]) fire({ ...event, type: "resync", data: {} });
    if (event.type === "resync") return;
  }
  if (event.id > 0) cursor = event.id;
  if (event.epoch) epoch = event.epoch;
  for (const fire of [...listeners]) fire(event);
}

/** Open the stream through the shell if it is there, and directly if it is not. */
function open(): () => void {
  if (window.kith) return window.kith.onEvent(deliver);

  /* A bare path, and no token: an `EventSource` cannot send headers, and putting the token in the
   * query string would print it into the request log on every reconnect. The route is gated on
   * being same-origin server-side instead — see `api/auth.py`, where this exemption is written
   * down along with the fact that the desktop app no longer relies on it.
   *
   * No `onerror` handler. `EventSource` reconnects on its own and sends `Last-Event-ID` as it
   * does, which is the whole recovery path; a handler that closed the source would turn one
   * dropped connection into a window that never updates again. */
  const source = new EventSource("/api/events");
  const read = (type: ServerEventType) => (event: MessageEvent<string>) => {
    let data: unknown;
    try {
      data = JSON.parse(event.data);
    } catch {
      return; /* a malformed frame is not worth breaking the stream over */
    }
    const { epoch: run, n } = readId(event.lastEventId ?? "");
    deliver({ id: n, epoch: run, type, data });
  };
  const changed = read("changed");
  const activity = read("activity");
  const resync = read("resync");
  source.addEventListener("changed", changed as EventListener);
  source.addEventListener("activity", activity as EventListener);
  source.addEventListener("resync", resync as EventListener);
  return () => {
    source.removeEventListener("changed", changed as EventListener);
    source.removeEventListener("activity", activity as EventListener);
    source.removeEventListener("resync", resync as EventListener);
    source.close();
  };
}

/** Listen to the stream. Returns a function that stops listening. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  if (!close) close = open();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      close?.();
      close = null;
    }
  };
}

/** True in the desktop shell, where the stream is held by the main process. */
export function throughTheShell(): boolean {
  return Boolean(window.kith);
}
