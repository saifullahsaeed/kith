/** A full-screen window into everything Kith is — browse, search, edit, prune.
 *
 * The shell only: the chrome, the tab routing, and the one snapshot every page reads.
 * Each tab's page lives in its own module beside this one — this file was 3,224 lines with
 * forty-five components in it, and the pages share nothing but `props` and the furniture in
 * `chrome.tsx`, so the seams were already there.
 *
 * The open tab and open task come from the URL (react-router), so deep links, refresh, and
 * back/forward all work; project drill-down stays local state.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  BellRing,
  Brain,
  Clock,
  FileText,
  FolderKanban,
  FolderTree,
  NotebookPen,
  RefreshCw,
  Repeat,
  Search,
  Sparkles,
  StickyNote,
  User,
  X,
} from "lucide-react";
import { TaskDetailPage } from "@/components/control-panel/task-detail";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { useChanges } from "@/hooks/use-changes";
import {
  createBrainItem,
  deleteBrainItem,
  fetchBrain,
  fetchTimeline,
  ingestSource,
  setMemoryLevel,
  updateBrainItem,
} from "@/lib/backend/brain";
import type { BrainSnapshot, TimelineEvent } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { NavGroup, TabButton } from "./chrome";
import { WorkspaceFiles } from "./files";
import { matches, projectLabel } from "./format";
import { Journal } from "./journal";
import { Memories } from "./memories";
import { Notes } from "./notes";
import { Lifetime, Overview } from "./overview";
import { People } from "./people";
import { ProjectPage } from "./project-page";
import { Projects } from "./projects";
import { Reminders } from "./reminders";
import { Schedules } from "./schedules";
import { Sources } from "./sources";
import { INPUT, KIND_LABEL } from "./types";
import type { ProjectRef, Tab } from "./types";

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
  // "Is anything happening" — which is now only ever a task someone is working. This used
  // to also poll /api/autonomy every five seconds to ask whether a step was running; that
  // route is gone, so the request was a 404 on a timer and the answer it fed was always
  // false.
  const busy = Boolean(snap?.tasks?.some((task) => task.status === "working"));
  // Everything on this panel comes from one snapshot, so it subscribes to everything that can change
  // one: the board, a project, a message, a background task starting.
  useChanges(["task", "project", "message", "process"], () => void load());
  useEffect(() => {
    // A backstop. `useChanges` below is what refreshes the board the moment it moves; this covers a
    // dropped stream. (was 3s while a task was working, 20s otherwise.)
    const id = window.setInterval(load, 30_000);
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
              ) : (
                <WorkspaceFiles />
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
