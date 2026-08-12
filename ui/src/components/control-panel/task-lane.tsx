import { useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { clamp } from "./format";
import { FIELD, TASK_STATUSES } from "./types";
import type { Handlers } from "./types";

export function TaskLane({
  tasks,
  milestones,
  blocked,
  projectId,
  create,
  update,
  remove,
  onOpenTask,
}: {
  tasks: BrainSnapshot["tasks"];
  milestones: BrainSnapshot["projects"][number]["milestones"];
  blocked: Set<number>;
  /** Where a task added from here lands. Undefined hides the add row. */
  projectId?: number | null;
  onOpenTask: (id: number) => void;
} & Pick<Handlers, "create" | "update" | "remove">) {
  const [goal, setGoal] = useState("");
  const add = () => {
    if (!goal.trim()) return;
    const data: Record<string, unknown> = { goal };
    if (projectId != null) data.project_id = projectId;
    create("task", data);
    setGoal("");
  };
  const titleOf = (id: number | null | undefined) =>
    milestones.find((m) => m.id === id)?.title ?? null;

  const rank = (task: BrainSnapshot["tasks"][number]) => {
    if (task.status === "working") return 0;
    // A plan waiting on a decision is the only thing here that is the person's turn, so it sits
    // above approved work that has simply not started. Everything that used to rank between the
    // two — `waiting`, `review` — is a question in chat now rather than a row in this list.
    if (task.status === "planning") return 1;
    if (task.milestone_id && blocked.has(task.milestone_id)) return 3;
    return 2;
  };
  /*
    Stable, and that is the whole point of the second key.

    The server returns tasks by priority then **most-recently-updated** (`_newest_first`), which is
    right for picking what to work on next — it is how he stays on the task it just touched —
    and wrong for a board you are trying to read. Every comment he posts and every checklist item
    he checks off changes `updated_at`, so while he works, tasks leap up the list on each poll and the
    board reshuffles under the cursor every few seconds.

    Ranking alone did not fix it: `sort` is stable, so equal ranks kept whatever order the server
    sent — which is the order that keeps changing. Falling back to the id pins them: a task's
    position now only moves when its status does.
  */
  const ordered = [...tasks].sort((a, b) => rank(a) - rank(b) || a.id - b.id);

  const addRow =
    projectId === undefined ? null : (
      <div className="mt-2 flex items-center gap-2 rounded-xl border border-border/60 bg-card/40 p-1.5 pl-3 focus-within:border-ring/60">
        <input
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && add()}
          placeholder="Add a task…"
          className={`${FIELD} flex-1 text-xs`}
        />
        <Button size="xs" variant="outline" onClick={add}>
          <Plus className="size-3.5" />
          Add
        </Button>
      </div>
    );

  if (ordered.length === 0) {
    return (
      <>
        <p className="text-muted-foreground rounded-xl border border-dashed p-4 text-center text-sm">
          Nothing open here.
        </p>
        {addRow}
      </>
    );
  }

  return (
    <>
      <ul className="divide-y rounded-xl border">
        {ordered.map((task) => {
          const held = Boolean(task.milestone_id && blocked.has(task.milestone_id));
          return (
            <li key={task.id} className="group flex items-center gap-3 px-3 py-2.5">
              <button
                onClick={() =>
                  update("task", task.id, { status: task.status === "done" ? "working" : "done" })
                }
                aria-label={task.status === "done" ? "Reopen" : "Mark done"}
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center rounded-full border transition-colors",
                  task.status === "done"
                    ? "border-kith bg-kith text-primary-foreground"
                    : "border-muted-foreground/40 hover:border-kith",
                )}
              >
                {task.status === "done" ? <Check className="size-3" /> : null}
              </button>

              <button
                onClick={() => onOpenTask(task.id)}
                className="min-w-0 flex-1 text-left"
                title="Open this task"
              >
                <span className="block truncate text-sm">{task.goal}</span>
                <span className="text-muted-foreground/70 flex items-center gap-1.5 text-[10px]">
                  {task.status === "working" ? (
                    <span className="text-kith">working on it</span>
                  ) : null}
                  {task.status === "planning" ? (
                    <span className="text-violet-500/90">plan ready for your look</span>
                  ) : null}
                  {held ? <span>held until “{titleOf(task.milestone_id)}” is ready</span> : null}
                  {!held && task.milestone_id ? <span>{titleOf(task.milestone_id)}</span> : null}
                </span>
              </button>

              {task.priority && task.priority !== "normal" ? (
                <span className="text-muted-foreground/60 shrink-0 font-mono text-[10px]">
                  {task.priority}
                </span>
              ) : null}
              <Dropdown
                value={task.status}
                onChange={(next) => update("task", task.id, { status: next })}
                options={TASK_STATUSES}
                className="w-24 shrink-0"
                ariaLabel="Task status"
              />
              <button
                onClick={() => remove("task", task.id, task.goal)}
                className="text-muted-foreground shrink-0 opacity-0 transition group-hover:opacity-100 hover:text-destructive"
                aria-label="Delete task"
              >
                <X className="size-3.5" />
              </button>
            </li>
          );
        })}
      </ul>
      {addRow}
    </>
  );
}

export function ProgressRing({ pct, size = 52 }: { pct: number; size?: number }) {
  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          className="fill-none stroke-muted"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          strokeLinecap="round"
          className="fill-none stroke-kith transition-all duration-500"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - clamp(pct) / 100)}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[11px] font-semibold tabular-nums">
        {pct}%
      </span>
    </div>
  );
}
