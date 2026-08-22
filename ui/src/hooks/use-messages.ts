import { useCallback, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  clearMessages,
  fetchMessages,
  markAllMessagesRead,
  sendMessage,
  type KithMessage,
} from "@/lib/backend/messages";
import { deleteBrainItem } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";

interface Inbox {
  messages: KithMessage[];
  unread: number;
  counts: Record<string, number>;
}

/**
 * Kith's outbound messages, the unread count, and the notification when a new one arrives.
 *
 * This polled every eight seconds. `message` has always been a kind of change the server
 * publishes — `add_message` is decorated with it — so the poll was never the mechanism, only a
 * second one nobody had removed: the badge could be up to eight seconds behind a message that had
 * already been announced on the stream.
 *
 * The notification is raised from a `useEffect`-free path on purpose. It fires as messages arrive
 * in the query's data, and `seen` is what keeps it to once each — a set that survives refetches,
 * because a refetch returns the same messages and a naive check would re-announce all of them
 * every time the cache revalidated.
 */
export function useMessages() {
  const cache = useQueryClient();
  const seen = useRef<Set<number>>(new Set());
  const primed = useRef(false);

  const { data } = useQuery({
    queryKey: keys.messages(),
    queryFn: async (): Promise<Inbox> => {
      const fetched = await fetchMessages();
      announce(fetched.messages);
      return fetched;
    },
  });

  /* Announce what has not been announced.
   *
   * Inside the query function rather than in an effect on the data, because the data can be handed
   * back from the cache unchanged — two components mounting, a window regaining focus — and an
   * effect would fire on each of those while this fires only when the server actually answered.
   *
   * `primed` skips the first load: everything already in the inbox when the window opens is old
   * news, and announcing forty of them at once is how a notification system gets muted. */
  function announce(messages: KithMessage[]): void {
    const canNotify =
      primed.current && "Notification" in window && Notification.permission === "granted";
    for (const message of messages) {
      if (canNotify && message.sender === "kith" && !message.read && !seen.current.has(message.id)) {
        new Notification("Kith", { body: message.body, tag: `kith-${message.id}` });
      }
      seen.current.add(message.id);
    }
    primed.current = true;
  }

  const refresh = useCallback(
    () => void cache.invalidateQueries({ queryKey: keys.messages() }),
    [cache],
  );

  /** Change what is on screen now, without waiting to be told. */
  const now = useCallback(
    (change: (was: Inbox) => Inbox) =>
      cache.setQueryData<Inbox>(keys.messages(), (was) => (was ? change(was) : was)),
    [cache],
  );

  const markAllRead = useCallback(async () => {
    // `read` is a number on the wire (SQLite's 0/1), so 1 rather than true — the row shape has to
    // stay exactly what a refetch would hand back or the optimistic view and the real one differ.
    now((was) => ({
      ...was,
      unread: 0,
      messages: was.messages.map((one) => ({ ...one, read: 1 })),
    }));
    await markAllMessagesRead();
    refresh();
  }, [now, refresh]);

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

  /** Prune one, or a thread's worth. The panel is the only place that can.
   *
   *  Deleted together and refreshed once: a five-note thread dismissed one request at a time
   *  re-rendered the list under the cursor five times, and the rows moved between clicks. */
  const dismiss = useCallback(
    async (ids: number | number[]) => {
      const many = Array.isArray(ids) ? ids : [ids];
      // Optimistic, because a row that lingers after you dismissed it reads as a click that did
      // not land — and that was true when the answer was eight seconds away and is still true when
      // it is one round trip away.
      now((was) => ({ ...was, messages: was.messages.filter((one) => !many.includes(one.id)) }));
      await Promise.all(many.map((id) => deleteBrainItem("message", id).catch(() => {})));
      refresh();
    },
    [now, refresh],
  );

  /** Empty the channel, or one kind of thing in it. `kinds` omitted means all of it. */
  const clear = useCallback(
    async (kinds?: string[]) => {
      now((was) => ({
        ...was,
        messages: kinds
          ? was.messages.filter((one) => one.sender === "user" || !kinds.includes(one.kind))
          : [],
      }));
      await clearMessages(kinds).catch(() => {});
      refresh();
    },
    [now, refresh],
  );

  return {
    messages: data?.messages ?? [],
    unread: data?.unread ?? 0,
    counts: data?.counts ?? {},
    markAllRead,
    send,
    refresh,
    dismiss,
    clear,
    enableNotifications,
  };
}
