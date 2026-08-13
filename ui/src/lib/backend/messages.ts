/** Kith's own messages — the things he says to you, unprompted. */

export interface KithMessage {
  id: number;
  body: string;
  read: number; // 0 unread, 1 read
  sender: "kith" | "user";
  /** What sort of thing this is: note | asked | stuck | delivered | reachout | user. Decides
   *  how it reads in the alerts panel and whether it was allowed to interrupt you at all. */
  kind: string;
  created_at: string;
  link?: string | null; // e.g. "/tasks/12" — makes the alert click through to its task
}

export interface MessageFeed {
  messages: KithMessage[];
  unread: number;
  /** How many of each kind exist in total, which is not the same as how many came back
   *  above — the list is a page. The panel's clear options are named from these, so what
   *  the button says it will remove is what it removes. */
  counts: Record<string, number>;
}

export async function fetchMessages(): Promise<MessageFeed> {
  const res = await fetch("/api/messages");
  if (!res.ok) throw new Error(`/api/messages ${res.status}`);
  const data = (await res.json()) as MessageFeed;
  return { ...data, counts: data.counts ?? {} };
}

/** Clear alerts in bulk. `kinds` narrows it (e.g. just the notes); omit it to clear the lot.
 *  Your own replies to him are not the target. */
export async function clearMessages(kinds?: string[]): Promise<number> {
  const res = await fetch("/api/messages/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(kinds ? { kinds } : {}),
  });
  if (!res.ok) throw new Error("clear failed");
  return ((await res.json()) as { deleted: number }).deleted;
}

export async function markAllMessagesRead(): Promise<void> {
  const res = await fetch("/api/messages/read-all", { method: "POST" });
  if (!res.ok) throw new Error("mark all read failed");
}

/** Send a message to Kith on his own channel — he sees it on his next step and replies. */
export async function sendMessage(body: string): Promise<void> {
  const res = await fetch("/api/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  if (!res.ok) throw new Error("send failed");
}
