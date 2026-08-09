import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell,
  Check,
  ChevronRight,
  CircleHelp,
  Gift,
  Megaphone,
  TriangleAlert,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/files";
import { groupByDay, time } from "@/lib/dates";
import { cn } from "@/lib/utils";
import type { useMessages } from "@/hooks/use-messages";

type Messages = ReturnType<typeof useMessages>;

/**
 * What he has told you, and which of it wants something.
 *
 * Every row used to look the same — one orb, one paragraph, one timestamp — which was exactly
 * the problem the message kinds were introduced to solve on the notification side and hadn't
 * been carried through to here. A question he is blocked on and a note he made while working
 * are not the same event, and a list that renders them identically makes you read all of it
 * to find the one that matters.
 *
 * So: each kind says what it is, in an icon and a word and a colour. Grouped by day, because
 * "3 days ago" in a flat list is a date you have to compute. And a filter, since the common
 * question is not "what has he said" but "what is waiting on me".
 */
const KINDS: Record<string, { label: string; icon: typeof Bell; tone: string; wants: boolean }> = {
  asked: { label: "Needs your answer", icon: CircleHelp, tone: "text-orange-400", wants: true },
  stuck: { label: "Stuck", icon: TriangleAlert, tone: "text-orange-400", wants: true },
  delivered: { label: "Finished", icon: Gift, tone: "text-roam", wants: false },
  reachout: { label: "He reached out", icon: Megaphone, tone: "text-kith", wants: false },
  note: { label: "Note", icon: Check, tone: "text-muted-foreground/60", wants: false },
};

export function InboxPanel({ inbox, onClose }: { inbox: Messages; onClose: () => void }) {
  const { messages, unread, markAllRead, dismiss } = inbox;
  const feedRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const [onlyWanting, setOnlyWanting] = useState(false);

  // What was unread when you opened it, kept for this viewing. Marking everything read on
  // open and *also* dropping the highlight meant the act of looking erased what was new.
  const [wasUnread] = useState(
    () => new Set(messages.filter((one) => !one.read).map((one) => one.id)),
  );

  useEffect(() => {
    if (unread > 0) void markAllRead();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = 0; // newest first, keep the top in view
  }, [messages.length]);

  const shown = onlyWanting
    ? messages.filter((one) => KINDS[one.kind]?.wants)
    : messages.filter((one) => one.sender !== "user");
  const wanting = messages.filter((one) => KINDS[one.kind]?.wants).length;

  const days = useMemo(() => groupByDay(shown, (one) => one.created_at), [shown]);

  return (
    <aside className="bg-background fixed top-0 right-0 z-20 flex h-dvh w-[26rem] max-w-full flex-col border-s shadow-xl">
      <div className="flex items-center gap-2 border-b px-4 py-2.5">
        <Bell className="text-muted-foreground size-4" />
        <span className="font-semibold">Alerts</span>
        <div className="flex-1" />
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      {/* The filter is the whole point of having kinds. Hidden when there is nothing waiting,
          since a toggle that can only ever empty the list is not a useful control. */}
      {wanting > 0 ? (
        <div className="flex items-center gap-1 border-b px-3 py-2">
          {(
            [
              [false, `Everything · ${messages.filter((one) => one.sender !== "user").length}`],
              [true, `Waiting on you · ${wanting}`],
            ] as const
          ).map(([value, label]) => (
            <button
              key={String(value)}
              type="button"
              onClick={() => setOnlyWanting(value)}
              className={cn(
                "rounded-md px-2 py-1 text-[11px] transition-colors",
                onlyWanting === value
                  ? "bg-kith-soft text-kith"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {shown.length === 0 ? (
          <p className="text-muted-foreground p-4 text-center text-sm leading-relaxed">
            {onlyWanting
              ? "Nothing is waiting on you."
              : "Nothing yet. When he finishes something, gets stuck, or needs an answer, it shows up here."}
          </p>
        ) : (
          <div className="space-y-4">
            {days.map(([label, group]) => (
              <div key={label}>
                <p className="text-muted-foreground/50 mb-1.5 px-1 text-[10px] tracking-wide uppercase">
                  {label}
                </p>
                <ul className="space-y-1.5">
                  {group.map((message) => {
                    const kind = KINDS[message.kind] ?? KINDS.note;
                    const Icon = kind.icon;
                    return (
                      <li key={message.id} className="group relative">
                        <button
                          type="button"
                          disabled={!message.link}
                          onClick={() => {
                            if (!message.link) return;
                            onClose();
                            navigate(message.link);
                          }}
                          className={cn(
                            "w-full rounded-xl border p-3 text-start transition-colors",
                            wasUnread.has(message.id)
                              ? "border-kith/30 bg-kith-soft/40"
                              : "border-border/60 bg-card/30",
                            message.link && "hover:border-kith/50 hover:bg-kith-soft/30",
                            !message.link && "cursor-default",
                          )}
                        >
                          <span className="flex items-center gap-1.5">
                            <Icon className={cn("size-3.5 shrink-0", kind.tone)} />
                            <span className={cn("text-[11px] font-medium", kind.tone)}>
                              {kind.label}
                            </span>
                            <span className="text-muted-foreground/50 ms-auto text-[10px] tabular-nums">
                              {time(message.created_at)}
                            </span>
                          </span>
                          <span className="mt-1 block text-sm">
                            {/* He writes these, and he formats them. */}
                            <Markdown>{message.body}</Markdown>
                          </span>
                          {message.link ? (
                            <span className="text-kith mt-1 flex items-center text-[10px]">
                              {message.link.startsWith("/tasks/") ? "Open the task" : "Open"}
                              <ChevronRight className="size-3" />
                            </span>
                          ) : null}
                        </button>

                        {/* Pruning, which the removed Messages tab was the only place to do.
                            On hover so it never competes with the thing being read. */}
                        <button
                          type="button"
                          aria-label="Dismiss"
                          title="Dismiss"
                          onClick={() => void dismiss(message.id)}
                          className="text-muted-foreground/40 hover:text-destructive absolute end-2 top-2 opacity-0 transition group-hover:opacity-100"
                        >
                          <X className="size-3.5" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

