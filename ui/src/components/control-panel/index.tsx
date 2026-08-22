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
  X,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { TaskDetailPage } from "@/components/control-panel/task-detail";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { useNow } from "@/hooks/use-now";
import {
  createBrainItem,
  deleteBrainItem,
  fetchBrain,
  fetchTimeline,
  ingestSource,
  setMemoryLevel,
  updateBrainItem,
} from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import { NavGroup, TabButton } from "./chrome";
import { WorkspaceFiles } from "./files";
import { matches, projectLabel } from "./format";
import { Journal } from "./journal";
import { Memories } from "./memories";
import { Lifetime, Overview } from "./overview";
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
  const cache = useQueryClient();
  const [query, setQuery] = useState("");
  // Work drills down: projects list → one project → one task.
  const [openProject, setOpenProject] = useState<ProjectRef | null>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  // A clock, not a poll: the relative times on this panel go stale with nothing having changed.
  // See hooks/use-now.ts for why that distinction is worth naming.
  const now = useNow(10_000);

  /* One query for the whole panel.
   *
   * Everything here comes from one snapshot, and it used to be fetched into `useState` by a `load`
   * that four subscriptions and a 30-second interval all called — so a turn that filed three tasks
   * ran it three times over, and the interval ran it again for luck. The cache dedupes that: three
   * invalidations inside a frame are one refetch, and any other reader of `["brain"]` gets the same
   * response rather than making its own request. */
  const board = useQuery({ queryKey: keys.brain(), queryFn: fetchBrain });
  const events = useQuery({ queryKey: keys.timeline(), queryFn: fetchTimeline });
  const snap = board.data ?? null;
  const timeline = events.data ?? [];
  const loading = board.isFetching || events.isFetching;
  // What the freshness line reports. `dataUpdatedAt` is when the answer arrived, which is the thing
  // that was being tracked by hand — and unlike a hand-kept timestamp it cannot drift from the data
  // it describes. It is 0 until the first success, though, and rendering that as an age gives "29
  // million minutes ago"; there is nothing to be fresh about before then, so the line waits.
  const freshAt = Math.max(board.dataUpdatedAt, events.dataUpdatedAt);
  const everLoaded = freshAt > 0;

  /* Two keys, not one, and the reason is worth stating: these are two endpoints, and one key must
   * mean one shape. They were fetched together into a single `["brain"]` entry at first, until the
   * history panel wanted the project names — which are in the board snapshot — and would have had
   * to either duplicate the request under its own key or read a shape invented for this screen.
   * Split, both panels share the snapshot and neither knows the other is there. */
  const load = useCallback(() => {
    void cache.invalidateQueries({ queryKey: keys.brain() });
    void cache.invalidateQueries({ queryKey: keys.timeline() });
  }, [cache]);

  /* Always live, and nothing here decides when.
   *
   * There used to be a Live toggle and a Refresh button, and on a desktop app both were the wrong
   * idea: what is on screen should simply be current, and a control that exists to make it current
   * is an admission that it might not be. They went, and what replaced them was a poll whose rate
   * was tuned to whether anything was happening — 3 seconds while a task was working, 20 otherwise,
   * later 30 flat as "a backstop" once events arrived.
   *
   * None of that is here now. `task`, `project`, `message` and `process` all invalidate `["brain"]`
   * through `STALE_ON`, so the board refetches when the board moves; refetch-on-focus is in the
   * cache's defaults, so coming back to the window asks once. What is left is Cmd-R, because it is
   * what someone would try, and it is one line rather than a lifecycle.
   */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r") {
        event.preventDefault();
        load();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [load]);

  const remove = async (kind: string, key: string | number, label: string) => {
    const ok = await confirm({
      title: `Delete this ${KIND_LABEL[kind] ?? kind}?`,
      description: "It's gone from his mind for good — this can't be undone.",
      subject: label,
      destructive: true,
    });
    if (!ok) return false;
    try {
      await deleteBrainItem(kind, key);
    } catch {
      // Stay where you are rather than navigate away from a delete that did not happen.
      return false;
    }
    load();
    return true;
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
        // Something below already dealt with it — a date picker closing its calendar, a field
        // reverting its own draft. Handled once, by whoever is nearest.
        if (event.defaultPrevented) return;
        // Escape inside a field belongs to that field. `typing` was computed here and never
        // consulted, so pressing Escape to abandon an edit also wiped the panel-wide search —
        // two unrelated things undone by one key.
        if (typing && event.target !== searchBox.current) return;
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
  const freshness = !everLoaded
    ? "loading…"
    : seconds < 30
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
          title="Updates when something changes, and whenever you come back to the window"
        >
          {/* This used to read "keeping up" whenever a task was working, because the panel polled
              faster while one was — a statement about its own refresh rate. There is no rate to
              report now: it updates when the board moves. So the dot says whether a refetch is in
              flight, and the text says when the answer on screen arrived. */}
          <span
            className={cn(
              "size-1.5 rounded-full",
              loading ? "bg-kith animate-pulse" : "bg-muted-foreground/40",
            )}
          />
          {freshness}
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
              icon={<NotebookPen className="size-4" />}
              active={tab === "journal"}
              count={counts.journal}
              onClick={() => openTab("journal")}
            >
              Journal
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
                <Lifetime events={timeline.filter((event) => matches(query, event.text))} />
              ) : tab === "memory" ? (
                <Memories {...props} snap={snap} />
              ) : tab === "journal" ? (
                <Journal snap={snap} query={query} remove={remove} />
              ) : tab === "projects" ? (
                <Projects {...props} snap={snap} onOpenProject={setOpenProject} />
              ) : tab === "reminders" ? (
                <Reminders {...props} snap={snap} />
              ) : tab === "schedules" ? (
                <Schedules {...props} snap={snap} />
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
