import type { ThreadMessage } from "@assistant-ui/react";

import type { BackendEvent } from "./types";

/** Read the server's newline-delimited JSON stream as typed events. */
export async function* readEvents(response: Response): AsyncGenerator<BackendEvent> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newlineIndex: number;
      while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newlineIndex).trim();
        buffer = buffer.slice(newlineIndex + 1);
        if (line) yield JSON.parse(line) as BackendEvent;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Flatten an assistant-ui message's text parts into a plain string. */
function textOf(message: ThreadMessage): string {
  return message.content
    .filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text")
    .map((part) => part.text)
    .join("");
}

/** Convert assistant-ui messages into the server's wire format. */
export function toWireMessages(
  messages: readonly ThreadMessage[],
): { role: string; content: string }[] {
  const out: { role: string; content: string }[] = [];
  for (const message of messages) {
    if (message.role === "user" || message.role === "assistant") {
      out.push({ role: message.role, content: textOf(message) });
    }
  }
  return out;
}
