/**
 * Start a chat with him and send it its first message, from the panel.
 *
 * The task page's composer ("Reply to him — or say what to change in the plan…") and the Sources
 * reader's "Ask about this" have nowhere of their own to send to. A task has no thread any more —
 * the comment thread was removed because every note on it raised an alert and none of them could
 * be answered — and nothing reads the channel `sendMessage` posts to (`pending_user_messages` has
 * no caller), so a note sent there would be read by nobody. Where you talk to him is a chat, so
 * that is where this goes: a new one, in the right project, with what you wrote sent as its first
 * message — and you are taken there to watch the reply arrive.
 *
 * Until 2026-10-01 this only put the words in that chat's composer and left you to press Enter in
 * another screen, which read as the button not working. It sends now, but not from here: the
 * panel posts the message to `lib/outbox` for the draft tab, and that tab's pane sends it through
 * its own runtime, exactly as if you had typed it. The two cases the old version handled by hand —
 * the draft tab not mounted yet (it opens behind the route change and takes the message when it
 * mounts) and already mounted under the panel (it is subscribed and takes it at once) — are the
 * outbox's to handle, and it hands the message over once.
 *
 * Text already drafted in that tab stays in its composer; the message is sent on its own.
 */
import { useLayout } from "@/components/shell/layout/store";
import { panes } from "@/components/shell/layout/tree";
import { post } from "@/lib/outbox";

/**
 * Open a new chat with `open` (the shell's `onNewConversation`, which also leaves the panel) and
 * send `text` as its first message, in `projectId`. False when no draft tab came of it — the
 * caller says so rather than pretending it worked.
 */
export function sendInNewChat(open: () => void, text: string, projectId: number | null): boolean {
  if (!text.trim()) return false;
  open();
  // `onNewConversation` opens the draft synchronously, so the tab is in the tree by now.
  const tab = panes(useLayout.getState().tree)
    .flatMap((pane) => pane.tabs)
    .find((one) => one.surface === "chat" && !one.conversationId && one.uid);
  if (!tab?.uid) return false;
  post(tab.uid, { text, projectId });
  return true;
}
