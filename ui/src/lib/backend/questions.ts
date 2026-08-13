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
  /**
   * Recovered from a transcript after the server restarted, so the turn that asked it is gone
   * and nothing is waiting on the answer.
   *
   * It changes where the answer goes, not how it looks. `answerQuestion` would post it to a
   * thread that no longer exists and the reply would be dropped on the floor; sent as an
   * ordinary message it starts a fresh turn, which reads the whole conversation — including the
   * question, now that the interrupted call has been closed off — and carries on.
   */
  interrupted?: boolean;
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

/**
 * Told when an `ask` tool call comes down the stream, so the card does not wait for a tick.
 *
 * The card is polled, and a poll is a `setInterval`, and Chromium throttles those hard in a
 * window that is not focused. Measured: 50 polls a minute while the app has focus, and twelve
 * in three and a half minutes when it does not — one every eighteen seconds. So he asks, and
 * the card takes anywhere up to twenty seconds to appear, which from the other side is a turn
 * that has hung. Quitting and reopening the app fetches immediately, which is exactly why
 * reopening looked like the fix.
 *
 * The stream is not throttled — it is an open response being written to. The `tool_call` event
 * announcing the question is already in it and arrives the instant he asks, so that is what
 * raises the card; the interval stays as the fallback for attaching to a turn whose call went
 * past before this window was watching.
 *
 * Deliberately a signal rather than the question itself. The id is minted server-side when the
 * tool runs, after the event has gone out, and answering needs the id — so the event says
 * "look now" and the existing fetch is what looks.
 */
const raised = new Set<() => void>();

export function onAskRaised(listener: () => void): () => void {
  raised.add(listener);
  return () => raised.delete(listener);
}

export function askRaised(): void {
  for (const listener of [...raised]) listener();
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
