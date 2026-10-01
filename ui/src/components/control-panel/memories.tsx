/**
 * Memory: what he holds onto, and how close to hand — a list to scan on the left, the one you
 * picked in full on the right.
 *
 * It replaced two grids of full-height cards, one per level, where ninety memories were a wall
 * you scrolled past rather than read. Two things the cards hid are what this screen is built
 * around:
 *
 *   - the copies. One checkpoint went in eight times on 10 August, and front-of-mind memories
 *     ride along on every turn, so each copy is paid for every time he speaks. The groups are
 *     found here from the words (`near-duplicates.ts`), flagged on their rows, and merged by
 *     keeping one and forgetting the rest.
 *   - the knobs. Level, importance and tags decide what he carries and what he recalls, and the
 *     cards showed them as badges you could not change.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowUpDown,
  ArrowUpToLine,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Copy,
  GitMerge,
  Minus,
  Pencil,
  Plus,
  X,
} from "lucide-react";

import { Markdown } from "@/components/files";
import { useConfirm } from "@/components/ui/confirm";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ItemMenu } from "@/components/ui/item-menu";
import { Layer, useLayer } from "@/hooks/use-layer";
import { deleteBrainItem } from "@/lib/backend/brain";
import type { BrainSnapshot, Memory } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { BUTTON, Chip, Empty, Kbd, Label, PageTitle, Segmented } from "./chrome";
import { matches } from "./format";
import { addsSomething, describeDifference, nearDuplicateGroups, similarity } from "./near-duplicates";
import type { Handlers } from "./types";

type Level = "core" | "recall";
type Lens = Level | "all";
type Sort = "importance" | "newest";

/** Rows drawn before "Show N more". Enough to fill the column twice; the rest on asking. */
const PAGE = 25;

const at = (iso: string) => {
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
};
const isCore = (m: Memory) => m.level === "core";

/* ── Dates, as the design writes them ─────────────────────────────────────
 * Day before month ("13 Aug", "10 August 2026, 5:58 AM") whatever the locale's order, because
 * that is what the approved board says; the month names and the clock still come from the
 * locale. */

function dayMonth(iso: string, month: "short" | "long" = "short"): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getDate()} ${d.toLocaleDateString(undefined, { month })}`;
}

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function sameDay(a: string, b: string): boolean {
  return new Date(a).toDateString() === new Date(b).toDateString();
}

function ago(iso: string, now = Date.now()): string {
  const minutes = Math.floor((now - at(iso)) / 60_000);
  const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  if (minutes < 1) return "just now";
  if (minutes < 60) return plural(minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return plural(hours, "hour");
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 14) return plural(days, "day");
  // Weeks run long on purpose: "7 weeks ago" says more than "1 month ago" about a checkpoint.
  if (days < 63) return plural(Math.floor(days / 7), "week");
  if (days < 365) return plural(Math.floor(days / 30), "month");
  return plural(Math.floor(days / 365), "year");
}

const NUMBER = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
const spelled = (n: number) => NUMBER[n] ?? String(n);
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** One line of plain text from his markdown, for a two-line row. */
function plain(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[*_`#>]+/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/** "#38, #37 and 2 more" */
function idList(ids: number[], max = 3): string {
  const named = ids.slice(0, max).map((id) => `#${id}`);
  const rest = ids.length - named.length;
  if (rest > 0) return `${named.join(", ")} and ${rest} more`;
  if (named.length < 2) return named.join("");
  return `${named.slice(0, -1).join(", ")} and ${named[named.length - 1]}`;
}

/** Session-only memory of "Not now": the notice stays down until the copies change. */
const DISMISS_KEY = "kith-memory-duplicates-dismissed";
function readDismissed(): string {
  try {
    return sessionStorage.getItem(DISMISS_KEY) ?? "";
  } catch {
    return "";
  }
}
function writeDismissed(value: string) {
  try {
    sessionStorage.setItem(DISMISS_KEY, value);
  } catch {
    /* private window: it just comes back next time */
  }
}

/* ── The page ───────────────────────────────────────────────────────────── */

export function Memories({
  snap,
  query,
  refresh,
  remove,
  relevel,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const confirm = useConfirm();
  const layer = useLayer(Layer.Panel);

  const [content, setContent] = useState("");
  const [level, setLevel] = useState<Level>("recall");
  const [lens, setLens] = useState<Lens>("core");
  const [tag, setTag] = useState<string | null>(null);
  const [allTags, setAllTags] = useState(false);
  const [sort, setSort] = useState<Sort>("importance");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [merging, setMerging] = useState<number[] | null>(null);
  const [dismissed, setDismissed] = useState(readDismissed);
  const lastIndex = useRef({ key: "", index: 0 });
  const listRef = useRef<HTMLDivElement>(null);

  const all = snap.memories;
  const byId = useMemo(() => new Map(all.map((m) => [m.id, m])), [all]);

  /* The copies, over every memory rather than what the filters leave: a row's "similar" should
     not come and go as you narrow the list. Newest first, so a tie between groups goes to the
     more recent one — the one still being saved. */
  const groups = useMemo(
    () => nearDuplicateGroups([...all].sort((a, b) => at(b.created_at) - at(a.created_at))),
    [all],
  );
  const groupOf = useMemo(() => {
    const map = new Map<number, number[]>();
    for (const group of groups) for (const id of group) map.set(id, group);
    return map;
  }, [groups]);

  const searched = useMemo(() => all.filter((m) => matches(query, m.content, m.tags.join(" "))), [all, query]);
  const counts = {
    core: searched.filter(isCore).length,
    recall: searched.filter((m) => !isCore(m)).length,
    all: searched.length,
  };
  /* A search that only matches the other level moves the view to where the matches are. Opening
     a recall memory from ⌘K landed on "Front of mind" and said "Nothing here" with the memory one
     click away. Only on a new search — a segment you pick yourself stays picked. */
  useEffect(() => {
    if (!query) return;
    if (lens === "core" && counts.core === 0 && counts.recall > 0) setLens("recall");
    else if (lens === "recall" && counts.recall === 0 && counts.core > 0) setLens("core");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to the search, not to the lens
  }, [query]);
  const inLens = useMemo(
    () => searched.filter((m) => lens === "all" || (lens === "core" ? isCore(m) : !isCore(m))),
    [searched, lens],
  );
  // The tags worth filtering by are the ones in what you are looking at, most used first.
  const tagCounts = useMemo(() => {
    const tally = new Map<string, number>();
    for (const m of inLens) for (const t of m.tags) tally.set(t, (tally.get(t) ?? 0) + 1);
    return [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [inLens]);
  const activeTag = tag && tagCounts.some(([t]) => t === tag) ? tag : null;

  const list = useMemo(() => {
    const rows = inLens.filter((m) => !activeTag || m.tags.includes(activeTag));
    return rows.sort((a, b) =>
      sort === "importance"
        ? b.importance - a.importance || at(b.created_at) - at(a.created_at)
        : at(b.created_at) - at(a.created_at),
    );
  }, [inLens, activeTag, sort]);

  /* The selection survives the list changing under it: forget the selected memory and the one
     that slid into its place is selected, rather than the list jumping back to the top. Only
     within one view, though — switch to Recall and the place you held in Front of mind means
     nothing there, so it starts at the top. */
  const listKey = `${lens}|${activeTag}|${sort}|${query}`;
  const found = list.findIndex((m) => m.id === selectedId);
  const held = lastIndex.current.key === listKey ? lastIndex.current.index : 0;
  const index = found >= 0 ? found : Math.min(held, list.length - 1);
  const selected = index >= 0 ? list[index] : null;
  if (index >= 0) lastIndex.current = { key: listKey, index };

  // "Show N more" pages within one view of the list; a new filter starts the count again.
  const [paging, setPaging] = useState({ key: listKey, shown: PAGE });
  const shown = Math.max(paging.key === listKey ? paging.shown : PAGE, index + 1);
  const showMore = () => setPaging({ key: listKey, shown: shown + PAGE });

  /* The notice speaks about what is in view: groups with at least two copies in this list,
     biggest first. */
  const inView = useMemo(() => {
    const ids = new Set(list.map((m) => m.id));
    return groups
      .map((group) => group.filter((id) => ids.has(id)).map((id) => byId.get(id)!))
      .filter((group) => group.length > 1);
  }, [groups, list, byId]);
  const signature = inView.map((group) => group.map((m) => m.id).join(",")).join(";");
  const showNotice = inView.length > 0 && dismissed !== signature;

  const select = (id: number) => {
    setEditingId(null);
    setSelectedId(id);
  };
  const move = (by: number) => {
    const next = list[Math.max(0, Math.min(list.length - 1, index + by))];
    if (next) select(next.id);
  };
  /* Open a memory from somewhere it may not be listed — a copy in the other level, say. Widen
     the list to everything first, so the reader is never showing something the list hides. */
  const reveal = (id: number) => {
    if (!list.some((m) => m.id === id)) {
      setLens("all");
      setTag(null);
    }
    select(id);
  };

  // Keep the selected row in sight as the keyboard walks the list.
  useEffect(() => {
    if (!selected) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-memory="${selected.id}"]`);
    row?.scrollIntoView({ block: "nearest" });
    // Focus follows the selection while it is in the list, so the ring is on the row you moved to.
    if (row && listRef.current?.contains(document.activeElement)) row.focus({ preventScroll: true });
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const forget = (m: Memory) => void remove("memory", m.id, m.content);
  const toggleLevel = (m: Memory) => relevel(m.id, isCore(m) ? "recall" : "core");

  /** Keep one, forget the rest — one question for the lot, not one per copy. */
  const merge = async (keep: Memory, others: Memory[]) => {
    if (others.length === 0) return false;
    const ok = await confirm({
      title: `Forget ${others.length === 1 ? "the other copy" : `the other ${others.length} copies`}?`,
      description: `He keeps #${keep.id} and loses ${idList(others.map((m) => m.id), 6)} for good — this can't be undone.`,
      subject: plain(keep.content),
      confirmLabel: `Forget ${others.length}`,
      destructive: true,
    });
    if (!ok) return false;
    for (const m of others) {
      try {
        await deleteBrainItem("memory", m.id);
      } catch {
        // One that would not go stays listed after the refresh, which says so better than a toast.
      }
    }
    refresh();
    reveal(keep.id);
    return true;
  };

  /* ↑↓ move, E edits, F forgets. On the window rather than the list so they work without first
     clicking into it, and only while nothing sits on top (a menu, a dialog) and no field of this
     panel has the keys — the same rule the panel's own Escape follows.

     "Of this panel" is the part that matters. The panel covers the chat rather than replacing
     it, so the chat composer underneath keeps focus when the panel opens, and the first test of
     this pressed ↓ three times into nothing and then typed "e" and "f" into a composer nobody
     could see. A field outside the panel is not one you are typing in: the key is ours, and it
     is kept from reaching the hidden field. */
  const root = useRef<HTMLDivElement>(null);
  const typingHere = (target: EventTarget | null) => {
    if (!(target instanceof HTMLElement)) return false;
    if (!target.isContentEditable && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return false;
    // The panel's own frame is its first `fixed` ancestor (the overlay covering the window).
    const panel = root.current?.closest(".fixed") ?? document.body;
    return panel.contains(target);
  };
  useEffect(() => {
    // Opening the page leaves focus in that hidden composer. Let go of it — blurring rather than
    // focusing a row, so the keys work at once without a focus ring greeting you on arrival.
    const active = document.activeElement;
    const panel = root.current?.closest(".fixed");
    if (active instanceof HTMLElement && active !== document.body && panel && !panel.contains(active)) active.blur();
  }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      if (typingHere(event.target)) return;
      if (!layer.frontmost()) return;
      const key = event.key.toLowerCase();
      if (key === "arrowdown" || key === "arrowup") {
        event.preventDefault();
        move(key === "arrowdown" ? 1 : -1);
      } else if (key === "e" && selected) {
        event.preventDefault();
        setEditingId(selected.id);
      } else if (key === "f" && selected) {
        event.preventDefault();
        forget(selected);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const composer = useRef<HTMLInputElement>(null);
  const add = () => {
    // Remember with nothing typed is a pointer to where the typing goes, not a dimmed button.
    if (!content.trim()) return composer.current?.focus();
    create("memory", { content, level });
    setContent("");
  };

  const lensName = lens === "core" ? "Front of mind" : lens === "recall" ? "Recall" : "All memories";
  const visibleTags = allTags ? tagCounts : fitTags(tagCounts);
  const hiddenTags = tagCounts.length - visibleTags.length;

  return (
    <div ref={root} className="flex h-[calc(100vh-100px)] min-h-[620px] flex-col gap-[22px]">
      <PageTitle title="Memory" count={searched.length} subtitle="What he holds onto — and how close to hand." />

      <form
        className="shrink-0"
        onSubmit={(event) => {
          event.preventDefault();
          add();
        }}
      >
        <label htmlFor="mem-new" className="sr-only">
          New memory
        </label>
        <div className="border-input bg-card focus-within:border-ring flex h-11 items-center gap-2.5 rounded-[10px] border ps-3.5 pe-1.5 transition-colors">
          <Plus aria-hidden className="text-muted-foreground/80 size-[15px] shrink-0" />
          <input
            id="mem-new"
            ref={composer}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Give him something to remember…"
            className="text-foreground placeholder:text-muted-foreground/80 h-10 min-w-0 flex-1 bg-transparent text-[13.5px] outline-none"
          />
          <LevelToggle label="Remember as" value={level} onChange={setLevel} />
          <button type="submit" className={BUTTON.primary}>
            Remember
          </button>
        </div>
      </form>

      <div className="grid min-h-0 flex-1 grid-cols-[600px_minmax(0,1fr)] gap-4">
        {/* ── The list ── */}
        <section aria-label="Memories" className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-[14px] border">
          <div className="flex shrink-0 items-center gap-2 px-3 pt-3">
            <Segmented
              label="Which memories"
              value={lens}
              onChange={(value) => {
                setLens(value);
                setTag(null);
              }}
              options={[
                { value: "core", label: "Front of mind", count: counts.core },
                { value: "recall", label: "Recall", count: counts.recall },
                { value: "all", label: "All", count: counts.all },
              ]}
            />
            <span className="flex-1" />
            <button
              type="button"
              onClick={() => setSort(sort === "importance" ? "newest" : "importance")}
              aria-label={sort === "importance" ? "Sorted by importance — sort by newest" : "Sorted by newest — sort by importance"}
              className="text-foreground/80 hover:bg-muted/50 hover:text-foreground flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] transition-colors"
            >
              <ArrowUpDown className="size-3.5" />
              {sort === "importance" ? "Importance" : "Newest"}
            </button>
          </div>

          {tagCounts.length > 0 ? (
            <div
              role="group"
              aria-label="Filter by tag"
              className={cn(
                "flex shrink-0 gap-1.5 px-3 pt-2.5",
                allTags ? "max-h-[132px] flex-wrap overflow-y-auto" : "overflow-hidden",
              )}
            >
              {visibleTags.map(([name, count]) => {
                const on = activeTag === name;
                return (
                  <button
                    key={name}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setTag(on ? null : name)}
                    className={cn(
                      "flex h-6 shrink-0 items-center gap-[5px] rounded-full border px-[9px] text-[11.5px] whitespace-nowrap transition-colors",
                      on
                        ? "border-input bg-muted text-foreground"
                        : "border-border bg-muted/60 text-foreground/80 hover:text-foreground",
                    )}
                  >
                    {name}
                    <span className="text-muted-foreground/80 tabular-nums">{count}</span>
                  </button>
                );
              })}
              {hiddenTags > 0 || allTags ? (
                <button
                  type="button"
                  onClick={() => setAllTags(!allTags)}
                  aria-expanded={allTags}
                  className="text-muted-foreground hover:text-foreground h-6 shrink-0 rounded-full px-[9px] text-[11.5px] whitespace-nowrap"
                >
                  {allTags ? "Fewer" : `+ ${hiddenTags} more`}
                </button>
              ) : null}
            </div>
          ) : null}

          {showNotice ? (
            <DuplicatesNotice
              groups={inView}
              onDismiss={() => {
                writeDismissed(signature);
                setDismissed(signature);
              }}
              onReview={() => setMerging(inView[0].map((m) => m.id))}
            />
          ) : null}

          <div
            ref={listRef}
            role="listbox"
            aria-label={`${lensName}, sorted by ${sort === "importance" ? "importance" : "newest"}`}
            aria-activedescendant={selected ? `memory-row-${selected.id}` : undefined}
            className={cn("flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-1", showNotice ? "" : "mt-2")}
          >
            {list.length === 0 ? (
              <Empty className="m-2" title={all.length === 0 ? "Nothing yet" : "Nothing here"}>
                {all.length === 0
                  ? "He hasn't saved anything. Give him something to remember above."
                  : query || activeTag
                    ? "No memory matches that."
                    : lens === "core"
                      ? "Nothing is front of mind — everything he knows comes up only when it's related."
                      : "Nothing in recall."}
              </Empty>
            ) : (
              list.slice(0, shown).map((m) => (
                <MemoryRow
                  key={m.id}
                  m={m}
                  selected={m.id === selected?.id}
                  similar={groupOf.has(m.id)}
                  onSelect={() => select(m.id)}
                  onEdit={() => {
                    select(m.id);
                    setEditingId(m.id);
                  }}
                  onLevel={() => toggleLevel(m)}
                  onForget={() => forget(m)}
                />
              ))
            )}
          </div>

          <div className="text-muted-foreground/80 flex h-[42px] shrink-0 items-center gap-2 border-t border-border/80 px-4 text-[11.5px]">
            {list.length > shown ? (
              <button type="button" onClick={showMore} className="text-foreground/80 hover:text-foreground text-xs">
                Show {list.length - shown} more
              </button>
            ) : null}
            <span className="flex-1" />
            <Kbd>↑↓</Kbd>
            <span>move</span>
            <span className="text-muted-foreground/50">·</span>
            <Kbd>E</Kbd>
            <span>edit</span>
            <span className="text-muted-foreground/50">·</span>
            <Kbd>F</Kbd>
            <span>forget</span>
          </div>
        </section>

        {/* ── The reader ── */}
        {selected ? (
          <Reader
            key={selected.id}
            m={selected}
            position={index + 1}
            total={list.length}
            copies={(groupOf.get(selected.id) ?? []).filter((id) => id !== selected.id).map((id) => byId.get(id)!)}
            editing={editingId === selected.id}
            onEdit={setEditingId}
            onPrev={index > 0 ? () => move(-1) : undefined}
            onNext={index < list.length - 1 ? () => move(1) : undefined}
            onOpen={reveal}
            onLevel={(value) => value !== selected.level && relevel(selected.id, value)}
            onForget={() => forget(selected)}
            onMerge={merge}
            update={update}
          />
        ) : (
          <section aria-label="Memory" className="border-border bg-card flex min-h-0 flex-col justify-center rounded-[14px] border p-10">
            <Empty title="Nothing selected">Pick a memory on the left to read it in full.</Empty>
          </section>
        )}
      </div>

      {merging ? (
        <MergeDialog
          group={merging.map((id) => byId.get(id)).filter((m): m is Memory => Boolean(m))}
          preferred={selected?.id}
          onClose={() => setMerging(null)}
          onMerge={async (keep, others) => {
            if (await merge(keep, others)) setMerging(null);
          }}
        />
      ) : null}
    </div>
  );
}

/** As many tag chips as fit one row of the 600px column, by a character budget rather than a
 *  measurement — close enough, and it cannot flicker. */
function fitTags(tags: [string, number][]): [string, number][] {
  let budget = 76;
  const out: [string, number][] = [];
  for (const entry of tags) {
    const cost = entry[0].length + String(entry[1]).length + 5;
    if (out.length > 0 && cost > budget) break;
    budget -= cost;
    out.push(entry);
  }
  return out;
}

/* ── Pieces ─────────────────────────────────────────────────────────────── */

/** Recall / Front of mind, in the compact pill pair the composer and the reader share. */
function LevelToggle({ label, value, onChange }: { label: string; value: string; onChange: (value: Level) => void }) {
  const options: [Level, string][] = [
    ["recall", "Recall"],
    ["core", "Front of mind"],
  ];
  return (
    <div role="group" aria-label={label} className="border-border bg-background flex shrink-0 gap-0.5 rounded-lg border p-0.5">
      {options.map(([level, name]) => {
        const on = (value === "core") === (level === "core");
        return (
          <button
            key={level}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(level)}
            className={cn(
              "h-[26px] rounded-md px-2.5 text-xs whitespace-nowrap transition-colors",
              on ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {name}
          </button>
        );
      })}
    </div>
  );
}

/** Importance as five dots: two points a dot, rounded. */
function Dots({ importance }: { importance: number }) {
  const filled = Math.round(importance / 2);
  return (
    <span className="flex shrink-0 gap-[3px]" role="img" aria-label={`Importance ${importance} of 10`}>
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={cn("size-[5px] rounded-full", i < filled ? "bg-foreground/75" : "bg-foreground/[0.13]")} />
      ))}
    </span>
  );
}

function SimilarChip() {
  return (
    <span className="border-kith/25 bg-kith/[0.08] text-kith/90 flex h-[18px] shrink-0 items-center rounded-full border px-[7px] text-[10.5px]">
      similar
    </span>
  );
}

function MemoryRow({
  m,
  selected,
  similar,
  onSelect,
  onEdit,
  onLevel,
  onForget,
}: {
  m: Memory;
  selected: boolean;
  similar: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onLevel: () => void;
  onForget: () => void;
}) {
  const core = isCore(m);
  const tags = m.tags.slice(0, 3).map((t) => `#${t}`).join(" ") + (m.tags.length > 3 ? ` +${m.tags.length - 3}` : "");
  return (
    <ItemMenu
      title={`Memory #${m.id}`}
      copy={m.content}
      copyId={String(m.id)}
      actions={[
        { label: "Edit", icon: <Pencil className="size-3.5" />, hint: "E", onSelect: onEdit },
        {
          label: core ? "Move to recall" : "Move to front of mind",
          icon: core ? <ArrowDownToLine className="size-3.5" /> : <ArrowUpToLine className="size-3.5" />,
          onSelect: onLevel,
        },
      ]}
      onDelete={onForget}
      deleteLabel="Forget"
    >
      <button
        type="button"
        role="option"
        id={`memory-row-${m.id}`}
        data-memory={m.id}
        aria-selected={selected}
        tabIndex={selected ? 0 : -1}
        onClick={onSelect}
        onContextMenu={onSelect}
        className={cn(
          "flex shrink-0 flex-col gap-[5px] rounded-[10px] px-3 py-2 text-left transition-colors",
          selected ? "bg-muted" : "hover:bg-muted/60",
        )}
      >
        <span
          className={cn(
            "line-clamp-2 text-[13px] leading-[1.45] break-words",
            selected ? "text-foreground" : "text-foreground/80",
          )}
        >
          {plain(m.content)}
        </span>
        <span className="text-muted-foreground/80 flex h-[18px] min-w-0 items-center gap-2 text-[11px]">
          <span className="shrink-0 font-mono">#{m.id}</span>
          {tags ? <span className="min-w-0 truncate">{tags}</span> : null}
          {similar ? <SimilarChip /> : null}
          <span className="flex-1" />
          <Dots importance={m.importance} />
          <span className="w-11 shrink-0 text-right tabular-nums">{dayMonth(m.created_at)}</span>
        </span>
      </button>
    </ItemMenu>
  );
}

/** "8 of these say nearly the same thing", and what that costs him. */
function DuplicatesNotice({
  groups,
  onDismiss,
  onReview,
}: {
  groups: Memory[][];
  onDismiss: () => void;
  onReview: () => void;
}) {
  const [first, ...more] = groups;
  const day = (group: Memory[]) => {
    const dates = group.map((m) => m.created_at).sort();
    const from = dates[0];
    const to = dates[dates.length - 1];
    return sameDay(from, to) ? `on ${dayMonth(from, "long")}` : `between ${dayMonth(from, "long")} and ${dayMonth(to, "long")}`;
  };
  // Front of mind rides along on every turn; recall only competes for a place when it's related.
  const cost = first.every(isCore)
    ? "he carries every copy into every turn"
    : "every copy competes for the same place when he recalls";
  const others = more.slice(0, 3).map((group) => `${group.length} from ${dayMonth(group.map((m) => m.created_at).sort()[0])}`);
  const tail = more.length > 3 ? `, and ${more.length - 3} more` : "";
  return (
    <div
      role="status"
      className="border-kith/[0.22] bg-kith/[0.06] mx-3 mt-3 mb-1 flex shrink-0 items-start gap-3 rounded-[10px] border px-3.5 py-3"
    >
      <span aria-hidden className="bg-kith/[0.14] text-kith flex size-7 shrink-0 items-center justify-center rounded-lg">
        <Copy className="size-3.5" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
        <div className="flex min-h-7 items-center gap-2">
          <span className="flex-1 text-[13px] font-semibold">{first.length} of these say nearly the same thing</span>
          <button
            type="button"
            onClick={onDismiss}
            className="text-muted-foreground hover:bg-muted/50 hover:text-foreground h-7 rounded-[7px] px-2.5 text-[12.5px] transition-colors"
          >
            Not now
          </button>
          <button type="button" onClick={onReview} className={cn(BUTTON.primary, BUTTON.small)}>
            Review and merge
          </button>
        </div>
        <span className="text-foreground/80 text-[12.5px] leading-[1.45]">
          One memory saved {spelled(first.length)} times {day(first)} — {cost}.
          {more.length > 0
            ? ` ${capital(spelled(more.length))} more group${more.length === 1 ? "" : "s"} like it: ${others.join(", ")}${tail}.`
            : ""}
        </span>
      </div>
    </div>
  );
}

/* ── The reader ─────────────────────────────────────────────────────────── */

function Reader({
  m,
  position,
  total,
  copies,
  editing,
  onEdit,
  onPrev,
  onNext,
  onOpen,
  onLevel,
  onForget,
  onMerge,
  update,
}: {
  m: Memory;
  position: number;
  total: number;
  /** The other memories that say nearly the same thing. */
  copies: Memory[];
  editing: boolean;
  onEdit: (id: number | null) => void;
  onPrev?: () => void;
  onNext?: () => void;
  onOpen: (id: number) => void;
  onLevel: (level: Level) => void;
  onForget: () => void;
  onMerge: (keep: Memory, others: Memory[]) => Promise<boolean>;
  update: Handlers["update"];
}) {
  const core = isCore(m);
  return (
    <section aria-label={`Memory #${m.id}`} className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-[14px] border">
      <div className="flex h-[52px] shrink-0 items-center gap-2.5 border-b border-border/80 ps-6 pe-4">
        <span className="text-muted-foreground font-mono text-xs">#{m.id}</span>
        <Chip>{core ? "Front of mind" : "Recall"}</Chip>
        {copies.length > 0 ? (
          <Chip tone="attention">
            Nearly the same as {copies.length} other{copies.length === 1 ? "" : "s"}
          </Chip>
        ) : null}
        <span className="flex-1" />
        <span className="text-muted-foreground/80 text-xs tabular-nums">
          {position} of {total}
        </span>
        <IconButton label="Previous memory" onClick={onPrev}>
          <ChevronUp className="size-[15px]" />
        </IconButton>
        <IconButton label="Next memory" onClick={onNext}>
          <ChevronDown className="size-[15px]" />
        </IconButton>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-7 pt-[22px] pb-6">
        {editing ? (
          <Editor
            value={m.content}
            onCancel={() => onEdit(null)}
            onSave={(value) => {
              onEdit(null);
              if (value.trim() && value !== m.content) update("memory", m.id, { content: value });
            }}
          />
        ) : (
          <div
            onDoubleClick={() => onEdit(m.id)}
            className="text-foreground max-w-[720px] [&>div]:text-[14.5px] [&>div]:leading-[1.6] [&_p:first-child]:mt-0"
          >
            <Markdown>{m.content}</Markdown>
          </div>
        )}

        <dl className="m-0 grid grid-cols-[112px_minmax(0,1fr)] items-center gap-y-3.5">
          <dt>
            <Label>Level</Label>
          </dt>
          <dd className="m-0 flex items-center gap-3">
            <LevelToggle label="Level" value={m.level} onChange={onLevel} />
            <span className="text-muted-foreground/80 text-xs">
              {core
                ? "Always with him. Recall only comes up when it's related to the work."
                : "Comes up when it's related to the work. Front of mind is always with him."}
            </span>
          </dd>

          <dt>
            <Label>Importance</Label>
          </dt>
          <dd className="m-0 flex items-center gap-3">
            <Importance value={m.importance} onSave={(importance) => update("memory", m.id, { importance })} />
          </dd>

          <dt>
            <Label>Tags</Label>
          </dt>
          <dd className="m-0">
            <Tags value={m.tags} onSave={(tags) => update("memory", m.id, { tags })} />
          </dd>

          <dt>
            <Label>Saved</Label>
          </dt>
          <dd className="text-foreground/80 m-0 text-[13px] tabular-nums">
            {dayMonth(m.created_at, "long")} {new Date(m.created_at).getFullYear()}, {clock(m.created_at)}{" "}
            <span className="text-muted-foreground/80">· {ago(m.created_at)}</span>
          </dd>
        </dl>

        {copies.length > 0 ? <Copies m={m} copies={copies} onOpen={onOpen} onMerge={onMerge} /> : null}
      </div>

      <div className="flex h-[58px] shrink-0 items-center gap-2 border-t border-border/80 ps-6 pe-4">
        <button type="button" className={BUTTON.secondary} onClick={() => onEdit(editing ? null : m.id)} aria-pressed={editing}>
          <Pencil className="size-3.5" />
          Edit
        </button>
        <button type="button" className={BUTTON.secondary} onClick={() => onLevel(core ? "recall" : "core")}>
          {core ? <ArrowDownToLine className="size-3.5" /> : <ArrowUpToLine className="size-3.5" />}
          {core ? "Move to recall" : "Move to front of mind"}
        </button>
        <span className="flex-1" />
        <button type="button" className={cn(BUTTON.danger, "px-2.5")} onClick={onForget}>
          Forget…
        </button>
      </div>
    </section>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick?: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={!onClick}
      className="text-muted-foreground hover:bg-muted/60 hover:text-foreground disabled:text-muted-foreground/50 flex size-7 items-center justify-center rounded-[7px] transition-colors disabled:pointer-events-none"
    >
      {children}
    </button>
  );
}

/** The raw text, to edit in place — what `EditableText` did on the card, opened by the Edit
 *  button or E rather than a hover pencil, since the reader has room to say Edit out loud. */
function Editor({ value, onSave, onCancel }: { value: string; onSave: (value: string) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(value);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const box = ref.current;
    if (!box) return;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }, []);
  return (
    <div className="flex max-w-[720px] flex-col gap-2">
      <label htmlFor="memory-edit" className="sr-only">
        Memory text
      </label>
      <textarea
        id="memory-edit"
        ref={ref}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            // The field's own Escape: abandon the edit, and nothing behind it.
            e.preventDefault();
            onCancel();
          }
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            onSave(draft);
          }
        }}
        className="border-input bg-background focus-visible:border-ring min-h-48 w-full resize-y rounded-lg border px-3 py-2.5 font-mono text-[13px] leading-relaxed outline-none"
      />
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground/80 me-auto text-[11px]">⌘↵ saves · Esc cancels</span>
        <button type="button" className={cn(BUTTON.ghost, BUTTON.small)} onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className={cn(BUTTON.primary, BUTTON.small)} onClick={() => onSave(draft)}>
          Save
        </button>
      </div>
    </div>
  );
}

/** A 1–10 stepper. Clicks land at once on screen and are saved together once you stop, so five
 *  quick presses are one write rather than five racing each other. */
function Importance({ value, onSave }: { value: number; onSave: (value: number) => void }) {
  const [draft, setDraft] = useState(value);
  const pending = useRef<{ timer: ReturnType<typeof setTimeout>; value: number } | null>(null);
  const save = useRef(onSave);
  save.current = onSave;

  // A fresh value from the server wins once nothing of ours is still waiting to go.
  useEffect(() => {
    if (!pending.current) setDraft(value);
  }, [value]);
  // Leaving the memory mid-click still saves the click.
  useEffect(
    () => () => {
      if (pending.current) {
        clearTimeout(pending.current.timer);
        save.current(pending.current.value);
      }
    },
    [],
  );

  const step = (by: number) => {
    const next = Math.max(1, Math.min(10, draft + by));
    if (next === draft) return;
    setDraft(next);
    if (pending.current) clearTimeout(pending.current.timer);
    pending.current = {
      value: next,
      timer: setTimeout(() => {
        pending.current = null;
        if (next !== value) save.current(next);
      }, 450),
    };
  };

  const note = draft >= 10 ? "of 10 — the highest" : draft <= 1 ? "of 10 — the lowest" : "of 10";
  return (
    <>
      <div className="border-input bg-muted/60 flex h-[30px] items-center overflow-hidden rounded-lg border">
        <button
          type="button"
          aria-label="Lower importance"
          onClick={() => step(-1)}
          disabled={draft <= 1}
          className="text-foreground/80 hover:bg-muted hover:text-foreground disabled:text-muted-foreground/50 flex h-7 w-[30px] items-center justify-center transition-colors disabled:pointer-events-none"
        >
          <Minus className="size-[13px]" />
        </button>
        <span
          aria-live="polite"
          aria-label={`Importance ${draft} of 10`}
          className="h-7 w-[34px] border-x border-border text-center text-[13px] leading-7 font-semibold tabular-nums"
        >
          {draft}
        </span>
        <button
          type="button"
          aria-label="Raise importance"
          onClick={() => step(1)}
          disabled={draft >= 10}
          className="text-foreground/80 hover:bg-muted hover:text-foreground disabled:text-muted-foreground/50 flex h-7 w-[30px] items-center justify-center transition-colors disabled:pointer-events-none"
        >
          <Plus className="size-[13px]" />
        </button>
      </div>
      <span className="text-muted-foreground/80 text-xs">{note}</span>
    </>
  );
}

/** Tags as chips: × takes one off, "+ tag" puts one on. The whole set is saved each time — the
 *  server replaces the list, so "what is there now" is the only thing worth sending. */
function Tags({ value, onSave }: { value: string[]; onSave: (tags: string[]) => void }) {
  const [tags, setTags] = useState(value);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const fresh = value.join("\u0000");
  // What the server says is what there is — including when it did not keep a change.
  useEffect(() => setTags(value), [fresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const commit = (next: string[]) => {
    setTags(next);
    onSave(next);
  };
  const add = () => {
    const name = draft.trim().replace(/^#+/, "").trim();
    setDraft("");
    setAdding(false);
    if (name && !tags.includes(name)) commit([...tags, name]);
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {tags.map((t) => (
        <span
          key={t}
          className="border-border bg-muted/60 text-foreground/80 flex h-6 items-center gap-0.5 rounded-full border ps-[9px] pe-1 text-[11.5px]"
        >
          {t}
          <button
            type="button"
            aria-label={`Remove tag ${t}`}
            onClick={() => commit(tags.filter((x) => x !== t))}
            className="text-muted-foreground/80 hover:bg-muted hover:text-foreground flex size-[18px] items-center justify-center rounded-full"
          >
            <X className="size-2.5" strokeWidth={2.4} />
          </button>
        </span>
      ))}
      {adding ? (
        <input
          autoFocus
          aria-label="New tag"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={add}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setDraft("");
              setAdding(false);
            }
          }}
          placeholder="tag"
          className="border-ring bg-background text-foreground placeholder:text-muted-foreground/80 h-6 w-[120px] rounded-full border px-[9px] text-[11.5px] outline-none"
        />
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="text-muted-foreground hover:text-foreground h-6 rounded-full border border-dashed border-[color-mix(in_oklab,var(--foreground)_18%,transparent)] px-[9px] text-[11.5px] transition-colors"
        >
          + tag
        </button>
      )}
    </div>
  );
}

/** A similarity, as a 56px bar and a percentage. */
function Overlap({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <>
      <span className="bg-muted flex h-1 w-14 shrink-0 overflow-hidden rounded-full" aria-hidden>
        <span className="bg-muted-foreground/80 block h-1 rounded-full" style={{ width: `${pct}%` }} />
      </span>
      <span className="text-muted-foreground/80 w-[34px] shrink-0 text-right text-[11.5px] tabular-nums">{pct}%</span>
    </>
  );
}

/** "Says nearly the same as": each copy, what it says differently, and the merge. */
function Copies({
  m,
  copies,
  onOpen,
  onMerge,
}: {
  m: Memory;
  copies: Memory[];
  onOpen: (id: number) => void;
  onMerge: (keep: Memory, others: Memory[]) => Promise<boolean>;
}) {
  const rows = useMemo(
    () =>
      [...copies]
        .sort((a, b) => at(b.created_at) - at(a.created_at))
        .map((copy) => ({
          copy,
          overlap: similarity(m.content, copy.content),
          diff: describeDifference(m.content, copy.content),
          adds: addsSomething(m.content, copy.content),
        })),
    [m.content, copies],
  );
  const adding = rows.filter((row) => row.adds).map((row) => row.copy.id);
  const verdict =
    adding.length === 0
      ? "None of them says anything this one doesn't. Merging loses nothing."
      : adding.length === 1
        ? `Only #${adding[0]} says something this one doesn't — read it before merging.`
        : `${idList(adding)} say things this one doesn't — read them before merging.`;

  return (
    <div className="flex flex-col gap-2 border-t border-border/80 pt-4">
      <div className="flex items-center gap-2.5">
        <Label count={copies.length}>Says nearly the same as</Label>
        <span className="flex-1" />
        <button type="button" className={cn(BUTTON.secondary, "h-7 rounded-[7px] px-[11px] text-[12.5px]")} onClick={() => void onMerge(m, copies)}>
          <GitMerge className="size-[13px]" />
          Merge all {copies.length + 1} into this one
        </button>
      </div>
      <span className="text-muted-foreground text-[12.5px] leading-[1.45]">{verdict}</span>
      <div className="-mx-2.5 flex flex-col gap-px">
        {rows.map(({ copy, overlap, diff }) => (
          <button
            key={copy.id}
            type="button"
            onClick={() => onOpen(copy.id)}
            title={plain(copy.content)}
            className="text-foreground/80 hover:bg-muted/50 flex h-8 items-center gap-3 rounded-lg px-2.5 text-left text-[12.5px] transition-colors"
          >
            <span className="text-muted-foreground w-[30px] shrink-0 font-mono text-[11.5px]">#{copy.id}</span>
            <span className="text-muted-foreground/80 w-16 shrink-0 text-xs tabular-nums">
              {sameDay(copy.created_at, m.created_at) ? clock(copy.created_at) : dayMonth(copy.created_at)}
            </span>
            <span className="min-w-0 flex-1 truncate">{diff}</span>
            <Overlap value={overlap} />
            <ChevronRight className="text-muted-foreground/50 size-3.5 shrink-0" />
          </button>
        ))}
      </div>
    </div>
  );
}

/* ── Review and merge ───────────────────────────────────────────────────── */

/** One group of copies side by side: pick the one to keep, see what each of the others says
 *  differently from it, and forget them in one go. */
function MergeDialog({
  group,
  preferred,
  onClose,
  onMerge,
}: {
  group: Memory[];
  preferred?: number;
  onClose: () => void;
  onMerge: (keep: Memory, others: Memory[]) => Promise<void>;
}) {
  const ordered = useMemo(() => [...group].sort((a, b) => at(b.created_at) - at(a.created_at)), [group]);
  // Keep the one you were reading if it is in here; otherwise the weightiest, newest copy.
  const initial =
    ordered.find((m) => m.id === preferred) ??
    [...ordered].sort((a, b) => b.importance - a.importance || at(b.created_at) - at(a.created_at))[0];
  const [keepId, setKeepId] = useState(initial?.id);
  const keep = ordered.find((m) => m.id === keepId) ?? initial;
  const others = ordered.filter((m) => m.id !== keep?.id);
  const rows = useMemo(
    () =>
      keep
        ? ordered.map((m) => ({
            m,
            overlap: m.id === keep.id ? 1 : similarity(keep.content, m.content),
            diff: m.id === keep.id ? "Keeping this one" : describeDifference(keep.content, m.content, `#${keep.id}`),
          }))
        : [],
    [ordered, keep],
  );
  if (!keep) return null;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[min(720px,calc(100vh-4rem))] max-w-[760px] flex-col gap-0 overflow-hidden p-0 sm:max-w-[760px]">
        <div className="flex flex-col gap-1.5 px-6 pt-5 pb-4">
          <DialogTitle className="text-[15px] leading-snug font-semibold">
            {group.length} of these say nearly the same thing
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-[13px] leading-relaxed">
            Keep one and he forgets the rest. Each line says what that copy says differently from the one you keep.
          </DialogDescription>
        </div>

        <div className="border-border bg-muted/40 mx-6 mb-3 rounded-[10px] border px-4 py-3">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-muted-foreground font-mono text-[11.5px]">#{keep.id}</span>
            <Label>Keeping</Label>
          </div>
          <p className="text-foreground m-0 line-clamp-4 text-[13px] leading-[1.5]">{plain(keep.content)}</p>
        </div>

        <fieldset className="m-0 min-h-0 flex-1 overflow-y-auto border-0 px-4 pb-2">
          <legend className="sr-only">Which copy to keep</legend>
          {rows.map(({ m, overlap, diff }) => {
            const on = m.id === keep.id;
            return (
              <label
                key={m.id}
                className={cn(
                  "flex h-9 cursor-pointer items-center gap-3 rounded-lg px-2 text-[12.5px] transition-colors",
                  on ? "bg-muted text-foreground" : "text-foreground/80 hover:bg-muted/50",
                )}
              >
                <input
                  type="radio"
                  name="keep"
                  checked={on}
                  onChange={() => setKeepId(m.id)}
                  className="accent-kith size-3.5 shrink-0"
                  aria-label={`Keep #${m.id}`}
                />
                <span className="text-muted-foreground w-[30px] shrink-0 font-mono text-[11.5px]">#{m.id}</span>
                <span className="text-muted-foreground/80 w-[104px] shrink-0 text-xs tabular-nums">
                  {dayMonth(m.created_at)}, {clock(m.created_at)}
                </span>
                <span className="min-w-0 flex-1 truncate" title={plain(m.content)}>
                  {diff}
                </span>
                {on ? <span className="w-[102px] shrink-0" /> : <Overlap value={overlap} />}
              </label>
            );
          })}
        </fieldset>

        <div className="border-border bg-muted/25 flex items-center gap-2 border-t px-6 py-3.5">
          <span className="text-muted-foreground/80 me-auto text-xs">Merging forgets the copies; #{keep.id} stays as it is.</span>
          <button type="button" className={BUTTON.ghost} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={BUTTON.danger} onClick={() => void onMerge(keep, others)}>
            Keep #{keep.id}, forget {others.length}…
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
