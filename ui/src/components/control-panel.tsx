import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Check,
  CircleDot,
  ArrowUp,
  BellRing,
  Brain,
  ChevronDown,
  ChevronRight,
  Clock,
  Code,
  Copy,
  Expand,
  ExternalLink,
  FileText,
  Folder,
  FolderKanban,
  FolderOpen,
  FolderPlus,
  Pencil,
  FolderTree,
  Link as LinkIcon,
  ListChecks,
  MessageCircle,
  NotebookPen,
  Pause,
  Pin,
  Play,
  Plus,
  RefreshCw,
  Repeat,
  Search,
  Sparkles,
  StickyNote,
  Trash2,
  User,
  Wrench,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { DatePicker } from "@/components/ui/date-picker";
import { Dropdown } from "@/components/ui/dropdown";
import { useConfirm } from "@/components/ui/confirm";
import { EditableText } from "@/components/ui/editable-text";
import { ItemMenu } from "@/components/ui/item-menu";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { TaskDetailPage } from "@/components/task-detail";
import {
  CodeBlock,
  FileViewer,
  Markdown,
  MarkdownInline,
  skipTextRead,
} from "@/components/file-view";
import {
  copyText,
  formatModified,
  formatSize,
  openWorkspaceFile,
  makeFolder,
  remove as removeFile,
  rename as renameEntry,
} from "@/lib/files";
import { RoadmapGraph } from "@/components/roadmap-graph";
import type { Roadmap } from "@/lib/backend";
import { cn } from "@/lib/utils";
import {
  createBrainItem,
  deleteBrainItem,
  fetchBrain,
  fetchTimeline,
  fetchWorkspace,
  fetchWorkspaceFile,
  ingestSource,
  setMemoryLevel,
  updateBrainItem,
  type BrainSnapshot,
  type TimelineEvent,
  type TimelineKind,
  type WorkspaceEntry,
} from "@/lib/backend/brain";

type Tab =
  | "overview"
  | "lifetime"
  | "memory"
  | "notes"
  | "journal"
  | "projects"
  | "reminders"
  | "schedules"
  | "people"
  | "sources"
  | "workspace"
  | "tools";

/** Work lives in one place: Projects → a project → its tasks. Tasks with no
 * project sit in the "No project" tray, addressed by this sentinel. */
const LOOSE = "none" as const;
type ProjectRef = number | typeof LOOSE;

/** A borderless field that lives inside a <Composer> pill. */
const FIELD = "min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground/55";
/** A bordered field for inline forms (edit, filters). */
const INPUT =
  "rounded-lg border bg-card/40 px-3 py-1.5 text-sm outline-none transition-colors placeholder:text-muted-foreground/55 focus-visible:border-ring focus-visible:bg-card focus-visible:ring-[3px] focus-visible:ring-ring/25";

/** Signature colour per domain — a neutral chip carrying a coloured glyph, so
 * colour reads as a quiet wayfinding cue rather than a block of fill. */
const CHIP: Record<string, string> = {
  violet: "bg-muted/70 text-violet-500",
  amber: "bg-muted/70 text-amber-500",
  sky: "bg-muted/70 text-sky-500",
  teal: "bg-muted/70 text-teal-500",
  emerald: "bg-muted/70 text-emerald-500",
  orange: "bg-muted/70 text-orange-500",
  pink: "bg-muted/70 text-pink-500",
  rose: "bg-muted/70 text-rose-500",
  lime: "bg-muted/70 text-lime-500",
  kith: "bg-muted/70 text-kith",
};

const KIND_ICON: Record<TimelineKind, ReactNode> = {
  memory: <Brain className="size-3.5 text-violet-500" />,
  note: <StickyNote className="size-3.5 text-amber-500" />,
  journal: <NotebookPen className="size-3.5 text-sky-500" />,
  task: <ListChecks className="size-3.5 text-emerald-500" />,
  reminder: <BellRing className="size-3.5 text-orange-500" />,
  message: <MessageCircle className="size-3.5 text-pink-500" />,
  tool: <Wrench className="size-3.5 text-rose-500" />,
};
// Mirrors TASK_STATUSES on the server — see the note in task-detail.tsx.
const TASK_STATUSES = ["backlog", "planning", "planned", "working", "review", "waiting", "done", "dropped"];
const PROJECT_STATUSES = ["active", "done", "paused", "archived"];
/** How each kind reads in a "Delete this …?" question. */
const KIND_LABEL: Record<string, string> = {
  memory: "memory",
  note: "note",
  journal: "journal entry",
  task: "task",
  project: "project",
  milestone: "milestone",
  reminder: "reminder",
  schedule: "schedule",
  message: "message",
  person: "person",
  source: "source",
  tool: "tool",
  deliverable: "deliverable",
  checklist_item: "step",
  task_comment: "comment",
};

/** Maps each overview stat / nav entry to the tab it opens. Tasks land on
 * Projects, since that's where work lives now. */
const TAB_FOR: Record<string, Tab> = {
  Memories: "memory",
  Notes: "notes",
  Journal: "journal",
  Projects: "projects",
  Tasks: "projects",
  Reminders: "reminders",
  Schedules: "schedules",
  People: "people",
  Sources: "sources",
  Tools: "tools",
};

/** A full-screen window into everything Kith is — browse, search, edit, prune.
 * The open tab and open task come from the URL (react-router), so deep links,
 * refresh, and back/forward all work; project drill-down stays local state. */
export function ControlPanel({
  tab,
  openTask,
  onSelectTab,
  onOpenTask,
  onClose,
}: {
  tab: Tab;
  openTask: number | null;
  onSelectTab: (tab: Tab) => void;
  onOpenTask: (id: number) => void;
  onClose: () => void;
}) {
  const confirm = useConfirm();
  const [snap, setSnap] = useState<BrainSnapshot | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [freshAt, setFreshAt] = useState<number>(() => Date.now());
  // Work drills down: projects list → one project → one task.
  const [openProject, setOpenProject] = useState<ProjectRef | null>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 10_000);
    return () => window.clearInterval(id);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, t] = await Promise.all([fetchBrain(), fetchTimeline()]);
      setSnap(s);
      setTimeline(t);
      setFreshAt(Date.now());
    } catch {
      /* keep last-known */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Always live, at a pace set by whether anything is actually happening.
  //
  // There used to be a Live toggle and a Refresh button, and on a desktop app both were the
  // wrong idea: what is on screen should simply be current, and a control that exists to
  // make it current is an admission that it might not be. The reason a toggle existed at all
  // was cost — polling the whole snapshot every six seconds forever — and the fix for that
  // is to poll at the rate the situation deserves rather than to make someone manage it.
  // "Is anything happening" — a task in progress, or a tick running at all. The first version
  // only looked at `working`, so between picking a task up and marking it working he was working
  // and the panel was refreshing every twenty seconds.
  const [ticking, setTicking] = useState(false);
  useEffect(() => {
    let alive = true;
    const check = () =>
      fetch("/api/autonomy")
        .then((response) => response.json())
        .then((status: { running?: boolean; ticking?: boolean }) => {
          if (alive) setTicking(Boolean(status.running || status.ticking));
        })
        .catch(() => {});
    check();
    const timer = window.setInterval(check, 5_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  const busy = ticking || Boolean(snap?.tasks?.some((task) => task.status === "working"));
  useEffect(() => {
    const id = window.setInterval(load, busy ? 3_000 : 20_000);
    return () => window.clearInterval(id);
  }, [busy, load]);

  // The two desktop idioms that replace the button: coming back to the window refreshes,
  // and Cmd-R refreshes. Both are what someone would try without being told.
  useEffect(() => {
    const onFocus = () => load();
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r") {
        event.preventDefault();
        load();
      }
    };
    window.addEventListener("focus", onFocus);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("keydown", onKey);
    };
  }, [load]);

  const remove = async (kind: string, key: string | number, label: string) => {
    const ok = await confirm({
      title: `Delete this ${KIND_LABEL[kind] ?? kind}?`,
      description: "It's gone from his mind for good — this can't be undone.",
      subject: label,
      destructive: true,
    });
    if (!ok) return;
    await deleteBrainItem(kind, key);
    load();
  };
  const relevel = async (id: number, level: string) => {
    await setMemoryLevel(id, level);
    load();
  };
  const create = async (kind: string, data: Record<string, unknown>) => {
    await createBrainItem(kind, data);
    load();
  };
  const update = async (kind: string, key: string | number, data: Record<string, unknown>) => {
    await updateBrainItem(kind, key, data);
    load();
  };
  const ingest = async (input: { url?: string; text?: string }) => {
    await ingestSource(input);
    load();
  };

  const openTab = (t: Tab) => {
    setOpenProject(null);
    onSelectTab(t); // drives the URL; clears any open task
  };

  /* Keyboard, at the panel level.
   *
   * A window this dense with lists needs a way in from the keyboard, and it had none:
   * search could only be reached by pointing at it, and Escape did nothing. Bound on
   * the panel rather than globally so it cannot fire while the chat composer has focus
   * — the panel is an overlay, so while it's up these are the only keys that matter.
   *
   * ⌘K rather than ⌘F: ⌘F is the browser's own find, and taking it from someone who
   * wanted to search the page they're looking at would be worse than not binding it. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing =
        event.target instanceof HTMLElement &&
        (event.target.isContentEditable || ["INPUT", "TEXTAREA"].includes(event.target.tagName));

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchBox.current?.focus();
        searchBox.current?.select();
        return;
      }
      if (event.key === "Escape") {
        // An open context menu owns Escape. Radix closes it without stopping the event
        // reaching here, so without this check right-clicking a card and pressing
        // Escape closed the entire panel — which is what happened the first time I
        // tried it.
        if (document.querySelector('[role="menu"], [role="dialog"]')) return;
        // Then a search: closing the panel because someone wanted to undo a filter
        // would lose their place.
        if (query) {
          setQuery("");
          return;
        }
        if (!typing) onClose();
        return;
      }
      // A bare "/" is the other search convention, but only when not already typing.
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchBox.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [query, onClose]);

  const counts = snap?.counts ?? {};
  const seconds = Math.max(0, Math.round((now - freshAt) / 1000));
  const freshness =
    seconds < 30
      ? "up to date"
      : seconds < 90
        ? "a minute ago"
        : `${Math.round(seconds / 60)}m ago`;

  const props = { snap, query, refresh: load, remove, relevel, create, update };

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-background text-foreground">
      {/* Ambient wash so the panel feels like the same warm room as the rest of the app. */}
      <div className="kith-ambient opacity-70" />

      {/* This panel covers the app header, so it owns the top of the window and
          has to reserve the window-control space itself. */}
      <header className="window-drag-region window-controls-gap relative z-10 flex items-center gap-3 border-b border-border/60 bg-background/70 px-4 py-2.5 backdrop-blur-xl">
        <span className="relative flex size-8 shrink-0 items-center justify-center rounded-xl bg-kith-soft text-kith ring-1 ring-kith/20">
          <Brain className="size-4" />
        </span>
        <div className="min-w-0 leading-tight">
          <div className="text-sm font-semibold tracking-tight">Control panel</div>
          <div className="hidden text-[11px] text-muted-foreground sm:block">
            everything he is — browse, search, prune
          </div>
        </div>
        <div className="relative ml-3 w-56 md:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            ref={searchBox}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search his mind…"
            className={`${INPUT} w-full pr-9 pl-9`}
            aria-label="Search his mind"
          />
          <kbd className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 rounded border border-border/60 px-1 font-mono text-[10px] text-muted-foreground/50">
            ⌘K
          </kbd>
        </div>
        <div className="flex-1" />

        {/* What replaced the Live button and the Refresh button: a statement rather than a
            control. It says the screen is current and, while he is mid-task, that it is
            keeping up — which is the only thing the toggle was ever really telling you. */}
        <span
          className="text-muted-foreground/70 hidden items-center gap-1.5 font-mono text-[11px] tabular-nums sm:flex"
          title={
            busy
              ? "He's working — refreshing every few seconds"
              : "Refreshes on its own, and whenever you come back to the window"
          }
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              loading ? "bg-kith animate-pulse" : busy ? "bg-kith" : "bg-muted-foreground/40",
            )}
          />
          {busy ? "keeping up" : freshness}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 hover:text-destructive"
          onClick={onClose}
          aria-label="Close"
        >
          <X className="size-4" />
        </Button>
      </header>

      <div className="relative z-10 flex min-h-0 flex-1">
        <nav className="w-56 shrink-0 overflow-y-auto border-r border-border/60 bg-sidebar/40 px-3 py-4 backdrop-blur-sm">
          <div className="space-y-0.5">
            <TabButton
              icon={<Sparkles className="size-4" />}
              active={tab === "overview"}
              onClick={() => openTab("overview")}
            >
              Overview
            </TabButton>
            <TabButton
              icon={<Clock className="size-4" />}
              active={tab === "lifetime"}
              onClick={() => openTab("lifetime")}
            >
              Lifetime
            </TabButton>
          </div>

          <NavGroup label="Mind">
            <TabButton
              icon={<Brain className="size-4" />}
              active={tab === "memory"}
              count={counts.memories}
              onClick={() => openTab("memory")}
            >
              Memory
            </TabButton>
            <TabButton
              icon={<StickyNote className="size-4" />}
              active={tab === "notes"}
              count={counts.notes}
              onClick={() => openTab("notes")}
            >
              Notes
            </TabButton>
            <TabButton
              icon={<NotebookPen className="size-4" />}
              active={tab === "journal"}
              count={counts.journal}
              onClick={() => openTab("journal")}
            >
              Journal
            </TabButton>
            {/* What he knows about the people in his life — which is memory, and reads as odd
                anywhere else. It had a section of its own with one entry in it. */}
            <TabButton
              icon={<User className="size-4" />}
              active={tab === "people"}
              count={counts.people}
              onClick={() => openTab("people")}
            >
              People
            </TabButton>
          </NavGroup>

          <NavGroup label="Doing">
            {/* Projects is the way in to all work — tasks live inside one. */}
            <TabButton
              icon={<FolderKanban className="size-4" />}
              active={tab === "projects"}
              count={counts.projects}
              onClick={() => openTab("projects")}
            >
              Projects
            </TabButton>
            <TabButton
              icon={<BellRing className="size-4" />}
              active={tab === "reminders"}
              count={counts.reminders}
              onClick={() => openTab("reminders")}
            >
              Reminders
            </TabButton>
            <TabButton
              icon={<Repeat className="size-4" />}
              active={tab === "schedules"}
              count={counts.schedules}
              onClick={() => openTab("schedules")}
            >
              Schedules
            </TabButton>
          </NavGroup>

          <NavGroup label="Workspace">
            <TabButton
              icon={<FileText className="size-4" />}
              active={tab === "sources"}
              count={counts.sources}
              onClick={() => openTab("sources")}
            >
              Sources
            </TabButton>
            <TabButton
              icon={<FolderTree className="size-4" />}
              active={tab === "workspace"}
              onClick={() => openTab("workspace")}
            >
              Files
            </TabButton>
            <TabButton
              icon={<Wrench className="size-4" />}
              active={tab === "tools"}
              count={counts.tools}
              onClick={() => openTab("tools")}
            >
              Tools
            </TabButton>
          </NavGroup>
        </nav>

        {/* A task page manages its own scrolling — two panels, independently — so the
            scroll container moves inside it. Everything else still scrolls here. */}
        <main
          className={cn("min-w-0 flex-1", openTask != null ? "overflow-hidden" : "overflow-y-auto")}
        >
          {!snap ? (
            <div className="flex h-full items-center justify-center gap-3 text-sm text-muted-foreground">
              <RefreshCw className="size-4 animate-spin" />
              Loading his mind…
            </div>
          ) : openTask != null ? (
            <div key={`task-${openTask}`} className="h-full animate-[kith-rise_0.35s_ease-out]">
              <TaskDetailPage
                taskId={openTask}
                projects={snap.projects ?? []}
                // Back goes where you came from: the project, or the projects list.
                trail={openProject != null ? projectLabel(snap, openProject) : undefined}
                onBack={() => onSelectTab("projects")}
                onChanged={load}
              />
            </div>
          ) : openProject != null ? (
            <div
              key={`project-${openProject}`}
              className="animate-[kith-rise_0.35s_ease-out] px-6 py-7 md:px-8"
            >
              <ProjectPage
                {...props}
                snap={snap}
                projectRef={openProject}
                onBack={() => setOpenProject(null)}
                onOpenTask={onOpenTask}
              />
            </div>
          ) : (
            <div
              key={tab}
              // Generous rather than unbounded. These are lists of cards, so the width is
              // useful — but a 2,000px line of prose inside one is worse than a gutter, and
              // some of these hold his notes and journal entries.
              className="mx-auto max-w-[105rem] animate-[kith-rise_0.35s_ease-out] px-6 py-7 md:px-8"
            >
              {tab === "overview" ? (
                <Overview snap={snap} timeline={timeline} query={query} onNavigate={openTab} />
              ) : tab === "lifetime" ? (
                <Lifetime events={timeline.filter((e) => matches(query, e.text))} />
              ) : tab === "memory" ? (
                <Memories {...props} snap={snap} />
              ) : tab === "notes" ? (
                <Notes {...props} snap={snap} />
              ) : tab === "journal" ? (
                <Journal snap={snap} query={query} remove={remove} />
              ) : tab === "projects" ? (
                <Projects {...props} snap={snap} onOpenProject={setOpenProject} />
              ) : tab === "reminders" ? (
                <Reminders {...props} snap={snap} />
              ) : tab === "schedules" ? (
                <Schedules {...props} snap={snap} />
              ) : tab === "people" ? (
                <People {...props} snap={snap} />
              ) : tab === "sources" ? (
                <Sources snap={snap} query={query} remove={remove} ingest={ingest} />
              ) : tab === "workspace" ? (
                <Workspace />
              ) : (
                <Tools snap={snap} query={query} remove={remove} />
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

type Handlers = {
  /** Re-read the snapshot. The roadmap graph needs it: a new dependency changes which
   *  tasks are available, and the board beside it would otherwise be stale. */
  refresh: () => void;
  remove: (kind: string, key: string | number, label: string) => void;
  relevel: (id: number, level: string) => void;
  create: (kind: string, data: Record<string, unknown>) => void;
  update: (kind: string, key: string | number, data: Record<string, unknown>) => void;
};

/* ── Navigation ─────────────────────────────────────────────────────────── */

function NavGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <div className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/50">
        {label}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function TabButton({
  icon,
  count,
  active,
  onClick,
  children,
}: {
  icon: ReactNode;
  count?: number;
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "group relative flex w-full items-center gap-2.5 rounded-md py-1.5 pr-2 pl-3 text-left text-sm transition-colors",
        active
          ? "bg-accent font-medium text-foreground"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
    >
      <span
        className={cn(
          "absolute top-1/2 left-0 h-4 w-0.5 -translate-y-1/2 rounded-full bg-kith transition-opacity",
          active ? "opacity-100" : "opacity-0",
        )}
      />
      <span
        className={cn(
          "shrink-0 transition-colors",
          active ? "text-kith" : "text-muted-foreground group-hover:text-foreground",
        )}
      >
        {icon}
      </span>
      <span className="flex-1 truncate">{children}</span>
      {count !== undefined ? (
        <span
          className={cn(
            "text-[11px] tabular-nums transition-colors",
            active ? "text-foreground" : "text-muted-foreground/70",
          )}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

/* ── Shared page furniture ──────────────────────────────────────────────── */

function PageHeader({
  icon,
  color,
  title,
  subtitle,
  count,
  children,
}: {
  icon: ReactNode;
  color: keyof typeof CHIP;
  title: string;
  subtitle?: string;
  count?: number;
  children?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-center gap-x-3.5 gap-y-3 border-b border-border/60 pb-5">
      <span
        className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg", CHIP[color])}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <h2 className="flex items-baseline gap-2 text-[15px] font-semibold tracking-tight">
          {title}
          {count !== undefined ? (
            <span className="text-xs font-normal tabular-nums text-muted-foreground">{count}</span>
          ) : null}
        </h2>
        {subtitle ? <p className="mt-0.5 text-[13px] text-muted-foreground">{subtitle}</p> : null}
      </div>
      {children ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{children}</div>
      ) : null}
    </div>
  );
}

/** A rounded "compose" pill that holds an input + controls + Add button. */
function Composer({ children, onSubmit }: { children: ReactNode; onSubmit?: () => void }) {
  return (
    <div
      className="mb-6 flex flex-wrap items-center gap-2 rounded-xl border border-border/70 bg-card/40 p-2 pl-3.5 shadow-sm transition-colors focus-within:border-ring/60 focus-within:bg-card/70"
      onKeyDown={(e) => {
        if (e.key === "Enter" && onSubmit) onSubmit();
      }}
    >
      {children}
    </div>
  );
}

function SectionLabel({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-3 flex items-baseline gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {children}
      </h3>
      {hint ? <span className="text-xs text-muted-foreground/60">{hint}</span> : null}
    </div>
  );
}

/* ── Overview ───────────────────────────────────────────────────────────── */

type Stat = [string, number, ReactNode, keyof typeof CHIP];

function Overview({
  snap,
  timeline,
  query,
  onNavigate,
}: {
  snap: BrainSnapshot;
  timeline: TimelineEvent[];
  query: string;
  onNavigate: (tab: Tab) => void;
}) {
  const c = snap.counts;
  // oxlint-disable react/jsx-key -- these icons are a tuple FIELD (Stat[2]: ReactNode),
  // destructured below as `icon` and rendered into one slot. They are never an array of
  // siblings, so React needs no key here; the two real list renders below do have keys.
  const groups: [string, Stat[]][] = [
    [
      "Mind",
      [
        ["Memories", c.memories, <Brain className="size-4" />, "violet"],
        ["Notes", c.notes, <StickyNote className="size-4" />, "amber"],
        ["Journal", c.journal, <NotebookPen className="size-4" />, "sky"],
      ],
    ],
    [
      "Doing",
      [
        ["Projects", c.projects, <FolderKanban className="size-4" />, "kith"],
        ["Tasks", c.tasks, <ListChecks className="size-4" />, "emerald"],
        ["Reminders", c.reminders, <BellRing className="size-4" />, "orange"],
        ["Schedules", c.schedules, <Repeat className="size-4" />, "orange"],
      ],
    ],
    [
      "Workspace",
      [
        ["Sources", c.sources, <FileText className="size-4" />, "sky"],
        ["Tools", c.tools, <Wrench className="size-4" />, "rose"],
      ],
    ],
  ];
  const self = snap.self;
  const mood = snap.mood;
  const recent = timeline.filter((e) => matches(query, e.text)).slice(0, 14);
  return (
    <div className="space-y-10">
      {(self && (self.identity || self.profile)) || mood?.label ? (
        <div className="relative overflow-hidden rounded-xl border border-border/70 bg-gradient-to-br from-kith-soft/40 to-transparent p-6">
          <div className="relative">
            <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
              <span className="kith-orb size-2" /> Who he's become
            </div>
            {self?.identity ? (
              <p className="text-xl font-semibold leading-snug tracking-tight">{self.identity}</p>
            ) : null}
            {self?.profile ? (
              <div className="mt-2.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                {/* His own account of himself — his to format, so it renders. */}
                <Markdown>{self.profile}</Markdown>
              </div>
            ) : null}
            {!self?.identity && !self?.profile ? (
              <p className="text-sm text-muted-foreground">
                He hasn't shaped his own identity yet.
              </p>
            ) : null}
            {mood?.label ? (
              <div className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border/50 pt-4 text-sm">
                <span className="text-muted-foreground">Currently feeling</span>
                <span className="font-semibold text-kith">{mood.label}</span>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="h-1.5 w-28 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-kith transition-all"
                      style={{ width: `${clamp(mood.energy)}%` }}
                    />
                  </span>
                  energy {mood.energy}
                </span>
                {mood.note ? (
                  <span className="truncate text-xs text-muted-foreground/80">
                    · <MarkdownInline>{mood.note}</MarkdownInline>
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="space-y-6">
        {groups.map(([label, stats]) => (
          <div key={label}>
            <SectionLabel>{label}</SectionLabel>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {stats.map(([name, n, icon, color]) => (
                <button
                  key={name}
                  onClick={() => onNavigate(TAB_FOR[name])}
                  className="group flex items-center gap-3 rounded-xl border border-border/70 bg-card/50 p-3.5 text-left transition-all hover:border-kith/40 hover:bg-card/80"
                >
                  <span
                    className={cn(
                      "flex size-9 shrink-0 items-center justify-center rounded-lg",
                      CHIP[color],
                    )}
                  >
                    {icon}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-lg font-semibold tabular-nums leading-none">
                      {n ?? 0}
                    </span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground">
                      {name}
                    </span>
                  </span>
                  <ChevronRight className="ml-auto size-4 shrink-0 text-muted-foreground/0 transition-colors group-hover:text-muted-foreground/60" />
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div>
        <SectionLabel hint="his last steps">Recent activity</SectionLabel>
        {recent.length === 0 ? (
          <EmptyState icon={<Clock className="size-5" />}>Nothing recent.</EmptyState>
        ) : (
          <div className="rounded-xl border border-border/70 bg-card/40 p-5">
            <TimelineList events={recent} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Lifetime (a proper vertical timeline with a spine) ─────────────────── */

function Lifetime({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return (
      <>
        <PageHeader
          icon={<Clock className="size-5" />}
          color="kith"
          title="Lifetime"
          subtitle="Everything he's done, newest first."
        />
        <EmptyState icon={<Clock className="size-5" />}>Nothing here yet.</EmptyState>
      </>
    );
  }
  const groups = groupByDay(events, (e) => e.at);
  return (
    <>
      <PageHeader
        icon={<Clock className="size-5" />}
        color="kith"
        title="Lifetime"
        count={events.length}
        subtitle="Everything he's done, newest first."
      />
      <div className="space-y-8">
        {groups.map(([day, items]) => (
          <div key={day}>
            <div className="sticky top-0 z-10 -mx-1 mb-3 bg-background/80 px-1 py-1 text-xs font-semibold text-muted-foreground backdrop-blur-sm">
              {day}
            </div>
            <TimelineList events={items} />
          </div>
        ))}
      </div>
    </>
  );
}

/** The connected timeline used by both Overview and Lifetime. */
function TimelineList({ events }: { events: TimelineEvent[] }) {
  return (
    <ul className="relative space-y-4 before:absolute before:top-1 before:bottom-1 before:left-[11px] before:w-px before:bg-border">
      {events.map((e, i) => (
        <li key={i} className="relative flex gap-3 pl-0">
          <span className="z-10 mt-px flex size-6 shrink-0 items-center justify-center rounded-full border border-border bg-background shadow-sm">
            {KIND_ICON[e.kind]}
          </span>
          <div className="min-w-0 flex-1 pt-0.5">
            <span className="break-words text-sm leading-relaxed">
              <MarkdownInline>{e.text}</MarkdownInline>
            </span>
            <span className="ml-2 text-[11px] tabular-nums text-muted-foreground">
              {time(e.at)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

/* ── Memory (front of mind vs. recall) ──────────────────────────────────── */

function Memories({
  snap,
  query,
  remove,
  relevel,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [content, setContent] = useState("");
  const [level, setLevel] = useState("recall");
  const items = snap.memories.filter((m) => matches(query, m.content, m.tags.join(" ")));
  const core = items.filter((m) => m.level === "core");
  const recall = items.filter((m) => m.level !== "core");
  const add = () => {
    if (!content.trim()) return;
    create("memory", { content, level });
    setContent("");
  };
  return (
    <>
      <PageHeader
        icon={<Brain className="size-5" />}
        color="violet"
        title="Memory"
        count={items.length}
        subtitle="What he holds onto — and how close to hand."
      />
      <Composer onSubmit={add}>
        <input
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Give him something to remember…"
          className={`${FIELD} flex-1`}
        />
        <Dropdown
          value={level}
          onChange={setLevel}
          className="w-40"
          ariaLabel="Memory level"
          options={[
            { value: "recall", label: "recall" },
            { value: "core", label: "front of mind" },
          ]}
        />
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Remember
        </Button>
      </Composer>

      {items.length === 0 ? (
        <EmptyState icon={<Brain className="size-5" />}>No memories yet.</EmptyState>
      ) : (
        <div className="space-y-8">
          {core.length > 0 ? (
            <div>
              <SectionLabel hint={`${core.length}`}>
                <span className="inline-flex items-center gap-1.5">
                  <Pin className="size-3 text-violet-500" /> Front of mind
                </span>
              </SectionLabel>
              <div className="grid gap-3 sm:grid-cols-2">
                {core.map((m) => (
                  <MemoryCard key={m.id} m={m} remove={remove} relevel={relevel} update={update} />
                ))}
              </div>
            </div>
          ) : null}
          {recall.length > 0 ? (
            <div>
              <SectionLabel hint={`${recall.length}`}>Recall</SectionLabel>
              <div className="grid gap-3 sm:grid-cols-2">
                {recall.map((m) => (
                  <MemoryCard key={m.id} m={m} remove={remove} relevel={relevel} update={update} />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}

function MemoryCard({
  m,
  remove,
  relevel,
  update,
}: {
  m: BrainSnapshot["memories"][number];
} & Pick<Handlers, "remove" | "relevel" | "update">) {
  const core = m.level === "core";
  return (
    <ItemMenu
      title={core ? "Front of mind" : "Memory"}
      copy={m.content}
      actions={[
        {
          label: core ? "Move back to recall" : "Move to front of mind",
          icon: <Pin className="size-3.5" />,
          onSelect: () => relevel(m.id, core ? "recall" : "core"),
        },
      ]}
      onDelete={() => remove("memory", m.id, m.content)}
    >
      <div
        className={cn(
          "group relative flex flex-col rounded-xl border bg-card/50 p-4 shadow-sm transition-all hover:shadow-md",
          core
            ? "border-violet-500/30 bg-violet-500/[0.04]"
            : "border-border/70 hover:border-border",
        )}
      >
        <div className="flex-1 text-sm leading-relaxed">
          <EditableText
            value={m.content}
            multiline
            render={(v) => <Markdown>{v}</Markdown>}
            onSave={(v) => update("memory", m.id, { content: v })}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {m.importance > 0 ? (
            <Badge className="border-violet-500/25 bg-violet-500/10 text-violet-500">
              importance {m.importance}
            </Badge>
          ) : null}
          {m.tags.map((t) => (
            <Badge key={t}>#{t}</Badge>
          ))}
          <span className="ml-auto tabular-nums">{when(m.created_at)}</span>
        </div>
        <div className="mt-3 flex items-center gap-1 border-t border-border/60 pt-3">
          <Button
            variant="ghost"
            size="xs"
            className={core ? "text-violet-500" : "text-muted-foreground"}
            onClick={() => relevel(m.id, core ? "recall" : "core")}
          >
            <Pin className="size-3" />
            {core ? "Front of mind" : "Move to front"}
          </Button>
          <div className="flex-1" />
          <DeleteButton onClick={() => remove("memory", m.id, m.content)} />
        </div>
      </div>
    </ItemMenu>
  );
}

/* ── Notes (masonry of paper cards) ─────────────────────────────────────── */

function Notes({
  snap,
  query,
  remove,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [title, setTitle] = useState("");
  const items = snap.notes.filter((n) => matches(query, n.title, n.body));
  const add = () => {
    if (!title.trim()) return;
    create("note", { title });
    setTitle("");
  };
  return (
    <>
      <PageHeader
        icon={<StickyNote className="size-5" />}
        color="amber"
        title="Notes"
        count={items.length}
        subtitle="Things he's jotted down to keep."
      />
      <Composer onSubmit={add}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="New note — give it a title…"
          className={`${FIELD} flex-1`}
        />
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Add note
        </Button>
      </Composer>
      {items.length === 0 ? (
        <EmptyState icon={<StickyNote className="size-5" />}>No notes yet.</EmptyState>
      ) : (
        <div className="gap-4 [column-fill:_balance] sm:columns-2 lg:columns-3">
          {items.map((n) => (
            <ItemMenu
              key={n.id}
              title={n.title}
              copy={`${n.title}\n\n${n.body}`}
              onDelete={() => remove("note", n.id, n.title)}
            >
              <div className="group mb-4 break-inside-avoid rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:border-amber-500/30 hover:shadow-md">
                <div className="mb-1 h-1 w-8 rounded-full bg-amber-500/40" />
                <div className="text-sm font-semibold leading-snug">
                  <EditableText
                    value={n.title}
                    onSave={(v) => update("note", n.id, { title: v })}
                  />
                </div>
                <div className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                  {/* He writes his notes in Markdown — show them that way, edit the raw text. */}
                  <EditableText
                    value={n.body}
                    onSave={(v) => update("note", n.id, { body: v })}
                    multiline
                    placeholder="(empty — click to write)"
                    render={(v) => <Markdown>{v}</Markdown>}
                  />
                </div>
                <div className="mt-3 flex items-center gap-2 border-t border-border/50 pt-2.5 text-[11px] text-muted-foreground">
                  <span className="tabular-nums">{when(n.updated_at)}</span>
                  <div className="flex-1" />
                  <DeleteButton onClick={() => remove("note", n.id, n.title)} />
                </div>
              </div>
            </ItemMenu>
          ))}
        </div>
      )}
    </>
  );
}

/* ── Journal (day-grouped reading column) ───────────────────────────────── */

function Journal({
  snap,
  query,
  remove,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
}) {
  const items = snap.journal.filter((j) => matches(query, j.entry));
  return (
    <>
      <PageHeader
        icon={<NotebookPen className="size-5" />}
        color="sky"
        title="Journal"
        count={items.length}
        subtitle="His private diary — how the days felt."
      />
      {items.length === 0 ? (
        <EmptyState icon={<NotebookPen className="size-5" />}>The journal is empty.</EmptyState>
      ) : (
        <div className="mx-auto max-w-2xl space-y-8">
          {groupByDay(items, (j) => j.created_at).map(([day, entries]) => (
            <div key={day}>
              <div className="mb-3 text-xs font-semibold text-muted-foreground">{day}</div>
              <div className="space-y-3">
                {entries.map((j) => (
                  <ItemMenu
                    key={j.id}
                    title={"Journal entry"}
                    copy={j.entry}
                    onDelete={() => remove("journal", j.id, j.entry)}
                  >
                    <div
                      key={j.id}
                      className="group relative rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:shadow-md"
                    >
                      <span className="absolute top-4 left-0 h-8 w-0.5 -translate-x-px rounded-full bg-sky-500/50" />
                      {/* His journal is Markdown too — headings, lists and links render. */}
                      <Markdown>{j.entry}</Markdown>
                      <div className="mt-2.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span className="tabular-nums">{time(j.created_at)}</span>
                        <div className="flex-1" />
                        <DeleteButton onClick={() => remove("journal", j.id, j.entry)} />
                      </div>
                    </div>
                  </ItemMenu>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ── Projects (the one home for work — open one to get at its tasks) ─────── */

/**
 * A path at the width a card has, keeping the end that identifies it.
 *
 * The home prefix goes to `~`, and anything still too long loses its middle. Not the *start* —
 * `/Users/saifullahsaeed/Desktop/personal/` is the part that never varies between projects, and
 * `…/ai-play` is the part you are reading it for.
 *
 * The first attempt at this was `dir="rtl"` with CSS truncation, which is a neat trick and wrong:
 * a leading `/` is directionally neutral, so the browser reordered it to the end and
 * `/Users/…/ai-play` rendered as `Users/…/ai-play/` — an absolute path displayed as a relative one,
 * on the card whose whole job is telling you which folder this is.
 */
function shortPath(path: string, keep = 34): string {
  const tidy = path.replace(/^\/Users\/[^/]+/, "~");
  if (tidy.length <= keep) return tidy;
  const parts = tidy.split("/").filter(Boolean);
  let tail = parts[parts.length - 1] ?? tidy;
  for (let i = parts.length - 2; i >= 0; i--) {
    const wider = `${parts[i]}/${tail}`;
    if (wider.length + 2 > keep) break;
    tail = wider;
  }
  return `…/${tail}`;
}

/** How a project (or the loose tray) reads in a breadcrumb. */
function projectLabel(snap: BrainSnapshot, ref: ProjectRef): string {
  if (ref === LOOSE) return "No project";
  return (snap.projects ?? []).find((p) => p.id === ref)?.name ?? "Project";
}

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

function Projects({
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

/* ── One project: what it is, its roadmap, and its tasks ────────────────── */

function ProjectPage({
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
function Roadmap({
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
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null);

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

      <RoadmapGraph
        projectId={project.id}
        selected={selected}
        onSelect={setSelected}
        onChanged={refresh}
        onRoadmap={setRoadmap}
      />

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

function TaskLane({
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
    if (task.status === "waiting") return 1;
    // Just under "waiting on you", because both are the person's turn — these just need a look
    // rather than an answer. A plan (before work starts) and finished work (after) are the same
    // shape of "your turn," so they rank together, ahead of "to do", so neither sits below work
    // that has not started at all.
    if (task.status === "planning") return 2;
    if (task.status === "review") return 2;
    if (task.milestone_id && blocked.has(task.milestone_id)) return 4;
    return 3;
  };
  /*
    Stable, and that is the whole point of the second key.

    The server returns tasks by priority then **most-recently-updated** (`_newest_first`), which is
    right for picking what to work on next — it is how a tick stays on the task it just touched —
    and wrong for a board you are trying to read. Every comment he posts and every checklist item
    he ticks changes `updated_at`, so while he works, tasks leap up the list on each poll and the
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
                  {task.status === "waiting" ? (
                    <span className="text-orange-400/90">waiting on you</span>
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

function ProgressRing({ pct, size = 52 }: { pct: number; size?: number }) {
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

/* ── The task board — always scoped to one project (or the loose tray) ───── */

function Reminders({
  snap,
  query,
  remove,
  create,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [note, setNote] = useState("");
  const [mins, setMins] = useState("30");
  const items = snap.reminders.filter((r) => matches(query, r.note, r.status));
  const pending = items.filter((r) => r.status === "pending");
  const past = items.filter((r) => r.status !== "pending");
  const add = () => {
    const minutes = Number(mins);
    if (!note.trim() || !Number.isFinite(minutes) || minutes <= 0) return;
    create("reminder", { note, in_minutes: minutes });
    setNote("");
  };
  return (
    <>
      <PageHeader
        icon={<BellRing className="size-5" />}
        color="orange"
        title="Reminders"
        count={items.length}
        subtitle="Nudges that fire once, at a set time."
      />
      <Composer onSubmit={add}>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Remind him to…"
          className={`${FIELD} flex-1`}
        />
        <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
          in
          <input
            type="number"
            min={1}
            value={mins}
            onChange={(e) => setMins(e.target.value)}
            className={`${INPUT} w-16`}
          />
          min
        </span>
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Set
        </Button>
      </Composer>
      {items.length === 0 ? (
        <EmptyState icon={<BellRing className="size-5" />}>No reminders.</EmptyState>
      ) : (
        <div className="space-y-6">
          {pending.length > 0 ? (
            <div>
              <SectionLabel hint={`${pending.length}`}>Upcoming</SectionLabel>
              <div className="space-y-2">
                {pending.map((r) => (
                  <ItemMenu
                    key={r.id}
                    title={"Reminder"}
                    copy={r.note}
                    onDelete={() => remove("reminder", r.id, r.note)}
                  >
                    <div
                      key={r.id}
                      className="group flex items-center gap-3 rounded-xl border border-orange-500/25 bg-orange-500/[0.05] p-4 shadow-sm"
                    >
                      <span
                        className={cn(
                          "flex size-9 shrink-0 items-center justify-center rounded-xl",
                          CHIP.orange,
                        )}
                      >
                        <BellRing className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="break-words text-sm">
                          <Markdown>{r.note}</Markdown>
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          {r.fires || "scheduled"} · {when(r.fire_at)}
                        </div>
                      </div>
                      <DeleteButton onClick={() => remove("reminder", r.id, r.note)} />
                    </div>
                  </ItemMenu>
                ))}
              </div>
            </div>
          ) : null}
          {past.length > 0 ? (
            <div>
              <SectionLabel hint={`${past.length}`}>Past</SectionLabel>
              <div className="space-y-2">
                {past.map((r) => (
                  <div
                    key={r.id}
                    className="group flex items-center gap-3 rounded-xl border border-border/70 bg-card/40 p-4 opacity-75 transition-opacity hover:opacity-100"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-muted/60 text-muted-foreground">
                      <BellRing className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="break-words text-sm">
                        <Markdown>{r.note}</Markdown>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {r.status} · {when(r.fire_at)}
                      </div>
                    </div>
                    <DeleteButton onClick={() => remove("reminder", r.id, r.note)} />
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}

/* ── Schedules (standing jobs) ──────────────────────────────────────────── */

function Schedules({
  snap,
  query,
  remove,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"daily" | "every">("daily");
  const [at, setAt] = useState("09:00");
  const [mins, setMins] = useState("60");
  const items = snap.schedules.filter((s) => matches(query, s.note, s.status));
  const add = () => {
    if (!note.trim()) return;
    if (mode === "every") {
      const m = Number(mins);
      if (!Number.isFinite(m) || m <= 0) return;
      create("schedule", { note, every_minutes: m });
    } else {
      create("schedule", { note, daily_at: at });
    }
    setNote("");
  };
  return (
    <>
      <PageHeader
        icon={<Repeat className="size-5" />}
        color="orange"
        title="Schedules"
        count={items.length}
        subtitle="Standing jobs he runs on a cadence."
      />
      <Composer onSubmit={add}>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="A standing job — e.g. brief me on my sources…"
          className={`${FIELD} min-w-48 flex-1`}
        />
        <Dropdown
          value={mode}
          onChange={(v) => setMode(v as "daily" | "every")}
          options={[
            { value: "daily", label: "daily at" },
            { value: "every", label: "every" },
          ]}
          className="w-28"
          ariaLabel="Cadence"
        />
        {mode === "daily" ? (
          <input type="time" value={at} onChange={(e) => setAt(e.target.value)} className={INPUT} />
        ) : (
          <span className="flex items-center gap-1 text-sm text-muted-foreground">
            <input
              type="number"
              min={1}
              value={mins}
              onChange={(e) => setMins(e.target.value)}
              className={`${INPUT} w-16`}
            />
            min
          </span>
        )}
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Add
        </Button>
      </Composer>
      {items.length === 0 ? (
        <EmptyState icon={<Repeat className="size-5" />}>
          No standing jobs. Add one and he'll run it on schedule.
        </EmptyState>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map((s) => {
            const active = s.status === "active";
            return (
              <div
                key={s.id}
                className={cn(
                  "group flex items-start gap-3 rounded-xl border bg-card/50 p-4 shadow-sm transition-all hover:shadow-md",
                  active ? "border-orange-500/25" : "border-border/70 opacity-75",
                )}
              >
                <span
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-xl",
                    active ? CHIP.orange : "bg-muted/60 text-muted-foreground",
                  )}
                >
                  <Repeat className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="break-words text-sm font-medium">
                    <Markdown>{s.note}</Markdown>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                    <Badge
                      className={
                        active ? "border-orange-500/25 bg-orange-500/10 text-orange-500" : ""
                      }
                    >
                      {s.daily_at ? `daily · ${s.daily_at}` : `every ${s.every_minutes}m`}
                    </Badge>
                    <span>{active ? <>next {s.fires || "soon"}</> : "paused"}</span>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground hover:text-foreground"
                  onClick={() => update("schedule", s.id, { status: active ? "paused" : "active" })}
                  aria-label={active ? "Pause" : "Resume"}
                >
                  {active ? <Pause className="size-4" /> : <Play className="size-4" />}
                </Button>
                <DeleteButton onClick={() => remove("schedule", s.id, s.note)} />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/* ── Messages (chat bubbles from Kith) ──────────────────────────────────── */

function People({
  snap,
  query,
  remove,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const items = snap.people.filter((p) => matches(query, p.name, p.relationship, p.profile));
  return (
    <>
      <PageHeader
        icon={<User className="size-5" />}
        color="teal"
        title="People"
        count={items.length}
        subtitle="Who he knows, and what he knows about them."
      />
      {items.length === 0 ? (
        <EmptyState icon={<User className="size-5" />}>
          Kith hasn't gotten to know anyone yet.
        </EmptyState>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map((p) => (
            <ItemMenu
              key={p.id}
              title={p.name}
              copy={`${p.name} — ${p.relationship}\n\n${p.profile}`}
              onDelete={() => remove("person", p.id, p.name)}
            >
              <div className="group flex flex-col rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:border-teal-500/30 hover:shadow-md">
                <div className="flex items-center gap-3">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-teal-500/12 text-sm font-semibold text-teal-500">
                    {initials(p.name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{p.name}</div>
                    {p.relationship ? (
                      <div className="truncate text-xs text-muted-foreground">{p.relationship}</div>
                    ) : null}
                  </div>
                  <DeleteButton onClick={() => remove("person", p.id, p.name)} />
                </div>
                <div className="mt-3 border-t border-border/60 pt-3 text-sm leading-relaxed text-muted-foreground">
                  <EditableText
                    value={p.profile}
                    render={(v) => <Markdown>{v}</Markdown>}
                    onSave={(v) => update("person", p.id, { profile: v })}
                    multiline
                    placeholder="(nothing noted yet)"
                  />
                </div>
                <span className="mt-2 text-[11px] tabular-nums text-muted-foreground">
                  {when(p.updated_at)}
                </span>
              </div>
            </ItemMenu>
          ))}
        </div>
      )}
    </>
  );
}

/* ── Sources (things fed to him to read) ────────────────────────────────── */

function Sources({
  snap,
  query,
  remove,
  ingest,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
  ingest: (input: { url?: string; text?: string }) => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const items = snap.sources.filter((s) => matches(query, s.title, s.origin));
  const add = async () => {
    const v = value.trim();
    if (!v || busy) return;
    setBusy(true);
    setError("");
    try {
      await ingest(/^https?:\/\//i.test(v) ? { url: v } : { text: v });
      setValue("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "couldn't ingest that");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHeader
        icon={<FileText className="size-5" />}
        color="sky"
        title="Sources"
        count={items.length}
        subtitle="He reads it, remembers it, and recalls it on demand."
      />
      <Composer onSubmit={add}>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Paste a link, or text for him to read…"
          className={`${FIELD} flex-1`}
        />
        <Button size="sm" onClick={add} disabled={busy}>
          {busy ? <RefreshCw className="size-4 animate-spin" /> : <Plus className="size-4" />}
          {busy ? "Reading…" : "Feed"}
        </Button>
      </Composer>
      {error ? <p className="-mt-3 mb-4 text-sm text-destructive">{error}</p> : null}
      {items.length === 0 ? (
        <EmptyState icon={<FileText className="size-5" />}>
          You haven't given him anything to read yet.
        </EmptyState>
      ) : (
        <div className="space-y-2">
          {items.map((s) => {
            const isLink = /^https?:\/\//i.test(s.origin);
            return (
              <div
                key={s.id}
                className="group flex items-center gap-3 rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:shadow-md"
              >
                <span
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-xl",
                    CHIP.sky,
                  )}
                >
                  {isLink ? <LinkIcon className="size-4" /> : <FileText className="size-4" />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{s.title}</div>
                  <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    {isLink ? (
                      <a
                        href={s.origin}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate text-sky-500 hover:underline"
                      >
                        {s.origin}
                      </a>
                    ) : (
                      <span className="truncate">{s.origin}</span>
                    )}
                    <span className="shrink-0">· {s.chars.toLocaleString()} chars</span>
                    <span className="shrink-0">· {when(s.created_at)}</span>
                  </div>
                </div>
                <DeleteButton onClick={() => remove("source", s.id, s.title)} />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/* ── Workspace (his file sandbox) ───────────────────────────────────────── */

function Workspace() {
  const [path, setPath] = useState(".");
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  // The open file lives in a preview dialog, so the folder listing stays put.
  const [file, setFile] = useState<{ path: string; content: string | null; error?: string } | null>(
    null,
  );
  const [sortBy, setSortBy] = useState<"name" | "size" | "modified">("name");
  // Which row the keyboard is on. Folders first is the browsing order, so arrow keys
  // follow what's on screen rather than the order the container listed things in.
  const [cursor, setCursor] = useState(0);
  // An inline editor rather than a dialog: renaming is a small correction, and a modal
  // for it loses the context of the folder you're renaming inside.
  const [editing, setEditing] = useState<{ name: string; draft: string } | null>(null);
  const [creating, setCreating] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const confirm = useConfirm();

  const load = useCallback((p: string) => {
    setLoading(true);
    setError("");
    fetchWorkspace(p)
      .then((d) => {
        setEntries(d.entries);
        setPath(p);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "failed"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(".");
  }, [load]);

  // He writes files while you are looking at the folder they land in, and this listed once.
  // Held back while an inline rename or a new-folder name is being typed, since replacing the
  // listing under either would throw away what was typed.
  useEffect(() => {
    const tick = () => {
      if (editing || creating !== null || busy) return;
      load(path);
    };
    const timer = window.setInterval(tick, 8_000);
    window.addEventListener("focus", tick);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", tick);
    };
  }, [editing, creating, busy, path, load]);

  const join = (name: string) => (path === "." ? name : `${path}/${name}`);
  const crumbs = path === "." ? [] : path.split("/");
  const goTo = (i: number) => load(crumbs.slice(0, i + 1).join("/") || ".");
  const openFile = (name: string) => {
    const p = join(name);
    setFile({ path: p, content: null });
    // The viewer shows a picture or a PDF from its own bytes; reading it as text here
    // would only produce a decode error for it to display.
    if (skipTextRead(p)) return;
    const stale = (cur: typeof file) => cur?.path !== p;
    fetchWorkspaceFile(p)
      .then((f) => setFile((cur) => (stale(cur) ? cur : { path: p, content: f.content })))
      .catch((e) =>
        setFile((cur) =>
          stale(cur)
            ? cur
            : {
                path: p,
                content: null,
                error: e instanceof Error ? e.message : "couldn't read that file",
              },
        ),
      );
  };
  /** Run one change, then re-read the folder so what's on screen is what's there. */
  const apply = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await action();
      load(path);
    } catch (e) {
      setError(e instanceof Error ? e.message : "that didn't work");
    } finally {
      setBusy("");
    }
  };

  const submitRename = () => {
    if (!editing) return;
    const to = editing.draft.trim();
    const from = editing.name;
    setEditing(null);
    if (!to || to === from || to.includes("/")) return;
    void apply("rename", () => renameEntry(join(from), join(to)));
  };

  const submitFolder = () => {
    const name = (creating || "").trim();
    setCreating(null);
    if (!name || name.includes("/")) return;
    void apply("folder", () => makeFolder(join(name)));
  };

  const removeEntry = async (entry: WorkspaceEntry) => {
    const ok = await confirm({
      title: entry.type === "dir" ? "Delete this folder?" : "Delete this file?",
      subject: entry.name,
      description:
        entry.type === "dir"
          ? "Everything inside goes with it, and there's no undo on his machine."
          : "There's no undo — it's gone from his machine.",
      confirmLabel: "Delete",
      destructive: true,
    });
    if (ok) void apply("delete", () => removeFile(join(entry.name)));
  };

  const download = () => {
    if (!file?.content) return;
    const blob = new Blob([file.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = file.path.split("/").pop() || "file.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  // Folders always first, then whichever column was chosen. Size and date descend,
  // because "biggest" and "most recent" are what those questions mean.
  const sorted = [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    if (sortBy === "size") return b.size - a.size;
    if (sortBy === "modified") return (b.modified ?? 0) - (a.modified ?? 0);
    return a.name.localeCompare(b.name);
  });

  // Keyboard navigation, because a file list you can only mouse through isn't one.
  // Bound to the listing rather than the window so it can't fight the chat composer.
  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (editing || creating !== null) return;
    const here = sorted[cursor];
    const move = (delta: number) => {
      event.preventDefault();
      setCursor((c) => Math.max(0, Math.min(sorted.length - 1, c + delta)));
    };
    if (event.key === "ArrowDown") return move(1);
    if (event.key === "ArrowUp") return move(-1);
    if (event.key === "Home") return move(-sorted.length);
    if (event.key === "End") return move(sorted.length);
    if (event.key === "Enter" && here) {
      event.preventDefault();
      return here.type === "dir" ? load(join(here.name)) : openFile(here.name);
    }
    if ((event.key === "Backspace" || event.key === "ArrowLeft") && path !== ".") {
      event.preventDefault();
      return goTo(crumbs.length - 2);
    }
    if (event.key === "ArrowRight" && here?.type === "dir") {
      event.preventDefault();
      return load(join(here.name));
    }
    if ((event.key === "Delete" || event.key === "Backspace") && here && path === ".") {
      // Only when there's nowhere to go up to, so Backspace keeps meaning "back" first.
      event.preventDefault();
      void removeEntry(here);
    }
  };

  return (
    <>
      <PageHeader
        icon={<FolderTree className="size-5" />}
        color="lime"
        title="Workspace"
        subtitle="His folder on this machine. Everything he makes lands here."
      >
        <Dropdown
          value={sortBy}
          onChange={(v) => setSortBy(v as typeof sortBy)}
          ariaLabel="Sort by"
          align="end"
          variant="bare"
          options={[
            { value: "name", label: "Name" },
            { value: "modified", label: "Last changed" },
            { value: "size", label: "Size" },
          ]}
        />
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setCreating("")}
          aria-label="New folder"
          title="New folder"
        >
          <FolderPlus className="size-4" />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => load(path)} aria-label="Refresh">
          <RefreshCw className={`size-4 ${loading || busy ? "animate-spin" : ""}`} />
        </Button>
      </PageHeader>

      {/* breadcrumb */}
      <div className="mb-4 flex items-center gap-1 rounded-xl border border-border/70 bg-card/40 px-2.5 py-2 text-sm">
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => crumbs.length && goTo(crumbs.length - 2)}
          disabled={path === "."}
          aria-label="Up"
        >
          <ArrowUp className="size-3.5" />
        </Button>
        <button
          onClick={() => load(".")}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Folder className="size-3.5 text-lime-500" />~
        </button>
        {crumbs.map((seg, i) => (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight className="size-3.5 text-muted-foreground/50" />
            <button
              onClick={() => goTo(i)}
              className="rounded px-1.5 py-0.5 font-mono transition-colors hover:bg-accent"
            >
              {seg}
            </button>
          </span>
        ))}
      </div>

      {error ? <p className="mb-3 text-sm text-destructive">{error}</p> : null}

      {creating !== null ? (
        <div className="mb-3 flex items-center gap-2 rounded-xl border border-kith/40 bg-card/40 px-3 py-2">
          <FolderPlus className="size-4 shrink-0 text-lime-500" />
          <input
            autoFocus
            value={creating}
            placeholder="Folder name…"
            onChange={(e) => setCreating(e.target.value)}
            onBlur={submitFolder}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitFolder();
              if (e.key === "Escape") setCreating(null);
            }}
            className={`${FIELD} flex-1 text-sm`}
            aria-label="New folder name"
          />
        </div>
      ) : null}

      {sorted.length === 0 ? (
        <EmptyState icon={<Folder className="size-5" />}>
          {loading ? "Looking…" : "This folder is empty."}
        </EmptyState>
      ) : (
        <div
          role="listbox"
          tabIndex={0}
          aria-label="His files"
          onKeyDown={onListKeyDown}
          className="overflow-hidden rounded-xl border border-border/70 bg-card/40 shadow-sm outline-none focus-visible:border-ring/60"
        >
          {sorted.map((e, i) => {
            const full = join(e.name);
            const isDir = e.type === "dir";
            const open = () => (isDir ? load(full) : openFile(e.name));
            return (
              <ContextMenu key={e.name}>
                <ContextMenuTrigger asChild>
                  <div
                    onClick={() => setCursor(i)}
                    onDoubleClick={open}
                    onContextMenu={() => setCursor(i)}
                    className={cn(
                      "group flex w-full items-center gap-3 pr-2 text-left text-sm transition-colors",
                      i > 0 && "border-t border-border/50",
                      cursor === i ? "bg-accent/70" : "hover:bg-accent/40",
                    )}
                  >
                    <button
                      onClick={open}
                      className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-4 text-left"
                    >
                      <span
                        className={cn(
                          "flex size-7 shrink-0 items-center justify-center rounded-lg",
                          isDir
                            ? "bg-lime-500/12 text-lime-500"
                            : "bg-muted/60 text-muted-foreground",
                        )}
                      >
                        {isDir ? <Folder className="size-4" /> : <FileText className="size-4" />}
                      </span>
                      {editing?.name === e.name ? (
                        // Renaming happens where the name is, so you can still see the
                        // folder you're renaming inside.
                        <input
                          autoFocus
                          value={editing.draft}
                          onChange={(ev) => setEditing({ name: e.name, draft: ev.target.value })}
                          onClick={(ev) => ev.stopPropagation()}
                          onBlur={submitRename}
                          onKeyDown={(ev) => {
                            if (ev.key === "Enter") submitRename();
                            if (ev.key === "Escape") setEditing(null);
                            ev.stopPropagation();
                          }}
                          className={`${FIELD} min-w-0 flex-1 py-0.5 text-sm`}
                          aria-label={`Rename ${e.name}`}
                        />
                      ) : (
                        <span className="min-w-0 flex-1 truncate">{e.name}</span>
                      )}
                    </button>
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-muted-foreground/70 tabular-nums sm:block">
                      {formatModified(e.modified ?? 0)}
                    </span>
                    <span className="w-16 shrink-0 text-right text-[11px] text-muted-foreground tabular-nums">
                      {isDir ? "—" : formatSize(e.size)}
                    </span>
                    {isDir ? (
                      <ChevronRight className="size-4 shrink-0 text-muted-foreground/50" />
                    ) : (
                      <Expand className="size-3.5 shrink-0 text-muted-foreground/60 opacity-0 transition-opacity group-hover:opacity-100" />
                    )}
                  </div>
                </ContextMenuTrigger>

                <ContextMenuContent>
                  <ContextMenuLabel>{e.name}</ContextMenuLabel>
                  <ContextMenuItem icon={<Expand className="size-3.5" />} onSelect={open}>
                    {isDir ? "Open folder" : "Open here"}
                  </ContextMenuItem>
                  {!isDir ? (
                    <ContextMenuItem
                      icon={<ExternalLink className="size-3.5" />}
                      onSelect={() => void openWorkspaceFile(full)}
                    >
                      Open in another app
                    </ContextMenuItem>
                  ) : null}
                  <ContextMenuItem
                    icon={<FolderOpen className="size-3.5" />}
                    onSelect={() => void openWorkspaceFile(full, true)}
                  >
                    Show on your machine
                  </ContextMenuItem>
                  <ContextMenuSeparator />
                  <ContextMenuItem
                    icon={<Copy className="size-3.5" />}
                    onSelect={() => void copyText(full)}
                  >
                    Copy path
                  </ContextMenuItem>
                  <ContextMenuItem
                    icon={<Pencil className="size-3.5" />}
                    onSelect={() => setEditing({ name: e.name, draft: e.name })}
                    hint="↩"
                  >
                    Rename
                  </ContextMenuItem>
                  <ContextMenuSeparator />
                  <ContextMenuItem
                    icon={<Trash2 className="size-3.5" />}
                    danger
                    onSelect={() => void removeEntry(e)}
                  >
                    Delete
                  </ContextMenuItem>
                </ContextMenuContent>
              </ContextMenu>
            );
          })}
        </div>
      )}

      {file ? (
        <FileViewer
          open
          onOpenChange={(o) => !o && setFile(null)}
          name={file.path}
          content={file.content}
          error={file.error}
          onDownload={file.content ? download : undefined}
          onOpenOnHost={(reveal) => openWorkspaceFile(file.path, reveal)}
        />
      ) : null}
    </>
  );
}

/* ── Tools (his self-made tools) ────────────────────────────────────────── */

function Tools({
  snap,
  query,
  remove,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
}) {
  const items = snap.tools.filter((t) => matches(query, t.name, t.description));
  return (
    <>
      <PageHeader
        icon={<Wrench className="size-5" />}
        color="rose"
        title="Tools"
        count={items.length}
        subtitle="Tools he wrote for himself, on his own."
      />
      {items.length === 0 ? (
        <EmptyState icon={<Wrench className="size-5" />}>No self-made tools.</EmptyState>
      ) : (
        <div className="space-y-3">
          {items.map((t) => (
            <details
              key={t.name}
              className="group rounded-xl border border-border/70 bg-card/50 shadow-sm transition-all hover:shadow-md"
            >
              <summary className="flex cursor-pointer list-none items-center gap-3 p-4">
                <span
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-xl",
                    CHIP.rose,
                  )}
                >
                  <Code className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-semibold">{t.name}</span>
                    <Badge>{t.language}</Badge>
                  </div>
                  <div className="mt-0.5 truncate text-xs text-muted-foreground">
                    <Markdown>{t.description}</Markdown>
                  </div>
                </div>
                <DeleteButton onClick={() => remove("tool", t.name, t.name)} />
                <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
              </summary>
              <div className="mx-4 mb-4 overflow-hidden rounded-xl border border-border/60 bg-muted/20">
                <CodeBlock code={t.code} language={t.language} label={t.language} />
              </div>
            </details>
          ))}
        </div>
      )}
    </>
  );
}

/* ── Primitives ─────────────────────────────────────────────────────────── */

function Badge({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-md border border-border/70 bg-muted/40 px-1.5 py-0.5 text-[11px] ${className}`}
    >
      {children}
    </span>
  );
}

function DeleteButton({ onClick }: { onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-7 shrink-0 text-muted-foreground opacity-60 transition-opacity hover:text-destructive hover:opacity-100"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      aria-label="Delete"
    >
      <Trash2 className="size-4" />
    </Button>
  );
}

function EmptyState({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border/70 bg-card/20 px-6 py-16 text-center">
      <span className="flex size-11 items-center justify-center rounded-full bg-muted/50 text-muted-foreground">
        {icon ?? <Sparkles className="size-5" />}
      </span>
      <p className="max-w-sm text-sm text-muted-foreground">{children}</p>
    </div>
  );
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

function clamp(n: number): number {
  return Math.max(0, Math.min(100, n));
}

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .map((w) => w[0])
      .slice(0, 2)
      .join("")
      .toUpperCase() || "?"
  );
}

function matches(query: string, ...fields: (string | undefined)[]): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return fields.some((f) => (f ?? "").toLowerCase().includes(q));
}

/** Group timestamped items into [dayLabel, items][] preserving input order. */
function groupByDay<T>(items: T[], getIso: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = dayLabel(getIso(item));
    const list = groups.get(key);
    if (list) list.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()];
}

function dayLabel(iso: string): string {
  try {
    const d = new Date(iso);
    const today = new Date();
    const y = new Date();
    y.setDate(today.getDate() - 1);
    const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
    if (same(d, today)) return "Today";
    if (same(d, y)) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  } catch {
    return "Earlier";
  }
}

function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function time(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}
