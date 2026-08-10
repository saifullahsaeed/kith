/** A question he is waiting on, and the answer that releases him. */

export interface AskedOption {
  label: string;
  description: string;
}

export interface AskedQuestion {
  question: string;
  options: AskedOption[];
  multiple: boolean;
}

export interface OpenQuestion {
  id: string;
  conversationId: string;
  questions: AskedQuestion[];
}

export interface Reply {
  chosen: string[];
  text: string;
  skipped: boolean;
}

/**
 * What this conversation is waiting to be asked, if anything.
 *
 * Fetched rather than read off the tool call, and the reason is the id: the server mints it
 * when the tool runs, which is *after* the `tool_call` event describing the questions has
 * already gone out. The card can draw itself from the call; it cannot answer without asking
 * who is waiting.
 */
export async function fetchOpenQuestion(conversationId: string): Promise<OpenQuestion | null> {
  if (!conversationId) return null;
  const response = await fetch(`/api/chat/${conversationId}/question`);
  if (!response.ok) return null;
  const body = (await response.json()) as Partial<OpenQuestion>;
  return body?.id ? (body as OpenQuestion) : null;
}

export async function answerQuestion(questionId: string, answers: Reply[]): Promise<boolean> {
  const response = await fetch(`/api/questions/${questionId}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers }),
  });
  if (!response.ok) return false;
  const body = (await response.json()) as { answered?: boolean };
  return Boolean(body.answered);
}
