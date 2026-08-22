import { useQuery } from "@tanstack/react-query";
import { Check, ListChecks } from "lucide-react";

import { keys } from "@/lib/query-keys";
import { pathForTask } from "@/lib/router";
import { cn } from "@/lib/utils";

interface ChecklistItem {
  id: number;
  text: string;
  done: boolean | number;
}

interface WorkingTask {
  id: number;
  goal: string;
  status: string;
  checklist?: ChecklistItem[];
}

/**
 * What he is working on in this conversation, and how far through it he is.
 *
 * A task was only ever visible by asking for it — `list_tasks` in a tool call you had to
 * expand, or the control panel, which is a different screen from the one the work is happening
 * on. So the answer to "what is he actually doing" lived one navigation away from the place you
 * were watching him do it, and a checklist he was ticking off round by round was invisible
 * unless you went looking between turns.
 *
 * Only while a task is `working`. `update_task` stamps the conversation when a task starts and
 * clears it when it stops, so this empties itself — a strip that lingered on yesterday's task
 * would be worse than no strip, because it would be answering the question wrongly rather than
 * not answering it.
 *
 * Polled, like the question card, and for the same reason: it has to be right when you come
 * back to a conversation whose work carried on without you.
 */
export function WorkingOn({ conversationId }: { conversationId: string }) {
  /* A task moving, or a checklist item ticking, is a `task` event, and `STALE_ON` points that at
   * this key — so the card follows him round by round.
   *
   * The 30-second interval that used to sit here as a backstop is gone, and what replaced it is
   * better than a shorter one: the stream is resumable, so a dropped connection replays what was
   * missed instead of leaving a gap for a timer to stumble over. Focus and reconnect refetch too.
   * See lib/query.ts. */
  const { data: task = null } = useQuery({
    queryKey: keys.workingOn(conversationId),
    queryFn: async (): Promise<WorkingTask | null> => {
      const response = await fetch(`/api/chat/${conversationId}/working-on`);
      if (!response.ok) return null;
      const body = (await response.json()) as WorkingTask | null;
      return body && body.id ? body : null;
    },
    // Nothing to ask about until there is a conversation.
    enabled: Boolean(conversationId),
  });

  if (!task) return null;

  const items = task.checklist ?? [];
  const done = items.filter((one) => Boolean(one.done)).length;

  const pct = items.length ? Math.round((done / items.length) * 100) : 0;

  return (
    // Panel spacing, not composer spacing. This carried `mx-auto`, `max-w-(--thread-max-width)`
    // and a `mb-1.5` from sitting in the composer stack — a thread measurement and a gap between
    // siblings, neither of which means anything here. `px-4` matches the panel's other rows; the
    // vertical padding is its own, because it is the first thing under the header and had been
    // sitting flush against the rule.
    <div className="w-full px-4 pt-3 pb-3">
      {/* Quieter than the composer, deliberately. It had the same border weight and the same
          text size, so two boxes of equal loudness sat on top of each other and the one you
          type into was not obviously the subject. This is a status line: it should be readable
          when you look for it and ignorable when you are not. */}
      <div className="border-border/40 bg-muted/25 rounded-lg border px-3 py-2.5">
        <div className="flex items-center gap-2">
          <ListChecks className="text-kith/70 size-3.5 shrink-0" />
          <a
            href={pathForTask(task.id)}
            className="text-muted-foreground hover:text-foreground min-w-0 flex-1 truncate text-[11px] transition-colors"
            title={task.goal}
          >
            <span className="text-muted-foreground/50 me-1.5 font-mono">#{task.id}</span>
            {task.goal}
          </a>

          {/* The count sits with a bar rather than alone at the far right, where it was a
              number with nothing to compare itself to. Two glances become one. */}
          {items.length > 0 ? (
            <span className="flex shrink-0 items-center gap-1.5">
              <span className="bg-border/70 h-1 w-10 overflow-hidden rounded-full">
                <span
                  className="bg-roam block h-full rounded-full transition-[width] duration-500"
                  style={{ width: `${pct}%` }}
                />
              </span>
              <span className="text-muted-foreground/60 font-mono text-[10px] tabular-nums">
                {done}/{items.length}
              </span>
            </span>
          ) : null}
        </div>

        {items.length > 0 ? (
          <ul className="mt-2.5 flex flex-col gap-2.5 ps-[22px]">
            {items.map((item) => {
              const ticked = Boolean(item.done);
              return (
                <li key={item.id} className="flex items-start gap-2 text-[11px] leading-normal">
                  <span
                    className={cn(
                      "mt-[2px] flex size-3 shrink-0 items-center justify-center rounded-[3px] border transition-colors",
                      ticked ? "bg-roam border-roam text-background" : "border-muted-foreground/30",
                    )}
                  >
                    {ticked ? <Check className="size-2" strokeWidth={3} /> : null}
                  </span>
                  <span
                    className={cn(
                      "min-w-0 flex-1",
                      ticked
                        ? "text-muted-foreground/40 line-through"
                        : "text-muted-foreground/90",
                    )}
                  >
                    {item.text}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
