import { ArrowLeft, ChevronRight, FolderKanban, ListChecks } from "lucide-react";
import { Markdown } from "@/components/files";
import { Dropdown } from "@/components/ui/dropdown";
import { EditableText } from "@/components/ui/editable-text";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { DeleteButton, EmptyState, SectionLabel } from "./chrome";
import { matches } from "./format";
import { Roadmap } from "./roadmap";
import { ProgressRing, TaskLane } from "./task-lane";
import { LOOSE, PROJECT_STATUSES } from "./types";
import type { Handlers, ProjectRef } from "./types";

/* ── One project: what it is, its roadmap, and its tasks ────────────────── */

export function ProjectPage({
  snap,
  query,
  projectRef,
  refresh,
  remove,
  update,
  create,
  onBack,
  onOpenTask,
}: {
  snap: BrainSnapshot;
  query: string;
  projectRef: ProjectRef;
  onBack: () => void;
  onOpenTask: (id: number) => void;
} & Handlers) {
  const project =
    projectRef === LOOSE ? null : (snap.projects ?? []).find((p) => p.id === projectRef);
  const tasks = snap.tasks
    .filter((t) => (projectRef === LOOSE ? t.project_id == null : t.project_id === projectRef))
    .filter((t) => matches(query, t.goal, t.status, t.description));

  if (projectRef !== LOOSE && !project) {
    return (
      <>
        <Crumb onBack={onBack} label="Projects" />
        <EmptyState icon={<FolderKanban className="size-5" />}>That project is gone.</EmptyState>
      </>
    );
  }

  const pct = project?.milestones_total
    ? Math.round((project.milestones_done / project.milestones_total) * 100)
    : 0;

  return (
    <>
      <Crumb onBack={onBack} label="Projects" current={project ? project.name : "No project"} />

      <div className="mb-6 border-b border-border/60 pb-5">
        <div className="flex items-start gap-4">
          {project ? (
            <ProgressRing pct={pct} size={48} />
          ) : (
            <span className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-muted/70 text-muted-foreground">
              <ListChecks className="size-5" />
            </span>
          )}
          <div className="min-w-0 flex-1">
            {project ? (
              <>
                <div className="text-xl font-semibold tracking-tight">
                  <EditableText
                    value={project.name}
                    onSave={(v) => update("project", project.id, { name: v })}
                  />
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                  <EditableText
                    value={project.description}
                    render={(v) => <Markdown>{v}</Markdown>}
                    onSave={(v) => update("project", project.id, { description: v })}
                    multiline
                    placeholder="(what this project is / what done means)"
                  />
                </div>
              </>
            ) : (
              <>
                <div className="text-xl font-semibold tracking-tight">No project</div>
                <p className="mt-1 text-sm text-muted-foreground">
                  Loose work — tasks that aren't part of anything bigger. Give one a project from
                  inside the task.
                </p>
              </>
            )}
          </div>
          {project ? (
            <div className="flex shrink-0 items-center gap-1">
              <Dropdown
                value={project.status}
                onChange={(v) => update("project", project.id, { status: v })}
                options={PROJECT_STATUSES}
                className="w-28"
                ariaLabel="Project status"
              />
              <DeleteButton
                onClick={() => {
                  remove("project", project.id, project.name);
                  onBack();
                }}
              />
            </div>
          ) : null}
        </div>
      </div>

      {project ? (
        <Roadmap
          project={project}
          tasks={tasks}
          create={create}
          update={update}
          remove={remove}
          refresh={refresh}
          onOpenTask={onOpenTask}
        />
      ) : null}

      {/* Only the loose tray needs this: a project's work is shown inside its workflow,
          selected from the graph. */}
      {project ? null : (
        <section>
          <SectionLabel hint={`${tasks.length}`}>Tasks</SectionLabel>
          <TaskLane
            tasks={tasks}
            milestones={[]}
            blocked={new Set()}
            projectId={null}
            create={create}
            update={update}
            remove={remove}
            onOpenTask={onOpenTask}
          />
        </section>
      )}
    </>
  );
}

function Crumb({
  onBack,
  label,
  current,
}: {
  onBack: () => void;
  label: string;
  current?: string;
}) {
  return (
    <button
      onClick={onBack}
      className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="size-4" />
      {label}
      {current ? (
        <>
          <ChevronRight className="size-3.5 text-muted-foreground/50" />
          <span className="max-w-[30ch] truncate text-foreground">{current}</span>
        </>
      ) : null}
    </button>
  );
}
