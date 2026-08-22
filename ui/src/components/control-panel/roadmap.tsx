import { lazy, Suspense, useEffect, useState } from "react";
import { Check, CircleDot, Loader2, Plus } from "lucide-react";

/* Loaded when a roadmap is actually looked at, not when the app starts.
 *
 * `@xyflow/react` and its stylesheet are the graph and nothing else, and the graph lives two
 * screens in — control panel, a project, its workflow. Statically imported it rode in the main
 * bundle, which every window pays to parse before it can draw a chat. Mermaid and its cytoscape
 * layout engine were already split this way; this is the same call for the same reason. */
const RoadmapGraph = lazy(() =>
  import("@/components/control-panel/roadmap-graph").then((mod) => ({ default: mod.RoadmapGraph })),
);
import type { Roadmap as RoadmapData } from "@/lib/backend";
import { Button } from "@/components/ui/button";
import { DatePicker } from "@/components/ui/date-picker";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { SectionLabel } from "./chrome";
import { TaskLane } from "./task-lane";
import { FIELD } from "./types";
import type { Handlers } from "./types";

/**
 * A project, as its workflow.
 *
 * The old page was a checklist of milestones with a kanban of every task underneath, and the
 * kanban was the problem: five columns of thirty tasks says nothing about what happens next.
 * Most of those tasks are not available — they belong to milestones that are waiting — so a
 * board that shows them all with equal weight is actively misleading about the work.
 *
 * So the canvas is the page. It decides what is available, it shows where he is right now,
 * and the list underneath is whatever you have selected on it: one milestone's work, or —
 * with nothing selected — exactly the tasks he may pick up next. That is the same question
 * the graph answers, asked in words.
 */
export function Roadmap({
  project,
  tasks,
  create,
  update,
  remove,
  refresh,
  onOpenTask,
}: {
  project: BrainSnapshot["projects"][number];
  tasks: BrainSnapshot["tasks"];
  refresh: () => void;
  onOpenTask: (id: number) => void;
} & Pick<Handlers, "create" | "update" | "remove">) {
  const [ms, setMs] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [roadmap, setRoadmap] = useState<RoadmapData | null>(null);

  const addMilestone = () => {
    if (!ms.trim()) return;
    create("milestone", { project_id: project.id, title: ms });
    setMs("");
  };

  const chosen = project.milestones.find((m) => m.id === selected) ?? null;
  // Which milestones are actually waiting, from the server's own graph. Derived here once
  // and it was wrong: "every milestone that is not done" marked available work as held.
  const blockedMilestones = new Set(
    (roadmap?.milestones ?? [])
      .filter((one) => one.status !== "done" && !one.ready)
      .map((one) => one.id),
  );
  const shown = selected
    ? tasks.filter((task) => task.milestone_id === selected)
    : tasks.filter((task) => task.status !== "done");

  return (
    <section className="space-y-4">
      <SectionLabel hint={`${project.milestones_done}/${project.milestones_total}`}>
        Workflow
      </SectionLabel>

      <Suspense
        fallback={
          <div className="border-border/60 text-muted-foreground/60 flex h-48 items-center justify-center gap-2 rounded-xl border border-dashed text-sm">
            <Loader2 className="size-4 animate-spin" />
            Drawing the workflow…
          </div>
        }
      >
        <RoadmapGraph
          projectId={project.id}
          selected={selected}
          onSelect={setSelected}
          onChanged={refresh}
          onRoadmap={setRoadmap}
        />
      </Suspense>

      <div className="flex items-center gap-2 rounded-xl border border-border/60 bg-card/40 p-1.5 pl-3 focus-within:border-ring/60">
        <input
          value={ms}
          onChange={(e) => setMs(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addMilestone()}
          placeholder="Add a milestone, then drag between them to set the order…"
          className={`${FIELD} flex-1 text-xs`}
        />
        <Button size="xs" variant="outline" onClick={addMilestone}>
          <Plus className="size-3.5" />
          Add
        </Button>
      </div>

      {chosen ? (
        <MilestoneInspector
          milestone={chosen}
          onClear={() => setSelected(null)}
          update={update}
        />
      ) : null}

      <div>
        <div className="mb-2 flex items-baseline gap-3">
          <h3 className="text-sm font-semibold">{chosen ? "Its work" : "What he can work on"}</h3>
          <p className="text-muted-foreground min-w-0 flex-1 text-xs">
            {chosen
              ? "The tasks under this milestone."
              : "Every open task on this project. Ones under a waiting milestone are marked."}
          </p>
        </div>
        <TaskLane
          tasks={shown}
          milestones={project.milestones}
          blocked={blockedMilestones}
          projectId={project.id}
          create={create}
          update={update}
          remove={remove}
          onOpenTask={onOpenTask}
        />
      </div>
    </section>
  );
}

/**
 * The work, in the order it can actually happen.
 *
 * Not a kanban. A board's columns are statuses, and status is the least interesting thing
 * about a task here — whether it is *available* is what matters, and that comes from the
 * graph above. So: ready first, then in progress, then the ones held back with the reason
 * attached, then anything waiting on you. One column, honestly ordered.
 */
/**
 * The one milestone you have picked, and the things you can do to it.
 *
 * All three of these were unreachable from anywhere in the app. A milestone's target date
 * could only be set by Kith, through `add_milestone`, at the moment he created it — so a
 * date you wanted to change, or one you wanted to add later, could only be had by asking
 * him to do it. Marking one done had no control at all, and neither did renaming.
 *
 * The server could already do all of it: the generic brain edit for "milestone" takes
 * status, title and target_at. Nothing was missing but somewhere to click, which is the
 * most annoying kind of gap because it looks like a missing feature and is really a
 * missing button.
 *
 * It appears only when a milestone is selected, so the graph stays the way in and this
 * does not add a permanent panel to a page that already has plenty on it.
 */
function MilestoneInspector({
  milestone,
  onClear,
  update,
}: {
  milestone: BrainSnapshot["projects"][number]["milestones"][number];
  onClear: () => void;
  update: Handlers["update"];
}) {
  const [title, setTitle] = useState(milestone.title);
  useEffect(() => setTitle(milestone.title), [milestone.id, milestone.title]);
  const done = milestone.status === "done";

  const rename = () => {
    const next = title.trim();
    if (!next || next === milestone.title) {
      setTitle(milestone.title);
      return;
    }
    update("milestone", milestone.id, { title: next });
  };

  return (
    <div className="border-border/60 bg-card/40 flex flex-wrap items-center gap-2 rounded-xl border p-2 pl-3">
      <button
        type="button"
        title={done ? "Mark it not done" : "Mark it done"}
        onClick={() => update("milestone", milestone.id, { status: done ? "todo" : "done" })}
        className={cn(
          "shrink-0 rounded-md p-1 transition-colors",
          done ? "text-roam" : "text-muted-foreground/60 hover:text-foreground",
        )}
      >
        {done ? <Check className="size-4" /> : <CircleDot className="size-4" />}
      </button>
      <input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        onBlur={rename}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") setTitle(milestone.title);
        }}
        aria-label="Milestone name"
        className={cn(`${FIELD} min-w-40 flex-1 text-sm font-medium`, done && "line-through opacity-70")}
      />
      <DatePicker
        value={(milestone.target_at ?? "").slice(0, 10)}
        onChange={(next) => update("milestone", milestone.id, { target_at: next })}
        placeholder="No target date"
        ariaLabel="Target date"
        className="w-44 shrink-0"
      />
      <button
        type="button"
        onClick={onClear}
        className="text-muted-foreground hover:text-foreground shrink-0 px-2 text-xs"
      >
        Show everything
      </button>
    </div>
  );
}
