/**
 * A message to send as the first turn of a new chat, from somewhere that is not that chat.
 *
 * The Control Panel has two places that are really the start of a conversation — "Request
 * changes" on a task, and "Ask about this" on a source — and both used to stop one step short:
 * they put the words in a new chat's composer and left you to press Enter in another screen. The
 * panel sits above every chat pane, so it cannot reach a runtime to send through, and a second
 * way of starting turns from out here (its own `POST /api/chat`, its own stream) would be a copy
 * of the adapter that agrees with it until it quietly does not: the project binding on the first
 * turn, the conversation id the stream reports, the claim that stops the stored copy rendering
 * twice.
 *
 * So the panel posts the message here, keyed on the draft tab's `uid`, and the pane for that tab
 * sends it — through its own runtime, the same `append` that a typed message ends in.
 *
 * **Taken, not read.** `take` removes the entry in the same synchronous write that returns it,
 * which is the whole of the "never twice" guarantee: a remount, an HMR update, React's
 * development double-mount, or the pane mounting while its subscription also fires all find the
 * slot already empty. There is no "sent" flag to keep in step with anything.
 *
 * **One store, two cases.** The draft tab is either not mounted yet (its pane takes the entry on
 * mount) or already mounted, sitting under the panel (its pane is subscribed and takes it the
 * moment it lands). `usePosted` covers both, so the panel never has to know which.
 *
 * **Stale entries are dropped, not sent.** An entry nobody took — the tab was closed before its
 * pane ever mounted — must not go out the next time something with that `uid` appears: a message
 * sent a minute after you asked, into whatever you are doing then, is worse than one not sent.
 * Never persisted, for the reason `drafts` gives.
 */
import { useEffect, useRef } from "react";
import { create } from "zustand";

export interface Posted {
  /** The message, as the person will see it in the thread. */
  text: string;
  /** The project the new conversation starts in, or null for none. Applied before the send,
   *  because the first turn is the one that carries the binding (see the adapter's `project`). */
  projectId: number | null;
  /** When it was posted, for `STALE_MS`. */
  at: number;
}

/** Far longer than a tab switch and a pane mount take; past it, the tab never came. */
export const STALE_MS = 15_000;

const box = create<{ posted: Record<string, Posted> }>(() => ({ posted: {} }));

/** Leave a message for the tab `uid` to send as its first turn. Replaces one already waiting
 *  there — the panel only ever means its latest ask. */
export function post(uid: string, message: { text: string; projectId: number | null }, now = Date.now()): void {
  if (!uid || !message.text.trim()) return;
  box.setState((state) => ({ posted: { ...state.posted, [uid]: { ...message, at: now } } }));
}

/** Remove and return what is waiting for `uid`. Undefined when nothing is, or when what was
 *  there is too old to send (and it is gone either way). */
export function take(uid: string, now = Date.now()): Posted | undefined {
  const waiting = uid ? box.getState().posted[uid] : undefined;
  if (!waiting) return undefined;
  box.setState((state) => {
    const next = { ...state.posted };
    delete next[uid];
    return { posted: next };
  });
  return now - waiting.at > STALE_MS ? undefined : waiting;
}

/** Whether anything is waiting for `uid`. For tests and assertions, never for deciding to send. */
export function waitingFor(uid: string): boolean {
  return Boolean(uid && box.getState().posted[uid]);
}

/**
 * Deliver what is posted for `uid` to `deliver`, once: on mount, and whenever something is posted
 * while mounted.
 *
 * The take happens a tick after the effect, not in it, and the cleanup cancels that tick. Under
 * React's development double-mount the first effect is torn down before its tick fires, so only
 * the second one takes — and the send happens after the mount has committed rather than in the
 * middle of it, when the runtime's own effects have all run.
 */
export function usePosted(uid: string | undefined, deliver: (message: Posted) => void): void {
  const latest = useRef(deliver);
  latest.current = deliver;
  useEffect(() => {
    if (!uid) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let gone = false;
    const check = () => {
      if (timer !== undefined || !box.getState().posted[uid]) return;
      timer = setTimeout(() => {
        timer = undefined;
        if (gone) return;
        const message = take(uid);
        if (message) latest.current(message);
      }, 0);
    };
    check();
    const stop = box.subscribe(check);
    return () => {
      gone = true;
      stop();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [uid]);
}
