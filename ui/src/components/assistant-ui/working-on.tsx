import { useQuery } from "@tanstack/react-query";
import { Check, ListChecks } from "lucide-react";

import { useState } from "react";

import { keys } from "@/lib/query-keys";
import { pathForTask } from "@/lib/router";
import { cn } from "@/lib/utils";

/** How many steps show without asking. Four, the same as the task card in the thread — folding
 *  two costs a click and a row of chrome to hide one line, and above four the arithmetic flips. */
const INLINE_STEPS = 4;

interface ChecklistItem {
  id: number;
  text: string;
  done: boolean | number;
}

export interface WorkingTask {
  id: number;
  goal: string;
  status: string;
  checklist?: ChecklistItem[];
}

/** The task this conversation is working through, or null. Shared with the companion's Canvas
 *  tab, which opens that task's board, so both read the one cache entry. */
export async function fetchWorkingOn(conversationId: string): Promise<WorkingTask | null> {
  const response = await fetch(`/api/chat/${conversationId}/working-on`);
  if (!response.ok) return null;
  const body = (await response.json()) as WorkingTask | null;
  return body && body.id ? body : null;
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
    queryFn: () => fetchWorkingOn(conversationId),
    // Nothing to ask about until there is a conversation.
    enabled: Boolean(conversationId),
  });

  /* Whether the list is showing, or `null` for "nobody has said" — which is most of the time, and
   * then the length decides. Above the early return because it is a hook and hooks cannot be
   * conditional: the first version of this sat below `if (!task)`, which crashed the panel every
   * time no task was running. The test for a task with no checklist is what found it.
   *
   * Tri-state rather than a boolean seeded from the length, because the seed would be read once
   * and this component outlives the task it is showing — ticking the fourth item off a five-step
   * list would otherwise fold it under you. */
  const [asked, setAsked] = useState<boolean | null>(null);

  if (!task) return null;

  const items = task.checklist ?? [];
  const done = items.filter((one) => Boolean(one.done)).length;

  const pct = items.length ? Math.round((done / items.length) * 100) : 0;
  // Folded by default once there are more than a few, because this sits in a fixed-height column
  // with four sections under it. `plan_work` writes several tasks with their whole
  // checklists in one call, so nine steps here is now ordinary — and nine steps at this spacing
  // pushed the errands, the context and the background work off the bottom of a panel that did
  // not scroll past them. The count and the bar were always the part worth seeing anyway.
  const open = asked ?? (items.length > 0 && items.length <= INLINE_STEPS);
  const next = items.find((one) => !one.done);

  // "working" is the only status the endpoint answers with — see above — but a server that says
  // something else should be read back as it said it, not relabelled.
  const status = task.status && task.status !== "working" ? task.status : "Working";

  return (
    // A card in the Activity column: the task's number and state, its goal as a sentence, and a
    // bar for how far through its checklist he is. The count is the control that opens the list.
    <section
      aria-label={`Task ${task.id}`}
      className="flex shrink-0 flex-col gap-2 rounded-[14px] border border-border bg-card px-3.5 py-3"
    >
      <div className="flex min-w-0 items-center gap-2">
        <ListChecks className="size-3.5 shrink-0 text-blue-600 dark:text-blue-400" aria-hidden />
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground/80">#{task.id}</span>
        <span className="shrink-0 rounded-full bg-kith-soft px-[7px] py-0.5 text-[11px] font-semibold text-kith capitalize">
          {status}
        </span>
        <span className="flex-1" />
        {items.length > 0 ? (
          <button
            type="button"
            onClick={() => setAsked(!open)}
            aria-expanded={open}
            aria-label={open ? "Hide the checklist" : "Show the checklist"}
            disabled={items.length <= INLINE_STEPS}
            className={cn(
              "-me-1 shrink-0 rounded px-1 py-0.5 text-[11px] tabular-nums text-muted-foreground",
              items.length > INLINE_STEPS && "hover:bg-accent cursor-pointer transition-colors",
            )}
          >
            {/* The count is the control, as the design draws it — no chevron. What says it can be
                pressed is the hover, the pointer, and the "next ·" line under a folded list,
                which opens it too. */}
            {done} / {items.length}
          </button>
        ) : null}
      </div>

      <a
        href={pathForTask(task.id)}
        className="text-[13px] leading-[1.45] text-foreground/85 transition-colors hover:text-foreground"
        title="Open the task"
      >
        {task.goal}
      </a>

      {items.length > 0 ? (
        <span aria-hidden className="block h-1 overflow-hidden rounded-full bg-foreground/10">
          <span
            className="bg-roam block h-full rounded-full transition-[width] duration-500"
            style={{ width: `${pct}%` }}
          />
        </span>
      ) : null}

      {/* What is next, when the list is folded. One line, and the only line that answers the
          question you had when you glanced at this. */}
      {!open && next ? (
        <button
          type="button"
          onClick={() => setAsked(true)}
          className="block w-full cursor-pointer truncate text-start text-[12px] text-muted-foreground transition-colors hover:text-foreground"
        >
          next · {next.text}
        </button>
      ) : null}

      {open ? (
        // Capped even when open. A task with thirty steps would otherwise do to the column
        // exactly what this is fixing, one click later.
        <ul className="flex max-h-56 flex-col gap-1.5 overflow-y-auto">
          {items.map((item) => {
            const ticked = Boolean(item.done);
            return (
              <li key={item.id} className="flex items-start gap-2 text-[12px] leading-[1.45]">
                <span
                  className={cn(
                    "mt-[2px] flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border transition-colors",
                    ticked ? "border-roam/40 bg-roam/15 text-roam" : "border-foreground/20",
                  )}
                >
                  {ticked ? <Check className="size-2.5" strokeWidth={3} /> : null}
                </span>
                <span
                  className={cn(
                    "min-w-0 flex-1",
                    ticked ? "text-muted-foreground/70 line-through" : "text-foreground/85",
                  )}
                >
                  {item.text}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
