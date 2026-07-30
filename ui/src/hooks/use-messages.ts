import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchMessages,
  markAllMessagesRead,
  sendMessage,
  type KithMessage,
} from "@/lib/backend/messages";

const POLL_MS = 8000;

/** Polls Kith's outbound messages, tracks the unread count, and raises a browser
 * notification when a new one arrives (if you've allowed it) — so he can reach
 * you even when this tab isn't in front. */
export function useMessages() {
  const [messages, setMessages] = useState<KithMessage[]>([]);
  const [unread, setUnread] = useState(0);
  const seen = useRef<Set<number>>(new Set());
  const primed = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchMessages();
      setMessages(data.messages);
      setUnread(data.unread);

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

  return { messages, unread, markAllRead, send, refresh, enableNotifications };
}
