/** Client for the conversation history. */

export interface ConversationSummary {
  id: string;
  title: string;
  sessionId: string;
  createdAt: string;
  updatedAt: string;
  messages: number;
  /** Absolute path to the JSONL transcript, so the settings page can reveal it. */
  transcript: string;
}

export interface ConversationDetail extends ConversationSummary {
  /** User and assistant text, in the shape /api/chat wants back. */
  messages_: { role: string; content: string }[];
}

export async function fetchConversations(
  limit = 50,
): Promise<{
  conversations: ConversationSummary[];
  storage: { folder: string; files: number; bytes: number };
}> {
  const response = await fetch(`/api/conversations?limit=${limit}`);
  if (!response.ok) throw new Error(`/api/conversations returned ${response.status}`);
  return await response.json();
}

export async function fetchConversation(id: string): Promise<{
  id: string;
  title: string;
  messages: { role: string; content: string }[];
}> {
  const response = await fetch(`/api/conversations/${id}`);
  if (!response.ok) throw new Error(`could not open that conversation (${response.status})`);
  return await response.json();
}

export async function renameConversation(id: string, title: string): Promise<void> {
  const response = await fetch(`/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw new Error(`could not rename (${response.status})`);
}

/** Remove from the list. The transcript file stays unless `purge`. */
export async function deleteConversation(id: string, purge = false): Promise<void> {
  const response = await fetch(`/api/conversations/${id}?purge=${purge}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`could not remove (${response.status})`);
}
