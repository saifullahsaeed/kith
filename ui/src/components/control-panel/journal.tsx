/**
 * Journal: his private diary, read like one.
 *
 * It was one column of cards, 709 of them, newest first — fine for "what did he write today" and
 * useless for "what was he thinking the week Harrier shipped", which meant a long scroll with
 * nothing to tell you where you were. Now it is three columns:
 *
 *   - left, the days: a month as a heat map (how much he wrote, per day) and the list of days he
 *     wrote on. Either one takes you to that day.
 *   - centre, the reading column: whole entries in a serif at a reading measure, separated by
 *     space and a hairline rather than boxed.
 *   - right, what the entry in view is connected to: the projects it names, the memory he saved
 *     off the back of it, the files it mentions — read out of the text (`journal-links.ts`),
 *     since nothing in the database links them — and its Copy / Delete.
 *
 * "The entry in view" is the one under the top of the reading column as you scroll, or the one
 * you clicked; J and K step through them.
 */
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Brain, ChevronRight, Copy, Folder, Search } from "lucide-react";

import { Markdown } from "@/components/files";
import { ItemMenu } from "@/components/ui/item-menu";
import { Layer, useLayer } from "@/hooks/use-layer";
import type { BrainSnapshot, JournalEntry } from "@/lib/backend/brain";
import { copyText } from "@/lib/files";
import { cn } from "@/lib/utils";
import { BUTTON, Empty, Kbd, Label, PageTitle } from "./chrome";
import { clock, dayKey, keyMidnight, longDate, monthIndex, msOf, plural, relativeDay, shortDate } from "./day-format";
import { matches } from "./format";
import { useFitPanel } from "./fit-panel";
import { HeatCalendar } from "./heat-calendar";
import { firstLine, keptLabel, keptMemories, mentionedFiles, mentionedProjects, plain, wordCount } from "./journal-links";
import type { Handlers, ProjectRef, Tab } from "./types";

/** The serif, at the reading size, for his entries only — Markdown's own 14px and 1.625 are
 *  overridden from outside so the renderer stays the one chat uses. */
const SERIF = cn(
  "font-serif [font-family:ui-serif,'New_York',Georgia,serif] text-foreground text-[15.5px] leading-[1.7]",
  "[&>div]:text-[15.5px] [&>div]:leading-[1.7] [&_p]:leading-[1.7] [&_li]:leading-[1.7]",
  "[&_:not(pre)>code]:border-border [&_:not(pre)>code]:bg-muted/60 [&_:not(pre)>code]:rounded-[4px] [&_:not(pre)>code]:border [&_:not(pre)>code]:px-[5px] [&_:not(pre)>code]:py-px [&_:not(pre)>code]:text-[13px]",
);

/** How far below the top of the reading column an entry has to start to count as "in view". */
const READING_LINE = 96;

/* The column mounts in pieces. 709 entries of Markdown is most of a second of rendering, and
   all of it used to happen before the tab showed anything; now the first screenful paints at
   once and the rest arrives in idle time, a chunk at a time. A jump to a day that has not
   arrived yet asks for it first and scrolls once it is there. */
const FIRST_PAINT = 24;
const CHUNK = 80;

type Entry = JournalEntry & { ms: number };
type Day = { key: string; ms: number; entries: Entry[] };

export function Journal({
  snap,
  query,
  remove,
  onNavigate,
  onOpenProject,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
  /** Where "Open" on the linked memory goes. Without it the memory is shown, not linked. */
  onNavigate?: (tab: Tab) => void;
  onOpenProject?: (ref: ProjectRef) => void;
}) {
  const layer = useLayer(Layer.Panel);
  const [page, height] = useFitPanel<HTMLDivElement>();
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const reader = useRef<HTMLDivElement>(null);
  const dayList = useRef<HTMLDivElement>(null);
  const entryNodes = useRef(new Map<number, HTMLElement>());
  const dayNodes = useRef(new Map<string, HTMLElement>());
  /** Set while a scroll we started is settling, so the scroll handler does not re-pick an
   *  entry under the one that was just chosen. */
  const settling = useRef<number | null>(null);
  /** A scroll waiting for its target to be mounted; true once it has run. */
  const pending = useRef<(() => boolean) | null>(null);
  const [limit, setLimit] = useState(FIRST_PAINT);

  /* Entries keep their identity across refetches and searches, so a memoised entry that has not
     changed is not rendered again — the snapshot is a new object every time anything moves. */
  const known = useRef(new Map<number, Entry>());
  const all = useMemo<Entry[]>(() => {
    const next = new Map<number, Entry>();
    const out = snap.journal.map((entry) => {
      const seen = known.current.get(entry.id);
      const kept =
        seen && seen.entry === entry.entry && seen.created_at === entry.created_at ? seen : { ...entry, ms: msOf(entry.created_at) };
      next.set(entry.id, kept);
      return kept;
    });
    known.current = next;
    return out.sort((a, b) => b.ms - a.ms);
  }, [snap.journal]);
  const items = useMemo(
    () => all.filter((entry) => matches(query, entry.entry) && matches(search, entry.entry)),
    [all, query, search],
  );
  const days = useMemo<Day[]>(() => {
    const out: Day[] = [];
    for (const entry of items) {
      const key = dayKey(entry.ms);
      const last = out[out.length - 1];
      if (last && last.key === key) last.entries.push(entry);
      else out.push({ key, ms: keyMidnight(key), entries: [entry] });
    }
    return out;
  }, [items]);
  const counts = useMemo(() => new Map(days.map((day) => [day.key, day.entries.length])), [days]);
  // Whole days only, so a heading never shows half its entries.
  const shownDays = useMemo(() => {
    const out: Day[] = [];
    let n = 0;
    for (const day of days) {
      if (n >= limit) break;
      out.push(day);
      n += day.entries.length;
    }
    return out;
  }, [days, limit]);
  const rendered = useMemo(() => shownDays.flatMap((day) => day.entries), [shownDays]);

  useEffect(() => {
    if (rendered.length >= items.length) return;
    const id = window.requestIdleCallback(() => setLimit((n) => n + CHUNK), { timeout: 250 });
    return () => window.cancelIdleCallback(id);
  }, [rendered.length, items.length]);

  // Every render, after the DOM is in: a jump that was waiting for its day now has it.
  useLayoutEffect(() => {
    if (pending.current?.()) pending.current = null;
  });
  const oldest = useMemo(
    () => snap.journal.reduce((min, entry) => Math.min(min, msOf(entry.created_at) || min), Date.now()),
    [snap.journal],
  );

  const current = items.find((entry) => entry.id === selectedId) ?? items[0];
  const currentDay = current ? dayKey(current.ms) : null;

  // The calendar follows the day you are reading; paging it by hand holds until you move on.
  const [month, setMonth] = useState(() => monthIndex(current?.ms ?? Date.now()));
  useEffect(() => {
    if (currentDay) setMonth(monthIndex(keyMidnight(currentDay)));
  }, [currentDay]);

  // A new search is a new list: start it at the top, on its newest entry.
  useEffect(() => {
    reader.current?.scrollTo({ top: 0 });
    setSelectedId(null);
  }, [query, search]);

  // Keep the day you are on visible in the day list, without scrolling anything else.
  useEffect(() => {
    const list = dayList.current;
    const node = currentDay ? list?.querySelector<HTMLElement>(`[data-day="${currentDay}"]`) : null;
    if (!list || !node) return;
    const top = node.offsetTop;
    const bottom = top + node.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }, [currentDay]);

  const settle = () => {
    if (settling.current != null) window.clearTimeout(settling.current);
    settling.current = window.setTimeout(() => {
      settling.current = null;
    }, 160);
  };

  const scrollTo = (node: HTMLElement | undefined, smooth: boolean) => {
    if (!node || !reader.current) return;
    settle();
    // A day's heading carries the gap above it as padding; past that gap, a day you jumped to
    // sits exactly where the first day sits, 4px under the top.
    const gap = node.tagName === "HEADER" ? Math.max(0, Number.parseFloat(getComputedStyle(node).paddingTop) - 4) : 0;
    reader.current.scrollTo({ top: node.offsetTop + gap, behavior: smooth ? "smooth" : "auto" });
  };

  const onScroll = () => {
    const el = reader.current;
    if (!el) return;
    if (settling.current != null) {
      settle();
      return;
    }
    let chosen: number | undefined;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 2) {
      chosen = rendered[rendered.length - 1]?.id;
    } else {
      const line = el.scrollTop + READING_LINE;
      for (const entry of rendered) {
        const node = entryNodes.current.get(entry.id);
        if (!node) continue;
        if (node.offsetTop <= line) chosen = entry.id;
        else break;
      }
    }
    if (chosen != null && chosen !== current?.id) setSelectedId(chosen);
  };

  /** Scroll to a node once it exists, mounting the column up to `index` first if it must. */
  const reach = (index: number, node: () => HTMLElement | undefined, smooth: boolean) => {
    const run = () => {
      const target = node();
      if (!target) return false;
      scrollTo(target, smooth);
      return true;
    };
    if (index < rendered.length && run()) return;
    pending.current = run;
    setLimit((n) => Math.max(n, index + CHUNK));
  };

  const goToDay = (key: string) => {
    const day = days.find((d) => d.key === key);
    if (!day) return;
    setSelectedId(day.entries[0]!.id);
    reach(items.indexOf(day.entries[day.entries.length - 1]!), () => dayNodes.current.get(key), false);
  };

  /** J and K: the next or previous entry, scrolled to — with its day's heading when it opens one. */
  const step = (delta: 1 | -1) => {
    if (!current) return;
    const index = items.findIndex((entry) => entry.id === current.id);
    const next = items[index + delta];
    if (!next) return;
    setSelectedId(next.id);
    const before = items[index + delta - 1];
    const opensDay = !before || dayKey(before.ms) !== dayKey(next.ms);
    reach(
      index + delta,
      () => (opensDay ? dayNodes.current.get(dayKey(next.ms)) : entryNodes.current.get(next.id)),
      true,
    );
  };
  const stepRef = useRef(step);
  stepRef.current = step;

  /* Bound in the capture phase so "/" is this page's search, not the panel's: the panel takes a
     bare "/" for its own box, and on a page with a search of its own that is the wrong one. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      if (typing || !layer.frontmost()) return;
      if (event.key === "/") {
        event.preventDefault();
        event.stopImmediatePropagation();
        searchBox.current?.focus();
        searchBox.current?.select();
      } else if (event.key === "j" || event.key === "k") {
        event.preventDefault();
        stepRef.current(event.key === "j" ? 1 : -1);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [layer]);

  const registerEntry = useCallback((id: number, node: HTMLElement | null) => {
    if (node) entryNodes.current.set(id, node);
    else entryNodes.current.delete(id);
  }, []);
  const select = useCallback((id: number) => setSelectedId(id), []);
  // Through a ref: the panel makes a new `remove` every render (its clock ticks every ten
  // seconds), and a new callback here would re-render all 709 memoised entries with it.
  const removeRef = useRef(remove);
  removeRef.current = remove;
  const deleteEntry = useCallback((entry: JournalEntry) => void removeRef.current("journal", entry.id, entry.entry), []);

  return (
    <div ref={page} style={{ height }} className="-mb-7 flex h-[calc(100dvh-72px)] flex-col gap-[22px]">
      <PageTitle title="Journal" count={items.length.toLocaleString()} subtitle="His private diary — how the days felt.">
        <form role="search" className="m-0" onSubmit={(event) => event.preventDefault()}>
          <label htmlFor="journal-search" className="sr-only">
            Search his journal
          </label>
          <div className="border-input bg-card text-muted-foreground/80 focus-within:border-ring flex h-[34px] w-[300px] items-center gap-2 rounded-lg border ps-2.5 pe-2 transition-colors">
            <Search className="size-3.5 shrink-0" aria-hidden />
            <input
              id="journal-search"
              ref={searchBox}
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Escape") return;
                // A search to undo first; with nothing to undo, Escape just leaves the box.
                event.preventDefault();
                if (search) setSearch("");
                else event.currentTarget.blur();
              }}
              placeholder="Search his journal…"
              className="text-foreground placeholder:text-muted-foreground/80 h-[30px] min-w-0 flex-1 bg-transparent p-0 text-[13px] outline-none [&::-webkit-search-cancel-button]:hidden"
            />
            <Kbd>/</Kbd>
          </div>
        </form>
      </PageTitle>

      {snap.journal.length === 0 ? (
        <Empty title="The journal is empty">When he writes about a day, it reads here, day by day.</Empty>
      ) : (
        <div className="flex min-h-0 flex-1 gap-10">
          {/* ── The days ── */}
          <aside aria-label="Days" className="flex min-h-0 w-[280px] shrink-0 flex-col gap-[22px] pb-7">
            <HeatCalendar
              month={month}
              onMonth={setMonth}
              counts={counts}
              selected={currentDay}
              onPick={goToDay}
              min={monthIndex(oldest)}
              max={monthIndex(Date.now())}
              noun={["entry", "entries"]}
            />
            <section aria-label="Recent days" className="flex min-h-0 flex-col gap-1.5">
              <Label className="px-2.5">Recent days</Label>
              <div ref={dayList} className="relative flex min-h-0 flex-col gap-0.5 overflow-y-auto">
                {days.map((day) => {
                  const on = day.key === currentDay;
                  return (
                    <button
                      key={day.key}
                      type="button"
                      data-day={day.key}
                      aria-current={on ? "date" : undefined}
                      onClick={() => goToDay(day.key)}
                      className={cn(
                        "flex shrink-0 flex-col gap-[3px] rounded-[10px] px-2.5 py-2 text-left transition-colors",
                        on ? "bg-muted" : "hover:bg-muted/50",
                      )}
                    >
                      <span className="flex items-baseline gap-2">
                        <span className={cn("text-[12.5px]", on ? "text-foreground font-semibold" : "text-foreground/80 font-medium")}>
                          {shortDate(day.ms)}
                        </span>
                        <span className="flex-1" />
                        <span className="text-muted-foreground/80 text-[11.5px] tabular-nums">{day.entries.length}</span>
                      </span>
                      <span className="text-muted-foreground/80 truncate text-xs">{firstLine(day.entries[0]!.entry)}</span>
                    </button>
                  );
                })}
                {days.length === 0 ? <p className="text-muted-foreground/80 px-2.5 text-xs">No days match.</p> : null}
              </div>
            </section>
          </aside>

          {/* ── The reading column ── */}
          <div
            ref={reader}
            onScroll={onScroll}
            className="relative min-w-0 flex-1 overflow-y-auto"
            aria-label={current ? longDate(current.ms) : "Journal"}
            role="region"
          >
            <div className="mx-auto flex w-full max-w-[720px] flex-col pb-7">
              {days.length === 0 ? (
                <Empty title="Nothing matches">
                  Nothing in his journal matches “{search || query}”.
                </Empty>
              ) : (
                shownDays.map((day, dayIndex) => {
                  const rel = relativeDay(day.ms);
                  return (
                    <div key={day.key} className="flex flex-col">
                      <header
                        ref={(node) => {
                          if (node) dayNodes.current.set(day.key, node);
                          else dayNodes.current.delete(day.key);
                        }}
                        className={cn("border-border flex items-baseline gap-2.5 border-b pb-4", dayIndex === 0 ? "pt-1" : "pt-7")}
                      >
                        <h2 className="text-foreground m-0 text-[15px] font-semibold">{longDate(day.ms)}</h2>
                        <span className="text-muted-foreground/80 text-[12.5px]">· {plural(day.entries.length, "entry", "entries")}</span>
                        <span className="flex-1" />
                        {rel === "Today" || rel === "Yesterday" ? (
                          <span className="text-muted-foreground/80 text-[11.5px]">{rel}</span>
                        ) : null}
                      </header>
                      {day.entries.map((entry, index) => (
                        <EntryView
                          key={entry.id}
                          entry={entry}
                          selected={entry.id === current?.id}
                          divided={index > 0}
                          onSelect={select}
                          onDelete={deleteEntry}
                          register={registerEntry}
                        />
                      ))}
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* ── About the entry in view ── */}
          {current ? (
            <InThisEntry
              key={current.id}
              entry={current}
              snap={snap}
              onDelete={() => void remove("journal", current.id, current.entry)}
              onNavigate={onNavigate}
              onOpenProject={onOpenProject}
            />
          ) : (
            <div className="w-[300px] shrink-0" />
          )}
        </div>
      )}
    </div>
  );
}

/* ── One entry ──────────────────────────────────────────────────────────── */

const EntryView = memo(function EntryView({
  entry,
  selected,
  divided,
  onSelect,
  onDelete,
  register,
}: {
  entry: Entry;
  selected: boolean;
  divided: boolean;
  onSelect: (id: number) => void;
  onDelete: (entry: JournalEntry) => void;
  register: (id: number, node: HTMLElement | null) => void;
}) {
  const words = useMemo(() => wordCount(entry.entry), [entry.entry]);
  return (
    <ItemMenu title="Journal entry" copy={entry.entry} onDelete={() => onDelete(entry)}>
      {/* A click picks the entry for the right-hand column; J and K are the keyboard's way. */}
      <section
        ref={(node) => register(entry.id, node)}
        aria-label={`Entry at ${clock(entry.ms)}`}
        aria-current={selected ? "true" : undefined}
        onClick={() => onSelect(entry.id)}
        className={cn("pt-5 pb-6", divided && "border-border/80 border-t")}
      >
        <div className="mb-2.5 flex items-center gap-2">
          {selected ? <span aria-hidden className="bg-foreground/80 size-1.5 shrink-0 rounded-full" /> : null}
          <time
            dateTime={entry.created_at}
            className={cn("text-xs tabular-nums", selected ? "text-foreground font-semibold" : "text-muted-foreground/80")}
          >
            {clock(entry.ms)}
          </time>
          <span aria-hidden className="text-muted-foreground/50 text-xs">
            ·
          </span>
          <span className="text-muted-foreground/80 text-xs tabular-nums">{plural(words, "word")}</span>
        </div>
        {/* His journal is Markdown too — headings, lists and links render. */}
        <div className={SERIF}>
          <Markdown>{entry.entry}</Markdown>
        </div>
      </section>
    </ItemMenu>
  );
});

/* ── In this entry ──────────────────────────────────────────────────────── */

function InThisEntry({
  entry,
  snap,
  onDelete,
  onNavigate,
  onOpenProject,
}: {
  entry: Entry;
  snap: BrainSnapshot;
  onDelete: () => void;
  onNavigate?: (tab: Tab) => void;
  onOpenProject?: (ref: ProjectRef) => void;
}) {
  const [copied, setCopied] = useState(false);
  const projects = useMemo(() => mentionedProjects(entry.entry, snap.projects), [entry.entry, snap.projects]);
  const kept = useMemo(() => keptMemories(entry.ms, snap.memories), [entry.ms, snap.memories]);
  const files = useMemo(() => mentionedFiles(entry.entry), [entry.entry]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const CHIP =
    "border-border bg-muted/60 text-foreground/80 inline-flex h-6 max-w-full items-center gap-1.5 rounded-full border text-[11.5px]";

  return (
    <aside aria-label="In this entry" className="flex min-h-0 w-[300px] shrink-0 flex-col gap-[22px] overflow-y-auto pt-1 pb-7">
      <div className="border-border flex items-baseline gap-2 border-b pb-4">
        <span className="text-[13px] font-semibold">In this entry</span>
        <span className="text-muted-foreground/80 text-xs tabular-nums">{clock(entry.ms)}</span>
      </div>

      {projects.length ? (
        <div className="flex flex-col gap-2">
          <Label>{projects.length === 1 ? "Project" : "Projects"}</Label>
          <div className="flex flex-wrap gap-1.5">
            {projects.map((project) => {
              const body = (
                <>
                  <Folder className="text-muted-foreground/80 size-3 shrink-0" aria-hidden />
                  <span className="truncate">{project.name}</span>
                </>
              );
              return onOpenProject ? (
                <button
                  key={project.id}
                  type="button"
                  onClick={() => onOpenProject(project.id)}
                  className={cn(CHIP, "hover:bg-muted hover:text-foreground ps-[7px] pe-[9px] transition-colors")}
                >
                  {body}
                </button>
              ) : (
                <span key={project.id} className={cn(CHIP, "ps-[7px] pe-[9px]")}>
                  {body}
                </span>
              );
            })}
          </div>
        </div>
      ) : null}

      {kept.length ? (
        <div className="flex flex-col gap-2">
          <Label>What he kept from it</Label>
          {kept.map(({ memory, offset }) => {
            const body = (
              <>
                <span className="text-muted-foreground/80 flex items-center gap-2 text-[11px]">
                  <Brain className="size-[13px] shrink-0" aria-hidden />
                  <span className="font-mono">#{memory.id}</span>
                  <span className="text-muted-foreground/50">·</span>
                  <span>{memory.level === "core" ? "Front of mind" : "Recall"}</span>
                  <span className="text-muted-foreground/50">·</span>
                  <span className="tabular-nums">importance {memory.importance}</span>
                </span>
                <span className="text-foreground/80 line-clamp-5 text-[12.5px] leading-[1.5]">{plain(memory.content)}</span>
                <span className="text-muted-foreground/80 flex items-center gap-1.5 text-[11.5px]">
                  <span className="flex-1">{keptLabel(offset)}</span>
                  {onNavigate ? (
                    <>
                      <span className="text-foreground/80">Open</span>
                      <ChevronRight className="size-3" aria-hidden />
                    </>
                  ) : null}
                </span>
              </>
            );
            const card = "bg-card border-border text-foreground flex flex-col gap-2 rounded-[10px] border p-3 text-left";
            return onNavigate ? (
              <button
                key={memory.id}
                type="button"
                onClick={() => onNavigate("memory")}
                className={cn(card, "hover:bg-muted/40 transition-colors")}
              >
                {body}
              </button>
            ) : (
              <div key={memory.id} className={card}>
                {body}
              </div>
            );
          })}
        </div>
      ) : null}

      {files.length ? (
        <div className="flex flex-col gap-2">
          <Label>Also mentions</Label>
          <div className="flex flex-wrap gap-1.5">
            {files.map((file) => (
              <span key={file} className={cn(CHIP, "px-[9px] font-mono text-[11px]")}>
                <span className="truncate">{file}</span>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="border-border/80 flex flex-col gap-2 border-t pt-[18px]">
        <Label>Entry</Label>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={async () => setCopied(await copyText(entry.entry))}
            className={cn(BUTTON.secondary, "gap-[7px] [&_svg]:size-3.5")}
          >
            <Copy aria-hidden />
            {copied ? "Copied" : "Copy"}
          </button>
          <span className="flex-1" />
          <button type="button" onClick={onDelete} className={cn(BUTTON.danger, "px-2.5")}>
            Delete…
          </button>
        </div>
      </div>

      <div className="text-muted-foreground/80 mt-auto flex items-center gap-1.5 text-[11.5px]">
        <Kbd>J</Kbd>
        <Kbd>K</Kbd>
        <span>move between entries</span>
        <span className="text-muted-foreground/50">·</span>
        <Kbd>/</Kbd>
        <span>search</span>
      </div>
    </aside>
  );
}
