import type {
  ChatModelAdapter,
  ChatModelRunResult,
  ThreadAssistantMessagePart,
} from "@assistant-ui/react";

import type { TurnUsage } from "@/components/assistant-ui/turn-usage";
import type { Usage } from "@/lib/tokens";

import { foldNow, stopTurn } from "@/lib/commands";
import { useConversationForSteering } from "@/lib/queued-send";
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
  /** Something you said into the running turn, at the point in the stream it landed. */
  | { kind: "steered"; text: string }
  /** An errand's findings, at the round they went into the prompt. */
  | { kind: "errand"; text: string }
  | { kind: "tool"; tool: ToolPart };

/** Name on the data part carrying a turn's token count, shared with the renderer. */
export const USAGE_PART = "round-usage";

/** Name on the data part carrying something you said into the running turn. */
export const STEER_PART = "steered-in";

/** Name on the data part carrying what an errand came back with. */
export const ERRAND_PART = "errand-back";

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
  /**
   * The project this chat was started in, when it was started in one.
   *
   * Sent with the turn rather than written afterwards, and that ordering is the fix. "Start a
   * chat in this project" is asked from the history panel, where the project is in front of
   * you — but a fresh chat has no id until the stream reports one, so the binding used to be a
   * second request made *after* the first turn had already been assembled and sent. That first
   * turn is where somebody says what they want, and it was the one turn built with no project
   * at all: no project memory, no plan, no columns, and the whole cross-project listing in
   * their place.
   *
   * Read fresh for the same reason as `get`, and it returns null once the conversation exists
   * and is bound — the server ignores it then anyway, since a conversation keeps the project it
   * picked.
   */
  project?: () => number | null;
}): ChatModelAdapter {
  // The composer's keystroke needs to know which conversation it is in, and cannot reach it
  // through the runtime. Registered here because this is where the getter already exists.
  if (conversation) useConversationForSteering(conversation.get);
  return {
    async *run({ messages, abortSignal }) {
      /* A slash command never becomes a turn.
       *
       * The menu is not enough on its own. Typing `/fold` and pressing Enter sends text like
       * any other text — which is what happened: the command went to the model as a prompt, the
       * conversation was over its window, and the turn folded itself on the way past. 349,000
       * characters summarised, a real model call, and the command itself never ran.
       *
       * Intercepted here rather than in the composer because this is the one place every route
       * to sending passes through — Enter, the button, a paste, a suggestion. A menu selection
       * that fills the box and a hand-typed command now do the same thing.
       */
      const typed = lastUserText(messages);
      const command = matchCommand(typed);
      if (command) {
        const result = await command.run(conversation?.get() ?? "");
        yield { content: [{ type: "text", text: result }] };
        return;
      }

      /* Typing while he is working steers the turn rather than starting a second one.
       *
       * Until now the only lever mid-turn was Stop, which throws away everything the run had
       * worked out. So a correction cost you the work it was correcting.
       *
       * The server answers `{steering: false}` when nothing is running, and then this falls
       * through to the ordinary path below — which is the same thing the person meant, and the
       * check-then-send race resolves the right way in both directions.
       *
       * Held back by `queueSend`: ⌘⏎ means "wait your turn", so it skips this and posts
       * normally once the run is over.
       */
      /* ⌘⏎ waits for the running turn to finish before this one starts.
       *
       * Steering is NOT done here, and that is the whole design: reaching this function at all
       * means `performRoundtrip` has already run, and its first line aborts the turn in flight.
       * A steer routed through the runtime would kill the work it was meant to redirect. So the
       * keystroke steers directly, and ⌘⏎ waits directly — both in `rich-input`, which is where
       * the intent is.
       *
       * There was an `if (takeQueuedFlag()) await waitUntilIdle(...)` here, and it could not fire:
       * the composer stopped setting that flag when it started calling `holdUntilIdle` itself, so
       * this branch had been asking a question whose answer could no longer be yes.
       */
      const startingIn = conversation?.project?.() ?? null;
      const body = JSON.stringify({
        messages: toWireMessages(messages),
        // Omitted on the first turn; the server opens one and tells us which.
        ...(conversation?.get() ? { conversationId: conversation.get() } : {}),
        // Sent on that same first turn, so the conversation it creates is bound before its
        // prompt is built. Harmless afterwards: the server refuses to move a binding.
        ...(startingIn ? { projectId: startingIn } : {}),
      });

      /* Stopping is said, and it is not said from here.
       *
       * There was a `POST /api/chat/<id>/stop` on this signal's `abort`, and its own comment
       * described the bug it was meant to fix: "aborting the fetch hung up, and the turn died…
       * That is exactly what also killed a turn when you switched conversations — the same closed
       * socket, two completely different intentions." The fix was to make stopping explicit. Then
       * it was hung on `abort`, which is not a statement of intent — it fires for a person pressing
       * Stop, for a view being torn down, for a component unmounting, for a switch. So the bug came
       * straight back through a different door: leaving a conversation ended the work in it.
       *
       * An abort means only "this reader is going away". The turn lives on the server, on its own
       * thread, and outliving its reader is the entire point — `live_turns` keeps everything it says
       * so a later reader gets the backlog and then the rest. The Stop button posts `/stop` itself
       * (see thread.tsx), which is where the intent actually is, and `/stop` as a slash command has
       * always done the same.
       *
       * The trade, stated: a send while a turn is running would abort this run without ending the
       * server's, leaving two turns racing for one live-turn slot. Both routes in are already
       * closed — Enter steers into the running turn and ⌘⏎ waits for it — and the ordinary paths
       * out of that race were always worse than losing a turn to a switch.
       */

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
      /* A tool call being written right now.
       *
       * Tool-call arguments stream in silently and a big one takes a while: a 21,591-character
       * page arrives over roughly a minute during which the interface showed nothing at all, so
       * watching a file get written was indistinguishable from watching a turn hang. Cleared the
       * moment the call completes, because the tool row that replaces it says the same thing
       * better and with a result attached. */
      let writing: { name: string; path?: string; chars: number } | undefined;
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
        if (piece.kind === "errand") {
          // Its own part, drawn where it arrived. Not folded into his prose: he did not write
          // it, and a finding that reads as something he already knew hides the fact that it
          // turned up three rounds after he asked for it.
          return [{ type: "data", name: ERRAND_PART, data: { text: piece.text } }];
        }
        if (piece.kind === "steered") {
          // Its own part type rather than text, so the thread can draw it as something you
          // said rather than something he did. Placed where it actually arrived, which is the
          // only honest position: it went into the prompt at that round and not before.
          return [{ type: "data", name: STEER_PART, data: { text: piece.text } }];
        }
        // A channel can open and produce nothing; an empty part renders as a gap.
        if (!piece.text) return [];
        return [{ type: piece.kind === "reasoning" ? "reasoning" : "text", text: piece.text }];
      });
      // Last, so it reads as the message's footer and stays put as rounds arrive.
      // `folded` joins the gate: a fold before the first round is exactly the case where
      // there is nothing else to render and the person is staring at an empty message.
      if (rounds.length > 0 || context || folded || retrying || retried || writing) {
        const usage: TurnUsage = {
          rounds,
          context,
          baseline,
          folded,
          foldedChars,
          retrying,
          writing,
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
        if (event.type === "writing") {
          retrying = undefined;
          writing = { name: event.name, chars: event.chars, ...(event.path ? { path: event.path } : {}) };
          yield { content: snapshot() };
          continue;
        }
        // Anything else arriving means the round got through.
        retrying = undefined;
        // And anything that is not more of the same write means the write is over — the
        // completed `tool_call` row says it better, with its result.
        writing = undefined;

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
        } else if (event.type === "steered") {
          pieces.push({ kind: "steered", text: event.text });
        } else if (event.type === "errand_back") {
          pieces.push({ kind: "errand", text: event.text });
        } else if (event.type === "waiting_on_errands") {
          // Nothing to draw in the thread — the Work panel is already showing which errands
          // are still out, and a second "still waiting" line in the message would be the
          // duplicate feed all over again. Consumed so it does not fall through to `done`.
          continue;
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
export async function resumeTurn(
  conversationId: string,
): Promise<{ stream: AsyncGenerator<ChatModelRunResult>; discard: () => void } | null> {
  if (!conversationId) return null;
  const response = await fetch(`/api/chat/${conversationId}/attach`).catch(() => null);
  // 204 is "nothing is running"; a body is the backlog followed by the rest as it happens.
  if (!response || response.status === 204 || !response.ok || !response.body) return null;
  /* Handed back with a way to throw it away, because every caller has a path where it decides not
   * to read this after all — and dropping it is not free.
   *
   * `readTurn` is an async generator whose body has not run yet, so its `finally` has not been
   * armed: letting it go collects nothing, `getReader()` is never called, and the response body
   * stays open. On the server that is a `live_turns.watch` generator blocked writing to a reader
   * that will never drain, with its own watcher-discard `finally` equally unreached. Once per
   * turn, because `rejoin` fires on the `turn` event this window's own send just caused and then
   * declines the stream at its `isRunning` guard.
   *
   * `stream.return()` would not do it for the same reason the leak exists — the generator never
   * started. Cancelling the body is what actually closes it, which is what `queued-send.ts`
   * already does on this same endpoint. */
  const body = response.body;
  return { stream: readTurn(response), discard: () => void body.cancel().catch(() => {}) };
}

/** The text of the message just sent, or "" — commands are only ever the whole of it. */
function lastUserText(messages: readonly { role: string; content: readonly unknown[] }[]): string {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "user") return "";
  const parts = last.content as readonly { type?: string; text?: string }[];
  return parts
    .filter((part) => part?.type === "text")
    .map((part) => part.text ?? "")
    .join("")
    .trim();
}

/** Which command this is, if it is one. Unknown slashes fall through to him on purpose: he can
 *  answer "what does /foo do" and a hard error could not. */
function matchCommand(text: string): { run: (conversationId: string) => Promise<string> } | null {
  if (!text.startsWith("/")) return null;
  const [word] = text.slice(1).split(/\s+/, 1);
  if (word === "fold") {
    return { run: async (id) => (await foldNow(id)).note };
  }
  if (word === "stop") {
    return { run: async (id) => (await stopTurn(id)).note };
  }
  return null;
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
