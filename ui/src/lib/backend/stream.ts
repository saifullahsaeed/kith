import type { ThreadMessage } from "@assistant-ui/react";

import { currentCanvasState, type CanvasReading } from "@/lib/canvas-state";
import { currentFlowFailures } from "@/lib/flow-failures";

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

/**
 * The excerpt attached via "Reply" on a selection, if this message sent one.
 *
 * A quote only ever lives as `metadata.custom.quote` — the composer never folds it into
 * `content` itself (see `SelectionQuoteToolbar` in `thread.tsx`). Server has no idea what a
 * quote is, so this is where it has to become plain text or it silently never arrives:
 * the UI would show it attached, sent, and gone, with nothing to say it never reached him.
 */
function quoteOf(message: ThreadMessage): string {
  if (message.role !== "user") return "";
  const quote = (message.metadata?.custom as { quote?: unknown } | undefined)?.quote;
  if (!quote || typeof quote !== "object") return "";
  const text = (quote as { text?: unknown }).text;
  return typeof text === "string" ? text : "";
}

type WireMessage = {
  role: string;
  content: string;
  attachments?: WireAttachment[];
  canvas?: CanvasReading[];
  diagrams?: string[];
};

/** Convert assistant-ui messages into the server's wire format. */
export function toWireMessages(messages: readonly ThreadMessage[]): WireMessage[] {
  const out: WireMessage[] = [];
  for (const message of messages) {
    if (message.role !== "user" && message.role !== "assistant") continue;
    const attachments = attachmentsOf(message);
    const quote = quoteOf(message);
    const body = textOf(message);
    out.push({
      role: message.role,
      // A real blockquote, one `>` per line — not a label ahead of a dump, since this is what
      // he'll actually read as the reason the message exists.
      content: quote ? `> ${quote.replace(/\n/g, "\n> ")}\n\n${body}` : body,
      ...(attachments.length ? { attachments } : {}),
    });
  }
  // Whatever is set on a canvas he drew, carried by the message you are sending rather than by a
  // turn of its own. Attached to the last message for the same reason an attachment is: it is
  // part of what you are saying, not a separate thing that happened. Only the newest, because an
  // earlier message was sent when those controls read something else and rewriting history to
  // match the present would be a lie about both.
  const readings = currentCanvasState();
  const last = out[out.length - 1];
  if (readings.length && last?.role === "user") last.canvas = readings;
  // And any animated diagram whose choreography was refused. Same ride, same reason: the person's
  // screen knows something he does not, and the next thing they say is when it can be useful.
  const refused = currentFlowFailures();
  if (refused.length && last?.role === "user") last.diagrams = refused;
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
  const seen = new Set<string>();

  const take = (kind: WireAttachment["kind"], name: string, mediaType: string, data: string) => {
    if (!data || seen.has(data)) return;
    seen.add(data);
    out.push({ kind, name, mediaType, data });
  };

  // The attachments themselves, which is where the bytes actually are. The adapter's `send`
  // returns them on `attachment.content` — the previous version read `message.content` and
  // then `continue`d past every image here, believing the first pass had taken them. Neither
  // pass took anything: no conversation on this machine has ever carried an attachment.
  for (const attachment of message.attachments ?? []) {
    const media = attachment.contentType ?? "application/octet-stream";
    const kind = media.startsWith("image/") ? "image" : "file";
    const carried = (attachment as unknown as { kithData?: string }).kithData;
    const fromContent = (attachment.content ?? []).find(
      (part): part is { type: "image"; image: string } =>
        part.type === "image" && typeof (part as { image?: unknown }).image === "string",
    );
    take(kind, attachment.name || "attachment", media, carried ?? fromContent?.image ?? "");
  }

  // And images sitting directly on the message, which is the shape a resumed conversation
  // comes back in. Deduplicated by payload, so an attachment that appears both ways is sent
  // once rather than twice — the same picture twice is double the vision tokens.
  for (const part of message.content) {
    if (part.type === "image" && typeof part.image === "string") {
      take("image", "image", "image/*", part.image);
    }
  }
  return out;
}
