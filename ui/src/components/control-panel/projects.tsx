import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Folder, FolderKanban, ListChecks, MessageSquare, Plus } from "lucide-react";
import { MarkdownInline } from "@/components/files";
import { Button } from "@/components/ui/button";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { fetchConversations, type ConversationSummary } from "@/lib/backend";
import { dayLabel } from "@/lib/dates";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import { Badge, EmptyState } from "./chrome";
import { matches, shortPath, tally } from "./format";
import { Breakdown, ProgressBar } from "./progress";
import { FIELD, LOOSE } from "./types";
import type { Handlers, ProjectRef } from "./types";

type Project = BrainSnapshot["projects"][number];

/** The same ceiling the sidebar uses, and the same cache entry. */
const CHAT_LIMIT = 500;

/** Which projects the list shows. Active is where you land; the rest are one click away rather
 *  than stacked under it at 70% opacity, still taking a line of attention on every scan. */
const FILTERS = [
  { key: "active", label: "Active" },
  { key: "paused", label: "Paused" },
  { key: "done", label: "Done" },
  { key: "archived", label: "Archived" },
] as const;

type Filter = (typeof FILTERS)[number]["key"];

export function Projects({
  snap,
  query,
  create,
  onOpenProject,
}: {
  snap: BrainSnapshot;
  query: string;
  onOpenProject: (ref: ProjectRef) => void;
} & Handlers) {
  const [name, setName] = useState("");
  const [filter, setFilter] = useState<Filter>("active");
  const searching = query.trim().length > 0;

  // One listing for every card: eight cards each fetching it is eight reads of one file.
  const chats = useQuery({
    queryKey: keys.conversations(CHAT_LIMIT, ""),
    queryFn: async () => (await fetchConversations(CHAT_LIMIT)).conversations,
    staleTime: 30_000,
  });
  const chatsFor = (id: number) => (chats.data ?? []).filter((one) => one.projectId === id);

  const hits = (t: BrainSnapshot["tasks"][number]) => matches(query, t.goal, t.status, t.description);
  // Searching finds work wherever it lives: a project surfaces when it matches *or* when one of
  // its tasks does.
  const all = (snap.projects ?? []).filter(
    (p) =>
      matches(query, p.name, p.description, p.status) ||
      snap.tasks.some((t) => t.project_id === p.id && hits(t)),
  );
  const shown = all.filter((p) => p.status === filter);
  const loose = snap.tasks.filter((t) => t.project_id == null && hits(t));
  const everything = tally(snap.tasks);

  const add = () => {
    if (!name.trim()) return;
    create("project", { name });
    setName("");
  };

  return (
    <>
      <div className="mb-6 flex flex-wrap items-start gap-x-4 gap-y-3">
        <span className="bg-muted/70 text-kith flex size-10 shrink-0 items-center justify-center rounded-xl">
          <FolderKanban className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="flex items-baseline gap-2 text-xl font-semibold tracking-tight">
            Projects
            <span className="text-muted-foreground text-sm font-normal tabular-nums">
              {(snap.projects ?? []).length}
            </span>
          </h2>
          <p className="text-muted-foreground mt-0.5 text-[13px]">
            Everything he&apos;s working on. Open one for its board, conversations and outputs.
          </p>
        </div>
        <div className="border-border/70 bg-card/50 flex w-full max-w-sm items-center gap-2 rounded-xl border p-1 pl-3.5 transition-colors focus-within:border-ring/60 sm:w-80">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder="Start a project…"
            aria-label="New project name"
            className={`${FIELD} flex-1`}
          />
          <Button size="sm" onClick={add}>
            <Plus className="size-4" />
            New
          </Button>
        </div>
      </div>

      {everything.total + everything.dropped > 0 ? (
        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Waiting on you"
            value={everything.planning}
            note="plans waiting for your OK"
            emphasis={everything.planning > 0}
          />
          <Stat label="Working now" value={everything.working} note="tasks he is on right now" tone="kith" pulse />
          <Stat label="Up next" value={everything.approved} note="approved, not started yet" />
          <Stat label="Done" value={everything.done} note="tasks finished, all time" tone="roam" />
        </div>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Which projects">
        {FILTERS.map((one) => {
          const n = all.filter((p) => p.status === one.key).length;
          return (
            <button
              key={one.key}
              type="button"
              role="tab"
              aria-selected={filter === one.key}
              onClick={() => setFilter(one.key)}
              className={cn(
                "h-7 rounded-full border px-3 text-xs transition-colors",
                filter === one.key
                  ? "border-border bg-accent text-foreground"
                  : "text-muted-foreground hover:text-foreground border-transparent",
              )}
            >
              {one.label}
              <span className="text-muted-foreground/70 ml-1.5 tabular-nums">{n}</span>
            </button>
          );
        })}
        <span className="flex-1" />
        <span className="text-muted-foreground/60 text-xs">Most recently active first</span>
      </div>

      {shown.length === 0 && (filter !== "active" || loose.length === 0) ? (
        <EmptyState icon={<FolderKanban className="size-5" />}>
          {searching
            ? "Nothing here matches."
            : filter === "active"
              ? "No projects yet — start one above."
              : `No ${filter} projects.`}
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-2">
          {shown.map((p) => {
            const own = snap.tasks.filter((t) => t.project_id === p.id);
            return (
              <ProjectCard
                key={p.id}
                project={p}
                tasks={own}
                matching={searching ? own.filter(hits).length : undefined}
                chats={chatsFor(p.id)}
                onOpen={() => onOpenProject(p.id)}
              />
            );
          })}
          {filter === "active" && loose.length > 0 ? (
            <LooseCard tasks={loose} onOpen={() => onOpenProject(LOOSE)} />
          ) : null}
        </div>
      )}
    </>
  );
}

function Stat({
  label,
  value,
  note,
  tone,
  pulse,
  emphasis,
}: {
  label: string;
  value: number;
  note: string;
  tone?: "kith" | "roam";
  pulse?: boolean;
  /** "Your turn" is weight and a warm edge rather than a third hue — see STATE in types.ts. */
  emphasis?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-xl border px-4 py-3",
        emphasis ? "border-kith/35 bg-kith-soft/60" : "border-border/60 bg-card/40",
      )}
    >
      <span
        className={cn(
          "flex items-center gap-1.5 text-[11px] tracking-[0.08em] uppercase",
          emphasis ? "text-kith" : "text-muted-foreground",
        )}
      >
        {pulse && value > 0 ? <span className="bg-kith size-1.5 animate-pulse rounded-full" /> : null}
        {label}
      </span>
      <span
        className={cn(
          "text-2xl font-semibold tabular-nums",
          tone === "kith" && value > 0 && "text-kith",
          tone === "roam" && value > 0 && "text-roam",
        )}
      >
        {value}
      </span>
      <span className="text-muted-foreground text-xs">{note}</span>
    </div>
  );
}

/**
 * One project, as a card you open.
 *
 * It used to carry a milestone strip, a progress ring over milestones, a "Next:" line from the
 * roadmap, a status dropdown and a delete button — five things about the plan's shape and two
 * controls you almost never want from a list. What is left answers the three questions you scan
 * a projects list with: is he on it, does it need me, and where was it left.
 */
function ProjectCard({
  project,
  tasks,
  matching,
  chats,
  onOpen,
}: {
  project: Project;
  tasks: BrainSnapshot["tasks"];
  /** How many of its tasks match the current search (undefined = not searching). */
  matching?: number;
  /** This project's conversations, newest first. */
  chats: ConversationSummary[];
  onOpen: () => void;
}) {
  const counts = tally(tasks);
  const latest = chats[0];

  return (
    // Not a <button>: the description is Markdown, and a link in it renders as its own anchor or
    // button, which may not sit inside another. Keyboard-reachable all the same.
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      className={cn(
        "group border-border/70 bg-card/50 hover:border-kith/30 focus-visible:ring-ring/40 flex w-full cursor-pointer flex-col gap-2.5 rounded-2xl border p-4 text-left shadow-sm transition-all outline-none hover:shadow-md focus-visible:ring-[3px]",
        counts.working > 0 && "border-kith/30",
      )}
    >
      <span className="flex w-full items-center gap-2">
        <span className="min-w-0 truncate text-[15px] leading-snug font-semibold">{project.name}</span>
        {counts.working > 0 ? (
          <span className="text-kith flex shrink-0 items-center gap-1 text-[11px]">
            <span className="bg-kith size-1.5 animate-pulse rounded-full" />
            working
          </span>
        ) : null}
        <span className="flex-1" />
        {matching ? (
          <Badge className="border-kith/25 bg-kith-soft text-kith">
            {matching} match{matching === 1 ? "" : "es"}
          </Badge>
        ) : null}
        {counts.planning > 0 ? (
          <Badge className="border-kith/30 bg-kith-soft text-kith font-semibold">
            {counts.planning} to approve
          </Badge>
        ) : null}
        <ChevronRight className="text-muted-foreground/40 group-hover:text-foreground size-4 shrink-0 transition-colors" />
      </span>

      {project.description ? (
        // `line-clamp` is a display of its own (-webkit-box), so nothing here may also say `block`;
        // and the minimum height is exactly two lines, or a sliver of the third shows under it.
        <div className="text-muted-foreground line-clamp-2 min-h-10 text-[13px] leading-5">
          <MarkdownInline>{project.description}</MarkdownInline>
        </div>
      ) : (
        <span className="text-muted-foreground/50 block min-h-10 text-[13px] leading-5 italic">No description</span>
      )}

      {/* Where the work actually lands, cut from the left: the end of a path identifies it. */}
      {project.directory ? (
        <span
          title={project.directory}
          className="text-muted-foreground/70 flex min-w-0 items-center gap-1.5 font-mono text-[11px]"
        >
          <Folder className="size-3 shrink-0" />
          <span className="truncate">{shortPath(project.directory)}</span>
        </span>
      ) : (
        <span className="text-muted-foreground/50 text-[11px] italic">No folder — nothing here writes files</span>
      )}

      <span className="flex w-full flex-col gap-1.5">
        <ProgressBar counts={counts} />
        <span className="text-muted-foreground text-[11px]">
          <Breakdown counts={counts} />
        </span>
      </span>

      <span className="border-border/50 flex w-full items-center gap-2.5 border-t pt-2.5 text-xs">
        <MessageSquare className="text-muted-foreground/60 size-3.5 shrink-0" />
        {latest ? (
          <>
            <span className="text-muted-foreground shrink-0">
              {chats.length} conversation{chats.length === 1 ? "" : "s"}
            </span>
            <span className="text-foreground/80 min-w-0 flex-1 truncate">{latest.title}</span>
            {latest.working ? (
              <span
                className="bg-roam size-1.5 shrink-0 animate-pulse rounded-full"
                title="Still working in this conversation"
              />
            ) : null}
            <span className="text-muted-foreground/60 shrink-0 text-[11px]">{dayLabel(latest.updatedAt)}</span>
          </>
        ) : (
          <span className="text-muted-foreground/60">No conversations yet</span>
        )}
      </span>
    </div>
  );
}

function LooseCard({ tasks, onOpen }: { tasks: BrainSnapshot["tasks"]; onOpen: () => void }) {
  const counts = tally(tasks);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group border-border/70 bg-card/20 hover:border-kith/40 hover:bg-card/50 flex w-full items-center gap-4 rounded-2xl border border-dashed p-4 text-left transition-all xl:col-span-2"
    >
      <span className="bg-muted/70 text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-xl">
        <ListChecks className="size-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] leading-snug font-semibold">No project</span>
        <span className="text-muted-foreground mt-0.5 block text-[13px]">
          Loose work — errands that aren&apos;t part of anything bigger. Same board, no project.
        </span>
      </span>
      <span className="text-muted-foreground hidden text-xs sm:block">
        <Breakdown counts={counts} />
      </span>
      <ChevronRight className="text-muted-foreground/40 group-hover:text-foreground size-4 shrink-0 transition-colors" />
    </button>
  );
}
