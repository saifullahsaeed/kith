import { useState, type DragEvent } from "react";
import { ArrowUp, Check, FileText, ListChecks, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { FIELD } from "./types";
import type { Handlers } from "./types";

type Task = BrainSnapshot["tasks"][number];

/**
 * The columns, in the order work moves through them.
 *
 * One per status, because status is now the whole story. It was not while milestones existed: a
 * task could be `approved` and still unavailable because its milestone was waiting, so the old
 * page refused to be a board and ranked one list by "availability" instead. With the graph gone,
 * an approved task is simply the next thing, and a column says so.
 *
 * `working` takes no drops. It is not a quiet state — it drives the busy light, the "working now"
 * marker on the projects list and the conversation link on the task — so dragging a card there
 * would announce to the whole app that he had picked up something he never touched. He moves
 * cards into it; you move them everywhere else.
 */
const COLUMNS = [
  { status: "planning", title: "Planning", hint: "Needs your OK", accepts: true },
  { status: "approved", title: "Up next", hint: "Approved", accepts: true },
  { status: "working", title: "Working", hint: "He's on it", accepts: false },
  { status: "done", title: "Done", hint: "", accepts: true },
] as const;

const DROPPED = { status: "dropped", title: "Dropped", hint: "", accepts: true } as const;

/** How many finished cards show before "Show more". Done only grows, and a column of fifty
 *  finished tasks pushes nothing useful off the screen but itself. */
const DONE_SHOWN = 5;

const PRIORITY_RANK: Record<string, number> = { high: 0, normal: 1, low: 2 };

/** Priority, then id — stable on purpose. The server orders by most-recently-updated, and every
 *  checklist tick he makes changes that, so a board sorted the server's way reshuffles under the
 *  cursor while he works. A card moves when its status or priority does, and not otherwise. */
const byPriority = (a: Task, b: Task) =>
  (PRIORITY_RANK[a.priority] ?? 1) - (PRIORITY_RANK[b.priority] ?? 1) || a.id - b.id;

/** Newest finished first: the column is read as "what just got done". */
const byRecent = (a: Task, b: Task) => (b.updated_at ?? "").localeCompare(a.updated_at ?? "");

export function Board({
  tasks,
  projectId,
  create,
  update,
  onOpenTask,
}: {
  tasks: Task[];
  /** Where a task added from here lands. Null for the "no project" board. */
  projectId: number | null;
  onOpenTask: (id: number) => void;
} & Pick<Handlers, "create" | "update">) {
  const [adding, setAdding] = useState(false);
  const [goal, setGoal] = useState("");
  const [showDropped, setShowDropped] = useState(false);
  const [allDone, setAllDone] = useState(false);
  const [over, setOver] = useState<string | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);

  const dropped = tasks.filter((task) => task.status === "dropped");
  const columns = showDropped ? [...COLUMNS, DROPPED] : COLUMNS;

  const add = () => {
    const text = goal.trim();
    if (!text) return;
    create("task", projectId == null ? { goal: text } : { goal: text, project_id: projectId });
    setGoal("");
  };

  const move = (taskId: number, status: string) => {
    const task = tasks.find((one) => one.id === taskId);
    if (!task || task.status === status) return;
    update("task", taskId, { status });
  };

  const onDrop = (event: DragEvent, status: string) => {
    event.preventDefault();
    setOver(null);
    setDragging(null);
    const id = Number(event.dataTransfer.getData("text/plain"));
    if (Number.isFinite(id) && id > 0) move(id, status);
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-muted-foreground min-w-0 flex-1 text-xs">
          Drag a card to move it. Dropping a plan into <span className="text-foreground">Up next</span>{" "}
          approves it.
        </p>
        {dropped.length > 0 ? (
          <label className="text-muted-foreground flex cursor-pointer items-center gap-1.5 text-xs select-none">
            <input
              type="checkbox"
              checked={showDropped}
              onChange={(event) => setShowDropped(event.target.checked)}
              className="accent-kith"
            />
            Show dropped ({dropped.length})
          </label>
        ) : null}
        <Button size="xs" variant="outline" onClick={() => setAdding((open) => !open)}>
          <Plus className="size-3.5" />
          Add task
        </Button>
      </div>

      <div
        className={cn(
          "grid items-start gap-3",
          showDropped
            ? "grid-cols-1 sm:grid-cols-2 xl:grid-cols-5"
            : "grid-cols-1 sm:grid-cols-2 xl:grid-cols-4",
        )}
      >
        {columns.map((column) => {
          const here = tasks.filter((task) => task.status === column.status);
          const ordered = [...here].sort(column.status === "done" || column.status === "dropped" ? byRecent : byPriority);
          const cut = column.status === "done" && !allDone ? ordered.slice(0, DONE_SHOWN) : ordered;
          const target = over === column.status && column.accepts;
          return (
            <div
              key={column.status}
              onDragOver={(event) => {
                if (!column.accepts || dragging == null) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                if (over !== column.status) setOver(column.status);
              }}
              onDragLeave={(event) => {
                // Leaving for one of this column's own cards is not leaving the column.
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(null);
              }}
              onDrop={(event) => (column.accepts ? onDrop(event, column.status) : undefined)}
              className={cn(
                "bg-sidebar/40 flex min-h-32 flex-col gap-2 rounded-xl border p-2 transition-colors",
                column.status === "planning" && here.length > 0
                  ? "border-kith/25"
                  : "border-border/60",
                target && "border-kith/60 bg-kith-soft",
                dragging != null && !column.accepts && "opacity-60",
              )}
            >
              <div className="flex items-center gap-2 px-1.5 pt-1 pb-0.5">
                <span className="text-[13px] font-semibold">{column.title}</span>
                <span className="text-muted-foreground/70 text-xs tabular-nums">{here.length}</span>
                <span className="flex-1" />
                {column.status === "working" && here.length > 0 ? (
                  <span className="text-kith flex items-center gap-1.5 text-[11px]">
                    <span className="bg-kith size-1.5 animate-pulse rounded-full" />
                    {column.hint}
                  </span>
                ) : column.status === "planning" && here.length > 0 ? (
                  <span className="text-kith text-[11px] font-semibold">{column.hint}</span>
                ) : column.status === "done" && here.length > 0 ? (
                  <Check className="text-roam size-3.5" />
                ) : column.hint ? (
                  <span className="text-muted-foreground/60 text-[11px]">{column.hint}</span>
                ) : null}
              </div>

              {column.status === "planning" && adding ? (
                <div className="border-border/70 bg-card flex items-center gap-1.5 rounded-lg border p-1.5 pl-2.5 focus-within:border-ring/60">
                  <input
                    autoFocus
                    value={goal}
                    onChange={(event) => setGoal(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") add();
                      if (event.key === "Escape") setAdding(false);
                    }}
                    placeholder="What should come out of it?"
                    aria-label="New task"
                    className={`${FIELD} flex-1 text-[13px]`}
                  />
                  <Button size="xs" onClick={add}>
                    Add
                  </Button>
                </div>
              ) : null}

              {cut.map((task) => (
                <Card
                  key={task.id}
                  task={task}
                  dragging={dragging === task.id}
                  onOpen={() => onOpenTask(task.id)}
                  onDragStart={(event) => {
                    event.dataTransfer.setData("text/plain", String(task.id));
                    event.dataTransfer.effectAllowed = "move";
                    setDragging(task.id);
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setOver(null);
                  }}
                />
              ))}

              {here.length === 0 ? (
                <p className="text-muted-foreground/60 border-border/60 rounded-lg border border-dashed px-3 py-5 text-center text-xs leading-relaxed">
                  {column.status === "planning"
                    ? "No plans waiting on you."
                    : column.status === "approved"
                      ? "Nothing approved and waiting. Drop a plan here to approve it."
                      : column.status === "working"
                        ? "He isn't on anything here right now."
                        : column.status === "done"
                          ? "Nothing finished yet."
                          : "Nothing dropped."}
                </p>
              ) : null}

              {column.status === "done" && ordered.length > DONE_SHOWN ? (
                <button
                  type="button"
                  onClick={() => setAllDone((all) => !all)}
                  className="text-muted-foreground hover:text-foreground rounded-md py-1.5 text-xs transition-colors"
                >
                  {allDone ? "Show fewer" : `Show ${ordered.length - DONE_SHOWN} more`}
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/**
 * One task on the board.
 *
 * A button, so it opens with Enter as well as a click; draggable, so it moves. The three things it
 * carries beyond the title are the three that decide what to do about it: is it high priority,
 * how far through its checklist is it, and has it produced anything yet.
 */
function Card({
  task,
  dragging,
  onOpen,
  onDragStart,
  onDragEnd,
}: {
  task: Task;
  dragging: boolean;
  onOpen: () => void;
  onDragStart: (event: DragEvent) => void;
  onDragEnd: () => void;
}) {
  const total = task.checklist_total ?? 0;
  const ticked = task.checklist_done ?? 0;
  const outputs = task.outputs ?? 0;
  const settled = task.status === "done" || task.status === "dropped";

  if (settled) {
    return (
      <button
        type="button"
        draggable
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onClick={onOpen}
        title="Open this task"
        className={cn(
          "border-border/50 bg-card/40 text-muted-foreground hover:text-foreground hover:border-border flex w-full cursor-grab items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors active:cursor-grabbing",
          dragging && "opacity-40",
        )}
      >
        {task.status === "done" ? (
          <Check className="text-roam mt-0.5 size-3.5 shrink-0" />
        ) : (
          <span className="bg-muted-foreground/40 mt-1.5 size-1.5 shrink-0 rounded-full" />
        )}
        <span className={cn("line-clamp-2 text-xs leading-snug", task.status === "dropped" && "line-through")}>
          {task.goal}
        </span>
      </button>
    );
  }

  return (
    <button
      type="button"
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onOpen}
      title="Open this task"
      className={cn(
        "bg-card hover:border-border group flex w-full cursor-grab flex-col gap-2 rounded-lg border p-3 text-left shadow-sm transition-all hover:shadow-md active:cursor-grabbing",
        task.status === "working" ? "border-kith/40" : "border-border/70",
        dragging && "opacity-40",
      )}
    >
      <span className="text-muted-foreground/70 flex w-full items-center gap-1.5 text-[11px]">
        {task.priority === "high" ? (
          <span className="text-kith flex items-center gap-0.5 font-semibold">
            <ArrowUp className="size-3" strokeWidth={2.6} />
            High
          </span>
        ) : task.priority === "low" ? (
          <span>Low</span>
        ) : null}
        {task.status === "working" ? (
          <span className="text-kith flex items-center gap-1">
            <span className="bg-kith size-1.5 animate-pulse rounded-full" />
            working
          </span>
        ) : null}
        <span className="flex-1" />
        <span className="tabular-nums">#{task.id}</span>
      </span>

      <span className="line-clamp-3 text-[13px] leading-snug">{task.goal}</span>

      {task.status === "working" && total > 0 ? (
        <span className="bg-muted block h-1 w-full overflow-hidden rounded-full">
          <span
            className="bg-kith block h-1 rounded-full transition-all duration-500"
            style={{ width: `${Math.round((ticked / total) * 100)}%` }}
          />
        </span>
      ) : null}

      <span className="text-muted-foreground flex w-full items-center gap-3 text-[11px]">
        {total > 0 ? (
          <span className="flex items-center gap-1 tabular-nums" title="Checklist">
            <ListChecks className="size-3.5" />
            {ticked}/{total}
          </span>
        ) : null}
        {outputs > 0 ? (
          <span className="flex items-center gap-1 tabular-nums" title="Outputs">
            <FileText className="size-3.5" />
            {outputs}
          </span>
        ) : null}
        <span className="flex-1" />
        {task.status === "planning" ? (
          <span className="text-kith font-semibold">Review plan →</span>
        ) : null}
      </span>
    </button>
  );
}
