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

export interface WireAttachment {
  kind: "image" | "file";
  name: string;
  mediaType: string;
  /** A data: URL. Everything is local, so there is nowhere to upload it to. */
  data: string;
}

/** Convert assistant-ui messages into the server's wire format. */
export function toWireMessages(
  messages: readonly ThreadMessage[],
): { role: string; content: string; attachments?: WireAttachment[] }[] {
  const out: { role: string; content: string; attachments?: WireAttachment[] }[] = [];
  for (const message of messages) {
    if (message.role !== "user" && message.role !== "assistant") continue;
    const attachments = attachmentsOf(message);
    out.push({
      role: message.role,
      content: textOf(message),
      ...(attachments.length ? { attachments } : {}),
    });
  }
  return out;
}

/**
 * Anything attached to a message, as data URLs.
 *
 * Images come off the parts array, where the image adapter puts them. Non-images are
 * reported by name only: the server points him at those instead of inlining them, because
 * he has a computer and can open a spreadsheet with python — which no vision model can.
 */
function attachmentsOf(message: ThreadMessage): WireAttachment[] {
  const out: WireAttachment[] = [];
  for (const part of message.content) {
    if (part.type === "image" && typeof part.image === "string") {
      out.push({ kind: "image", name: "image", mediaType: "image/*", data: part.image });
    }
  }
  for (const attachment of message.attachments ?? []) {
    const isImage = (attachment.contentType ?? "").startsWith("image/");
    if (isImage) continue; // already carried as a content part
    out.push({
      kind: "file",
      name: attachment.name,
      mediaType: attachment.contentType ?? "application/octet-stream",
      data: "",
    });
  }
  return out;
}
