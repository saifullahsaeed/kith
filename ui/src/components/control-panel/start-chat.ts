/**
 * Start a chat with words already in its composer.
 *
 * The task page's composer ("Reply to him — or say what to change in the plan…") has nowhere of its
 * own to send to. A task has no thread any more — the comment thread was removed because every
 * note on it raised an alert and none of them could be answered — and nothing reads the channel
 * `sendMessage` posts to (`pending_user_messages` has no caller), so a note sent there would be
 * read by nobody. Where you talk to him is a chat, so that is where this goes: a new one, in the
 * task's project, with what you typed waiting in its composer for you to send. You see exactly
 * what goes out, in the place the reply will come back.
 *
 * It rides on stores the shell already has rather than a new path into it, and there are two
 * cases because the shell keeps one draft chat at a time:
 *
 *   - **The draft tab is not mounted** (the usual case: it is new, or it sits behind another tab).
 *     `onNewConversation` opens it synchronously and the panel's route change re-renders on the
 *     next frame; in between, the tab exists and its pane has not mounted, so a draft held for its
 *     `uid` is what the pane restores when it does (`chat-pane`'s `heldOnMount`) — words *and*
 *     project.
 *   - **It is already mounted** — a "New chat" tab was the one in front, as it is in a fresh
 *     window. Its pane read its draft long ago and will not read it again, so the words go
 *     straight into its composer once it is the focused one (`active-composer`). Its project is
 *     its own state and stays as it was; the text names the task, so he can still find it.
 *
 * Text already drafted in that tab is kept, with this after it.
 */
import { useLayout } from "@/components/shell/layout/store";
import { panes } from "@/components/shell/layout/tree";
import { activeComposer } from "@/lib/active-composer";
import { draftFor, useDrafts } from "@/lib/drafts";

/** The tab in front of you: the focused pane's active tab. */
function frontTab() {
  const { tree, focused } = useLayout.getState();
  const pane = panes(tree).find((one) => one.id === focused);
  return pane?.tabs[pane.active];
}

/**
 * Open a new chat with `open` (the shell's `onNewConversation`) and put `text` in its composer.
 * False when no draft tab came of it — the caller says so rather than pretending it worked.
 */
export function startChatWith(open: () => void, text: string, projectId: number | null): boolean {
  // Taken before the open moves focus: which composer was claiming the slot, and whose it was.
  const before = { composer: activeComposer(), front: frontTab()?.uid };
  open();
  const tab = panes(useLayout.getState().tree)
    .flatMap((pane) => pane.tabs)
    .find((one) => one.surface === "chat" && !one.conversationId && one.uid);
  if (!tab?.uid) return false;
  const uid = tab.uid;
  const was = draftFor(uid);
  const whole = was?.text.trim() ? `${was.text.trimEnd()}\n\n${text}` : text;
  useDrafts.getState().hold(uid, {
    conversationId: "",
    text: whole,
    quote: was?.quote,
    attachments: was?.attachments ?? [],
    projectId,
  });

  /* The mounted case: wait (briefly) for the draft tab's composer to be the one claiming the
   * slot, then write into it — unless the restore on mount already did, which is the other case
   * arriving first. "Its composer" is the slot once the draft is in front *and* the slot has moved
   * off whatever chat was in front before — or straight away, when the draft already was. Until
   * then the slot can still hold the chat you were in, and writing there is the wrong chat. */
  let tries = 0;
  const tick = () => {
    tries += 1;
    const composer = activeComposer();
    const ours = frontTab()?.uid === uid && composer && (before.front === uid || composer !== before.composer);
    if (ours) {
      const now = composer.getState().text;
      if (!now.trim()) composer.setText(whole);
      else if (!now.includes(text)) composer.setText(`${now.trimEnd()}\n\n${text}`);
      return;
    }
    // A second is far longer than a tab switch takes; past it something else has focus.
    if (tries < 20) setTimeout(tick, 50);
  };
  setTimeout(tick, 50);
  return true;
}
