import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import { ArrowDownToLine, Check, ListChecks, Plus } from "lucide-react";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { BUTTON, LiveDot } from "./chrome";
import { LANE_TITLE, codeRuns, moveTarget, plainText, shortDay, type MoveTarget } from "./project-page-model";
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
 * cards into it; you move them everywhere else. The arrow keys follow the same rule
 * (`moveTarget`), so there is one answer to "can I put this here", not two.
 */
const COLUMNS = [
  { status: "planning", title: "Planning", accepts: true },
  { status: "approved", title: "Up next", accepts: true },
  { status: "working", title: "Working", accepts: false },
  { status: "done", title: "Done", accepts: true },
] as const;

const DROPPED = { status: "dropped", title: "Dropped", accepts: true } as const;

/** How many finished cards show before "Show more". Done only grows, and a column of fifty
 *  finished tasks pushes nothing useful off the screen but itself. Six is what the column holds
 *  at the window's real height with the rows the design gives it. */
const DONE_SHOWN = 6;

/** How long a refusal ("only he moves a card into Working") stays in place of the hint. */
const NOTE_MS = 2600;

/** How long a move waits for the snapshot to agree before the card goes back to what the
 *  server says. The refetch lands well inside this; past it, the server's answer is the truth. */
const SETTLE_MS = 3000;

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
  /** The card the arrow keys act on. Set by clicking it or by focusing it, so Tab reaches the
   *  same state a click does. */
  const [selected, setSelected] = useState<number | null>(null);
  /**
   * Moves the server has been asked for and has not yet answered with a new snapshot.
   *
   * Without these a keyboard move is a card that sits still for the length of a round trip and
   * then jumps — and the focus, which lived on the card, is gone by the time it lands, so the
   * second ← in a row goes nowhere. `at` stands in for `updated_at` so a card moved into Done
   * sorts to the top of it, which is where the real one will be.
   */
  const [moved, setMoved] = useState<Record<number, { status: string; at: string }>>({});
  const [note, setNote] = useState("");
  const noteTimer = useRef<number | undefined>(undefined);
  const buttons = useRef(new Map<number, HTMLButtonElement>());
  const refocus = useRef<number | null>(null);

  const view = tasks.map((task) => {
    const pending = moved[task.id];
    return pending && pending.status !== task.status ? { ...task, status: pending.status, updated_at: pending.at } : task;
  });

  // The snapshot has caught up with a move: the override has nothing left to say.
  const signature = tasks.map((task) => `${task.id}:${task.status}`).join(",");
  useEffect(() => {
    setMoved((was) => {
      const left = Object.entries(was).filter(([id, one]) => tasks.find((task) => task.id === Number(id))?.status !== one.status);
      return left.length === Object.keys(was).length ? was : Object.fromEntries(left);
    });
    // `tasks` is read through the signature on purpose: the array is new on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  // A moved card is a new element in another column; put the keyboard back on it.
  useLayoutEffect(() => {
    if (refocus.current == null) return;
    const el = buttons.current.get(refocus.current);
    if (el) {
      refocus.current = null;
      if (document.activeElement !== el) el.focus();
    }
  });

  useEffect(() => () => window.clearTimeout(noteTimer.current), []);

  const flash = (text: string) => {
    setNote(text);
    window.clearTimeout(noteTimer.current);
    noteTimer.current = window.setTimeout(() => setNote(""), NOTE_MS);
  };

  const dropped = view.filter((task) => task.status === "dropped");
  const columns = showDropped ? [...COLUMNS, DROPPED] : COLUMNS;

  const add = () => {
    const text = goal.trim();
    if (!text) return;
    create("task", projectId == null ? { goal: text } : { goal: text, project_id: projectId });
    setGoal("");
  };

  const forget = (taskId: number) =>
    setMoved((was) => {
      if (!(taskId in was)) return was;
      const next = { ...was };
      delete next[taskId];
      return next;
    });

  /** The one way a card changes column — the drag, the arrow keys and the footer buttons all
   *  come through here, so they cannot disagree about what a move is. */
  const move = (taskId: number, status: string, keyed = false) => {
    const task = view.find((one) => one.id === taskId);
    if (!task || task.status === status) return;
    if (keyed) refocus.current = taskId;
    setMoved((was) => ({ ...was, [taskId]: { status, at: new Date().toISOString() } }));
    Promise.resolve(update("task", taskId, { status }))
      .then(() => window.setTimeout(() => forget(taskId), SETTLE_MS))
      .catch(() => {
        // Back where it was — and, for a keyboard move, with the keyboard still on it.
        if (keyed) refocus.current = taskId;
        forget(taskId);
        flash("That move didn't go through — the card is back where it was");
      });
  };

  const step = (task: Task, direction: -1 | 1) => {
    const target = moveTarget(task.status, direction, showDropped);
    if (!target) return;
    if ("refused" in target) {
      flash(target.refused);
      return;
    }
    move(task.id, target.status, true);
  };

  const onDrop = (event: DragEvent, status: string) => {
    event.preventDefault();
    setOver(null);
    setDragging(null);
    const id = Number(event.dataTransfer.getData("text/plain"));
    if (Number.isFinite(id) && id > 0) move(id, status);
  };

  /** Everything a card needs from the board, in one place so the two card shapes share it. */
  const cardProps = (task: Task) => ({
    task,
    selected: selected === task.id,
    dragging: dragging === task.id,
    register: (el: HTMLButtonElement | null) => {
      if (el) buttons.current.set(task.id, el);
      else buttons.current.delete(task.id);
    },
    onSelect: () => setSelected(task.id),
    onDeselect: () => setSelected(null),
    onOpen: () => onOpenTask(task.id),
    onStep: (direction: -1 | 1) => step(task, direction),
    left: moveTarget(task.status, -1, showDropped),
    right: moveTarget(task.status, 1, showDropped),
    onDragStart: (event: DragEvent) => {
      event.dataTransfer.setData("text/plain", String(task.id));
      event.dataTransfer.effectAllowed = "move";
      setDragging(task.id);
    },
    onDragEnd: () => {
      setDragging(null);
      setOver(null);
    },
  });

  return (
    <section aria-label="Board" className="flex min-h-0 flex-1 flex-col">
      <div className="mt-3.5 flex h-[30px] shrink-0 items-center gap-3.5">
        <button
          type="button"
          onClick={() => setAdding((open) => !open)}
          aria-expanded={adding}
          className="border-input bg-muted/60 text-foreground hover:bg-muted flex h-[30px] items-center gap-1.5 rounded-lg border ps-[9px] pe-[11px] text-[12.5px] transition-colors"
        >
          <Plus className="size-3.5" />
          Add task
        </button>
        <button
          type="button"
          role="switch"
          aria-checked={showDropped}
          onClick={() => setShowDropped((was) => !was)}
          className="text-muted-foreground hover:text-foreground flex h-[30px] items-center gap-2 px-1 text-[12.5px] transition-colors"
        >
          <span
            aria-hidden
            className={cn(
              "flex h-4 w-[26px] items-center rounded-full border px-0.5 transition-colors",
              showDropped
                ? "border-kith/40 bg-kith/20"
                : "bg-muted border-[color-mix(in_oklab,var(--foreground)_14%,transparent)]",
            )}
          >
            <span
              className={cn(
                "size-2.5 rounded-full transition-transform",
                showDropped ? "bg-kith translate-x-[10px]" : "bg-muted-foreground/80",
              )}
            />
          </span>
          Show dropped
          <span className="text-muted-foreground/50 tabular-nums">{dropped.length}</span>
        </button>
        <span className="flex-1" />
        {/* The hint and what the keys refused share a place, so a refusal is read where the
            instruction was. */}
        <span role="status" aria-live="polite" className="flex items-center gap-1.5 text-xs">
          {note ? (
            <span className="text-kith">{note}</span>
          ) : (
            <span className="text-muted-foreground/80 flex items-center gap-1.5">
              Drag a card, or select it and press <Key>←</Key> <Key>→</Key> to move it
            </span>
          )}
        </span>
      </div>

      <div
        className={cn(
          "mt-3.5 grid min-h-[320px] flex-1 gap-4",
          showDropped ? "grid-cols-5" : "grid-cols-4",
        )}
      >
        {columns.map((column) => {
          const here = view.filter((task) => task.status === column.status);
          const ordered = [...here].sort(
            column.status === "done" || column.status === "dropped" ? byRecent : byPriority,
          );
          const cut = column.status === "done" && !allDone ? ordered.slice(0, DONE_SHOWN) : ordered;
          const target = over === column.status && column.accepts;
          const attention = column.status === "planning" && here.length > 0;
          const compact = column.status === "done" || column.status === "dropped";
          return (
            <section
              key={column.status}
              aria-label={column.title}
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
                "flex min-h-0 flex-col overflow-hidden rounded-[14px] border transition-colors",
                attention ? "border-kith/25 bg-kith/[0.035]" : "border-border bg-sidebar",
                target && "border-kith/60 bg-kith/[0.07]",
                dragging != null && !column.accepts && "opacity-60",
              )}
            >
              <div className="flex h-11 shrink-0 items-center gap-2 px-3.5">
                <span className="text-[13px] font-semibold">{column.title}</span>
                <span className="text-muted-foreground/80 text-xs tabular-nums">{here.length}</span>
                <span className="flex-1" />
                <ColumnHint status={column.status} count={here.length} />
              </div>

              <div
                // Clicking the column itself, not a card, lets go of the selection.
                onClick={(event) => {
                  if (event.target === event.currentTarget) setSelected(null);
                }}
                className={cn(
                  "flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2",
                  compact ? "gap-0.5" : "gap-2",
                )}
              >
                {column.status === "planning" && adding ? (
                  <div className="border-input bg-card focus-within:border-ring/60 flex shrink-0 items-center gap-1.5 rounded-[10px] border p-1.5 ps-2.5">
                    <input
                      autoFocus
                      value={goal}
                      onChange={(event) => setGoal(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") add();
                        if (event.key === "Escape") {
                          // The field's Escape, not the panel's.
                          event.preventDefault();
                          setAdding(false);
                        }
                      }}
                      placeholder="What should come out of it?"
                      aria-label="New task"
                      className={`${FIELD} flex-1 text-[13px]`}
                    />
                    <button type="button" onClick={add} className={cn(BUTTON.primary, BUTTON.small)}>
                      Add
                    </button>
                  </div>
                ) : null}

                {cut.map((task) =>
                  compact ? <Row key={task.id} {...cardProps(task)} /> : <Card key={task.id} {...cardProps(task)} />,
                )}

                {here.length === 0 ? <ColumnEmpty status={column.status} /> : null}

                {column.status === "done" && ordered.length > DONE_SHOWN ? (
                  <button
                    type="button"
                    onClick={() => setAllDone((all) => !all)}
                    className="text-muted-foreground/80 hover:text-foreground hover:bg-muted/50 mt-1 h-[34px] shrink-0 rounded-lg text-xs transition-colors"
                  >
                    {allDone ? "Show fewer" : `Show ${ordered.length - DONE_SHOWN} more`}
                  </button>
                ) : null}
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}

/** What the column's head says on its right. Amber only when something is actually waiting. */
function ColumnHint({ status, count }: { status: string; count: number }) {
  if (status === "planning") {
    return count > 0 ? (
      <span className="text-kith flex items-center gap-1.5 text-[11.5px] font-semibold">
        <span className="bg-kith size-1.5 rounded-full" />
        Needs your OK
      </span>
    ) : (
      <span className="text-muted-foreground/80 text-[11.5px]">Needs your OK</span>
    );
  }
  if (status === "working") {
    return count > 0 ? (
      <span className="text-roam flex items-center gap-1.5 text-[11.5px]">
        <LiveDot />
        He&apos;s on it
      </span>
    ) : (
      <span className="text-muted-foreground/80 text-[11.5px]">He&apos;s on it</span>
    );
  }
  // Blue is "approved, up next" everywhere — the same blue as that segment of the progress bar.
  if (status === "approved" && count > 0) {
    return <span className="text-[11.5px] text-sky-600 dark:text-[#6FA8F0]">Approved</span>;
  }
  const quiet: Record<string, string> = { approved: "Approved", done: "Newest first", dropped: "Given up" };
  return <span className="text-muted-foreground/80 text-[11.5px]">{quiet[status]}</span>;
}

function ColumnEmpty({ status }: { status: string }) {
  if (status === "approved") {
    return (
      <div className="flex h-[176px] shrink-0 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-6 text-center">
        <ArrowDownToLine className="text-muted-foreground/50 size-[18px]" />
        <span className="text-foreground/80 text-[13px]">Drop a plan here to approve it</span>
        <span className="text-muted-foreground/80 text-xs leading-normal">
          He starts on what&apos;s here next. Nothing is approved and waiting right now.
        </span>
      </div>
    );
  }
  const copy: Record<string, string> = {
    planning: "No plans waiting on you.",
    working: "He isn't on anything here right now.",
    done: "Nothing finished yet.",
    dropped: "Nothing dropped.",
  };
  return (
    <p className="text-muted-foreground/80 shrink-0 rounded-xl border border-dashed border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-4 py-6 text-center text-xs leading-normal">
      {copy[status]}
    </p>
  );
}

type CardProps = {
  task: Task;
  selected: boolean;
  dragging: boolean;
  register: (el: HTMLButtonElement | null) => void;
  onSelect: () => void;
  onDeselect: () => void;
  onOpen: () => void;
  onStep: (direction: -1 | 1) => void;
  left: MoveTarget | null;
  right: MoveTarget | null;
  onDragStart: (event: DragEvent) => void;
  onDragEnd: () => void;
};

/**
 * Click selects; a second click (so a double-click too) or Enter opens.
 *
 * A card used to open on the first click, which left nothing for the arrow keys to act on. What
 * makes the second click work is remembering, at pointer-down, whether the card was *already*
 * selected: focus lands between pointer-down and click and selects it, so by the time `click`
 * fires every card looks selected, and the first click would open everything.
 */
function useSelectable({ selected, onSelect, onDeselect, onOpen, onStep }: CardProps) {
  const wasSelected = useRef(false);
  return {
    onPointerDown: () => {
      wasSelected.current = selected;
    },
    onFocus: onSelect,
    onClick: (event: MouseEvent<HTMLButtonElement>) => {
      // `detail` is 0 for a click the keyboard made (Space): that is a request to open.
      if (event.detail === 0 || wasSelected.current) {
        onOpen();
        return;
      }
      onSelect();
      event.currentTarget.focus();
    },
    onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        onStep(event.key === "ArrowLeft" ? -1 : 1);
      } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        // Up and down walk the column, so a whole column can be worked from the keyboard.
        event.preventDefault();
        const lane = event.currentTarget.closest("section");
        const cards = [...(lane?.querySelectorAll<HTMLButtonElement>("[data-card]") ?? [])];
        const at = cards.indexOf(event.currentTarget);
        cards[at + (event.key === "ArrowUp" ? -1 : 1)]?.focus();
      } else if (event.key === "Enter") {
        event.preventDefault();
        onOpen();
      } else if (event.key === "Escape" && selected) {
        // Let go of the card before letting go of the panel: the first Escape is the nearer one.
        event.preventDefault();
        onDeselect();
        event.currentTarget.blur();
      }
    },
  };
}

/** Priority as three bars: high lights all three, normal two, low one. */
function PriorityGlyph({ priority }: { priority: string }) {
  const lit = priority === "high" ? 3 : priority === "low" ? 1 : 2;
  const label = priority === "high" ? "High priority" : priority === "low" ? "Low priority" : "Normal priority";
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" role="img" aria-label={label} className="shrink-0">
      {[
        { x: 0.5, y: 8, h: 4 },
        { x: 4.5, y: 5, h: 7 },
        { x: 8.5, y: 2, h: 10 },
      ].map((bar, at) => (
        <rect
          key={bar.x}
          x={bar.x}
          y={bar.y}
          width="3"
          height={bar.h}
          rx="0.8"
          className={at < lit ? "fill-foreground/80" : "fill-foreground/15"}
        />
      ))}
    </svg>
  );
}

/**
 * One task that is still in play.
 *
 * A focusable button carries the card's body, and the card around it holds the actions that
 * cannot live inside a button — Review plan on a plan, and the ← → footer on the selected card.
 * The three things it shows beyond the goal are the three that decide what to do about it: its
 * priority, its id, and how far through its checklist he is.
 */
function Card(props: CardProps) {
  const { task, selected, dragging, register, onOpen, onStep, left, right, onDragStart, onDragEnd } = props;
  const handlers = useSelectable(props);
  const total = task.checklist_total ?? 0;
  const ticked = task.checklist_done ?? 0;
  const planning = task.status === "planning";
  const description = planning && task.description ? codeRuns(plainText(task.description)) : [];
  const finished = total > 0 && ticked >= total;

  return (
    <article
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={cn(
        "bg-card flex shrink-0 cursor-grab flex-col rounded-[10px] border transition-colors active:cursor-grabbing",
        selected ? "border-kith" : planning ? "border-kith/25 hover:border-kith/45" : "border-border hover:border-input",
        dragging && "opacity-40",
      )}
    >
      <button
        ref={register}
        type="button"
        data-card
        aria-current={selected || undefined}
        title={selected ? "Press Enter, or click again, to open it" : "Select it — then ← → to move it"}
        {...handlers}
        className="flex w-full flex-col items-start gap-2 rounded-[10px] px-3 pt-[11px] pb-3 text-left outline-none"
      >
        <span className="text-muted-foreground/80 flex h-4 w-full items-center gap-[7px] text-[11px]">
          <PriorityGlyph priority={task.priority} />
          <span className="font-mono tabular-nums">#{task.id}</span>
          <span className="flex-1" />
          {planning ? <span>Plan filed {shortDay(task.created_at)}</span> : null}
        </span>
        <span className="text-foreground line-clamp-3 text-[13px] leading-[1.4]">{task.goal}</span>
        {description.length ? (
          <span className="text-muted-foreground/80 line-clamp-3 text-xs leading-normal">
            {description.map((run, at) =>
              run.code ? (
                <span key={at} className="text-muted-foreground font-mono text-[11px]">
                  {run.text}
                </span>
              ) : (
                <span key={at}>{run.text}</span>
              ),
            )}
          </span>
        ) : null}
        {total > 0 ? (
          <span className="flex w-full items-center gap-2.5">
            <span className="bg-muted flex h-[3px] flex-1 overflow-hidden rounded-full">
              <span
                className="bg-roam block h-[3px] rounded-full transition-all duration-500"
                style={{ width: `${Math.round((ticked / total) * 100)}%` }}
              />
            </span>
            <span className="text-muted-foreground flex items-center gap-1 text-[11.5px] leading-[13px] tabular-nums" title="Checklist">
              <ListChecks className="text-muted-foreground/80 size-3" />
              {ticked}/{total}
            </span>
          </span>
        ) : null}
      </button>

      {planning ? (
        <span className="-mt-0.5 flex items-center gap-2.5 px-3 pb-3">
          <button type="button" onClick={onOpen} className={cn(BUTTON.primary, BUTTON.small)}>
            Review plan
          </button>
          <span className="text-muted-foreground/80 text-[11.5px]">Approving moves it to Up next</span>
        </span>
      ) : null}

      {selected ? (
        <span className="border-border/80 text-muted-foreground/80 mx-3 mb-3 -mt-1 flex items-center gap-1.5 border-t pt-2 text-[11.5px]">
          <StepButton target={left} direction={-1} onStep={onStep} />
          <span className="flex-1" />
          <StepButton target={right} direction={1} onStep={onStep} suggest={task.status === "working" && finished} />
        </span>
      ) : null}
    </article>
  );
}

/** One side of the selected card's footer: where that arrow goes, as a button that goes there. */
function StepButton({
  target,
  direction,
  onStep,
  suggest,
}: {
  target: MoveTarget | null;
  direction: -1 | 1;
  onStep: (direction: -1 | 1) => void;
  /** The move his checklist says is next — all ticked, so Done. Amber, the one suggestion. */
  suggest?: boolean;
}) {
  if (!target) return null;
  if ("refused" in target) {
    return <span className="text-muted-foreground/50">{direction < 0 ? "← " : ""}Working is his{direction > 0 ? " →" : ""}</span>;
  }
  const title = LANE_TITLE[target.status] ?? target.status;
  return (
    <button
      type="button"
      onClick={() => onStep(direction)}
      className="text-foreground/80 hover:text-foreground flex items-center gap-1.5 transition-colors"
    >
      {direction < 0 ? <Key tone="strong">←</Key> : null}
      {suggest ? `All ticked — ${title}` : title}
      {direction > 0 ? <Key tone={suggest ? "suggest" : "strong"}>→</Key> : null}
    </button>
  );
}

/** A finished (or dropped) task: one compact row. It selects and moves like a card does. */
function Row(props: CardProps) {
  const { task, selected, dragging, register, onDragStart, onDragEnd } = props;
  const handlers = useSelectable(props);
  const done = task.status === "done";
  return (
    <button
      ref={register}
      type="button"
      data-card
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      aria-current={selected || undefined}
      title={task.goal}
      {...handlers}
      className={cn(
        "text-foreground/80 hover:bg-muted/50 hover:text-foreground flex h-[38px] w-full shrink-0 cursor-grab items-center gap-[9px] rounded-lg border px-2.5 text-left outline-none transition-colors active:cursor-grabbing",
        selected ? "border-kith" : "border-transparent",
        dragging && "opacity-40",
      )}
    >
      {done ? (
        <Check className="text-roam size-[13px] shrink-0" strokeWidth={2.4} />
      ) : (
        <span className="bg-muted-foreground/50 mx-[3.5px] size-1.5 shrink-0 rounded-full" />
      )}
      <span className={cn("min-w-0 flex-1 truncate text-[12.5px]", !done && "line-through")}>{task.goal}</span>
      <span className="text-muted-foreground/80 shrink-0 font-mono text-[11px] tabular-nums">#{task.id}</span>
    </button>
  );
}

/** A key, as the board draws it: quiet in a hint, brighter on the selected card, amber when it
 *  is the move the checklist suggests. */
function Key({ children, tone = "quiet" }: { children: ReactNode; tone?: "quiet" | "strong" | "suggest" }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-[18px] items-center rounded-[5px] px-[5px] font-mono text-[11px] leading-none",
        tone === "suggest"
          ? "bg-kith text-primary-foreground"
          : cn(
              "border border-[color-mix(in_oklab,var(--foreground)_14%,transparent)]",
              tone === "strong" ? "text-foreground/80" : "text-muted-foreground/80",
            ),
      )}
    >
      {children}
    </kbd>
  );
}
