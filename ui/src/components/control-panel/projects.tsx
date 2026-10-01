/**
 * Projects: every project he has, and which of them want something from you.
 *
 * It was a two-column grid of cards — fourteen of them on the Active tab, each a paragraph of
 * description, a folder, a bar, a breakdown and a conversation line — so the first screen showed
 * six projects and the question you open it with ("which of these needs me") took a scroll and a
 * read of every card. The 2026-09-30 design makes it a dense table instead, grouped by what each
 * project needs: plans waiting on you first, then the ones he is getting on with, then the quiet
 * ones, with the empty boards folded into a single line. The card grid is still one click away
 * (the view toggle) for anyone who wants the descriptions in full.
 *
 * The four tiles over the list are the board-wide counts and also filters: "Waiting on you"
 * narrows the list to the projects with a plan waiting.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import {
  ArrowUpDown,
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  Funnel,
  LayoutGrid,
  List,
  ListChecks,
  MessageSquare,
  Plus,
} from "lucide-react";
import { MarkdownInline } from "@/components/files";
import { useConfirm } from "@/components/ui/confirm";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { fetchConversations, type ConversationSummary } from "@/lib/backend";
import { keys } from "@/lib/query-keys";
import { pathForTask } from "@/lib/router";
import { cn } from "@/lib/utils";
import { BUTTON, Chip, Empty, LiveDot, PageTitle, Segmented } from "./chrome";
import { matches, shortPath, tally } from "./format";
import { SegmentBar } from "./progress";
import {
  SORTS,
  groupRows,
  meetsNeed,
  ms,
  rowFor,
  shortDate,
  sortRows,
  whenLabel,
  type GroupKey,
  type Need,
  type Row,
  type SortKey,
} from "./projects-model";
import { LOOSE } from "./types";
import type { Handlers, ProjectRef } from "./types";

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
type View = "list" | "grid";

/** One grid for the header and every row, so the columns line up: project, progress, working,
 *  to approve, last conversation, chevron. The header's 20px inset is the list's 8px plus a
 *  row's 12px. */
const COLS = "grid grid-cols-[minmax(0,1fr)_196px_88px_96px_300px_14px] items-center gap-x-6";

/** Per-viewer conveniences — which view and order you left it in. A private window or blocked
 *  storage just means the defaults. */
const VIEW_KEY = "kith.control-panel.projects.view";
const SORT_KEY = "kith.control-panel.projects.sort";
function remembered<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return allowed.includes(value as T) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}
function remember(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Not worth a word to anyone: the page still works, it just forgets.
  }
}

const plural = (n: number, one: string, many = `${one}s`) => (n === 1 ? one : many);

export function Projects({
  snap,
  query,
  create,
  update,
  onOpenProject,
}: {
  snap: BrainSnapshot;
  query: string;
  onOpenProject: (ref: ProjectRef) => void;
} & Handlers) {
  const navigate = useNavigate();
  const confirm = useConfirm();
  const [name, setName] = useState("");
  const [filter, setFilter] = useState<Filter>("active");
  const [need, setNeed] = useState<Need | null>(null);
  const [view, setView] = useState<View>(() => remembered(VIEW_KEY, ["list", "grid"], "list"));
  const [sort, setSort] = useState<SortKey>(() => remembered(SORT_KEY, ["recent", "waiting", "name"], "recent"));
  const [unfold, setUnfold] = useState(false);
  const searching = query.trim().length > 0;

  // One listing for every row: fourteen rows each fetching it is fourteen reads of one file.
  const chats = useQuery({
    queryKey: keys.conversations(CHAT_LIMIT, ""),
    queryFn: async () => (await fetchConversations(CHAT_LIMIT)).conversations,
    staleTime: 30_000,
  });

  const hits = (t: BrainSnapshot["tasks"][number]) => matches(query, t.goal, t.status, t.description);

  const rows = useMemo(() => {
    const byProject = new Map<number, ConversationSummary[]>();
    for (const one of chats.data ?? []) {
      if (one.projectId == null) continue;
      byProject.set(one.projectId, [...(byProject.get(one.projectId) ?? []), one]);
    }
    const tasksOf = new Map<number, BrainSnapshot["tasks"]>();
    for (const task of snap.tasks) {
      if (task.project_id == null) continue;
      tasksOf.set(task.project_id, [...(tasksOf.get(task.project_id) ?? []), task]);
    }
    return (snap.projects ?? []).map((p) => rowFor(p, tasksOf.get(p.id) ?? [], byProject.get(p.id) ?? [], chats.isSuccess));
  }, [snap.projects, snap.tasks, chats.data, chats.isSuccess]);

  // Searching finds work wherever it lives: a project surfaces when it matches *or* when one of
  // its tasks does.
  const found = rows.filter(
    (row) => matches(query, row.name, row.project?.description, row.project?.status) || row.tasks.some(hits),
  );
  const matchingIn = (row: Row) => (searching ? row.tasks.filter(hits).length : 0);
  const loose = snap.tasks.filter((t) => t.project_id == null && hits(t));
  const looseRow = loose.length ? rowFor(null, loose, []) : null;

  const wanted = found.filter((row) => meetsNeed(row.counts, need));
  const inTab = (key: Filter) => wanted.filter((row) => row.project?.status === key);
  const shown = sortRows(
    [...inTab(filter), ...(filter === "active" && looseRow && meetsNeed(looseRow.counts, need) ? [looseRow] : [])],
    sort,
  );
  const everything = tally(snap.tasks);

  const add = () => {
    if (!name.trim()) return;
    create("project", { name: name.trim() });
    setName("");
  };

  const chooseSort = (value: SortKey) => {
    setSort(value);
    remember(SORT_KEY, value);
  };
  const chooseView = (value: View) => {
    setView(value);
    remember(VIEW_KEY, value);
  };

  /* No batch-review screen exists, so "Review all" starts the review where the list does: the
     oldest plan in the top project. Back from it lands here again, and the link then opens the
     next one — a queue walked one plan at a time. */
  const reviewFirst = (needs: Row[]) => {
    const first = needs
      .flatMap((row) => row.tasks.filter((t) => t.status === "planning").sort((a, b) => ms(a.created_at) - ms(b.created_at)))[0];
    if (first) navigate(pathForTask(first.id));
  };

  const archiveAll = async (folded: Row[]) => {
    const ok = await confirm({
      title: `Archive ${folded.length} empty ${plural(folded.length, "project")}?`,
      description: "They move to Archived. Nothing is deleted, and any of them can be reopened from there.",
      subject: folded.map((row) => row.name).join(", "),
      confirmLabel: `Archive ${folded.length}`,
    });
    if (!ok) return;
    for (const row of folded) if (row.project) update("project", row.project.id, { status: "archived" });
  };

  return (
    <div className="flex flex-col gap-5">
      <PageTitle
        title="Projects"
        count={(snap.projects ?? []).length}
        subtitle="Everything he's working on. Open one for its board, conversations and outputs."
      >
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            add();
          }}
        >
          <label htmlFor="new-project" className="sr-only">
            New project name
          </label>
          <input
            id="new-project"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Start a project…"
            className="border-input bg-card text-foreground placeholder:text-muted-foreground/80 focus-visible:border-ring h-8 w-[300px] rounded-lg border px-3 text-[13px] outline-none transition-colors"
          />
          <button type="submit" className={cn(BUTTON.primary, "pr-3.5 pl-[11px]")}>
            <Plus className="size-3.5" strokeWidth={2.2} />
            New project
          </button>
        </form>
      </PageTitle>

      {everything.total + everything.dropped > 0 ? (
        <div role="group" aria-label="Filter by what the work needs" className="grid grid-cols-4 gap-3">
          <NeedTile
            attention={everything.planning > 0}
            marker={<span className="bg-kith size-[7px] shrink-0 rounded-full" />}
            label="Waiting on you"
            value={everything.planning}
            note={`${plural(everything.planning, "plan")} waiting for your OK`}
            hint="Show only the projects with a plan waiting for your OK"
            on={need === "waiting"}
            onToggle={() => setNeed(need === "waiting" ? null : "waiting")}
          />
          <NeedTile
            marker={everything.working ? <LiveDot /> : <span className="bg-roam size-[7px] shrink-0 rounded-full" />}
            label="Working now"
            value={everything.working}
            note={`${plural(everything.working, "task")} he's on`}
            hint="Show only the projects he is working in"
            on={need === "working"}
            onToggle={() => setNeed(need === "working" ? null : "working")}
          />
          <NeedTile
            marker={<span className="size-[7px] shrink-0 rounded-full bg-sky-500 dark:bg-[#6FA8F0]" />}
            label="Up next"
            value={everything.approved}
            note="approved, not started"
            hint="Show only the projects with approved work not started yet"
            on={need === "next"}
            onToggle={() => setNeed(need === "next" ? null : "next")}
          />
          <NeedTile
            marker={<Check className="text-muted-foreground size-3 shrink-0" strokeWidth={2.4} />}
            label="Done"
            value={everything.done}
            note="finished, all time"
            hint="Show only the projects with finished work"
            on={need === "done"}
            onToggle={() => setNeed(need === "done" ? null : "done")}
          />
        </div>
      ) : null}

      <div className="flex items-center gap-3">
        <Segmented
          label="Which projects"
          value={filter}
          onChange={(next) => {
            setFilter(next);
            // Each tab has its own empty boards; "show as rows" was said about the last tab's.
            setUnfold(false);
          }}
          options={FILTERS.map((one) => ({ value: one.key, label: one.label, count: inTab(one.key).length }))}
        />
        <span className="flex-1" />
        <SortMenu value={sort} onChange={chooseSort} />
        <div role="group" aria-label="View" className="border-border bg-sidebar flex gap-0.5 rounded-[10px] border p-[3px]">
          {(
            [
              { value: "list", label: "List view", icon: <List /> },
              { value: "grid", label: "Grid view", icon: <LayoutGrid /> },
            ] as const
          ).map((one) => (
            <button
              key={one.value}
              type="button"
              aria-label={one.label}
              title={one.label}
              aria-pressed={view === one.value}
              onClick={() => chooseView(one.value)}
              className={cn(
                "flex h-[26px] w-[30px] items-center justify-center rounded-[7px] transition-colors [&_svg]:size-[15px]",
                view === one.value ? "bg-muted text-foreground" : "text-muted-foreground/80 hover:text-foreground",
              )}
            >
              {one.icon}
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <Empty
          title={
            searching
              ? "Nothing here matches"
              : need
                ? `No ${filter} projects ${NEED_EMPTY[need]}`
                : filter === "active"
                  ? "No projects yet"
                  : `No ${filter} projects`
          }
          action={
            need ? (
              <button type="button" className={cn(BUTTON.secondary, BUTTON.small)} onClick={() => setNeed(null)}>
                Show every project
              </button>
            ) : undefined
          }
        >
          {searching ? "Try fewer words, or another tab." : !need && filter === "active" ? "Start one above — give it a name and he takes it from there." : null}
        </Empty>
      ) : view === "list" ? (
        <ProjectList
          rows={shown}
          byNeed={filter === "active"}
          tabLabel={FILTERS.find((one) => one.key === filter)!.label}
          unfold={unfold}
          setUnfold={setUnfold}
          matchingIn={matchingIn}
          onOpen={onOpenProject}
          onReviewAll={reviewFirst}
          onArchiveAll={filter === "archived" ? undefined : archiveAll}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-2">
          {shown.map((row) =>
            row.ref === LOOSE ? (
              <LooseCard key="loose" row={row} onOpen={() => onOpenProject(LOOSE)} />
            ) : (
              <ProjectCard key={row.ref} row={row} matching={matchingIn(row)} onOpen={() => onOpenProject(row.ref)} />
            ),
          )}
        </div>
      )}
    </div>
  );
}

/** How an empty filtered list finishes "No active projects …". */
const NEED_EMPTY: Record<Need, string> = {
  waiting: "have a plan waiting",
  working: "have work in progress",
  next: "have approved work waiting to start",
  done: "have finished work",
};

/* ── The tiles ──────────────────────────────────────────────────────────── */

function NeedTile({
  marker,
  label,
  value,
  note,
  hint,
  on,
  onToggle,
  attention,
}: {
  marker: ReactNode;
  label: string;
  value: number;
  note: string;
  hint: string;
  on: boolean;
  onToggle: () => void;
  /** Amber, for the one tile that is about you — and only while there is something in it. */
  attention?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      title={on ? "Show every project again" : hint}
      onClick={onToggle}
      className={cn(
        "text-foreground flex h-[74px] flex-col justify-between rounded-xl border px-4 py-3 text-left transition-colors",
        attention
          ? on
            ? "border-kith/60 bg-kith/[0.12]"
            : "border-kith/25 bg-kith/[0.05] hover:bg-kith/[0.08]"
          : on
            ? "border-ring/60 bg-muted"
            : "border-border bg-card hover:bg-muted/50",
      )}
    >
      <span className={cn("flex w-full items-center gap-2 text-[12.5px] leading-[1.2]", attention ? "text-kith/85" : "text-foreground/80")}>
        {marker}
        <span className="flex-1">{label}</span>
        <Funnel
          aria-hidden
          className={cn(
            "size-3 shrink-0",
            on ? (attention ? "text-kith" : "text-foreground") : attention ? "text-kith/45" : "text-muted-foreground/50",
          )}
        />
      </span>
      <span className="flex items-baseline gap-2">
        <span className={cn("text-[22px] leading-[1.2] font-[650] tracking-[-0.01em] tabular-nums", attention && "text-kith")}>{value}</span>
        <span className="text-muted-foreground text-xs">{note}</span>
      </span>
    </button>
  );
}

/* ── The sort control ───────────────────────────────────────────────────── */

function SortMenu({ value, onChange }: { value: SortKey; onChange: (value: SortKey) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const current = SORTS.find((one) => one.value === value) ?? SORTS[0]!;

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    // Escape closes the menu and nothing else: marked handled, so the panel behind it does not
    // also take it as "close the control panel".
    const key = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Sort: ${current.label}`}
        onClick={() => setOpen((was) => !was)}
        className="border-input bg-muted/60 text-foreground hover:bg-muted flex h-8 items-center gap-2 rounded-lg border pr-2.5 pl-3 text-[13px] transition-colors"
      >
        <ArrowUpDown aria-hidden className="text-muted-foreground/80 size-3.5" />
        {current.label}
        <ChevronDown aria-hidden className={cn("text-muted-foreground/80 size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <div
          role="listbox"
          aria-label="Sort projects"
          // `data-state="open"` is what the panel's Escape handling looks for to know a menu is up.
          data-state="open"
          className="border-border bg-popover absolute top-full right-0 z-50 mt-1 flex min-w-full flex-col gap-0.5 rounded-[10px] border p-1 shadow-xl"
        >
          {SORTS.map((one) => (
            <button
              key={one.value}
              type="button"
              role="option"
              aria-selected={one.value === value}
              onClick={() => {
                onChange(one.value);
                setOpen(false);
              }}
              className="text-foreground hover:bg-muted/60 flex h-8 items-center gap-2 rounded-[7px] px-2.5 text-left text-[13px] whitespace-nowrap"
            >
              <Check aria-hidden className={cn("size-3.5", one.value === value ? "text-kith" : "invisible")} />
              {one.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ── The list ───────────────────────────────────────────────────────────── */

const GROUP: Record<GroupKey, { label: string; dot: string; note?: string }> = {
  needs: { label: "Needs you", dot: "bg-kith" },
  working: { label: "Working", dot: "bg-roam", note: "nothing to approve — he's getting on with it" },
  quiet: { label: "Quiet", dot: "bg-muted-foreground/50" },
  all: { label: "", dot: "bg-muted-foreground/50" },
};

function ProjectList({
  rows,
  byNeed,
  tabLabel,
  unfold,
  setUnfold,
  matchingIn,
  onOpen,
  onReviewAll,
  onArchiveAll,
}: {
  rows: Row[];
  /** Active projects group by what they need; the other tabs are one plain group. */
  byNeed: boolean;
  tabLabel: string;
  unfold: boolean;
  setUnfold: (unfold: boolean) => void;
  matchingIn: (row: Row) => number;
  onOpen: (ref: ProjectRef) => void;
  onReviewAll: (needs: Row[]) => void;
  onArchiveAll?: (folded: Row[]) => void;
}) {
  const { groups, folded } = groupRows(rows, byNeed);
  // The folded line belongs to the last group (Quiet, or the tab's one group); unfolded, the
  // empty boards are rows of it, after the rest. Compared by key: the unfolded group is a copy.
  const lastKey = groups[groups.length - 1]!.key;
  const visible = groups
    .map((group) => (unfold && group.key === lastKey ? { ...group, rows: [...group.rows, ...folded] } : group))
    .filter((group) => group.rows.length > 0 || (group.key === lastKey && folded.length > 0));

  /* ↑ and ↓ move between rows; Enter opens one (it is a button). */
  const step = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const all = [...event.currentTarget.querySelectorAll<HTMLElement>("[data-project-row]")];
    const at = all.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    event.preventDefault();
    all[Math.max(0, Math.min(all.length - 1, at + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
  };

  return (
    <section
      aria-label={`${tabLabel} projects`}
      onKeyDown={step}
      className="border-border bg-card flex flex-col overflow-hidden rounded-[14px] border"
    >
      <div
        className={cn(
          COLS,
          "border-border/80 text-muted-foreground/80 h-9 shrink-0 border-b px-5 text-[10.5px] font-semibold tracking-[0.1em] uppercase",
        )}
      >
        <span>Project</span>
        <span>Progress</span>
        <span>Working</span>
        <span>To approve</span>
        <span>Last conversation</span>
        <span />
      </div>

      <div className="flex flex-col px-2 pb-2">
        {visible.map((group, index) => {
          const meta = GROUP[group.key];
          const plans = group.rows.reduce((n, row) => n + row.counts.planning, 0);
          const isLast = group.key === lastKey;
          return (
            <div key={group.key}>
              <GroupHead
                first={index === 0}
                dot={meta.dot}
                label={meta.label || tabLabel}
                count={group.rows.length + (isLast && !unfold ? folded.length : 0)}
                note={meta.note}
                action={
                  group.key === "needs" && plans > 0 ? (
                    <button type="button" onClick={() => onReviewAll(group.rows)} className="text-kith/85 hover:text-kith text-xs">
                      Review all {plans} {plural(plans, "plan")}
                    </button>
                  ) : isLast && unfold && folded.length > 0 ? (
                    <button type="button" onClick={() => setUnfold(false)} className="text-foreground/80 hover:text-foreground text-xs">
                      Fold the empty boards
                    </button>
                  ) : undefined
                }
              />
              {group.rows.length ? (
                <div className="flex flex-col gap-0.5 pt-1">
                  {group.rows.map((row) => (
                    <ProjectRow key={String(row.ref)} row={row} matching={matchingIn(row)} onOpen={() => onOpen(row.ref)} />
                  ))}
                </div>
              ) : null}
              {isLast && !unfold && folded.length > 0 ? (
                <FoldedLine
                  rows={folded}
                  onOpen={onOpen}
                  onUnfold={() => setUnfold(true)}
                  onArchiveAll={onArchiveAll ? () => onArchiveAll(folded) : undefined}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function GroupHead({
  first,
  dot,
  label,
  count,
  note,
  action,
}: {
  first: boolean;
  dot: string;
  label: string;
  count: number;
  note?: string;
  action?: ReactNode;
}) {
  return (
    <div className={cn("flex items-center gap-2 px-3", first ? "h-[30px] pt-2.5" : "h-[34px] pt-3.5")}>
      <span className={cn("size-1.5 shrink-0 rounded-full", dot)} />
      <span className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">{label}</span>
      <span className="text-muted-foreground/50 text-[11px] tabular-nums">{count}</span>
      {note ? <span className="text-muted-foreground/80 ml-1.5 text-[11.5px]">{note}</span> : null}
      <span className="flex-1" />
      {action}
    </div>
  );
}

/** What the last-conversation cell says under the title: when that conversation last moved, or
 *  — with none — when its tasks did. */
function activity(row: Row): string {
  const latest = row.chats[0];
  if (latest) return whenLabel(ms(latest.updatedAt));
  if (row.tasksMoved) return `Tasks last moved ${shortDate(row.tasksMoved)}`;
  return row.project ? `Started ${shortDate(ms(row.project.created_at))}` : "";
}

function ProjectRow({ row, matching, onOpen }: { row: Row; matching: number; onOpen: () => void }) {
  const { counts } = row;
  const latest = row.chats[0];
  const loose = row.ref === LOOSE;
  return (
    <button
      type="button"
      data-project-row
      onClick={onOpen}
      className={cn(
        COLS,
        // The board's rows set their two lines at the type's own height, not the body's 1.5.
        "group hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:ring-ring/40 h-[52px] w-full rounded-[10px] px-3 text-left leading-[1.2] transition-colors outline-none focus-visible:ring-2",
      )}
    >
      <span className="flex min-w-0 flex-col gap-[3px]">
        <span className="flex min-w-0 items-baseline gap-2.5">
          <span className="text-foreground min-w-0 shrink truncate text-[13.5px] font-semibold">{row.name}</span>
          <span
            title={row.directory ?? undefined}
            className={cn(
              "min-w-[60px] shrink-[100] truncate font-mono text-[11px]",
              row.directory ? "text-muted-foreground/80" : "text-muted-foreground/50",
            )}
          >
            {row.directory ? shortPath(row.directory, 48) : loose ? "loose work" : "no folder"}
          </span>
          {matching ? (
            <Chip tone="attention" className="h-[18px] self-center px-1.5 text-[10.5px]">
              {matching} {plural(matching, "match", "matches")}
            </Chip>
          ) : null}
        </span>
        <span className={cn("truncate text-xs", row.description ? "text-muted-foreground" : "text-muted-foreground/80 italic")}>
          {row.description || "No description yet"}
        </span>
      </span>

      <span className="flex items-center gap-2.5">
        {counts.total ? (
          <>
            <SegmentBar counts={counts} className="w-[88px]" />
            <span className="text-muted-foreground text-xs whitespace-nowrap tabular-nums">
              {counts.done} of {counts.total} done
            </span>
          </>
        ) : (
          // No tasks, no bar: an empty track reads as "0% done", which is a claim.
          <span className="text-muted-foreground/80 text-xs">No tasks yet</span>
        )}
      </span>

      <span className="flex items-center gap-[7px] text-[12.5px] tabular-nums">
        {counts.working ? (
          <>
            <span className="bg-roam size-1.5 shrink-0 rounded-full" />
            <span className="text-foreground" aria-label={`${counts.working} working`}>
              {counts.working}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground/50">—</span>
        )}
      </span>

      <span className="flex items-center">
        {counts.planning ? (
          <span
            aria-label={`${counts.planning} ${plural(counts.planning, "plan")} to approve`}
            className="border-kith/25 bg-kith/10 text-kith/85 flex h-[22px] min-w-6 items-center justify-center rounded-full border px-2 text-[11.5px] font-semibold tabular-nums"
          >
            {counts.planning}
          </span>
        ) : (
          <span className="text-muted-foreground/50 text-[12.5px]">—</span>
        )}
      </span>

      <span className="flex min-w-0 flex-col gap-[3px]">
        <span className="flex min-w-0 items-center gap-1.5">
          {latest?.working ? (
            <span title="Still working in this conversation" className="flex">
              <LiveDot className="size-1.5" />
            </span>
          ) : latest?.waiting ? (
            <span title="Waiting on your answer" className="bg-kith size-1.5 shrink-0 rounded-full" />
          ) : null}
          <span className={cn("truncate text-[12.5px]", latest ? "text-foreground/80" : "text-muted-foreground/80 italic")}>
            {latest ? latest.title : loose ? "From chats with no project" : "No conversations yet"}
          </span>
        </span>
        <span className="text-muted-foreground/80 truncate text-[11.5px] tabular-nums">{activity(row)}</span>
      </span>

      <ChevronRight aria-hidden className="text-muted-foreground/50 group-hover:text-foreground size-3.5 transition-colors" />
    </button>
  );
}

/** The empty boards, as one line: their names to open, and the two things you would do about a
 *  pile of them — see them properly, or put them away. */
function FoldedLine({
  rows,
  onOpen,
  onUnfold,
  onArchiveAll,
}: {
  rows: Row[];
  onOpen: (ref: ProjectRef) => void;
  onUnfold: () => void;
  onArchiveAll?: () => void;
}) {
  const touched = rows.reduce((latest, row) => Math.max(latest, row.lastActive), 0);
  return (
    <div className="border-input mt-1 flex h-11 items-center gap-3 rounded-[10px] border border-dashed px-3">
      <span className="text-muted-foreground text-[12.5px] whitespace-nowrap">No tasks and no conversations yet:</span>
      <span className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
        {rows.map((row) => (
          <button
            key={String(row.ref)}
            type="button"
            onClick={() => onOpen(row.ref)}
            className="border-border bg-muted/60 text-foreground/80 hover:text-foreground hover:bg-muted flex h-6 shrink-0 items-center rounded-full border px-[9px] text-xs whitespace-nowrap transition-colors"
          >
            {row.name}
          </button>
        ))}
        {touched ? (
          <span className="text-muted-foreground/80 text-[11.5px] whitespace-nowrap">
            {rows.length === 1 ? "not" : "none"} touched since {shortDate(touched)}
          </span>
        ) : null}
      </span>
      <button type="button" onClick={onUnfold} className={cn(BUTTON.ghost, "h-7 rounded-[7px] px-2.5 text-xs")}>
        Show as rows
      </button>
      {onArchiveAll ? (
        <button type="button" onClick={onArchiveAll} className={cn(BUTTON.secondary, "h-7 rounded-[7px] px-2.5 text-xs")}>
          Archive {rows.length === 1 ? "it" : `all ${rows.length}`}
        </button>
      ) : null}
    </div>
  );
}

/* ── The grid ───────────────────────────────────────────────────────────── */

/** The counts in words, coloured by the same rule as everything else on the page: green for work
 *  happening, amber for what waits on you. */
function Counts({ row }: { row: Row }) {
  const { counts } = row;
  if (!counts.total) return <span className="text-muted-foreground/80">No tasks yet</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 tabular-nums">
      <span className="text-foreground font-medium">
        {counts.done} of {counts.total} done
      </span>
      {counts.working ? <span className="text-roam">{counts.working} working</span> : null}
      {counts.planning ? <span className="text-kith/85">{counts.planning} to approve</span> : null}
      {counts.approved ? <span>{counts.approved} up next</span> : null}
    </span>
  );
}

/**
 * One project, as a card you open — the grid view.
 *
 * It used to carry a milestone strip, a progress ring over milestones, a "Next:" line from the
 * roadmap, a status dropdown and a delete button — five things about the plan's shape and two
 * controls you almost never want from a list. What is left answers the three questions you scan
 * a projects list with: is he on it, does it need me, and where was it left.
 */
function ProjectCard({ row, matching, onOpen }: { row: Row; matching: number; onOpen: () => void }) {
  const { counts, project } = row;
  const latest = row.chats[0];

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
      className="group border-border bg-card hover:border-input hover:bg-muted/40 focus-visible:ring-ring/40 flex w-full cursor-pointer flex-col gap-2.5 rounded-[14px] border p-4 text-left transition-colors outline-none focus-visible:ring-[3px]"
    >
      <span className="flex w-full items-center gap-2">
        <span className="min-w-0 truncate text-[15px] leading-snug font-semibold">{row.name}</span>
        {counts.working > 0 ? (
          <span className="text-roam flex shrink-0 items-center gap-1.5 text-[11.5px]">
            <LiveDot className="size-1.5" />
            working
          </span>
        ) : null}
        <span className="flex-1" />
        {matching ? (
          <Chip tone="attention">
            {matching} {plural(matching, "match", "matches")}
          </Chip>
        ) : null}
        {counts.planning > 0 ? <Chip tone="attention">{counts.planning} to approve</Chip> : null}
        <ChevronRight className="text-muted-foreground/50 group-hover:text-foreground size-4 shrink-0 transition-colors" />
      </span>

      {project?.description ? (
        // `line-clamp` is a display of its own (-webkit-box), so nothing here may also say `block`;
        // and the minimum height is exactly two lines, or a sliver of the third shows under it.
        <div className="text-muted-foreground line-clamp-2 min-h-10 text-[13px] leading-5">
          <MarkdownInline>{project.description}</MarkdownInline>
        </div>
      ) : (
        <span className="text-muted-foreground/80 block min-h-10 text-[13px] leading-5 italic">No description yet</span>
      )}

      {/* Where the work actually lands, cut from the left: the end of a path identifies it. */}
      {row.directory ? (
        <span title={row.directory} className="text-muted-foreground/80 flex min-w-0 items-center gap-1.5 font-mono text-[11px]">
          <Folder className="size-3 shrink-0" />
          <span className="truncate">{shortPath(row.directory)}</span>
        </span>
      ) : (
        <span className="text-muted-foreground/50 text-[11px] italic">No folder — nothing here writes files</span>
      )}

      <span className="flex w-full flex-col gap-1.5">
        {counts.total ? <SegmentBar counts={counts} className="w-full" /> : null}
        <span className="text-muted-foreground text-[11.5px]">
          <Counts row={row} />
        </span>
      </span>

      <span className="border-border flex w-full items-center gap-2.5 border-t pt-2.5 text-xs">
        <MessageSquare className="text-muted-foreground/80 size-3.5 shrink-0" />
        {latest ? (
          <>
            <span className="text-muted-foreground shrink-0">
              {row.chats.length} {plural(row.chats.length, "conversation")}
            </span>
            <span className="text-foreground/80 min-w-0 flex-1 truncate">{latest.title}</span>
            {latest.working ? (
              <span title="Still working in this conversation" className="flex">
                <LiveDot className="size-1.5" />
              </span>
            ) : null}
            <span className="text-muted-foreground/80 shrink-0 text-[11.5px]">{whenLabel(ms(latest.updatedAt))}</span>
          </>
        ) : (
          <span className="text-muted-foreground/80">No conversations yet</span>
        )}
      </span>
    </div>
  );
}

function LooseCard({ row, onOpen }: { row: Row; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group border-input hover:bg-muted/40 flex w-full items-center gap-4 rounded-[14px] border border-dashed p-4 text-left transition-colors xl:col-span-2"
    >
      <span className="bg-muted/70 text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-xl">
        <ListChecks className="size-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] leading-snug font-semibold">No project</span>
        <span className="text-muted-foreground mt-0.5 block text-[13px]">{row.description}</span>
      </span>
      <span className="text-muted-foreground hidden text-xs sm:block">
        <Counts row={row} />
      </span>
      <ChevronRight className="text-muted-foreground/50 group-hover:text-foreground size-4 shrink-0 transition-colors" />
    </button>
  );
}
