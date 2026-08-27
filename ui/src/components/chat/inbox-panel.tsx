import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Gift,
  Megaphone,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { Markdown } from "@/components/files";
import { groupAlerts, readAlert, type Alert, type AlertThread } from "@/lib/alerts";
import { groupByDay, time } from "@/lib/dates";
import { cn } from "@/lib/utils";
import type { useMessages } from "@/hooks/use-messages";
import { Layer, useLayer } from "@/hooks/use-layer";

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
 *
 * Two things were still wrong after that, and both are about volume. He works in bursts and
 * comments as he goes, so a morning on one task arrives as five rows that each open with the
 * same forty-word clause naming the task — the same story, told from the start each time. And
 * there was no way to end that: dismissing was one row at a time, on hover, one X per
 * hundred. Hence threads (`lib/alerts.ts` lifts the repeated subject out and folds the run
 * into one card) and hence the clear menu, which is the only control here that can act on
 * more than what you can see.
 */
const KINDS: Record<string, { label: string; icon: typeof Bell; tone: string; wants: boolean }> = {
  asked: {
    label: "Needs your answer",
    icon: CircleHelp,
    tone: "text-orange-400",
    wants: true,
  },
  stuck: {
    label: "Stuck",
    icon: TriangleAlert,
    tone: "text-orange-400",
    wants: true,
  },
  delivered: { label: "Finished", icon: Gift, tone: "text-roam", wants: false },
  reachout: {
    label: "He reached out",
    icon: Megaphone,
    tone: "text-kith",
    wants: false,
  },
  note: {
    label: "Note",
    icon: Check,
    tone: "text-muted-foreground/60",
    wants: false,
  },
};

const kindOf = (kind: string) => KINDS[kind] ?? KINDS.note;

/** How much of a folded thread you see without asking. Two, because one preview reads as a
 *  single alert with a stray counter, and three is most of the screen back again. */
const PREVIEW = 2;

export function InboxPanel({ inbox, onClose }: { inbox: Messages; onClose: () => void }) {
  const { messages, unread, counts, markAllRead, dismiss, clear } = inbox;
  const feedRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const layer = useLayer(Layer.Panel);
  const [onlyWanting, setOnlyWanting] = useState(false);
  const [opened, setOpened] = useState<Set<string>>(() => new Set());

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

  // Escape closes it. It is a panel over the room you were in, and every other layer here
  // already lets you back out that way — but only the topmost layer should answer, or
  // cancelling the "clear everything?" dialog would take the panel down with it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (!layer.frontmost()) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const his = useMemo(() => messages.filter((one) => one.sender !== "user"), [messages]);
  const wanting = his.filter((one) => kindOf(one.kind).wants).length;

  // The counts on the chips and in the clear menu come from the server's totals, not from
  // the page above — the list is the most recent hundred, and a button that said "clear 100"
  // over a channel of six hundred would be quoting the limit rather than the truth.
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0) || his.length;
  const held = total > his.length ? total - his.length : 0;

  const days = useMemo(() => {
    const shown = onlyWanting ? his.filter((one) => kindOf(one.kind).wants) : his;
    const alerts = shown.map((one) => readAlert(one, wasUnread.has(one.id)));
    return groupByDay(alerts, (one) => one.createdAt).map(
      ([label, group]) =>
        [label, groupAlerts(group, (one) => !kindOf(one.kind).wants)] as [string, AlertThread[]],
    );
  }, [his, onlyWanting, wasUnread]);

  return (
    <aside
      className="bg-background fixed top-0 right-0 z-20 flex h-dvh w-[26rem] max-w-full flex-col border-s shadow-xl"
      aria-label="Alerts"
    >
      <div className="flex items-center gap-2 border-b px-4 py-2.5">
        <Bell className="text-muted-foreground size-4" />
        <span className="font-semibold">Alerts</span>
        <div className="flex-1" />
        <ClearMenu counts={counts} total={total} onClear={clear} />
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      {/* The filter is the whole point of having kinds. "Waiting on you · 0" stays on screen
          rather than disappearing when it empties: nothing waiting is the answer you came
          for, and a control that vanishes when it becomes good news never gets to say so. */}
      {total > 0 ? (
        <div className="flex items-center gap-1 border-b px-3 py-2">
          {(
            [
              [false, `Everything · ${total}`, true],
              [true, `Waiting on you · ${wanting}`, wanting > 0],
            ] as const
          ).map(([value, label, live]) => (
            <button
              key={String(value)}
              type="button"
              disabled={!live}
              onClick={() => setOnlyWanting(value)}
              className={cn(
                "rounded-md px-2 py-1 text-[11px] transition-colors",
                onlyWanting === value && live
                  ? "bg-kith-soft text-kith"
                  : "text-muted-foreground hover:text-foreground",
                !live && "text-muted-foreground/40 hover:text-muted-foreground/40 cursor-default",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {days.length === 0 ? (
          <p className="text-muted-foreground p-4 text-center text-sm leading-relaxed">
            {onlyWanting
              ? "Nothing is waiting on you."
              : "Nothing yet. When he finishes something, gets stuck, or needs an answer, it shows up here."}
          </p>
        ) : (
          <div className="space-y-4">
            {days.map(([label, threads]) => (
              <div key={label}>
                <p className="text-muted-foreground/50 mb-1.5 px-1 text-[10px] tracking-wide uppercase">
                  {label}
                </p>
                <ul className="space-y-1.5">
                  {threads.map((thread) => (
                    <li key={thread.key}>
                      <Thread
                        thread={thread}
                        open={opened.has(thread.key)}
                        onToggle={() =>
                          setOpened((current) => {
                            const next = new Set(current);
                            if (!next.delete(thread.key)) next.add(thread.key);
                            return next;
                          })
                        }
                        onOpen={() => {
                          if (!thread.link) return;
                          onClose();
                          navigate(thread.link);
                        }}
                        onDismiss={(ids) => void dismiss(ids)}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            ))}

            {/* Said out loud rather than left as a list that simply stops. */}
            {held > 0 && !onlyWanting ? (
              <p className="text-muted-foreground/50 px-1 pb-1 text-center text-[10px]">
                {held} older {held === 1 ? "alert is" : "alerts are"} kept but not shown here.
              </p>
            ) : null}
          </div>
        )}
      </div>
    </aside>
  );
}

/**
 * One card: a single alert, or a run of them about the same thing.
 *
 * The subject is on the card, once. Each entry underneath is the sentence with that clause
 * taken off, so what you scan down is the part that differs — which was the whole complaint.
 */
function Thread({
  thread,
  open,
  onToggle,
  onOpen,
  onDismiss,
}: {
  thread: AlertThread;
  open: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onDismiss: (ids: number[]) => void;
}) {
  const kind = kindOf(thread.kind);
  const Icon = kind.icon;
  const many = thread.items.length > 1;
  const visible = !many || open ? thread.items : thread.items.slice(0, PREVIEW);
  const hidden = thread.items.length - visible.length;

  return (
    <article
      className={cn(
        "group/thread relative rounded-xl border transition-colors",
        thread.unread ? "border-kith/30 bg-kith-soft/40" : "border-border/60 bg-card/30",
        thread.link && "hover:border-kith/50 hover:bg-kith-soft/30",
      )}
    >
      <div className="flex items-start gap-1.5 px-3 pt-2.5">
        <Icon className={cn("mt-0.75 size-3.5 shrink-0", kind.tone)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className={cn("truncate text-[11px] font-medium", kind.tone)}>{kind.label}</span>
            {many ? (
              <span className="bg-muted/70 text-muted-foreground rounded-full px-1.5 py-px text-[10px] tabular-nums">
                {thread.items.length}
              </span>
            ) : null}
            {/* Oldest first in the range, though the list under it is newest first — a span
                of time reads forwards whichever way you stacked the rows. */}
            <span className="text-muted-foreground/50 ms-auto shrink-0 text-[10px] tabular-nums">
              {many ? `${time(thread.items[thread.items.length - 1].createdAt)}–` : ""}
              {time(thread.items[0].createdAt)}
            </span>
            {/* In the row rather than floating over it. As an absolute corner button it sat
                on top of the timestamp, so the one thing you could always read was covered
                by the one control you rarely want. Space is reserved whether or not it is
                showing, so nothing shifts under the cursor on hover. */}
            <button
              type="button"
              aria-label={many ? `Dismiss these ${thread.items.length}` : "Dismiss"}
              title={many ? `Dismiss these ${thread.items.length}` : "Dismiss"}
              onClick={() => onDismiss(thread.items.map((one) => one.id))}
              className="text-muted-foreground/40 hover:text-destructive focus-visible:text-destructive -me-0.5 shrink-0 opacity-0 transition group-hover/thread:opacity-100 focus-visible:opacity-100"
            >
              <X className="size-3.5" />
            </button>
          </div>

          {thread.subject ? (
            <p className="text-foreground/85 mt-0.5 line-clamp-2 text-[12px] leading-snug font-medium">
              {thread.subject}
              {thread.taskId ? (
                <span className="text-muted-foreground/50 font-normal"> · #{thread.taskId}</span>
              ) : null}
            </p>
          ) : null}
        </div>
      </div>

      <div className="space-y-1 px-3 pt-1.5 pb-2.5">
        {visible.map((item) => (
          <Entry
            key={item.id}
            item={item}
            standalone={!many}
            clamped={many && !open}
            onOpen={onOpen}
            onDismiss={() => onDismiss([item.id])}
          />
        ))}

        <div className="flex items-center gap-3 pt-0.5">
          {hidden > 0 || (many && open) ? (
            <button
              type="button"
              onClick={onToggle}
              className="text-muted-foreground hover:text-foreground flex items-center gap-0.5 text-[10px] transition-colors"
            >
              <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
              {open ? "Show less" : `${hidden} more`}
            </button>
          ) : null}

          {thread.link ? (
            <button
              type="button"
              onClick={onOpen}
              className="text-kith ms-auto flex items-center text-[10px] hover:underline"
            >
              {thread.link.startsWith("/tasks/") ? "Open the task" : "Open"}
              <ChevronRight className="size-3" />
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
}

/** One message inside a card. Standalone alerts get the room; entries in a thread get a
 *  timestamp gutter, so a run of them reads as a log of one morning. */
function Entry({
  item,
  standalone,
  clamped,
  onOpen,
  onDismiss,
}: {
  item: Alert;
  standalone: boolean;
  /** Folded thread, not yet opened: show the opening of each entry, not all of it. Three
   *  lines is enough to tell one commit note from another, which is what a preview is for. */
  clamped: boolean;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  // Clicking the text opens the task, but not when you were selecting it — the bodies are
  // full of commit hashes and paths, and a panel that navigates away mid-copy is a panel you
  // stop copying out of.
  const open = () => {
    if (window.getSelection()?.toString()) return;
    onOpen();
  };

  if (standalone) {
    return (
      <div onClick={open} className="cursor-default [&_a]:cursor-pointer">
        {/* He writes these, and he formats them. */}
        <Markdown>{item.body}</Markdown>
      </div>
    );
  }

  return (
    <div className="group/entry hover:bg-muted/30 -mx-1 flex items-start gap-2 rounded-md px-1 py-0.5 transition-colors">
      <span className="text-muted-foreground/40 mt-0.75 w-11 shrink-0 text-[10px] tabular-nums">
        {time(item.createdAt)}
      </span>
      <div
        onClick={open}
        className={cn("min-w-0 flex-1 cursor-default", clamped && "line-clamp-3")}
      >
        <Markdown>{item.body}</Markdown>
      </div>
      <button
        type="button"
        aria-label="Dismiss this one"
        title="Dismiss this one"
        onClick={onDismiss}
        className="text-muted-foreground/40 hover:text-destructive focus-visible:text-destructive mt-0.75 shrink-0 opacity-0 transition group-hover/entry:opacity-100 focus-visible:opacity-100"
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

/**
 * The only control here that reaches past what you can see.
 *
 * Three options rather than one "clear all", because the thing you almost always want is to
 * lose the commentary and keep the question — and a single button that takes both would be a
 * button nobody dares press with something waiting. Each names its own count, and anything
 * that would remove nothing is not offered.
 */
function ClearMenu({
  counts,
  total,
  onClear,
}: {
  counts: Record<string, number>;
  total: number;
  onClear: (kinds?: string[]) => Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const confirm = useConfirm();

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    // On the way down, and claimed: Escape with this menu open means "close the menu", and
    // the panel's own Escape handler is listening on the same document.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const notes = counts.note ?? 0;
  const quiet = Object.keys(counts).filter((kind) => !kindOf(kind).wants);
  const quietCount = quiet.reduce((sum, kind) => sum + counts[kind], 0);
  const waiting = total - quietCount;

  const options: {
    label: string;
    subject: string;
    kinds?: string[];
    count: number;
  }[] = [];
  if (notes > 0) {
    options.push({
      label: `Clear notes · ${notes}`,
      subject: `${notes} ${notes === 1 ? "note" : "notes"} he made while working`,
      kinds: ["note"],
      count: notes,
    });
  }
  // Only worth its own line when it would take more than the notes option already does, and
  // when there is in fact something waiting for it to spare.
  if (waiting > 0 && quietCount > notes) {
    options.push({
      label: `Clear all but what's waiting · ${quietCount}`,
      subject: `${quietCount} alerts, keeping the ${waiting} waiting on you`,
      kinds: quiet,
      count: quietCount,
    });
  }
  if (total > 0) {
    options.push({
      label: `Clear everything · ${total}`,
      subject: `all ${total} alerts${waiting > 0 ? `, including the ${waiting} waiting on you` : ""}`,
      count: total,
    });
  }

  if (options.length === 0) return null;

  return (
    <div ref={ref} className="relative">
      <Button
        variant="ghost"
        size="icon"
        className="size-7"
        aria-label="Clear alerts"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Clear alerts"
        onClick={() => setOpen((was) => !was)}
      >
        <Trash2 className="size-4" />
      </Button>
      {open ? (
        <div
          role="menu"
          className="bg-popover absolute end-0 z-50 mt-1 min-w-60 rounded-lg border p-1 shadow-xl"
        >
          {options.map((option) => (
            <button
              key={option.label}
              type="button"
              role="menuitem"
              onClick={async () => {
                setOpen(false);
                const ok = await confirm({
                  title: "Clear these alerts?",
                  description: "They go for good — the tasks and their comments are untouched.",
                  subject: option.subject,
                  confirmLabel: "Clear",
                  destructive: true,
                });
                if (ok) await onClear(option.kinds);
              }}
              className="hover:bg-accent flex w-full items-center rounded-md px-2 py-1.5 text-left text-[13px]"
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
