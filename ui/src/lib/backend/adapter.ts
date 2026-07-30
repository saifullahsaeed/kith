import type { ChatModelAdapter, ThreadAssistantMessagePart } from "@assistant-ui/react";

import type { TurnUsage } from "@/components/assistant-ui/turn-usage";
import type { Usage } from "@/lib/tokens";

import { readEvents, toWireMessages } from "./stream";
import type { JsonObject, JsonValue } from "./types";

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
      let response: Response;
      try {
        response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages: toWireMessages(messages),
            // Omitted on the first turn; the server opens one and tells us which.
            ...(conversation?.get() ? { conversationId: conversation.get() } : {}),
          }),
          signal: abortSignal,
        });
      } catch (error) {
        if (isAbort(error)) return;
        throw new Error(
          "Can't reach the Kith server. Start it: cd kith/server && .venv/bin/python app.py",
        );
      }

      if (!response.ok || !response.body) {
        const detail = await response.text().catch(() => "");
        throw new Error(
          `Server error ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`,
        );
      }

      const pieces: Piece[] = [];
      const toolById = new Map<string, ToolPart>();
      // One entry per model request. Kept out of `pieces` because the total belongs at
      // the foot of the message, not wherever its round happened to land.
      const rounds: Usage[] = [];

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
                toolCallId: tool.id,
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
        if (rounds.length > 0) {
          const usage: TurnUsage = { rounds };
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
          } else {
            continue; // done
          }

          yield { content: snapshot() };
        }
      } catch (error) {
        if (isAbort(error)) return;
        throw error;
      }
    },
  };
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
