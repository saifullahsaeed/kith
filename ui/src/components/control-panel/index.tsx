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
  ArrowLeft,
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
  Sun,
  X,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { TaskDetailPage } from "@/components/control-panel/task-detail";
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
import { Kbd, NavItem, NavLabel } from "./chrome";
import { PresencePill } from "@/components/shell/presence";
import type { Presence } from "@/components/shell/chat-states";
import { WorkspaceFiles } from "./files";
import { matches, projectLabel } from "./format";
import { Journal } from "./journal";
import { Memories } from "./memories";
import { Lifetime } from "./overview";
import { Today } from "./today";
import { ProjectPage } from "./project-page";
import { Projects } from "./projects";
import { Reminders } from "./reminders";
import { Schedules } from "./schedules";
import { Sources } from "./sources";
import { KIND_LABEL } from "./types";
import type { ProjectRef, Tab } from "./types";
import { Layer, useLayer } from "@/hooks/use-layer";

export function ControlPanel({
  tab,
  openTask,
  onSelectTab,
  onOpenTask,
  onNewConversation,
  onClose,
  presence,
}: {
  tab: Tab;
  openTask: number | null;
  onSelectTab: (tab: Tab) => void;
  onOpenTask: (id: number) => void;
  /** Start a chat inside a project, from that project's page. */
  onNewConversation?: (projectId: number) => void;
  onClose: () => void;
  /** Kith's presence, as the app's own bar says it — this screen covers that bar. */
  presence?: Presence;
}) {
  const confirm = useConfirm();
  const cache = useQueryClient();
  const layer = useLayer(Layer.Panel);
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
        // tried it. That test now lives in `useLayer`, along with the four other copies
        // of it this file's version had already drifted from.
        if (!layer.frontmost()) return;
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
  /* The nav's numbers are what is there to *do*, not how much exists: plans waiting for your OK on
     Projects, reminders still to fire, standing jobs that are on. */
  const waiting = (snap?.tasks ?? []).filter((task) => task.status === "planning").length;
  const activeProjects = (snap?.projects ?? []).filter((project) => project.status === "active").length;
  const upcoming = (snap?.reminders ?? []).filter((reminder) => reminder.status === "pending").length;
  const standing = (snap?.schedules ?? []).filter((schedule) => schedule.status === "active").length;
  const seconds = Math.max(0, Math.round((now - freshAt) / 1000));
  const freshness = !everLoaded
    ? "Loading…"
    : seconds < 30
      ? "Up to date"
      : seconds < 90
        ? "A minute ago"
        : `${Math.round(seconds / 60)} min ago`;

  const props = { snap, query, refresh: load, remove, relevel, create, update };

  return (
    /* **Deliberately a takeover, not a pane.** This was converted to `h-full w-full` alongside
       Settings and Context, and reverted the same day: at the pane widths its own `minWidth`
       permits (380), the eight-section board does not fit. The content column was left 217px
       against 247px of content, and because `overflow-y-auto` computes `overflow-x` to `auto`,
       the shortfall showed up as a horizontal scrollbar rather than as a squeeze.
       It cannot be fixed by tuning the width — the layout is proportional, so the pane sometimes
       gets *narrower* as the window grows, and widening `minWidth` far enough to fit would make
       the board's minimum exceed the window beside the chat's 560. Fitting this inside a pane is
       responsive work on the board's own layout (stack the nav, let the rows shrink and wrap),
       not a constant. Until that exists it covers the window, which is the shape it was designed
       for. `ContextDetailScreen` did convert — it has one scrolling column and measured clean at
       every width. */
    <div className="fixed inset-0 z-30 flex flex-col bg-background text-foreground">
      {/* No ambient wash: the design is flat neutral surfaces, so colour is free to mean state. */}

      {/* This panel covers the app header, so it owns the top of the window and
          has to reserve the window-control space itself. */}
      {/* One 44px bar, the same grammar as the app's own: where you are, the search, whether
          the screen is current, Kith, and the way out. It covers the app header, so it keeps the
          traffic lights' room itself. */}
      <header className="window-drag-region relative z-10 flex h-11 shrink-0 items-center border-b border-border bg-background">
        <div className="window-controls-gap flex h-11 w-[232px] shrink-0 items-center gap-2 border-e border-border pe-3">
          <button
            type="button"
            onClick={onClose}
            aria-label="Back to chats"
            title="Back to chats"
            className="text-muted-foreground hover:bg-muted/60 hover:text-foreground flex size-7 items-center justify-center rounded-[7px] transition-colors"
          >
            <ArrowLeft className="size-[15px]" />
          </button>
          <span className="truncate text-[13px] font-semibold">Control panel</span>
        </div>
        <div className="flex flex-1 items-center px-5">
          <label className="border-input bg-card text-muted-foreground/80 relative flex h-[30px] w-[460px] items-center gap-2 rounded-lg border px-2.5 text-[13px] transition-colors focus-within:border-ring">
            <Search className="size-3.5 shrink-0" />
            <input
              ref={searchBox}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search memory, journal, tasks, files…"
              aria-label="Search memory, journal, tasks, files"
              className="text-foreground placeholder:text-muted-foreground/80 min-w-0 flex-1 bg-transparent outline-none"
            />
            <Kbd>⌘K</Kbd>
          </label>
        </div>
        <div className="flex shrink-0 items-center gap-3 px-3.5">
          {/* What replaced the Live button and the Refresh button: a statement rather than a
              control. The dot says whether a refetch is in flight; the words, when the answer on
              screen arrived. */}
          <span
            className="text-muted-foreground/80 hidden items-center gap-1.5 font-mono text-[11px] tabular-nums sm:flex"
            title="Updates when something changes, and whenever you come back to the window"
          >
            <span
              className={cn(
                "size-1.5 rounded-full",
                loading ? "bg-kith animate-pulse" : "bg-muted-foreground/50",
              )}
            />
            {freshness}
          </span>
          {presence ? <PresencePill presence={presence} /> : null}
          <span className="flex items-center gap-1.5">
            <Kbd>Esc</Kbd>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close control panel"
              className="text-muted-foreground hover:bg-muted/60 hover:text-foreground flex size-[30px] items-center justify-center rounded-lg transition-colors"
            >
              <X className="size-4" />
            </button>
          </span>
        </div>
      </header>

      <div className="relative z-10 flex min-h-0 flex-1">
        <nav
          aria-label="Control panel sections"
          className="flex w-[232px] shrink-0 flex-col overflow-y-auto border-e border-border bg-sidebar px-2.5 py-3"
        >
          <NavItem icon={<Sun />} active={tab === "overview"} onClick={() => openTab("overview")}>
            Today
          </NavItem>

          <NavLabel>Work</NavLabel>
          <NavItem
            icon={<FolderKanban />}
            active={tab === "projects"}
            alert={waiting}
            alertLabel={`${waiting} plan${waiting === 1 ? "" : "s"} waiting for your OK`}
            count={activeProjects}
            onClick={() => openTab("projects")}
          >
            Projects
          </NavItem>
          {/* Upcoming only. The old count was every reminder ever set — 24 of them, all fired —
              which said there was something here when there was nothing to do. */}
          <NavItem icon={<BellRing />} active={tab === "reminders"} count={upcoming} onClick={() => openTab("reminders")}>
            Reminders
          </NavItem>
          <NavItem icon={<Repeat />} active={tab === "schedules"} count={standing} onClick={() => openTab("schedules")}>
            Schedules
          </NavItem>

          <NavLabel>Mind</NavLabel>
          <NavItem icon={<Brain />} active={tab === "memory"} count={counts.memories} onClick={() => openTab("memory")}>
            Memory
          </NavItem>
          <NavItem icon={<NotebookPen />} active={tab === "journal"} count={counts.journal} onClick={() => openTab("journal")}>
            Journal
          </NavItem>
          <NavItem icon={<Clock />} active={tab === "lifetime"} onClick={() => openTab("lifetime")}>
            Lifetime
          </NavItem>

          <NavLabel>Library</NavLabel>
          <NavItem icon={<FileText />} active={tab === "sources"} count={counts.sources} onClick={() => openTab("sources")}>
            Sources
          </NavItem>
          <NavItem icon={<FolderTree />} active={tab === "workspace"} onClick={() => openTab("workspace")}>
            Files
          </NavItem>

          <div className="text-muted-foreground/80 mt-auto flex gap-1 border-t border-border px-2 pt-3 pb-0.5 text-[11px] whitespace-nowrap">
            <span>⌘K search</span>
            <span className="text-muted-foreground/50">·</span>
            <span>⌘R refresh</span>
            <span className="text-muted-foreground/50">·</span>
            <span>Esc close</span>
          </div>
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
              className="animate-[kith-rise_0.35s_ease-out] px-10 pt-7 pb-7"
            >
              <ProjectPage
                {...props}
                snap={snap}
                projectRef={openProject}
                onBack={() => setOpenProject(null)}
                onOpenTask={onOpenTask}
                onNewConversation={onNewConversation}
              />
            </div>
          ) : (
            <div
              key={tab}
              // Generous rather than unbounded. These are lists of cards, so the width is
              // useful — but a 2,000px line of prose inside one is worse than a gutter, and
              // some of these hold his notes and journal entries.
              className="animate-[kith-rise_0.35s_ease-out] px-10 pt-7 pb-7"
            >
              {tab === "overview" ? (
                <Today
                  snap={snap}
                  timeline={timeline}
                  query={query}
                  onNavigate={openTab}
                  onOpenProject={(ref) => {
                    onSelectTab("projects");
                    setOpenProject(ref);
                  }}
                  onOpenTask={onOpenTask}
                  create={create}
                />
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
