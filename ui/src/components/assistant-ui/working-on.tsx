import { useEffect, useState } from "react";
import { Check, ListChecks } from "lucide-react";

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
  const [task, setTask] = useState<WorkingTask | null>(null);

  useEffect(() => {
    if (!conversationId) {
      setTask(null);
      return;
    }
    let alive = true;
    const load = () =>
      fetch(`/api/chat/${conversationId}/working-on`)
        .then((response) => (response.ok ? response.json() : null))
        .then((body: WorkingTask | null) => {
          if (alive) setTask(body && body.id ? body : null);
        })
        .catch(() => {});
    load();
    // Slower than the question card: nothing is blocked on this, it is a status line. Fast
    // enough that a tick lands while you are still looking at the round that made it.
    const timer = setInterval(load, 2_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [conversationId]);

  if (!task) return null;

  const items = task.checklist ?? [];
  const done = items.filter((one) => Boolean(one.done)).length;

  return (
    <div className="mx-auto mb-2 w-full max-w-(--thread-max-width) px-4">
      <div className="border-border/60 bg-card/60 rounded-xl border px-3 py-2 backdrop-blur">
        <div className="flex items-baseline gap-2">
          <ListChecks className="text-kith mt-0.5 size-3.5 shrink-0 self-start" />
          <a
            href={pathForTask(task.id)}
            className="min-w-0 flex-1 truncate text-xs font-medium hover:underline"
            title={task.goal}
          >
            <span className="text-muted-foreground/70 me-1.5 font-mono">#{task.id}</span>
            {task.goal}
          </a>
          {items.length > 0 ? (
            <span className="text-muted-foreground/70 shrink-0 font-mono text-[11px] tabular-nums">
              {done}/{items.length}
            </span>
          ) : null}
        </div>

        {items.length > 0 ? (
          <ul className="mt-1.5 flex flex-col gap-0.5 ps-[22px]">
            {items.map((item) => {
              const ticked = Boolean(item.done);
              return (
                <li key={item.id} className="flex items-baseline gap-1.5 text-[11px]">
                  <span
                    className={cn(
                      "mt-[3px] flex size-3 shrink-0 items-center justify-center rounded-[3px] border",
                      ticked ? "bg-roam border-roam text-background" : "border-border/70",
                    )}
                  >
                    {ticked ? <Check className="size-2" /> : null}
                  </span>
                  <span
                    className={cn(
                      "min-w-0 flex-1",
                      ticked ? "text-muted-foreground/50 line-through" : "text-muted-foreground",
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
