/**
 * Reading a server-sent-events stream, as pure string handling.
 *
 * Lifted out of `events.ts`, which is where it was written and the wrong place for it. That module
 * is about a *connection* — Electron windows, an HTTP client, a reconnect backoff — and none of that
 * has anything to say about where a frame ends. Split, this half is a function of a string and can
 * be tested as one, which matters more here than usual: incremental parsing is exactly the kind of
 * code that works on every frame you happen to try by hand and then fails on the one that arrives
 * split across two TCP reads.
 *
 * The browser gets this for free — `EventSource` is the parser — so this exists only because the
 * desktop shell holds the stream in its main process, where there is no `EventSource` to use. See
 * `events.ts` for why the stream lives there at all.
 */

/** One event, in the shape both transports hand the renderer. See ui/src/lib/backend/events.ts. */
export interface ParsedEvent {
  /** Position in the server's sequence. */
  id: number;
  /** Which run of the server that position belongs to — see `kernel/events.EPOCH`. */
  epoch: string;
  type: string;
  data: unknown;
  /** The id exactly as it arrived, for handing back as `Last-Event-ID`. Reassembling it from the
   *  two halves above would be a second place for the format to be defined. */
  raw: string;
}

/**
 * `<epoch>-<n>` as the server writes it.
 *
 * A bare number is read as no epoch, which is what a server too old to name its run sends — and
 * what the server's own fallback expects to receive back.
 */
export function splitId(id: string): { epoch: string; n: number } {
  const cut = id.lastIndexOf("-");
  if (cut === -1) return { epoch: "", n: Number(id) || 0 };
  return { epoch: id.slice(0, cut), n: Number(id.slice(cut + 1)) || 0 };
}

/**
 * A function you feed chunks to, which calls back once per complete event.
 *
 * Frames are separated by a blank line and nothing promises a chunk contains a whole one, so the
 * tail is held until it is terminated. Comment lines — which is what the keep-alive `: ping` is —
 * carry nothing and are skipped.
 */
export function reader(onEvent: (event: ParsedEvent) => void): (chunk: string) => void {
  let buffer = "";

  return (chunk: string) => {
    buffer += chunk;
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf("\n\n");

      let id = "";
      let type = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith(":")) continue; // a comment, which is what the keep-alive is
        // A line with no colon is a field with an empty value, per the spec. Ours never sends one,
        // and splitting on an index of -1 would quietly mangle the field name instead of ignoring
        // it — the kind of thing that shows up as a stream that half works.
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? "" : line.slice(colon + 1).trimStart();
        if (field === "id") id = value;
        else if (field === "event") type = value;
        // A `data:` line can legally repeat; the server never sends more than one, and joining is
        // cheaper than asserting it never will.
        else if (field === "data") data = data ? `${data}\n${value}` : value;
      }

      if (!data) continue;
      try {
        const { epoch, n } = splitId(id);
        onEvent({ id: n, epoch, type, data: JSON.parse(data), raw: id });
      } catch {
        // A malformed frame is not worth dropping the connection over: the next event prompts the
        // same refetch, and reconnecting would replay this one and fail on it again.
      }
    }
  };
}
