import type { ChatModelAdapter, ThreadAssistantMessagePart } from "@assistant-ui/react";

import { readEvents, toWireMessages } from "./stream";
import type { JsonObject, JsonValue, ServerConfig } from "./types";

interface ToolPart {
  id: string;
  name: string;
  args: JsonObject;
  result?: JsonValue;
}

/**
 * An assistant-ui adapter that streams from the Kith server. It accumulates the
 * reasoning and answer channels separately and yields them as message parts.
 * Config is read fresh on each run via `getConfig`, so settings changes take
 * effect without recreating the runtime.
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
        throw new Error(`Server error ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`);
      }

      let reasoning = "";
      let answer = "";
      const toolOrder: string[] = [];
      const toolById = new Map<string, ToolPart>();

      const snapshot = (): ThreadAssistantMessagePart[] => {
        const content: ThreadAssistantMessagePart[] = [];
        if (reasoning) content.push({ type: "reasoning", text: reasoning });
        for (const id of toolOrder) {
          const tool = toolById.get(id)!;
          content.push({
            type: "tool-call",
            toolCallId: tool.id,
            toolName: tool.name,
            args: tool.args,
            argsText: JSON.stringify(tool.args),
            result: tool.result,
          });
        }
        if (answer) content.push({ type: "text", text: answer });
        return content;
      };

      try {
        for await (const event of readEvents(response)) {
          if (event.type === "error") throw new Error(event.message);

          if (event.type === "delta") {
            if (event.role === "reasoning") reasoning += event.text;
            else answer += event.text;
          } else if (event.type === "tool_call") {
            toolById.set(event.id, { id: event.id, name: event.name, args: event.arguments });
            toolOrder.push(event.id);
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
