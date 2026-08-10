import type {
  ChatModelAdapter,
  ChatModelRunResult,
  ThreadAssistantMessagePart,
} from "@assistant-ui/react";

import type { TurnUsage } from "@/components/assistant-ui/turn-usage";
import type { Usage } from "@/lib/tokens";

import { readEvents, toWireMessages } from "./stream";
import type { ContextLedger, JsonObject, JsonValue } from "./types";

interface ToolPart {
  id: string;
  name: string;
  args: JsonObject;
  result?: JsonValue;
}

/** One piece of the reply, in the order it arrived. */
type Piece =
  | { kind: "reasoning"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool"; tool: ToolPart };

/** Name on the data part carrying a turn's token count, shared with the renderer. */
export const USAGE_PART = "round-usage";

/**
 * Attempts at *starting* a turn — and only at starting one.
 *
 * The distinction is the whole design. Retrying a connection that never opened is free: nothing
 * happened on the server, so trying again is the same request. Retrying a stream that has already
 * begun is not, and must never be added here: by then he may have called tools, written files and
 * committed, and the server has no idea the client gave up on the reply. A second attempt would
 * do all of it again. So this loop ends the instant a response body exists, and a mid-stream
 * failure surfaces as an error rather than being papered over.
 *
 * Five, because the thing this actually catches is the server being briefly away — a reloader
 * restart under `./run dev` takes a second or two, and before this, saving a .py file while a
 * turn was in flight lost the turn.
 */
const RETRIES = 5;

/** 300ms, 600, 1.2s, 2.4s — a bit over four seconds across all five attempts, which covers a
 *  restart without leaving someone watching a dead button for a quarter of a minute. */
const backoff = (attempt: number) => 300 * 2 ** (attempt - 1);

/** Sleep, unless we are aborted first. False means "stop, the person cancelled" — an abort
 *  during the wait must not be discovered only after the next attempt has been sent. */
function pause(ms: number, signal?: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve(false);
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve(true);
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * An assistant-ui adapter that streams from the Kith server.
 *
 * Pieces are kept in arrival order rather than grouped by kind. A turn is a loop —
 * he says something, calls tools, reads the results, says more — and grouping meant
 * every round's prose was concatenated into a single block below a single collapsed
 * "6 tool calls" summary. So each new round appeared to rewrite the message from the
 * top, and which sentence went with which tool call was lost. Chronological order is
 * what he actually did.
 *
 * The request carries messages and nothing else. It used to send the whole config as
 * per-request overrides, read from state fetched once at startup — so changing the model
 * in Settings left the server obeying the old one on every turn. Model, persona and
 * limits are server settings with a server-side store; reading them from anywhere else
 * could only ever agree or be wrong.
 */
export function createBackendAdapter(conversation?: {
  /** Read fresh each run, so resuming does not mean rebuilding the runtime. */
  get: () => string;
  /** The server reports the id it opened on the first turn; keep it for the next one. */
  set: (id: string) => void;
}): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const body = JSON.stringify({
        messages: toWireMessages(messages),
        // Omitted on the first turn; the server opens one and tells us which.
        ...(conversation?.get() ? { conversationId: conversation.get() } : {}),
      });

      /* Stopping is now said, not inferred.
       *
       * It used to be inferred: aborting the fetch hung up, and the turn died because the
       * server-side generator stopped being advanced. That is exactly what also killed a turn
       * when you switched conversations — the same closed socket, two completely different
       * intentions — so the turn no longer ends when the connection does. Which means Stop has
       * to say so, or it would quietly do nothing.
       *
       * Fire-and-forget: this is a best-effort request on the way out, and there is nothing
       * useful to do if it fails. No conversation id yet (a brand-new chat aborted before the
       * server named it) means there is no turn to stop.
       */
      abortSignal?.addEventListener(
        "abort",
        () => {
          const id = conversation?.get();
          if (id) void fetch(`/api/chat/${id}/stop`, { method: "POST" }).catch(() => {});
        },
        { once: true },
      );

      /* Getting the turn *started*, with retries. See `RETRIES` for what is and is not retried,
       * and why this stops the moment the response body begins. */
      let response: Response | null = null;
      let lastError = "";
      for (let attempt = 0; attempt < RETRIES; attempt++) {
        if (abortSignal?.aborted) return;
        if (attempt > 0 && !(await pause(backoff(attempt), abortSignal))) return;
        try {
          const tried = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
            signal: abortSignal,
          });
          if (tried.ok && tried.body) {
            response = tried;
            break;
          }
          const detail = await tried.text().catch(() => "");
          lastError = `Server error ${tried.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`;
          // A 4xx is the server saying this request is wrong, and sending it again unchanged
          // gets the same answer — only slower, and four more times. Retrying is for the
          // server being briefly unable, not for it being clear.
          if (tried.status < 500 && tried.status !== 429) break;
        } catch (error) {
          if (isAbort(error)) return;
          lastError =
            "Can't reach the Kith server. Start it: cd kith/server && .venv/bin/python app.py";
        }
      }

      if (!response) throw new Error(lastError || "Could not start the turn.");

      yield* readTurn(response, conversation);
    },
  };
}

/**
 * One turn's events, turned into the message as it grows.
 *
 * Lifted out of `run` so that starting a turn and *rejoining* one are the same code. The
 * screen is a window onto a turn, not the thing running it — the turn lives on the server and
 * survives you closing the window — so "I sent this" and "I came back to this" have to produce
 * the same message, and a second implementation of a hundred lines of event handling would be
 * two things that agree until they quietly don't.
 */
async function* readTurn(
  response: Response,
  conversation?: { get: () => string; set: (id: string) => void } | undefined,
): AsyncGenerator<ChatModelRunResult> {
    const pieces: Piece[] = [];
    const toolById = new Map<string, ToolPart>();
    // One entry per model request. Kept out of `pieces` because the total belongs at
    // the foot of the message, not wherever its round happened to land.
    const rounds: Usage[] = [];
    // The most recent reading of the context window, and whether this turn had to fold
    // itself to keep going. Both belong on the same footer as the token count.
    let context: ContextLedger | undefined;
    // Round one's reading — before this turn's own tool calls added anything, so it's what
    // actually carried over from the conversation so far. See turn-usage.tsx for why this,
    // not `context`, is the headline number.
    let baseline: ContextLedger | undefined;
    let folded = false;
/** How much prose the pre-turn fold removed, when it said. Rendered while it runs, which
 *  is the point — before this the wait was a dead composer with nothing on it. */
let foldedChars: { from: number; to: number } | undefined;
/** The round being retried, while it is being retried. Cleared the moment anything else
 *  arrives, because by then the retry has plainly worked. */
let retrying: { attempt: number; message: string } | undefined;
/** How many rounds were sent again over the whole turn. Never cleared — see `retried` in
 *  TurnUsage for why the live one above is not enough on its own. */
let retried = 0;

    /** Append to the piece being written, or start a new one when the channel
     *  changed — which is what keeps consecutive deltas from each becoming a part. */
    const append = (kind: "reasoning" | "text", text: string) => {
      const last = pieces.at(-1);
      if (last?.kind === kind) last.text += text;
      else pieces.push({ kind, text });
    };

    const snapshot = (): ThreadAssistantMessagePart[] => {
      const parts = pieces.flatMap((piece): ThreadAssistantMessagePart[] => {
        if (piece.kind === "tool") {
          const tool = piece.tool;
          return [
            {
              type: "tool-call",
              // The backend's own id resets to c1 at the start of every turn — never unique
              // across a whole conversation, only within the one it came from. There is only
              // ever one live turn at a time, so a fixed prefix is enough to keep it out of
              // reach of every past turn's ids, which `toThreadMessages` scopes by turn index
              // instead (see workspace.tsx) — the two schemes just have to never coincide,
              // not match. Internal lookups below still key on the raw id; only what the UI
              // sees needs to be unique.
              toolCallId: `live-${tool.id}`,
              toolName: tool.name,
              args: tool.args,
              argsText: JSON.stringify(tool.args),
              result: tool.result,
            },
          ];
        }
        // A channel can open and produce nothing; an empty part renders as a gap.
        if (!piece.text) return [];
        return [{ type: piece.kind === "reasoning" ? "reasoning" : "text", text: piece.text }];
      });
      // Last, so it reads as the message's footer and stays put as rounds arrive.
      // `folded` joins the gate: a fold before the first round is exactly the case where
      // there is nothing else to render and the person is staring at an empty message.
      if (rounds.length > 0 || context || folded || retrying || retried) {
        const usage: TurnUsage = {
          rounds,
          context,
          baseline,
          folded,
          foldedChars,
          retrying,
          retried,
        };
        parts.push({ type: "data", name: USAGE_PART, data: usage });
      }
      return parts;
    };

    try {
      for await (const event of readEvents(response)) {
        if (event.type === "error") throw new Error(event.message);

        if (event.type === "conversation") {
          conversation?.set(event.id);
          continue;
        }

        if (event.type === "retrying") {
          retrying = { attempt: event.attempt, message: event.message };
          retried += 1;
          yield { content: snapshot() };
          continue;
        }
        // Anything else arriving means the round got through.
        retrying = undefined;

        if (event.type === "delta") {
          append(event.role === "reasoning" ? "reasoning" : "text", event.text);
        } else if (event.type === "tool_call") {
          const tool: ToolPart = { id: event.id, name: event.name, args: event.arguments };
          toolById.set(event.id, tool);
          pieces.push({ kind: "tool", tool });
        } else if (event.type === "tool_result") {
          const tool = toolById.get(event.id);
          if (tool) tool.result = event.result;
        } else if (event.type === "stats") {
          // One of these lands per model request, so the total grows a round at a
          // time and the footer counts up while he works.
          rounds.push({
            uncached: event.stats.uncachedTokens ?? 0,
            cached: event.stats.cachedTokens ?? 0,
            out: event.stats.responseTokens ?? 0,
          });
        } else if (event.type === "context") {
          // `context` is replaced rather than accumulated: this is a reading of the window
          // as it stands, not a thing that happened, so the last one is the only one still
          // true. `baseline` is the opposite on purpose — set once, from the first reading,
          // since that is the one that predates anything this turn did.
          if (!baseline) baseline = event.context;
          context = event.context;
        } else if (event.type === "compacting") {
          // He is folding to make room. Worth showing because it costs a model call and takes
          // a moment, so an unexplained pause looks like a hang — and when it happens before
          // the turn starts, that pause is the entire time between hitting send and seeing
          // anything at all.
          folded = true;
          if (event.foldedFrom != null && event.foldedTo != null) {
            foldedChars = { from: event.foldedFrom, to: event.foldedTo };
          }
        } else {
          continue; // done
        }

        yield { content: snapshot() };
      }
  } catch (error) {
    if (isAbort(error)) return;
    throw error;
  }
}

/**
 * Rejoin the turn already running in a conversation, if there is one.
 *
 * Returns null when nothing is running, which is the ordinary case — opening an idle
 * conversation must not look like starting a turn in it.
 */
export async function resumeTurn(conversationId: string): Promise<AsyncGenerator<ChatModelRunResult> | null> {
  if (!conversationId) return null;
  const response = await fetch(`/api/chat/${conversationId}/attach`).catch(() => null);
  // 204 is "nothing is running"; a body is the backlog followed by the rest as it happens.
  if (!response || response.status === 204 || !response.ok || !response.body) return null;
  return readTurn(response);
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
