import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PresenceOrb } from "@/components/presence";
import { X } from "lucide-react";
import type { useMessages } from "@/hooks/use-messages";

type Messages = ReturnType<typeof useMessages>;

/** Notifications from Kith — the things he surfaces to you on his own (updates,
 * and "I need you on task X" pings). Read-only: the actual back-and-forth now
 * happens in each task's comment thread. Opening it clears the unread badge. */
export function InboxPanel({ inbox, onClose }: { inbox: Messages; onClose: () => void }) {
  const { messages, unread, markAllRead } = inbox;
  const feedRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const openLink = (link: string) => {
    onClose();
    navigate(link);
  };

  useEffect(() => {
    if (unread > 0) void markAllRead();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = 0; // newest-first, keep top in view
  }, [messages.length]);

  return (
    <aside className="fixed right-0 top-0 z-20 flex h-dvh w-96 max-w-full flex-col border-l bg-background shadow-xl">
      <div className="flex items-center gap-2 border-b px-4 py-2.5">
        <Bell className="size-4 text-muted-foreground" />
        <span className="font-semibold">Notifications</span>
        {messages.length ? <span className="text-xs text-muted-foreground">· {messages.length}</span> : null}
        <div className="flex-1" />
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {messages.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing yet. When Kith has something to tell you — a finding, or a task he needs your
            input on — it shows up here. To talk something through, open that task and use its
            comments.
          </p>
        ) : (
          <ul className="space-y-2.5">
            {messages.map((m) => (
              <li
                key={m.id}
                onClick={m.link ? () => openLink(m.link!) : undefined}
                className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${m.read ? "" : "border-kith/30 bg-kith-soft/50"} ${m.link ? "cursor-pointer transition-colors hover:border-kith/50 hover:bg-kith-soft/40" : ""}`}
              >
                <span className="mt-1"><PresenceOrb size={7} /></span>
                <div className="min-w-0 flex-1">
                  <p className="break-words whitespace-pre-wrap">{m.body}</p>
                  <span className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground">
                    {when(m.created_at)}
                    {m.link ? <span className="flex items-center text-kith">· open task<ChevronRight className="size-3" /></span> : null}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="border-t px-4 py-2 text-[11px] text-muted-foreground">
        Replies happen in each task's comments — open the task he mentions.
      </div>
    </aside>
  );
}

function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}
