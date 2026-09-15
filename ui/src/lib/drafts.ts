/**
 * What you typed and did not send, held until you come back to it.
 *
 * A chat pane owns a composer, and the composer's text lives in the assistant-ui runtime that
 * `useLocalRuntime` mints for that pane. A pane renders only while its tab is the active one in
 * its pane — `layout-view` draws the active tab and an `EmptyPane` for the rest — so clicking
 * another tab unmounts the pane, destroys the runtime, and takes a half-written message with it.
 * Nothing warned you and nothing could get it back. It is the same shape of fault as a reply
 * streaming into nobody: state that has to outlive your attention, kept in the one thing that
 * only exists while you are looking at it.
 *
 * So the text lives here and the pane borrows it.
 *
 * **Keyed on the tab's `uid`, not the conversation.** `uid` is "*this tab*, minted once and never
 * changed — including by the rename that gives a draft chat its conversation" (`layout/tree.ts`),
 * which is exactly the identity a draft needs. Keying on the conversation fails the two cases
 * that matter most: every chat that has not been spoken to yet is `""`, so a draft would be
 * orphaned the moment the stream names it *and* the next new chat would open holding the previous
 * one's text; and the same conversation open in two panes would be one slot, each pane
 * overwriting the other on mount. Keyed on the tab, the unnamed-chat case needs no migration at
 * all and two panes on one conversation are simply two places to type.
 *
 * **Deliberately the same shape as `canvas-state` and `flow-failures`**, which are the other two
 * keyed holding stores: a `create` over a `Record`, a per-key writer that returns the state
 * untouched when nothing changed, a per-key `forget`, and a non-subscribing getter for the
 * caller that reads at a moment rather than rendering against it. A store and not the
 * module-level slot `active-composer` uses, for the reason `focused-chat` states outright — this
 * one is *rendered against*, by the tab strip and the conversations list.
 *
 * **Never persisted**, which is the third thing it has in common with those two. It is tempting,
 * and `uid` looks like it would survive a reload — but it only survives inside the `kith-layout`
 * blob, whose reader throws the whole tree away on a version bump or a shape it does not
 * recognise. A draft store keyed on ids that can vanish independently of it is a set of entries
 * pointing at tabs that no longer exist: a mark on a conversation with nowhere to put the text.
 * The complaint this answers is losing a message by changing chats, not by quitting.
 *
 * **There is no `clearDraft`, and that is not an omission.** An empty composer *is* the clear:
 * `send()` blanks the text and notifies, and the ⏎ and ⌘⏎ paths both clear before they act, so
 * every way a message legitimately leaves the box arrives here as "the composer is now empty" and
 * drops the entry through the one path that also handles you deleting it by hand. A second
 * removal path could disagree with the screen; this one cannot. The only other way an entry goes
 * is `keep`, when its tab is gone.
 */
import { create } from "zustand";
import { useShallow } from "zustand/shallow";

import type { QuoteInfo } from "@assistant-ui/react";

export interface Draft {
  /** The conversation this was typed into, or `""` for a chat that has not been spoken to yet.
   *  Re-stamped when the stream names the chat, which is why `hold` compares it. */
  conversationId: string;
  /** The message, as markdown — the composer's own storage format, so this is the value and not
   *  a rendering of it. See `composer-input/markdown.ts`. */
  text: string;
  /** The excerpt attached by "Reply" on a selection. Held because it is part of what you were
   *  saying: restoring the words without the thing they answer is worse than restoring neither. */
  quote?: QuoteInfo;
  /** Files on the composer, held as the `File` objects themselves — which is safe precisely
   *  because this is never persisted.
   *
   *  Not a nicety. A paste over 2,000 characters or 30 lines is lifted out of the text by
   *  `composer-input/paste.ts` and becomes a `pasted-N.txt` attachment, so on the messages people
   *  most fear losing — a stack trace, a log — a large part of the draft is not in `text` at all.
   *  Holding the text and silently dropping those would be worse than today's loss, because today
   *  you can see that everything is gone. */
  attachments: File[];
  /** The project a chat was started in, before it has a conversation to record it against —
   *  "New chat here" from the sidebar.
   *
   *  Held for a reason that only appears once the text is: the pane reads it from a ref that
   *  `openConversation` nulls, so a restored draft would have its words back and its project
   *  gone, and the first turn — the one that says what the work is — would be assembled against
   *  no project at all. Silent, and wrong in the direction that costs the most. */
  projectId: number | null;
}

/** Whether two drafts say the same thing. Files by identity, which is what they are.
 *
 *  Every field, not just the text. `hold` runs on every composer notification — a keystroke, but
 *  also dictation starting and an attachment finishing — so the comparison is what keeps those
 *  from being writes. And it has to include `conversationId`, or the re-stamp when a chat is
 *  finally named is a no-op against unchanged text and the conversations list never gets its
 *  mark. */
function same(a: Draft | undefined, b: Draft): boolean {
  if (!a) return false;
  if (a.text !== b.text) return false;
  if (a.conversationId !== b.conversationId) return false;
  if (a.projectId !== b.projectId) return false;
  if (a.quote?.text !== b.quote?.text || a.quote?.messageId !== b.quote?.messageId) return false;
  if (a.attachments.length !== b.attachments.length) return false;
  return a.attachments.every((file, at) => file === b.attachments[at]);
}

/** Whether there is anything here worth keeping.
 *
 *  Exactly the library's own `isEmpty` — text that is only whitespace does not count — so a stray
 *  space or a `/` typed and deleted never lights a mark on a chat.
 *
 *  **And deliberately not the quote**, which is the one place this disagrees with the `Draft`
 *  shape it stores. A quote is held *with* a message and never counts as one on its own, because
 *  `setText("")` does not clear it: steering with ⏎ empties the box and leaves the quote behind,
 *  so counting it would leave a permanent "unsent text" mark on a chat where everything the
 *  person typed was delivered — and restoring it would put a stale excerpt on the next real
 *  message. A quote alone is not sendable either (`isEmpty` ignores it, so `canSend` is false),
 *  which settles it: if it cannot be sent, it is not an unsent message. */
function anything(draft: Draft): boolean {
  return Boolean(draft.text.trim() || draft.attachments.length);
}

/**
 * Whether a composer is not holding a draft but a message already on its way out.
 *
 * Here rather than at the call site because it answers this module's own question — what counts
 * as unsent — and because it is the one part of that question with no way to reach it from a
 * store test otherwise.
 *
 * `send()` blanks the text and the quote, raises its sending flag and notifies **before** it
 * awaits the attachment uploads, so for the length of an upload the composer looks exactly like
 * an attachment-only draft. Held as one, the tab grows an "unsent text" mark on the message you
 * just sent — and if the pane unmounts inside that window the notification that would have
 * cleared it never arrives, so the mark is permanent and coming back re-attaches files that were
 * already delivered.
 *
 * `!isEmpty && !canSend` is an exact read of that flag rather than a guess: on the local runtime
 * `canSend` is `!isEmpty && !isSendDisabled && !isSending`, and `isSendDisabled` is a permanent
 * `false`, so the only way a composer with something in it cannot send is that it is already
 * sending. Notably it stays `true` while a *turn* is running, which is what keeps a draft typed
 * mid-turn held.
 */
export function alreadyGoing(state: { isEmpty: boolean; canSend: boolean }): boolean {
  return !state.isEmpty && !state.canSend;
}

export const useDrafts = create<{
  /** uid -> what is unsent in that tab. */
  drafts: Record<string, Draft>;
  /** Write what the composer now holds, or drop the entry if it holds nothing. */
  hold: (uid: string, draft: Draft) => void;
  /** Drop one, by tab. */
  forget: (uid: string) => void;
  /** Drop every draft whose tab is not in `live` — the tabs that still exist.
   *
   *  The one removal path besides going empty, and it covers every way a tab can go: the X,
   *  "close the others", a pane closing, the layout being reset or loaded from a file. Called
   *  with the tree rather than hooked onto each of those, because "does this tab still exist" is
   *  a question with one answer and several ways to ask it. */
  keep: (live: ReadonlySet<string>) => void;
}>((set) => ({
  drafts: {},
  hold: (uid, draft) =>
    set((state) => {
      const was = state.drafts[uid];
      if (!anything(draft)) {
        if (!was) return state;
        const next = { ...state.drafts };
        delete next[uid];
        return { drafts: next };
      }
      if (same(was, draft)) return state;
      return { drafts: { ...state.drafts, [uid]: draft } };
    }),
  forget: (uid) =>
    set((state) => {
      if (!(uid in state.drafts)) return state;
      const next = { ...state.drafts };
      delete next[uid];
      return { drafts: next };
    }),
  keep: (live) =>
    set((state) => {
      const stale = Object.keys(state.drafts).filter((uid) => !live.has(uid));
      if (!stale.length) return state;
      const next = { ...state.drafts };
      for (const uid of stale) delete next[uid];
      return { drafts: next };
    }),
}));

/** What is unsent in one tab. Read at mount to restore it, never rendered against — the pair to
 *  `useDraftedTabs` below, and the same split `active-composer` draws between `activeComposer()`
 *  and `useCanAttach()`. */
export function draftFor(uid: string): Draft | undefined {
  return uid ? useDrafts.getState().drafts[uid] : undefined;
}

/**
 * Which tabs are holding one, and which conversation each belongs to. For rendering.
 *
 * Shallow-compared on purpose, and it is load-bearing rather than an optimisation. The store
 * changes on every keystroke; zustand compares a selector's result with `Object.is`, so a
 * selector returning a fresh object would re-render the tab strip and the whole conversations
 * list on every character typed. Projecting to just the ids and comparing shallowly means those
 * surfaces re-render when a chat *starts or stops* holding a draft, which is the only thing they
 * draw.
 *
 * The conversation id comes along because the conversations list keys its rows on it and has no
 * idea what a tab is. `""` for a chat with no conversation yet — it has no row to mark, and its
 * tab is the whole answer.
 */
export function useDraftedTabs(): Record<string, string> {
  return useDrafts(
    useShallow((state) => {
      const held: Record<string, string> = {};
      for (const [uid, draft] of Object.entries(state.drafts)) held[uid] = draft.conversationId;
      return held;
    }),
  );
}
