import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  ChevronRight,
  Download,
  FileCode2,
  FileText,
  Folder,
  FolderKanban,
  Link2,
  ListChecks,
  MessageSquare,
  Plus,
} from "lucide-react";
import { Markdown } from "@/components/files";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import { EditableText } from "@/components/ui/editable-text";
import { fetchConversations, type ConversationSummary } from "@/lib/backend";
import { fetchProjectOutputs, type BrainSnapshot, type ProjectOutput } from "@/lib/backend/brain";
import { formatBytes } from "@/lib/bytes";
import { dayLabel, time } from "@/lib/dates";
import { keys } from "@/lib/query-keys";
import { pathForConversation } from "@/lib/router";
import { cn } from "@/lib/utils";
import { Board } from "./board";
import { DeleteButton, EmptyState } from "./chrome";
import { matches, shortPath, tally } from "./format";
import { Breakdown, ProgressBar } from "./progress";
import { SharedBoardBanner } from "./shared-board";
import { LOOSE, PROJECT_STATUSES } from "./types";
import type { Handlers, ProjectRef } from "./types";

/** The project's pages. The board is the page; conversations and outputs are pages of their own
 *  beside it, rather than sections stacked underneath it — a kanban with a list under it is a
 *  kanban you scroll past to reach the list. */
type Section = "board" | "conversations" | "outputs";

/** The same ceiling the sidebar uses. Past that, search is the way in. */
const CHAT_LIMIT = 500;

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
  onNewConversation,
}: {
  snap: BrainSnapshot;
  query: string;
  projectRef: ProjectRef;
  onBack: () => void;
  onOpenTask: (id: number) => void;
  /** Start a chat that is already inside this project. Absent where the panel is not mounted in
   *  a window that can open one. */
  onNewConversation?: (projectId: number) => void;
} & Handlers) {
  const [section, setSection] = useState<Section>("board");
  const project =
    projectRef === LOOSE ? null : (snap.projects ?? []).find((p) => p.id === projectRef);
  const projectId = project ? project.id : null;
  const own = snap.tasks.filter((t) =>
    projectRef === LOOSE ? t.project_id == null : t.project_id === projectRef,
  );
  const tasks = own.filter((t) => matches(query, t.goal, t.status, t.description));

  // Loaded here, not in the tabs, so their counts are on the tab labels before you open them.
  const chats = useQuery({
    queryKey: keys.conversations(CHAT_LIMIT, ""),
    queryFn: async () => (await fetchConversations(CHAT_LIMIT)).conversations,
    enabled: projectId != null,
    staleTime: 30_000,
  });
  const outputs = useQuery({
    queryKey: keys.projectOutputs(projectId ?? 0),
    queryFn: () => fetchProjectOutputs(projectId ?? 0),
    enabled: projectId != null,
  });
  const mine = (chats.data ?? []).filter((one) => one.projectId === projectId);

  if (projectRef !== LOOSE && !project) {
    return (
      <>
        <Crumb onBack={onBack} label="Projects" />
        <EmptyState icon={<FolderKanban className="size-5" />}>That project is gone.</EmptyState>
      </>
    );
  }

  const counts = tally(own);

  return (
    <>
      <Crumb onBack={onBack} label="Projects" current={project ? project.name : "No project"} />

      <header className="border-border/60 mb-5 space-y-3 border-b pb-5">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            {project ? (
              <div className="text-2xl font-semibold tracking-tight">
                <EditableText
                  value={project.name}
                  onSave={(v) => update("project", project.id, { name: v })}
                />
              </div>
            ) : (
              <div className="text-2xl font-semibold tracking-tight">No project</div>
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

        {project ? (
          <Description
            value={project.description}
            onSave={(v) => update("project", project.id, { description: v })}
          />
        ) : (
          <p className="text-muted-foreground max-w-3xl text-sm">
            Loose work — errands that aren&apos;t part of anything bigger. Give one a project from
            inside the task.
          </p>
        )}

        {project ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {/* Where every file he writes for this project lands — the one fact that, when it was
                invisible, let a project run against a deleted folder for a whole session. Cut from
                the left, because the end of a path is the part that identifies it. */}
            {project.directory ? (
              <span
                title={project.directory}
                className="border-border/60 bg-card/50 text-foreground/80 inline-flex h-7 max-w-full items-center gap-1.5 rounded-lg border px-2.5 font-mono text-[11px]"
              >
                <Folder className="text-muted-foreground size-3.5 shrink-0" />
                <span className="truncate">{shortPath(project.directory, 48)}</span>
              </span>
            ) : (
              <span className="text-muted-foreground/70 italic">No folder — nothing here writes files</span>
            )}
            <span className="text-muted-foreground/70" title={`${dayLabel(project.updated_at)} ${time(project.updated_at)}`}>
              Updated {dayLabel(project.updated_at).toLowerCase()}
            </span>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
          <ProgressBar counts={counts} className="h-2 min-w-40 flex-1" />
          <span className="text-muted-foreground text-xs">
            <Breakdown counts={counts} />
          </span>
        </div>
      </header>

      {project ? (
        <nav
          role="tablist"
          aria-label="Project sections"
          className="border-border/60 -mt-2 mb-5 flex items-end gap-1 border-b"
        >
          <SectionTab
            label="Board"
            count={counts.total}
            active={section === "board"}
            onClick={() => setSection("board")}
          />
          <SectionTab
            label="Conversations"
            count={chats.data ? mine.length : undefined}
            active={section === "conversations"}
            onClick={() => setSection("conversations")}
          />
          <SectionTab
            label="Outputs"
            count={outputs.data?.length}
            active={section === "outputs"}
            onClick={() => setSection("outputs")}
          />
        </nav>
      ) : null}

      {section === "board" || !project ? (
        <>
          {/* Above the board, because it changes what the board is showing. Renders nothing
              when nothing has come in. */}
          {project ? <SharedBoardBanner projectId={project.id} onTaken={refresh} /> : null}
          <Board
            tasks={tasks}
            projectId={projectId}
            create={create}
            update={update}
            onOpenTask={onOpenTask}
          />
        </>
      ) : section === "conversations" ? (
        <Conversations
          items={mine}
          loading={chats.isPending}
          onNew={onNewConversation ? () => onNewConversation(project.id) : undefined}
        />
      ) : (
        <Outputs
          items={outputs.data ?? []}
          loading={outputs.isPending}
          projectId={project.id}
          onOpenTask={onOpenTask}
        />
      )}
    </>
  );
}

function SectionTab({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "-mb-px flex h-10 items-center gap-2 border-b-2 px-3.5 text-[13px] transition-colors",
        active
          ? "border-kith text-foreground font-semibold"
          : "text-muted-foreground hover:text-foreground border-transparent",
      )}
    >
      {label}
      {count !== undefined ? (
        <span
          className={cn(
            "rounded-full px-1.5 py-px text-[11px] tabular-nums",
            active ? "bg-accent text-foreground/80" : "bg-muted/60 text-muted-foreground/80",
          )}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

/** Two lines of the project's description, and the rest on request. A long brief used to push
 *  the whole page down by a screen before you reached anything you could act on. */
function Description({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const long = value.length > 220;
  return (
    <div className="text-muted-foreground max-w-4xl text-sm">
      <div className={cn(!open && long && "line-clamp-2")}>
        <EditableText
          value={value}
          render={(v) => <Markdown>{v}</Markdown>}
          onSave={onSave}
          multiline
          placeholder="(what this project is / what done means)"
        />
      </div>
      {long ? (
        <button
          type="button"
          onClick={() => setOpen((was) => !was)}
          className="text-kith hover:text-foreground mt-1 text-xs transition-colors"
        >
          {open ? "Show less" : "Show more"}
        </button>
      ) : null}
    </div>
  );
}

/**
 * Every chat that has worked on this project, and the way into a new one.
 *
 * Its own page now. It sat above the board first, then under it, and in both places it was
 * competing with the board for the same scroll. A project's conversations are a place you go,
 * not something you pass on the way to the tasks.
 */
function Conversations({
  items,
  loading,
  onNew,
}: {
  items: ConversationSummary[];
  loading: boolean;
  onNew?: () => void;
}) {
  const navigate = useNavigate();
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-muted-foreground min-w-0 flex-1 text-xs">
          Every chat that has worked on this project, newest first. A new one starts inside the
          project, with its board and memory already in front of him.
        </p>
        {onNew ? (
          <Button size="sm" onClick={onNew}>
            <Plus className="size-4" />
            New conversation
          </Button>
        ) : null}
      </div>
      {loading ? null : items.length === 0 ? (
        <EmptyState icon={<MessageSquare className="size-5" />}>
          Nothing has been said in this project yet.
        </EmptyState>
      ) : (
        <ul className="divide-border/40 border-border/60 divide-y overflow-hidden rounded-xl border">
          {items.map((one) => (
            <li key={one.id}>
              {/* Straight into the conversation, which closes the panel on the way — the route
                  for a chat is not a panel route, so opening one is also leaving here. */}
              <button
                type="button"
                onClick={() => navigate(pathForConversation(one.id))}
                className="hover:bg-accent/40 flex w-full items-start gap-3.5 px-4 py-3.5 text-left transition-colors"
              >
                <span className="bg-muted/70 text-muted-foreground mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg">
                  <MessageSquare className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{one.title}</span>
                    {one.working ? (
                      <span className="text-kith flex shrink-0 items-center gap-1 text-[11px]">
                        <span className="bg-kith size-1.5 animate-pulse rounded-full" />
                        working
                      </span>
                    ) : one.waiting ? (
                      <span className="text-foreground shrink-0 text-[11px] font-medium">
                        waiting on you
                      </span>
                    ) : null}
                  </span>
                  {one.lastSaid ? (
                    <span className="text-muted-foreground mt-1 line-clamp-2 block text-[13px] leading-relaxed">
                      {one.lastSaid}
                    </span>
                  ) : null}
                </span>
                <span className="flex shrink-0 flex-col items-end gap-1">
                  <span
                    className="text-foreground/80 text-xs"
                    title={`${dayLabel(one.updatedAt)} ${time(one.updatedAt)}`}
                  >
                    {dayLabel(one.updatedAt)}
                  </span>
                  <span className="text-muted-foreground/60 text-[11px] tabular-nums">
                    {one.messages} message{one.messages === 1 ? "" : "s"}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const OUTPUT_FILTERS = [
  { key: "all", label: "All" },
  { key: "file", label: "Files" },
  { key: "link", label: "Links" },
  { key: "text", label: "Notes" },
] as const;

/**
 * Everything the project's tasks produced, in one place.
 *
 * It was only ever reachable one task at a time, which answers "what did this task make" and not
 * "what has this project made" — and the second is the one you ask when you come back to a project
 * after a week. A row opens the task behind it, where the preview, the design editor and delete
 * already live; a file also downloads from here, because that is the thing most often wanted.
 */
function Outputs({
  items,
  loading,
  projectId,
  onOpenTask,
}: {
  items: ProjectOutput[];
  loading: boolean;
  projectId: number;
  onOpenTask: (id: number) => void;
}) {
  const [filter, setFilter] = useState<(typeof OUTPUT_FILTERS)[number]["key"]>("all");
  const [refused, setRefused] = useState("");
  const shown = filter === "all" ? items : items.filter((one) => one.kind === filter);

  /* The bytes, not a description of them — the same route and the same reasons as the task
   * page's download: `/api/workspace/raw` serves the file with its real type, and a failure says
   * so rather than saving a text file holding the path. */
  const download = async (one: ProjectOutput) => {
    setRefused("");
    const response = await fetch(
      `/api/workspace/raw?path=${encodeURIComponent(one.content)}&projectId=${projectId}`,
    ).catch(() => null);
    if (!response?.ok) {
      setRefused(`Couldn't read ${one.content} — it may have been moved or renamed.`);
      return;
    }
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = one.content.split("/").pop() || one.title || "output";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {OUTPUT_FILTERS.map((one) => {
          const n = one.key === "all" ? items.length : items.filter((item) => item.kind === one.key).length;
          return (
            <button
              key={one.key}
              type="button"
              onClick={() => setFilter(one.key)}
              aria-pressed={filter === one.key}
              className={cn(
                "h-7 rounded-full border px-3 text-xs transition-colors",
                filter === one.key
                  ? "border-border bg-accent text-foreground"
                  : "text-muted-foreground hover:text-foreground border-transparent",
              )}
            >
              {one.label}
              {n ? <span className="text-muted-foreground/70 ml-1.5 tabular-nums">{n}</span> : null}
            </button>
          );
        })}
        <span className="flex-1" />
        <span className="text-muted-foreground/70 text-xs">What his tasks produced, newest first.</span>
      </div>

      {refused ? (
        <p className="text-destructive/90 border-destructive/30 bg-destructive/5 rounded-lg border px-3 py-2 text-[12.5px]">
          {refused}
        </p>
      ) : null}

      {loading ? null : shown.length === 0 ? (
        <EmptyState icon={<ListChecks className="size-5" />}>
          {items.length === 0 ? "Nothing produced on this project yet." : "Nothing of that kind."}
        </EmptyState>
      ) : (
        <div className="border-border/60 overflow-hidden rounded-xl border">
          <div className="bg-sidebar/40 border-border/60 text-muted-foreground/70 hidden grid-cols-[minmax(0,5fr)_minmax(0,4fr)_5rem_6rem_2.25rem] gap-4 border-b px-4 py-2 text-[11px] tracking-wider uppercase md:grid">
            <span>Output</span>
            <span>From task</span>
            <span>Size</span>
            <span>Updated</span>
            <span />
          </div>
          <ul className="divide-border/40 divide-y">
            {shown.map((one) => (
              <li
                key={one.id}
                className="hover:bg-accent/30 grid grid-cols-[minmax(0,1fr)_2.25rem] items-center gap-4 px-4 py-2.5 transition-colors md:grid-cols-[minmax(0,5fr)_minmax(0,4fr)_5rem_6rem_2.25rem]"
              >
                <button
                  type="button"
                  onClick={() =>
                    one.kind === "link"
                      ? window.open(one.content, "_blank", "noreferrer")
                      : onOpenTask(one.task_id)
                  }
                  className="flex min-w-0 items-center gap-3 text-left"
                  title={one.kind === "link" ? "Open the link" : "Open the task that made it"}
                >
                  <span className="bg-muted/70 text-muted-foreground flex size-8 shrink-0 items-center justify-center rounded-lg">
                    {one.kind === "link" ? (
                      <Link2 className="size-4" />
                    ) : one.kind === "file" ? (
                      <FileCode2 className="size-4" />
                    ) : (
                      <FileText className="size-4" />
                    )}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm">{one.title}</span>
                    {one.kind !== "text" ? (
                      <span className="text-muted-foreground/70 block truncate font-mono text-[11px]">
                        {one.content}
                      </span>
                    ) : null}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => onOpenTask(one.task_id)}
                  className="text-muted-foreground hover:text-foreground hidden min-w-0 truncate text-left text-xs transition-colors md:block"
                  title={one.task_goal}
                >
                  #{one.task_id} {one.task_goal}
                </button>
                <span className="text-muted-foreground/70 hidden text-xs tabular-nums md:block">
                  {one.bytes !== undefined ? formatBytes(one.bytes) : one.kind === "text" ? `${one.content.length.toLocaleString()} ch` : "—"}
                </span>
                <span
                  className="text-muted-foreground/70 hidden text-xs md:block"
                  title={`${dayLabel(one.modified ?? one.created_at)} ${time(one.modified ?? one.created_at)}`}
                >
                  {dayLabel(one.modified ?? one.created_at)}
                </span>
                {one.kind === "file" ? (
                  <button
                    type="button"
                    onClick={() => void download(one)}
                    aria-label={`Download ${one.title}`}
                    title="Download"
                    className="text-muted-foreground hover:text-foreground hover:bg-accent flex size-8 items-center justify-center rounded-md transition-colors"
                  >
                    <Download className="size-4" />
                  </button>
                ) : one.kind === "link" ? (
                  <a
                    href={one.content}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`Open ${one.title}`}
                    className="text-muted-foreground hover:text-foreground hover:bg-accent flex size-8 items-center justify-center rounded-md transition-colors"
                  >
                    <ArrowUpRight className="size-4" />
                  </a>
                ) : (
                  <span />
                )}
              </li>
            ))}
          </ul>
        </div>
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
      className="text-muted-foreground hover:text-foreground mb-4 inline-flex items-center gap-1.5 text-sm transition-colors"
    >
      <ArrowLeft className="size-4" />
      {label}
      {current ? (
        <>
          <ChevronRight className="text-muted-foreground/50 size-3.5" />
          <span className="text-foreground max-w-[40ch] truncate">{current}</span>
        </>
      ) : null}
    </button>
  );
}
