/**
 * Making a hundred alerts readable: what each one is *about*, and which of them are the
 * same story told five times.
 *
 * He writes his notifications as sentences, and the sentence carries the context:
 *
 *     New note on “Restore CI compatibility after the domain import migration” (task #79):
 *     Pushed b432a34 to origin/main successfully.
 *
 * Which is right for a desktop notification — that popup is all you get — and wrong for a
 * list of them. Five notes on one task repeat that title five times, and the part you have
 * not read yet starts a hundred and twenty characters in. Reading the panel meant reading
 * the same clause over and over to find where each row actually began.
 *
 * So the subject is pulled off the front here, shown once per thread instead of once per
 * row, and the sentences are stored on the server exactly as they were — the notification
 * still says what it is about, and old rows already in the database parse the same as new
 * ones. That is the reason this is a reader rather than a change to what he writes.
 */

import type { KithMessage } from "@/lib/backend/messages";

/**
 * The lead-in he puts in front of a quoted subject: a short verb phrase, the title in
 * curly quotes, optionally the task number, then a colon or a dash.
 *
 * Every one of his message templates is this shape — "New note on", "I need your input on",
 * "I couldn't finish", "I've left … with you —", "Finished something on" — so one pattern
 * covers them without a list that would go stale the next time he learns to say something.
 * The bounded lead-in is what keeps it honest: a message that merely happens to quote
 * something halfway through a sentence does not match, and comes through untouched.
 */
const SUBJECT =
  /^[^“”"]{0,48}[“"]([^“”"]{1,240})[”"]\s*(?:\(task #\d+\))?\s*(?:with you\s*)?(?::|—)\s*/u;

const TASK_LINK = /^\/tasks\/(\d+)\b/;

/** One alert, with the repeated part lifted out of its text. */
export interface Alert {
  id: number;
  kind: string;
  /** What it is about — a task's goal, usually. Null when he wrote a bare sentence. */
  subject: string | null;
  /** The message with the subject clause removed, which is the part you have not read. */
  body: string;
  taskId: number | null;
  link: string | null;
  createdAt: string;
  /** Was it unread when the panel was opened. Kept for the viewing, not the truth now. */
  unread: boolean;
}

/**
 * A run of alerts about the same thing, shown as one card.
 *
 * A thread of one is still a thread — the panel renders both through the same path, so a
 * lone note and a run of five cannot drift into looking like different kinds of object.
 */
export interface AlertThread {
  key: string;
  subject: string | null;
  link: string | null;
  taskId: number | null;
  /** The kind they share. A thread is only ever formed from one kind. */
  kind: string;
  items: Alert[];
  unread: boolean;
}

/** Strip the subject clause off a message and say what it was about. */
export function readAlert(message: KithMessage, unread = false): Alert {
  const body = message.body ?? "";
  const found = SUBJECT.exec(body);
  const rest = found ? body.slice(found[0].length).trim() : "";
  const link = message.link ?? null;
  const task = link ? TASK_LINK.exec(link) : null;
  return {
    id: message.id,
    kind: message.kind,
    // A message that is *only* its lead-in ("Finished something on “X”:" and nothing after)
    // would otherwise render as an empty row, so in that case the sentence stays whole.
    subject: found && rest ? found[1].trim() : null,
    body: found && rest ? rest : body,
    taskId: task ? Number(task[1]) : null,
    link,
    createdAt: message.created_at,
    unread,
  };
}

/**
 * Fold consecutive alerts about the same subject into threads.
 *
 * Consecutive only, and never across a subject or a kind. Collapsing every note on task #79
 * wherever it fell would reorder his day — the panel is a timeline, and a card that gathers
 * up rows from Tuesday and Thursday is no longer telling you when anything happened.
 *
 * `groupable` decides what may be folded at all, and the panel says no to the kinds that are
 * waiting on an answer. Those are the reason to open the panel; burying one behind "and 4
 * more" would be this whole exercise defeating itself.
 */
export function groupAlerts(alerts: Alert[], groupable: (alert: Alert) => boolean): AlertThread[] {
  const threads: AlertThread[] = [];
  for (const alert of alerts) {
    const last = threads[threads.length - 1];
    const joins =
      last !== undefined &&
      groupable(alert) &&
      groupable(last.items[0]) &&
      last.kind === alert.kind &&
      last.subject !== null &&
      last.subject === alert.subject;

    if (joins) {
      last.items.push(alert);
      last.unread = last.unread || alert.unread;
    } else {
      threads.push({
        key: `t${alert.id}`,
        subject: alert.subject,
        link: alert.link,
        taskId: alert.taskId,
        kind: alert.kind,
        items: [alert],
        unread: alert.unread,
      });
    }
  }
  return threads;
}
