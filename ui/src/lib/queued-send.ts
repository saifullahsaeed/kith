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

/**
 * Which conversation the composer is in.
 *
 * The keystroke needs it and cannot reach it: the composer lives inside assistant-ui's runtime,
 * which carries text and attachments, and the conversation id belongs to the shell that built
 * the adapter. Registered here by that shell rather than threaded down through the library.
 *
 * It has to be the keystroke that steers, not the adapter, and that is not a preference. Sending
 * anything while a turn is running goes through `performRoundtrip`, whose first line is
 * `this.abortController?.abort()` — so the in-flight run is cancelled, our abort handler posts
 * `/stop`, and the server turn dies. A steer routed that way would kill the work it was meant to
 * redirect and then start a fresh turn, which is Stop wearing a better name.
 */
let readConversation: (() => string) | null = null;

export function useConversationForSteering(get: () => string): void {
  readConversation = get;
}

export function currentConversation(): string {
  try {
    return readConversation?.() ?? "";
  } catch {
    return "";
  }
}

/**
 * Whether a ⌘⏎ message is sitting here waiting for the turn to end, and who to tell.
 *
 * The wait happens inside the adapter (`if (takeQueuedFlag()) await waitUntilIdle(...)`), which
 * blocks *before* the fetch — so for up to twenty minutes assistant-ui shows your message in the
 * thread, shows the turn as running, and has sent nothing. That is indistinguishable from a
 * message that was sent and got no reply, which is the one reading it must not have.
 *
 * Published here rather than returned, because the composer that needs to say "waiting" and the
 * adapter that is doing the waiting are separated by the assistant-ui runtime and cannot reach
 * each other any other way.
 */
let holding = false;
const listeners = new Set<() => void>();

function setHolding(value: boolean): void {
  if (holding === value) return;
  holding = value;
  for (const notify of listeners) notify();
}

/** For `useSyncExternalStore` in the composer. */
export function subscribeHolding(notify: () => void): () => void {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

export function isHolding(): boolean {
  return holding;
}

/**
 * A ⌘⏎ message, held here until the turn is over, then sent.
 *
 * **The flag-and-wait version did not work, and could not have.** It set a flag and then called
 * the runtime's `send`, whose first line is `abortController.abort()` — so the turn it was
 * meant to wait behind was killed by the very act of queueing behind it. `waitUntilIdle` then
 * asked whether anything was running, was told no (correctly — it had just been stopped), and
 * sent immediately. ⌘⏎ was a Stop with extra steps. The adapter's own comment says the abort
 * happens before it gets there; the conclusion drawn from it was the wrong one.
 *
 * So the wait happens *before* the runtime is touched, in the keystroke, exactly as steering
 * does and for exactly the same reason: anything that reaches `performRoundtrip` has already
 * aborted the run in flight, so nothing that wants the run to survive may go through it.
 */
interface Held {
  text: string;
  deliver: (text: string) => void;
}
let held: Held | null = null;

/** What is waiting, for the composer to show. Empty when nothing is. */
export function heldMessage(): string {
  return held?.text ?? "";
}

/**
 * Hold `text` until the conversation is quiet, then hand it back to `deliver` to send.
 *
 * `deliver` puts the words back in the composer and sends them — by then there is genuinely no
 * turn to abort, which is the condition the old code assumed rather than established.
 */
export async function holdUntilIdle(
  conversationId: string,
  text: string,
  deliver: (text: string) => void,
): Promise<void> {
  // One at a time: a second ⌘⏎ replaces the first rather than queueing two turns behind each
  // other, which is a shape nobody asked for and every caller would have to reason about.
  held = { text, deliver };
  setHolding(true);
  try {
    await pollUntilIdle(conversationId);
  } finally {
    setHolding(false);
  }
  const mine = held;
  held = null;
  // Dropped if something else claimed the slot while this was waiting — that message is the
  // one the person meant, and sending both would be two turns from one intent.
  if (mine && mine.text === text) mine.deliver(mine.text);
}

/** Give up on whatever is waiting. */
export function dropHeld(): void {
  held = null;
  setHolding(false);
}

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
  setHolding(true);
  try {
    await pollUntilIdle(conversationId);
  } finally {
    // Cleared on every path — a give-up, a thrown fetch, an unmount. A composer stuck saying
    // "waiting to send" about a message that already went is the same lie in the other
    // direction.
    setHolding(false);
  }
}

async function pollUntilIdle(conversationId: string): Promise<void> {
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
