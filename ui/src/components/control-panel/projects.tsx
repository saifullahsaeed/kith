import { useState } from "react";
import { ChevronRight, FolderKanban, ListChecks, Plus } from "lucide-react";
import { MarkdownInline } from "@/components/files";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { Badge, Composer, DeleteButton, EmptyState, PageHeader } from "./chrome";
import { matches, shortPath } from "./format";
import { ProgressRing } from "./task-lane";
import { FIELD, LOOSE, PROJECT_STATUSES } from "./types";
import type { Handlers, ProjectRef } from "./types";

/** Counts of a project's tasks by where they sit. */
function taskTally(tasks: BrainSnapshot["tasks"]) {
  const of = (...s: string[]) => tasks.filter((t) => s.includes(t.status)).length;
  return {
    total: tasks.length,
    open: of("backlog", "planned"),
    // Counted apart from `open`, for the same reason `review` is counted apart from `done`
    // below: a plan waiting on a decision is not the same thing as work nobody has looked at
    // yet, even though both currently show as "nothing is happening on this."
    planning: of("planning"),
    working: of("working"),
    // Counted apart from `done`, because that is the whole point of the column: work he believes
    // is finished but nobody has checked. Rolling it into `done` would put the project's progress
    // bar at 100% on his own say-so.
    review: of("review"),
    waiting: of("waiting"),
    done: of("done"),
  };
}

export function Projects({
  snap,
  query,
  remove,
  create,
  update,
  onOpenProject,
}: {
  snap: BrainSnapshot;
  query: string;
  onOpenProject: (ref: ProjectRef) => void;
} & Handlers) {
  const [name, setName] = useState("");
  const searching = query.trim().length > 0;
  const hits = (t: BrainSnapshot["tasks"][number]) =>
    matches(query, t.goal, t.status, t.description);
  // Searching finds work wherever it lives: a project surfaces when it matches
  // *or* when one of its tasks does.
  const projects = (snap.projects ?? []).filter(
    (p) =>
      matches(query, p.name, p.description, p.status) ||
      snap.tasks.some((t) => t.project_id === p.id && hits(t)),
  );
  const loose = snap.tasks.filter((t) => t.project_id == null && hits(t));
  // Finished and paused work is kept, but out of the way: a done project at 70% opacity in
  // the same list still takes a line of attention every time you scan for what is live.
  const live = projects.filter((one) => one.status === "active");
  const closedOnes = projects.filter((one) => one.status !== "active");
  const all = taskTally(snap.tasks);

  // Where he is, and how much is genuinely pickup-able. Both come from the roadmap rather
  // than from task status: a task under a waiting milestone is open but not available, and
  // counting it as work-to-do is the misreading the whole graph exists to prevent.
  const onNow = (snap.projects ?? [])
    .flatMap((project) =>
      project.milestones
        .filter((one) => one.tasks_doing > 0)
        .map((one) => ({ title: one.title, project: project.name })),
    )
    .at(0);
  const available = (snap.projects ?? []).reduce(
    (total, project) =>
      total +
      project.milestones.filter((one) => one.ready).reduce((sum, one) => sum + one.tasks_active, 0),
    0,
  );
  const add = () => {
    if (!name.trim()) return;
    create("project", { name });
    setName("");
  };
  return (
    <>
      <PageHeader
        icon={<FolderKanban className="size-5" />}
        color="kith"
        title="Projects"
        count={projects.length}
        subtitle="Everything he's working on. Open one to see its tasks."
      />
      <Composer onSubmit={add}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Start a project — a bigger goal…"
          className={`${FIELD} flex-1`}
        />
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Add project
        </Button>
      </Composer>

      {/* What he is actually doing, then the numbers. The tally alone answered "how much
          work is there", which is not the question anyone opens this page with. */}
      {all.total > 0 ? (
        <div className="border-border/60 bg-card/40 mb-4 rounded-xl border px-4 py-2.5">
          <p className="text-sm">
            {onNow ? (
              <>
                <span className="bg-kith mr-2 inline-block size-1.5 animate-pulse rounded-full align-middle" />
                Working on <span className="text-kith">{onNow.title}</span>
                <span className="text-muted-foreground"> in {onNow.project}</span>
              </>
            ) : all.waiting ? (
              <span className="text-orange-400/90">
                {all.waiting} thing{all.waiting === 1 ? "" : "s"} waiting on you
              </span>
            ) : available > 0 ? (
              <span className="text-muted-foreground">
                <span className="text-foreground">{available}</span> task
                {available === 1 ? "" : "s"} available to pick up
              </span>
            ) : (
              <span className="text-muted-foreground">
                Nothing to pick up — he&apos;s caught up.
              </span>
            )}
          </p>
          <div className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
            <span className="text-foreground font-medium">
              {all.total} task{all.total === 1 ? "" : "s"}
            </span>
            <TaskTally tally={all} />
          </div>
        </div>
      ) : null}

      {projects.length === 0 && loose.length === 0 ? (
        <EmptyState icon={<FolderKanban className="size-5" />}>
          No projects yet — start one above.
        </EmptyState>
      ) : (
        <div className="space-y-3">
          {live.map((p) => {
            const own = snap.tasks.filter((t) => t.project_id === p.id);
            return (
              <ProjectCard
                key={p.id}
                project={p}
                tasks={own}
                matching={searching ? own.filter(hits).length : undefined}
                remove={remove}
                update={update}
                onOpen={() => onOpenProject(p.id)}
              />
            );
          })}
          {loose.length > 0 ? (
            <LooseCard tasks={loose} onOpen={() => onOpenProject(LOOSE)} />
          ) : null}

          {closedOnes.length > 0 ? (
            <div className="pt-2">
              <p className="text-muted-foreground/60 mb-2 text-[11px] tracking-wide uppercase">
                Finished &amp; paused · {closedOnes.length}
              </p>
              <div className="space-y-2">
                {closedOnes.map((p) => (
                  <ProjectCard
                    key={p.id}
                    project={p}
                    tasks={snap.tasks.filter((one) => one.project_id === p.id)}
                    matching={0}
                    remove={remove}
                    update={update}
                    onOpen={() => onOpenProject(p.id)}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}

function TaskTally({ tally }: { tally: ReturnType<typeof taskTally> }) {
  if (!tally.total) return <span className="text-muted-foreground/70">no tasks yet</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 tabular-nums">
      {tally.working ? <span className="text-emerald-500">{tally.working} working</span> : null}
      {tally.waiting ? (
        <span className="font-medium text-kith">{tally.waiting} waiting on you</span>
      ) : null}
      {/* Ahead of "to do", because it is the shortest path to progress: these are finished and
          need a glance, not work. Amber rather than the accent — it wants attention, but less
          urgently than something actually blocked on an answer. */}
      {tally.review ? (
        <span className="font-medium text-amber-600 dark:text-amber-400">
          {tally.review} to check
        </span>
      ) : null}
      {/* Same shape as `review`, one step earlier: a plan waiting on a decision before any
          work starts, rather than work waiting on a check after it's done. */}
      {tally.planning ? (
        <span className="font-medium text-violet-600 dark:text-violet-400">
          {tally.planning} to approve
        </span>
      ) : null}
      {tally.open ? <span>{tally.open} to do</span> : null}
      {tally.done ? <span className="text-muted-foreground/70">{tally.done} done</span> : null}
    </span>
  );
}

function ProjectCard({
  project,
  tasks,
  matching,
  remove,
  update,
  onOpen,
}: {
  project: BrainSnapshot["projects"][number];
  tasks: BrainSnapshot["tasks"];
  /** How many of its tasks match the current search (undefined = not searching). */
  matching?: number;
  onOpen: () => void;
} & Pick<Handlers, "remove" | "update">) {
  const pct = project.milestones_total
    ? Math.round((project.milestones_done / project.milestones_total) * 100)
    : 0;
  const closed = project.status !== "active";
  const tally = taskTally(tasks);

  // Where the project actually is, from its own graph. A card that only showed "3/5
  // milestones" was telling you how much of it was behind rather than what happens next,
  // which is the question you open a project to answer.
  const ordered = [...project.milestones].sort((a, b) => a.order_index - b.order_index);
  const here = ordered.find((one) => one.tasks_doing > 0) ?? null;
  const next = ordered.find((one) => one.status !== "done" && one.ready) ?? null;
  const stalled = !here && !next && ordered.some((one) => one.status !== "done");

  return (
    // Click anywhere to go into the project; the status dropdown and delete opt out.
    <div
      onClick={onOpen}
      className={cn(
        "group cursor-pointer rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:border-kith/30 hover:shadow-md",
        closed && "opacity-70",
        here && "border-kith/40",
      )}
    >
      <div className="flex items-start gap-4">
        <ProgressRing pct={pct} size={44} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="min-w-0 truncate text-[15px] leading-snug font-semibold transition-colors group-hover:text-kith">
              {project.name}
            </span>
            {here ? (
              <span className="text-kith flex shrink-0 items-center gap-1 text-[11px]">
                <span className="bg-kith size-1.5 animate-pulse rounded-full" />
                working now
              </span>
            ) : null}
            {tally.waiting ? (
              <Badge className="border-kith/25 bg-kith-soft text-kith">waiting on you</Badge>
            ) : null}
            {matching ? (
              <Badge className="border-kith/25 bg-kith-soft text-kith">
                {matching} match{matching === 1 ? "" : "es"}
              </Badge>
            ) : null}
          </div>

          {project.description ? (
            <div className="text-muted-foreground mt-0.5 line-clamp-2 text-sm">
              <MarkdownInline>{project.description}</MarkdownInline>
            </div>
          ) : null}

          {/*
            Where the work actually lands. The server has always sent this and the interface never
            showed it, so the single fact that decides where every file he writes goes was
            invisible — which is how a project ran against a folder that had been deleted, and how
            a relative path quietly resolved into his own folder instead of the codebase.

            Truncated from the *left*: the end of a path is the part that identifies it, and
            `/Users/saifullahsaeed/Desktop/personal/…` is the part that never varies.
          */}
          {project.directory ? (
            <p
              title={project.directory}
              className="text-muted-foreground/60 mt-1 truncate font-mono text-[11px]"
            >
              {shortPath(project.directory)}
            </p>
          ) : (
            <p className="text-muted-foreground/50 mt-1 text-[11px] italic">
              No folder — nothing here writes files
            </p>
          )}

          {/* What happens next, in words. The single most useful line on the card. */}
          {!closed && ordered.length > 0 ? (
            <p className="mt-1.5 truncate text-xs">
              {here ? (
                <span className="text-kith">On “{here.title}”</span>
              ) : next ? (
                <span className="text-muted-foreground">
                  Next: <span className="text-foreground">{next.title}</span>
                </span>
              ) : stalled ? (
                <span className="text-orange-400/90">
                  Nothing available — every milestone is waiting on another
                </span>
              ) : (
                <span className="text-roam">Every milestone done</span>
              )}
            </p>
          ) : null}

          <div className="text-muted-foreground mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
            <span className="tabular-nums">
              {project.milestones_done}/{project.milestones_total} milestones
            </span>
            <span className="text-muted-foreground/40">·</span>
            <TaskTally tally={tally} />
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <Dropdown
            value={project.status}
            onChange={(v) => update("project", project.id, { status: v })}
            options={PROJECT_STATUSES}
            className="w-28"
            ariaLabel="Project status"
          />
          <DeleteButton onClick={() => remove("project", project.id, project.name)} />
        </div>
        <ChevronRight className="text-muted-foreground/40 group-hover:text-kith mt-2 size-4 shrink-0 transition-colors" />
      </div>

      {/* The roadmap as a strip: the shape of the project without opening it. Segments in
          order, coloured by state, the one he is on marked. A ring can only say how much is
          left; this says where the work actually is. */}
      {ordered.length > 1 ? (
        <div className="mt-3 flex items-center gap-1">
          {ordered.map((one) => (
            <span
              key={one.id}
              title={
                one.status === "done"
                  ? `${one.title} — done`
                  : one.tasks_doing > 0
                    ? `${one.title} — being worked on now`
                    : one.ready
                      ? `${one.title} — ready`
                      : `${one.title} — waits for ${one.blocked_by.join(", ")}`
              }
              className={cn(
                "h-1.5 flex-1 rounded-full transition-colors",
                one.status === "done" && "bg-roam/60",
                one.tasks_doing > 0 && "bg-kith animate-pulse",
                one.status !== "done" && one.tasks_doing === 0 && one.ready && "bg-kith/50",
                one.status !== "done" && !one.ready && "bg-muted-foreground/20",
              )}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LooseCard({ tasks, onOpen }: { tasks: BrainSnapshot["tasks"]; onOpen: () => void }) {
  const tally = taskTally(tasks);
  return (
    <div
      onClick={onOpen}
      className="group flex cursor-pointer items-center gap-4 rounded-xl border border-dashed border-border/70 bg-card/30 p-4 transition-all hover:border-kith/40 hover:bg-card/50"
    >
      <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-muted/70 text-muted-foreground">
        <ListChecks className="size-5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[15px] font-semibold leading-snug transition-colors group-hover:text-kith">
            No project
          </span>
          {tally.waiting ? (
            <Badge className="border-kith/25 bg-kith-soft text-kith">waiting on you</Badge>
          ) : null}
        </div>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Loose work — tasks that aren't part of anything bigger.
        </p>
        <div className="mt-2 text-[11px] text-muted-foreground">
          <TaskTally tally={tally} />
        </div>
      </div>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-kith" />
    </div>
  );
}
