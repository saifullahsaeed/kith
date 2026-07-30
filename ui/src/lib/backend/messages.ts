/** Kith's own messages — the things he says to you, unprompted. */

export interface KithMessage {
  id: number;
  body: string;
  read: number; // 0 unread, 1 read
  sender: "kith" | "user";
  created_at: string;
  link?: string | null; // e.g. "/tasks/12" — makes the alert click through to its task
}

export async function fetchMessages(): Promise<{ messages: KithMessage[]; unread: number }> {
  const res = await fetch("/api/messages");
  if (!res.ok) throw new Error(`/api/messages ${res.status}`);
  return (await res.json()) as { messages: KithMessage[]; unread: number };
}

export async function markAllMessagesRead(): Promise<void> {
  const res = await fetch("/api/messages/read-all", { method: "POST" });
  if (!res.ok) throw new Error("mark all read failed");
}

export async function markMessageRead(id: number): Promise<void> {
  const res = await fetch(`/api/messages/${id}/read`, { method: "POST" });
  if (!res.ok) throw new Error("mark read failed");
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
