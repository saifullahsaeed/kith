/**
 * ⌘⏎ — say it after he finishes, not to the turn he is in.
 *
 * Enter, while he is working, steers: the text lands in the running turn at its next round and
 * changes what it does. That is the right default, because the thing people type mid-turn is
 * usually a correction.
 *
 * It is not always. "When you're done, also do the UI side" is a queue, not a correction — and
 * steering it would make him abandon what he is doing to start the next thing. So ⌘⏎ holds the
 * message until the turn is over and then sends it as an ordinary one.
 *
 * **It genuinely waits.** Posting immediately would start a second turn beside the first, which
 * is a real hazard rather than a tidiness point: two turns in one conversation share a stop
 * switch and a live-turn slot, and the loser of that race is a run nobody can stop. So this
 * polls until the conversation is quiet, then lets the normal send proceed.
 *
 * A one-shot flag rather than a parameter because the composer and the adapter are separated by
 * the assistant-ui runtime, which carries text and attachments and no room for an intent. It is
 * set by the keystroke and consumed by the very next send.
 */

/** Set by ⌘⏎, read once by the send it belongs to. */
let queuedNext = false;

/** Called by the composer when ⌘⏎ is used. */
export function queueNextSend(): void {
  queuedNext = true;
}

/** Read *and clear*. The flag belongs to one send; a stale one would silently make the next
 *  ordinary message wait too. */
export function takeQueuedFlag(): boolean {
  const was = queuedNext;
  queuedNext = false;
  return was;
}

/** How often to ask whether the turn has ended. Two seconds is under the round time of
 *  anything real, so the wait ends within a round of the turn actually finishing, and it is
 *  slow enough that a ten-minute turn costs a few hundred cheap requests rather than thousands. */
const POLL_MS = 2000;

/** Give up waiting after this long and send anyway. A turn that has run for twenty minutes is
 *  one the person is watching; holding their message hostage to it indefinitely is worse than
 *  sending it into a conversation that is still busy. */
const GIVE_UP_MS = 20 * 60 * 1000;

/**
 * Resolve once no turn is running in this conversation.
 *
 * Uses `/attach`, which answers 204 when there is nothing to watch — a probe that already
 * exists and costs the server nothing, rather than a status endpoint invented for this.
 */
export async function waitUntilIdle(conversationId: string): Promise<void> {
  if (!conversationId) return;
  const until = Date.now() + GIVE_UP_MS;
  while (Date.now() < until) {
    try {
      const response = await fetch(`/api/chat/${conversationId}/attach`, { method: "GET" });
      // 204 is "nothing running". Anything else — a stream, or an error we cannot read — means
      // carry on waiting, except that an error should not wait forever, hence the deadline.
      if (response.status === 204) {
        void response.body?.cancel();
        return;
      }
      void response.body?.cancel();
    } catch {
      // The server being briefly unreachable is not a reason to drop someone's message.
    }
    await new Promise((wake) => setTimeout(wake, POLL_MS));
  }
}
