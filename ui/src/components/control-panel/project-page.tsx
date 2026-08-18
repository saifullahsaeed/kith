import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ChevronRight, FolderKanban, ListChecks, MessageSquare } from "lucide-react";
import { Markdown } from "@/components/files";
import { fetchConversations, type ConversationSummary } from "@/lib/backend";
import { dayLabel, time } from "@/lib/dates";
import { pathForConversation } from "@/lib/router";
import { Dropdown } from "@/components/ui/dropdown";
import { EditableText } from "@/components/ui/editable-text";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { DeleteButton, EmptyState, SectionLabel } from "./chrome";
import { matches } from "./format";
import { Roadmap } from "./roadmap";
import { SharedBoardBanner } from "./shared-board";
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
              {/* Wait for the answer before leaving. Calling both on the same tick opened the
                  "are you sure?" over the projects list, about a project you were no longer
                  looking at — and cancelling still cost you your place. */}
              <DeleteButton
                onClick={async () => {
                  if (await remove("project", project.id, project.name)) onBack();
                }}
              />
            </div>
          ) : null}
        </div>
      </div>

      {/* Above the workflow, not under it.
          This sat at the foot of the page first — below a roadmap graph, a task lane and two
          composers — which is reachable the way the far end of a corridor is reachable. Someone
          on a project page wanting a conversation is not going to scroll a graph to find one. */}
      <ProjectConversations projectId={project ? project.id : null} />

      {/* Above the roadmap, because it changes what the roadmap is showing. A person who takes
          in three tasks and then finds the graph unchanged has been told the wrong thing about
          what just happened. It renders nothing when nothing has come in — a permanent panel
          saying "nothing new" is one people stop reading, and this has to be noticed on the
          day it matters. */}
      {project ? <SharedBoardBanner projectId={project.id} onTaken={refresh} /> : null}

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

/**
 * The conversations that belong to this project, and the way back into one.
 *
 * This page had none, and that made the sidebar's "All projects" link a dead end: you would go
 * looking for a session, land on tasks and milestones, and find nothing that opened a chat. A
 * project is not only its plan — it is also everything that was said while doing it, and those
 * two lived on screens that did not know about each other.
 *
 * Its own fetch rather than a prop threaded down from the panel. The Control Panel is built from
 * a brain snapshot that has no conversations in it, and widening that snapshot to carry them —
 * for one section, on one tab — would put the listing behind every other page's refresh.
 */
function ProjectConversations({ projectId }: { projectId: number | null }) {
  const navigate = useNavigate();
  const [items, setItems] = useState<ConversationSummary[] | null>(null);

  useEffect(() => {
    let live = true;
    // The same ceiling the sidebar uses. Past that, its search is the way in — and this is a
    // section on a page rather than the place anyone hunts through a thousand sessions.
    void fetchConversations(500)
      .then((data) => {
        if (live) setItems(data.conversations.filter((one) => one.projectId === projectId));
      })
      .catch(() => {
        if (live) setItems([]);
      });
    return () => {
      live = false;
    };
  }, [projectId]);

  if (items === null) return null;

  return (
    <section className="mb-6">
      <SectionLabel hint={items.length ? String(items.length) : undefined}>
        Conversations
      </SectionLabel>
      {items.length === 0 ? (
        <EmptyState icon={<MessageSquare className="size-5" />}>
          Nothing has been said in this project yet.
        </EmptyState>
      ) : (
        <ul className="divide-border/40 border-border/60 divide-y overflow-hidden rounded-lg border">
          {items.map((one) => (
            <li key={one.id}>
              {/* Straight into the conversation, which closes the panel on the way — the route
                  for a chat is not a panel route, so opening one is also leaving here. */}
              <button
                type="button"
                onClick={() => navigate(pathForConversation(one.id))}
                className="hover:bg-accent/40 flex w-full items-start gap-3 px-3 py-2.5 text-left transition-colors"
              >
                <MessageSquare className="text-muted-foreground/40 mt-0.5 size-3.5 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px]">{one.title}</span>
                  {one.lastSaid ? (
                    <span className="text-muted-foreground/60 mt-0.5 block truncate text-[11px]">
                      {one.lastSaid}
                    </span>
                  ) : null}
                </span>
                {one.working ? (
                  <span
                    className="bg-roam mt-1.5 size-1.5 shrink-0 animate-pulse rounded-full"
                    title="Still working in this conversation"
                  />
                ) : null}
                <span
                  className="text-muted-foreground/40 mt-0.5 shrink-0 text-[10px] tabular-nums"
                  title={`${dayLabel(one.updatedAt)} ${time(one.updatedAt)}`}
                >
                  {dayLabel(one.updatedAt)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
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
