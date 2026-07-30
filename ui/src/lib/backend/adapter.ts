import type { ChatModelAdapter, ThreadAssistantMessagePart } from "@assistant-ui/react";

import { readEvents, toWireMessages } from "./stream";
import type { JsonObject, JsonValue, ServerConfig } from "./types";

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
 * Config is read fresh on each run via `getConfig`, so settings changes take effect
 * without recreating the runtime.
 */
export function createBackendAdapter(getConfig: () => ServerConfig): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      let response: Response;
      try {
        response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: toWireMessages(messages), config: getConfig() }),
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

      /** Append to the piece being written, or start a new one when the channel
       *  changed — which is what keeps consecutive deltas from each becoming a part. */
      const append = (kind: "reasoning" | "text", text: string) => {
        const last = pieces.at(-1);
        if (last?.kind === kind) last.text += text;
        else pieces.push({ kind, text });
      };

      const snapshot = (): ThreadAssistantMessagePart[] =>
        pieces.flatMap((piece): ThreadAssistantMessagePart[] => {
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

      try {
        for await (const event of readEvents(response)) {
          if (event.type === "error") throw new Error(event.message);

          if (event.type === "delta") {
            append(event.role === "reasoning" ? "reasoning" : "text", event.text);
          } else if (event.type === "tool_call") {
            const tool: ToolPart = { id: event.id, name: event.name, args: event.arguments };
            toolById.set(event.id, tool);
            pieces.push({ kind: "tool", tool });
          } else if (event.type === "tool_result") {
            const tool = toolById.get(event.id);
            if (tool) tool.result = event.result;
          } else {
            continue; // stats / done
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
