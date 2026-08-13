import { useCallback, useEffect, useRef, useState } from "react";

import {
  clearMessages,
  fetchMessages,
  markAllMessagesRead,
  sendMessage,
  type KithMessage,
} from "@/lib/backend/messages";
import { deleteBrainItem } from "@/lib/backend/brain";

const POLL_MS = 8000;

/** Polls Kith's outbound messages, tracks the unread count, and raises a browser
 * notification when a new one arrives (if you've allowed it) — so he can reach
 * you even when this tab isn't in front. */
export function useMessages() {
  const [messages, setMessages] = useState<KithMessage[]>([]);
  const [unread, setUnread] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const seen = useRef<Set<number>>(new Set());
  const primed = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchMessages();
      setMessages(data.messages);
      setUnread(data.unread);
      setCounts(data.counts);

      const canNotify =
        primed.current && "Notification" in window && Notification.permission === "granted";
      for (const m of data.messages) {
        if (canNotify && m.sender === "kith" && !m.read && !seen.current.has(m.id)) {
          new Notification("Kith", { body: m.body, tag: `kith-${m.id}` });
        }
        seen.current.add(m.id);
      }
      primed.current = true;
    } catch {
      /* keep last-known */
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const markAllRead = useCallback(async () => {
    await markAllMessagesRead();
    refresh();
  }, [refresh]);

  const send = useCallback(
    async (body: string) => {
      await sendMessage(body);
      refresh();
    },
    [refresh],
  );

  /** Ask for notification permission — call from a click so browsers allow it. */
  const enableNotifications = useCallback(() => {
    if ("Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }
  }, []);

  /** Prune one, or a thread's worth. The panel is the only place that can now, since the
   *  browse tab it used to live in was a worse version of this list and has gone.
   *
   *  Deleted together and refreshed once: a five-note thread dismissed one request at a time
   *  re-rendered the list under the cursor five times, and the rows moved between clicks. */
  const dismiss = useCallback(
    async (ids: number | number[]) => {
      const many = Array.isArray(ids) ? ids : [ids];
      // Optimistic, because the poll is 8 seconds away and a row that lingers after you
      // dismissed it reads as a click that did not land.
      setMessages((current) => current.filter((one) => !many.includes(one.id)));
      await Promise.all(many.map((id) => deleteBrainItem("message", id).catch(() => {})));
      await refresh();
    },
    [refresh],
  );

  /** Empty the channel, or one kind of thing in it. `kinds` omitted means all of it. */
  const clear = useCallback(
    async (kinds?: string[]) => {
      setMessages((current) =>
        kinds ? current.filter((one) => one.sender === "user" || !kinds.includes(one.kind)) : [],
      );
      await clearMessages(kinds).catch(() => {});
      await refresh();
    },
    [refresh],
  );

  return {
    messages,
    unread,
    counts,
    markAllRead,
    send,
    refresh,
    dismiss,
    clear,
    enableNotifications,
  };
}
