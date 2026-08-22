/**
 * The one event stream, held by the shell rather than by the page.
 *
 * The renderer used to open this itself — two `EventSource`s, in fact, one for what changed and
 * one for the activity feed. That works, and it costs more than it looks:
 *
 * **The connection outlived nothing.** Closing the window *hides* it, a reload replaces the
 * document, a crashed renderer is recreated — and each of those dropped the subscription and
 * started a new one with no idea where the old one got to. Every gap was silent, which is why the
 * interface kept eleven timers as a safety net. Held here, the stream and its cursor belong to the
 * *app*: the page can come and go underneath it.
 *
 * **It could not authenticate.** A browser `EventSource` cannot send headers, so the route had to
 * be exempted from the API token and gated on being same-origin instead — a hole that existed
 * purely because of a limitation in the client. Node's HTTP client has no such limitation, so the
 * shell's stream sends `X-Kith-Token` like every other call it makes.
 *
 * **It was in Chromium's socket pool.** The renderer loads the backend over plain HTTP/1.1, where
 * Chromium allows six connections per origin — and two of them were permanently spent on streams.
 * A parked turn holding a third is how a window came to sit there with every fetch queued behind a
 * full pool. This connection is Node's, and Node does not share that pool.
 *
 * `Last-Event-ID` is the whole point. See `server/kith/kernel/events.py` for the three ways a
 * cursor can be wrong; this end's job is to remember it across everything the page cannot.
 */

import * as http from "node:http";
import * as https from "node:https";

import { apiHeaders } from "./api-token";
import { reader, type ParsedEvent } from "./sse";
import { BACKEND_ORIGIN } from "../config";
import { getMainWindow } from "../window/window";

/** The channel a renderer listens on. Mirrored in `preload.ts` and nowhere else. */
export const CHANNEL = "kith:event";

/** What the page receives. The parsed event minus the raw id, which is ours to keep. */
export type ServerEvent = Omit<ParsedEvent, "raw">;

/* Reconnect backoff.
 *
 * Starts fast because the overwhelmingly common case is the server restarting under `KITH_RELOAD`
 * during development, which is over in under a second. Caps at ten, because past that the answer
 * is not a shorter interval — the server is down, and `Last-Event-ID` means waiting costs a replay
 * rather than a gap. */
const RETRY_MIN_MS = 500;
const RETRY_MAX_MS = 10_000;

/** Long enough that a keep-alive comment (every 15s server-side) always beats it. */
const IDLE_TIMEOUT_MS = 45_000;

let request: http.ClientRequest | null = null;
let retry: NodeJS.Timeout | null = null;
let wait = RETRY_MIN_MS;
let stopped = false;

/** Where we got to, exactly as the server wrote it (`<epoch>-<n>`), so it can be handed straight
 *  back as `Last-Event-ID`. Survives reconnects, renderer reloads, and the window being hidden. */
let cursor = "";

/**
 * Hand an event to our own window, and only ours.
 *
 * Not `BrowserWindow.getAllWindows()`, which is what this said first. The render service keeps
 * hidden windows open on *arbitrary web pages* — that is its whole job — and sending the app's
 * internal events into those is wrong even though they cannot read them: they have no preload, so
 * there is no `ipcRenderer` to listen with. "Cannot read it" is not a reason to send it.
 *
 * Sent while hidden, though. Closing this app's window hides it and the page keeps its React
 * state, so it has to keep hearing or it comes back showing a world from whenever it was last
 * visible.
 */
function fanOut(event: ServerEvent): void {
  const window = getMainWindow();
  if (!window || window.isDestroyed()) return;
  window.webContents.send(CHANNEL, event);
}

/** Open the stream, and keep it open. */
function connect(): void {
  if (stopped || request) return;

  const url = new URL("/api/events", BACKEND_ORIGIN);
  const client = url.protocol === "https:" ? https : http;
  const headers = apiHeaders({
    Accept: "text/event-stream",
    "Cache-Control": "no-cache",
    // Only once we have somewhere to resume from. On a first connection the page fetches its own
    // state anyway, and asking to replay from zero would hand it a backlog it has already got.
    ...(cursor ? { "Last-Event-ID": cursor } : {}),
  });

  const outgoing = client.request(
    { protocol: url.protocol, hostname: url.hostname, port: url.port, path: url.pathname, headers },
    (response) => {
      if (response.statusCode !== 200) {
        // Most likely the server is still starting, or has just been replaced by the reloader.
        response.resume();
        outgoing.destroy();
        return;
      }
      // Connected: the next drop starts its backoff from the bottom again rather than from
      // however long the last outage took to end.
      wait = RETRY_MIN_MS;
      response.setEncoding("utf8");
      const feed = reader(({ raw, ...event }) => {
        // The raw id is what goes back on the wire; the page gets everything but it.
        if (raw) cursor = raw;
        fanOut(event);
      });
      response.on("data", feed);
      response.on("end", () => outgoing.destroy());
    },
  );

  request = outgoing;
  // A stream is idle by design between events, so this guards only against a connection that has
  // died without telling us — which the server's keep-alive is what makes detectable.
  outgoing.setTimeout(IDLE_TIMEOUT_MS, () => outgoing.destroy());
  outgoing.on("error", () => {
    /* Reconnecting is the response to every error here; `close` is where that happens. */
  });
  outgoing.on("close", () => {
    request = null;
    if (stopped) return;
    if (retry) clearTimeout(retry);
    retry = setTimeout(connect, wait);
    wait = Math.min(wait * 2, RETRY_MAX_MS);
  });
  outgoing.end();
}

/**
 * Start listening, and keep the app listening for as long as it runs.
 *
 * Called once the backend answers, so the first attempt is not spent on a server that is still
 * booting — but nothing depends on that being true, because a failed attempt is just the first
 * retry.
 */
export function startEvents(): void {
  stopped = false;
  connect();
}

/** Let go, on quit. */
export function stopEvents(): void {
  stopped = true;
  if (retry) clearTimeout(retry);
  retry = null;
  request?.destroy();
  request = null;
}
