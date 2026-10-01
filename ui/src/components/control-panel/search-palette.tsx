/** ⌘K: search everything he knows, from anywhere in the panel.
 *
 * Built from the "Search" board. The top bar's field used to be the panel's only search, and it
 * filtered whichever page was open — so finding a memory from Today meant guessing it was a memory,
 * going to Memory, and typing again. This searches every kind at once (projects, memory, journal,
 * tasks, chats, files, and reminders and sources under All), groups what it finds in a fixed order,
 * and shows the selected one in full on the right, so most searches end without opening anything.
 *
 * Filtering a page still exists; it is an action here now ("filter this page", ⇧↵) rather than the
 * only thing the box could do. The ranking, grouping, snippets and "did you mean" live in
 * `search.ts`, which is tested on its own.
 *
 * Mouse model is Spotlight's: a click selects (and shows the preview), a second click or Enter
 * opens. A row that opened on first click would leave the preview unreachable by pointer.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog as DialogPrimitive } from "radix-ui";
import {
  ArrowRight,
  BellRing,
  Brain,
  Check,
  Copy,
  FileCode,
  FileText,
  Folder,
  FolderKanban,
  MessageCircle,
  NotebookPen,
  Search,
  SquareCheckBig,
  X,
} from "lucide-react";

import { fetchConversations, fetchPermissions } from "@/lib/backend";
import { fetchWorkspace, type BrainSnapshot, type WorkspaceEntry } from "@/lib/backend/brain";
import { searchWorkspace, type WorkspaceHit } from "@/lib/backend/workspace";
import { formatBytes } from "@/lib/bytes";
import { copyText, useFileViewer } from "@/lib/files";
import { keys } from "@/lib/query-keys";
import { pathForConversation } from "@/lib/router";
import { cn } from "@/lib/utils";
import { BUTTON, Chip, KindTile } from "./chrome";
import { shortPath } from "./format";
import {
  CHIP_KINDS,
  KIND_CHIP,
  anywhere,
  buildEntries,
  filesFrom,
  filesFromHits,
  folderLabel,
  groups as groupsOf,
  marks,
  mergeFiles,
  search,
  searchedLine,
  shortName,
  snippet,
  stamp,
  statusLabel,
  suggest,
  terms,
  vocabulary,
  type SearchEntry,
  type SearchKind,
} from "./search";
import type { Tab } from "./types";

/** The same ceiling, and so the same cache entry, as the sidebar and the projects page. */
const CHAT_LIMIT = 500;

/** How long typing has to pause before his folder is searched. Short enough that a search feels
 *  live; long enough that "retry policy" is one request and not twelve. */
const SEARCH_PAUSE_MS = 180;
/** One letter matches half of every file. Two is where a content search starts to mean something. */
const SEARCH_MIN = 2;
const NO_HITS: WorkspaceHit[] = [];

/** `value`, once it has stopped changing for `ms`. */
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

type Filter = "all" | SearchKind;
const FILTERS: readonly Filter[] = ["all", ...CHIP_KINDS];

/** The last thing searched, so ⌘K twice in a row is "where was I", not a blank box. Module-level:
 *  the dialog unmounts when it closes, and this is not worth a slot in the panel's state. */
let lastText = "";

const ICON: Record<SearchKind, ReactNode> = {
  project: <FolderKanban />,
  memory: <Brain />,
  journal: <NotebookPen />,
  task: <SquareCheckBig />,
  chat: <MessageCircle />,
  file: <FileCode />,
  reminder: <BellRing />,
  source: <FileText />,
};

/** What Enter does to the selected row, said on the button that does the same. */
const OPEN_LABEL: Record<SearchKind, string> = {
  project: "Open project",
  memory: "Open in Memory",
  journal: "Open in Journal",
  task: "Open task",
  chat: "Open chat",
  file: "Open file",
  reminder: "Open in Reminders",
  source: "Open in Sources",
};

export interface PaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  snap: BrainSnapshot | null;
  /** The page's filter, when one is set — the palette starts from it rather than from blank. */
  seed: string;
  /** Narrow the page behind to this text, the way the top bar's field used to. */
  onFilter: (text: string) => void;
  /** Go to a tab, filtered to `filter` — how a memory or journal entry is opened, since those
   *  pages are lists and the filter is what brings the one you picked to the top of it. */
  onOpenTab: (tab: Tab, filter: string) => void;
  onOpenProject: (id: number) => void;
  onOpenTask: (id: number) => void;
}

export function SearchPalette(props: PaletteProps) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <DialogPrimitive.Root open={props.open} onOpenChange={props.onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/25 dark:bg-[rgba(10,9,8,0.55)]" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            input.current?.focus();
            input.current?.select();
          }}
          // Sized and placed as the board has it at the window it was drawn for, and pulled in
          // from the edges of a smaller one rather than running off them.
          style={{
            top: "max(16px, min(116px, calc((100vh - 764px) / 2)))",
            height: "min(764px, calc(100vh - 32px))",
            width: "min(880px, calc(100vw - 32px))",
          }}
          className="border-input bg-card fixed left-1/2 z-50 flex -translate-x-1/2 flex-col overflow-hidden rounded-2xl border shadow-[0_28px_80px_rgba(0,0,0,0.22),0_8px_24px_rgba(0,0,0,0.12)] outline-none dark:shadow-[0_28px_80px_rgba(0,0,0,0.55),0_8px_24px_rgba(0,0,0,0.35)]"
        >
          <DialogPrimitive.Title className="sr-only">Search everything he knows</DialogPrimitive.Title>
          <Palette {...props} input={input} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function Palette({
  snap,
  seed,
  onOpenChange,
  onFilter,
  onOpenTab,
  onOpenProject,
  onOpenTask,
  input,
}: PaletteProps & { input: RefObject<HTMLInputElement | null> }) {
  const navigate = useNavigate();
  const cache = useQueryClient();
  const [text, setText] = useState(() => seed || lastText);
  const [filter, setFilter] = useState<Filter>("all");
  const [picked, setPicked] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);

  const chats = useQuery({
    queryKey: keys.conversations(CHAT_LIMIT, ""),
    queryFn: async () => (await fetchConversations(CHAT_LIMIT)).conversations,
    staleTime: 30_000,
  });
  // The root listing, under the Files page's own key: if that page has fetched it, this is free.
  const root = useQuery({
    queryKey: keys.workspace("."),
    queryFn: async (): Promise<WorkspaceEntry[]> => (await fetchWorkspace(".")).entries,
    staleTime: 30_000,
  });
  const permissions = useQuery({ queryKey: keys.permissions(), queryFn: fetchPermissions, staleTime: 60_000 });
  const rootPath = permissions.data?.workspace?.root ?? "";

  /* His folder, searched on the server: names anywhere in it and lines inside files. Asked once the
     typing pauses, and the query's `signal` cancels a search the next keystroke has made moot —
     a 1.5-second search left running for every letter is how six sockets get used up.

     Silent when it cannot: an older server answers 404 and `supported` comes back false, an error
     leaves `data` empty, and either way the names below are still there — which is what this
     palette had before the route existed. `keepPreviousData` holds the last answer while the next
     is in flight; `search()` re-scores it against what is typed now, so a stale hit that no longer
     matches simply drops out rather than flickering. */
  const typed = text.trim();
  const asked = useSettled(typed, SEARCH_PAUSE_MS);
  const found = useQuery({
    queryKey: keys.workspaceSearch(asked),
    queryFn: ({ signal }) => searchWorkspace(asked, { signal }),
    enabled: asked.length >= SEARCH_MIN,
    staleTime: 15_000,
    retry: false,
    placeholderData: keepPreviousData,
  });
  // A module constant when there is nothing, so the file list below is not rebuilt every render.
  const hits = typed.length >= SEARCH_MIN && found.data ? found.data.results : NO_HITS;
  // Whether file search looked inside files, for what an empty result says it searched.
  const inside = found.data?.supported === true;
  const looking =
    typed.length >= SEARCH_MIN && found.data?.supported !== false && (asked !== typed || found.isFetching);

  /* Plus names from listings already in the cache — the root, and every folder the Files page has
     opened. They are what the empty query lists, and the whole of file search on an older server. */
  const cached = useMemo(() => {
    const listings: Record<string, WorkspaceEntry[]> = {};
    for (const [key, data] of cache.getQueriesData<unknown>({ queryKey: ["workspace"] })) {
      const path = key[1];
      if (typeof path !== "string") continue;
      const entries = Array.isArray(data)
        ? data
        : data && typeof data === "object" && Array.isArray((data as { entries?: unknown }).entries)
          ? (data as { entries: unknown[] }).entries
          : null;
      if (entries) listings[path] = entries as WorkspaceEntry[];
    }
    return filesFrom(listings);
    // `root.data` is the dependency that matters: it is the listing this palette asked for.
  }, [cache, root.data]);
  const files = useMemo(() => mergeFiles(cached, filesFromHits(hits)), [cached, hits]);

  const entries = useMemo(() => buildEntries(snap, chats.data ?? [], files, rootPath), [snap, chats.data, files, rootPath]);
  const totals = useMemo(() => search(entries, "").counts, [entries]);
  const words = useMemo(() => terms(text), [text]);
  const result = useMemo(() => search(entries, text), [entries, text]);
  const shown = groupsOf(result, filter);
  const rows = shown.flatMap((group) => group.rows);
  const selectedIndex = Math.max(0, picked ? rows.findIndex((row) => row.key === picked) : 0);
  const selected = rows[selectedIndex] as SearchEntry | undefined;
  const searching = words.length > 0;

  // "Did you mean" only once a search has come back empty, and only a suggestion that finds something.
  const vocab = useRef<{ from: SearchEntry[]; words: Map<string, number> } | null>(null);
  const suggestion = useMemo(() => {
    if (!searching || rows.length > 0) return null;
    if (vocab.current?.from !== entries) vocab.current = { from: entries, words: vocabulary(entries) };
    const better = suggest(text, vocab.current.words, (word) => anywhere(entries, word));
    if (!better) return null;
    const found = search(entries, better);
    return (filter === "all" ? found.total : found.counts[filter]) > 0 ? better : null;
  }, [searching, rows.length, text, entries, filter]);

  // Keep the selected row in view as the arrows walk past the edge of the list.
  useEffect(() => {
    list.current?.querySelector(`[data-index="${selectedIndex}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  const type = (next: string) => {
    setText(next);
    setPicked(null);
  };
  const choose = (next: Filter) => {
    setFilter(next);
    setPicked(null);
    list.current?.scrollTo({ top: 0 });
  };

  const open = (row: SearchEntry) => {
    lastText = text;
    onOpenChange(false);
    switch (row.kind) {
      case "project":
        return onOpenProject(row.value.id);
      case "task":
        return onOpenTask(row.value.id);
      case "chat":
        // The route for a chat is not a panel route, so opening one is also leaving here.
        return navigate(pathForConversation(row.value.id));
      case "file":
        // A folder has nowhere narrower to go than the Files page.
        return row.value.type === "dir" ? onOpenTab("workspace", "") : useFileViewer.getState().open(row.value.path);
      case "memory":
        return onOpenTab("memory", text.trim());
      case "journal":
        return onOpenTab("journal", text.trim());
      case "reminder":
        return onOpenTab("reminders", text.trim());
      case "source":
        return onOpenTab("sources", text.trim());
    }
  };

  const filterPage = () => {
    lastText = text;
    onOpenChange(false);
    onFilter(text.trim());
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (rows.length === 0) return;
      const next = Math.min(rows.length - 1, Math.max(0, selectedIndex + (event.key === "ArrowDown" ? 1 : -1)));
      setPicked(rows[next].key);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (event.shiftKey) {
        if (searching) filterPage();
        return;
      }
      if (selected) open(selected);
      else if (suggestion) type(suggestion);
      return;
    }
    // The board's "Tab next kind": the chips are the one other thing to move through here.
    if (event.key === "Tab") {
      event.preventDefault();
      const at = FILTERS.indexOf(filter);
      choose(FILTERS[(at + (event.shiftKey ? FILTERS.length - 1 : 1)) % FILTERS.length]);
    }
  };

  const optionId = (index: number) => `palette-option-${index}`;

  return (
    <>
      {/* Query */}
      <div className="border-border flex h-[60px] shrink-0 items-center gap-3 border-b pr-3.5 pl-[18px]">
        <Search className="text-muted-foreground size-[18px] shrink-0" aria-hidden />
        <label htmlFor="palette-query" className="sr-only">
          Search memory, journal, tasks, chats and files
        </label>
        <input
          id="palette-query"
          ref={input}
          type="text"
          role="combobox"
          aria-expanded={rows.length > 0}
          aria-controls="palette-results"
          aria-autocomplete="list"
          aria-activedescendant={selected ? optionId(selectedIndex) : undefined}
          autoComplete="off"
          spellCheck={false}
          value={text}
          onChange={(event) => type(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Search memory, journal, tasks, files…"
          className="text-foreground placeholder:text-muted-foreground/80 caret-kith h-10 min-w-0 flex-1 bg-transparent text-[17px] outline-none"
        />
        {searching ? (
          <span className="text-muted-foreground/80 text-xs whitespace-nowrap tabular-nums" aria-live="polite">
            {result.total} result{result.total === 1 ? "" : "s"}
          </span>
        ) : null}
        {text ? (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => {
              type("");
              input.current?.focus();
            }}
            className="bg-muted/60 text-muted-foreground hover:text-foreground flex size-7 shrink-0 items-center justify-center rounded-[7px] transition-colors"
          >
            <X className="size-3.5" />
          </button>
        ) : null}
      </div>

      {/* Kind filter */}
      <div role="group" aria-label="Filter results by kind" className="border-border/80 flex h-12 shrink-0 items-center gap-1 border-b px-3">
        {FILTERS.map((one) => {
          const on = filter === one;
          const count = one === "all" ? result.total : result.counts[one];
          return (
            <button
              key={one}
              type="button"
              aria-pressed={on}
              onClick={() => choose(one)}
              className={cn(
                "flex h-7 items-center rounded-full border px-[11px] text-[12.5px] transition-colors",
                on ? "border-input bg-muted text-foreground" : "text-muted-foreground hover:text-foreground border-transparent",
              )}
            >
              {one === "all" ? "All" : KIND_CHIP[one]}
              <span className="text-muted-foreground/80 ms-1.5 tabular-nums">{count}</span>
            </button>
          );
        })}
        <span className="flex-1" />
        <span className="text-muted-foreground/80 flex items-center gap-1.5 text-[11.5px]">
          <Key>Tab</Key>next kind
        </span>
      </div>

      {/* Results + preview */}
      {rows.length === 0 ? (
        <NoResults
          text={text}
          filter={filter}
          totals={totals}
          inside={inside}
          // Only where files are among what is shown: a Journal search has nothing to wait for.
          looking={looking && (filter === "all" || filter === "file")}
          searching={searching}
          suggestion={suggestion}
          onSuggest={(next) => {
            type(next);
            input.current?.focus();
          }}
          onWiden={() => {
            choose("all");
            input.current?.focus();
          }}
        />
      ) : (
        <div className="flex min-h-0 flex-1">
          <div
            ref={list}
            id="palette-results"
            role="listbox"
            aria-label="Results"
            className="flex w-[440px] shrink-0 flex-col overflow-y-auto px-2 pt-1.5 pb-2"
          >
            {shown.map((group) => (
              <div key={group.kind} role="group" aria-label={KIND_CHIP[group.kind]} className="flex flex-col">
                <div className="flex h-7 shrink-0 items-baseline gap-2 px-2.5 pt-2.5">
                  <span className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">
                    {KIND_CHIP[group.kind]}
                  </span>
                  <span className="text-muted-foreground/50 text-[11px] tabular-nums">{group.count}</span>
                </div>
                {group.rows.map((row) => {
                  const index = rows.indexOf(row);
                  const on = index === selectedIndex;
                  return (
                    <div
                      key={row.key}
                      id={optionId(index)}
                      data-index={index}
                      role="option"
                      aria-selected={on}
                      onClick={() => (on ? open(row) : setPicked(row.key))}
                      className={cn(
                        "flex h-11 shrink-0 cursor-default items-center gap-2.5 rounded-[10px] px-2.5 transition-colors",
                        on ? "bg-muted" : "hover:bg-muted/50",
                      )}
                    >
                      <KindTile>{row.kind === "file" && row.value.type === "dir" ? <Folder /> : ICON[row.kind]}</KindTile>
                      <span className="flex min-w-0 flex-1 flex-col gap-px">
                        <span className="text-muted-foreground truncate text-[13px]">
                          {row.prefix}
                          <Marked text={snippet(row.title, words)} words={words} />
                        </span>
                        {row.kind === "file" && row.value.match === "content" ? (
                          // A line inside the file: its number, then the line, cut to keep the match
                          // on screen — the reason this row exists is that text.
                          <span className="text-muted-foreground/80 truncate font-mono text-[11px]">
                            <span className="text-muted-foreground/50 me-1.5 tabular-nums">{row.value.line}</span>
                            <Marked text={snippet(row.meta, words, 18)} words={words} />
                          </span>
                        ) : (
                          <span className="text-muted-foreground/80 truncate text-[11.5px]">
                            <Marked text={row.meta} words={words} />
                          </span>
                        )}
                      </span>
                      {on ? <Key className="text-foreground/80">↵</Key> : null}
                    </div>
                  );
                })}
                {group.more ? (
                  <button
                    type="button"
                    onClick={() => choose(group.kind)}
                    className="text-foreground/80 hover:text-foreground flex h-7 shrink-0 items-center pr-2.5 pl-12 text-left text-xs"
                  >
                    {group.more}
                  </button>
                ) : null}
              </div>
            ))}
          </div>
          {selected ? (
            <Preview
              key={selected.key}
              row={selected}
              words={words}
              snap={snap}
              chats={chats.data ?? []}
              rootPath={rootPath}
              inside={inside}
              onOpen={() => open(selected)}
            />
          ) : null}
        </div>
      )}

      {/* Keyboard */}
      <div className="border-border text-muted-foreground/80 flex h-10 shrink-0 items-center gap-4 border-t px-4 text-[11.5px]">
        <Hint keys={["↑", "↓"]}>move</Hint>
        <Hint keys={["↵"]}>open</Hint>
        {/* Where the board has "open beside": the panel is one surface with nothing to open a
            result beside, so that slot carries the thing this box used to be for instead. */}
        <button
          type="button"
          onClick={filterPage}
          disabled={!searching}
          className="hover:text-foreground flex items-center gap-1.5 transition-colors disabled:pointer-events-none disabled:opacity-50"
        >
          <Key className="text-muted-foreground">⇧↵</Key>filter this page
        </button>
        <Hint keys={["Esc"]}>close</Hint>
        <span className="flex-1" />
        <span className="truncate">Across his memory, journal, projects, tasks, chats and files</span>
      </div>
    </>
  );
}

/* ── Pieces ─────────────────────────────────────────────────────────────── */

/** A key, as a key — `Kbd`, with the colour left to the caller (the footer's keys are a step
 *  brighter than the hints around them). */
function Key({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "text-muted-foreground/80 inline-flex h-[18px] shrink-0 items-center rounded-[5px] border border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-[5px] font-mono text-[11px] leading-none",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

function Hint({ keys: list, children }: { keys: string[]; children: ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="flex gap-[3px]">
        {list.map((one) => (
          <Key key={one} className="text-muted-foreground">
            {one}
          </Key>
        ))}
      </span>
      {children}
    </span>
  );
}

/** Text with every match bold, wherever it falls. */
function Marked({ text, words }: { text: string; words: string[] }) {
  return (
    <>
      {marks(text, words).map((piece, i) =>
        piece.hit ? (
          <b key={i} className="text-foreground font-semibold">
            {piece.text}
          </b>
        ) : (
          <span key={i}>{piece.text}</span>
        ),
      )}
    </>
  );
}

function NoResults({
  text,
  filter,
  totals,
  inside,
  looking,
  searching,
  suggestion,
  onSuggest,
  onWiden,
}: {
  text: string;
  filter: Filter;
  totals: Record<SearchKind, number>;
  inside: boolean;
  /** His folder is still being searched — "nothing matches" would be said too early. */
  looking: boolean;
  searching: boolean;
  suggestion: string | null;
  onSuggest: (text: string) => void;
  onWiden: () => void;
}) {
  if (!searching) {
    return (
      <div role="status" className="text-muted-foreground/80 flex min-h-0 flex-1 items-center justify-center text-[12.5px]">
        {filter === "all" ? "Nothing to search yet." : `No ${KIND_CHIP[filter].toLowerCase()} yet.`}
      </div>
    );
  }
  if (looking) {
    return (
      <div role="status" className="text-muted-foreground/80 flex min-h-0 flex-1 items-center justify-center text-[12.5px]">
        Looking through his folder…
      </div>
    );
  }
  // What widening would add: every kind but the one the chip narrowed to.
  const others = (["memory", "task", "project", "chat", "file", "journal", "source"] as SearchKind[])
    .filter((kind) => kind !== filter)
    .map((kind) => (kind === "memory" ? "memory" : KIND_CHIP[kind].toLowerCase()));
  return (
    <div role="status" className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 text-center">
      <span className="text-foreground text-sm font-semibold">Nothing matches ‘{text.trim()}’</span>
      <span className="text-muted-foreground/80 mt-[5px] max-w-[560px] text-xs">{searchedLine(filter, totals, inside)}</span>
      {suggestion ? (
        <div className="mt-[18px] flex items-center gap-2">
          <span className="text-muted-foreground text-[12.5px]">Did you mean</span>
          <button
            type="button"
            onClick={() => onSuggest(suggestion)}
            className="border-input bg-muted/60 text-foreground hover:bg-muted flex h-[26px] items-center gap-1.5 rounded-full border pr-1.5 pl-2.5 text-[12.5px] transition-colors"
          >
            {suggestion}
            <kbd className="text-muted-foreground/80 inline-flex h-4 items-center rounded-[5px] border border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-1 font-mono text-[10.5px] leading-none">
              ↵
            </kbd>
          </button>
          <span className="text-muted-foreground text-[12.5px]">?</span>
        </div>
      ) : null}
      {filter !== "all" ? (
        <>
          <button
            type="button"
            onClick={onWiden}
            className="text-foreground/80 hover:text-foreground mt-4 flex items-center gap-1.5 text-[12.5px] transition-colors"
          >
            Search everything instead
            <ArrowRight className="size-[13px]" />
          </button>
          <span className="text-muted-foreground/80 mt-1 text-[11px]">
            {others.slice(0, -1).join(", ")} and {others[others.length - 1]} too
          </span>
        </>
      ) : null}
    </div>
  );
}

/* ── Preview ────────────────────────────────────────────────────────────── */

const clip = (text: string, n: number) => (text.length > n ? `${text.slice(0, n).trimEnd()}…` : text);
/** Two things written within this long of each other were written together. */
const TOGETHER = 120_000;

function Preview({
  row,
  words,
  snap,
  chats,
  rootPath,
  inside,
  onOpen,
}: {
  row: SearchEntry;
  words: string[];
  snap: BrainSnapshot | null;
  chats: { projectId: number | null }[];
  rootPath: string;
  /** Whether the server searched inside files — what a file's "found by" line can claim. */
  inside: boolean;
  onOpen: () => void;
}) {
  const projects = snap?.projects ?? [];
  const project = (id: number | null | undefined) => (id != null ? projects.find((p) => p.id === id) : undefined);
  // Keyed by the row, so a new selection starts with no "copied" left over from the last.
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(null), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  let heading: ReactNode;
  let sub = "";
  let chips: ReactNode = null;
  let body: ReactNode = null;
  const facts: [string, ReactNode][] = [];
  /** A line in the facts table, when there is something true to put in it. */
  const fact = (label: string, value: ReactNode) => {
    if (value) facts.push([label, value]);
  };
  let copy: { label: string; text: string } | null = null;

  switch (row.kind) {
    case "memory": {
      const m = row.value;
      heading = (
        <>
          Memory <span className="text-muted-foreground font-mono text-xs font-normal">#{m.id}</span>
        </>
      );
      sub = `Remembered ${stamp(m.created_at).replace(/^(Today|Yesterday)/, (d) => d.toLowerCase())}`;
      chips = (
        <>
          <Chip>{m.level === "core" ? "Front of mind" : "Recall"}</Chip>
          <Chip>Importance {m.importance}</Chip>
        </>
      );
      const lower = m.content.toLowerCase();
      // Its project when it was filed with one; otherwise the one it names, if it names exactly one.
      const named = projects.filter((p) => shortName(p.name).length >= 4 && lower.includes(shortName(p.name).toLowerCase()));
      const about = project(m.project_id) ?? (named.length === 1 ? named[0] : undefined);
      const at = Date.parse(m.created_at);
      const diary = (snap?.journal ?? []).find((j) => Math.abs(Date.parse(j.created_at) - at) < TOGETHER);
      body = (
        <>
          <Text words={words}>{m.content}</Text>
          {m.tags?.length ? (
            <div className="mt-4 flex flex-wrap gap-1.5">
              {m.tags.map((tag) => {
                const hit = words.some((word) => tag.toLowerCase().includes(word.replace(/^#/, "")));
                return (
                  <Chip key={tag} className={hit ? "border-input bg-muted text-foreground font-semibold" : undefined}>
                    #{tag}
                  </Chip>
                );
              })}
            </div>
          ) : null}
        </>
      );
      fact("Project", about?.name);
      fact("Written with", diary ? `His journal entry, the same minute — “${clip(diary.entry.replace(/\s+/g, " "), 90)}”` : null);
      copy = { label: "Copy memory text", text: m.content };
      break;
    }
    case "journal": {
      const j = row.value;
      heading = "Journal entry";
      sub = `Written ${stamp(j.created_at).replace(/^(Today|Yesterday)/, (d) => d.toLowerCase())}`;
      const about = project(j.project_id);
      chips = about ? <Chip>{shortName(about.name)}</Chip> : null;
      const at = Date.parse(j.created_at);
      const kept = (snap?.memories ?? []).find((m) => Math.abs(Date.parse(m.created_at) - at) < TOGETHER);
      body = (
        <Text words={words} serif>
          {j.entry}
        </Text>
      );
      fact("Remembered with", kept ? `Memory #${kept.id} — “${clip(kept.content.replace(/\s+/g, " "), 90)}”` : null);
      copy = { label: "Copy entry text", text: j.entry };
      break;
    }
    case "project": {
      const p = row.value;
      const own = (snap?.tasks ?? []).filter((t) => t.project_id === p.id);
      const of = (status: string) => own.filter((t) => t.status === status).length;
      const board = [
        [of("planning"), "planning"],
        [of("approved"), "up next"],
        [of("working"), "working"],
        [of("done"), "done"],
      ]
        .filter(([n]) => (n as number) > 0)
        .map(([n, label]) => `${n} ${label}`)
        .join(" · ");
      const talks = chats.filter((c) => c.projectId === p.id).length;
      heading = <Marked text={p.name} words={words} />;
      sub = row.meta.split(" · ").slice(0, 2).join(" · ");
      chips = of("planning") > 0 ? <Chip tone="attention">{of("planning")} to approve</Chip> : <Chip>{statusLabel(p.status)}</Chip>;
      body = p.description ? <Text words={words}>{p.description}</Text> : <p className="text-muted-foreground/80 mt-[18px] text-[13px]">No description.</p>;
      fact(
        "Folder",
        p.directory ? (
          <span className="font-mono text-xs" title={p.directory}>
            <Marked text={shortPath(p.directory, 52)} words={words} />
          </span>
        ) : null,
      );
      fact("Board", board);
      fact("Conversations", talks ? String(talks) : null);
      fact("Last active", stamp(p.updated_at));
      copy = p.directory ? { label: "Copy folder path", text: p.directory } : null;
      break;
    }
    case "task": {
      const t = row.value;
      const about = project(t.project_id);
      heading = (
        <>
          Task <span className="text-muted-foreground font-mono text-xs font-normal">#{t.id}</span>
        </>
      );
      sub = about ? shortName(about.name) : "No project";
      chips = (
        <>
          <Chip tone={t.status === "planning" ? "attention" : t.status === "working" ? "live" : "neutral"}>
            {t.status === "planning" ? "Waiting for your OK" : statusLabel(t.status)}
          </Chip>
          {t.priority === "high" ? <Chip>High priority</Chip> : null}
        </>
      );
      body = (
        <>
          <p className="text-foreground mt-[18px] text-[15px] leading-[1.5] font-medium">
            <Marked text={t.goal} words={words} />
          </p>
          {t.description ? <Text words={words} small>{t.description}</Text> : null}
        </>
      );
      fact("Project", about ? <Marked text={about.name} words={words} /> : null);
      fact("Checklist", t.checklist_total ? `${t.checklist_done ?? 0} of ${t.checklist_total} done` : null);
      fact("Deliverables", t.outputs ? String(t.outputs) : null);
      fact("Filed by", t.created_by === "user" ? "You" : "Him");
      fact("Last changed", stamp(t.updated_at));
      copy = { label: "Copy task goal", text: t.goal };
      break;
    }
    case "chat": {
      const c = row.value;
      const about = project(c.projectId);
      heading = "Chat";
      sub = `${c.messages} message${c.messages === 1 ? "" : "s"} · ${stamp(c.updatedAt)}`;
      chips = c.working ? <Chip tone="live">Working</Chip> : c.waiting ? <Chip tone="attention">Waiting on you</Chip> : null;
      body = (
        <>
          <p className="text-foreground mt-[18px] text-[15px] leading-[1.5] font-medium">
            <Marked text={c.title} words={words} />
          </p>
          {c.lastSaid ? (
            <>
              <p className="text-muted-foreground/80 mt-4 text-[10.5px] font-semibold tracking-[0.1em] uppercase">Where it was left</p>
              <Text words={words} small>{c.lastSaid}</Text>
            </>
          ) : null}
        </>
      );
      fact("Project", about ? <Marked text={about.name} words={words} /> : null);
      fact("Started", stamp(c.createdAt));
      break;
    }
    case "file": {
      const f = row.value;
      heading = <Marked text={f.name} words={words} />;
      sub = folderLabel(f.folder, rootPath);
      chips = (
        <>
          {f.match === "content" && f.line ? <Chip>Line {f.line}</Chip> : null}
          <Chip>{f.type === "dir" ? "Folder" : formatBytes(f.size)}</Chip>
        </>
      );
      body =
        f.match === "content" ? (
          <>
            <div className="border-border bg-card mt-[18px] flex gap-3 overflow-hidden rounded-[10px] border px-3 py-2.5 font-mono text-[12px] leading-[1.55]">
              <span className="text-muted-foreground/50 shrink-0 tabular-nums select-none">{f.line}</span>
              <span className="text-foreground/80 min-w-0 break-words whitespace-pre-wrap">
                <Marked text={f.text ?? ""} words={words} />
              </span>
            </div>
            <p className="text-muted-foreground/80 mt-3 text-[12.5px] leading-normal">
              Found inside the file, on line {f.line}. Opening it shows the whole file.
            </p>
          </>
        ) : (
          <p className="text-muted-foreground/80 mt-[18px] text-[12.5px] leading-normal">
            {inside
              ? "Found by its name."
              : "Found by its name. Files are searched by name in his folder and in the folders you’ve opened on the Files page — not by what’s inside them."}
          </p>
        );
      fact(
        "Where",
        <span className="font-mono text-xs">
          <Marked text={folderLabel(f.folder, rootPath)} words={words} />
        </span>,
      );
      fact("Changed", f.modified ? stamp(new Date(f.modified * 1000).toISOString()) : null);
      copy = { label: "Copy path", text: rootPath ? `${rootPath}/${f.path}` : f.path };
      break;
    }
    case "reminder": {
      const r = row.value;
      heading = "Reminder";
      sub = row.meta;
      chips = <Chip tone={r.status === "pending" ? "attention" : "neutral"}>{statusLabel(r.status)}</Chip>;
      body = <Text words={words}>{r.note}</Text>;
      fact("Set", stamp(r.created_at));
      break;
    }
    case "source": {
      const s = row.value;
      heading = <Marked text={s.title} words={words} />;
      sub = s.origin;
      body = null;
      fact("From", s.origin ? <Marked text={s.origin} words={words} /> : null);
      fact("Length", s.chars ? `${s.chars.toLocaleString()} characters` : null);
      fact("Added", stamp(s.created_at));
      break;
    }
  }

  const action = copy;
  return (
    <article
      aria-label={typeof heading === "string" ? heading : OPEN_LABEL[row.kind]}
      className="border-border bg-sidebar flex min-w-0 flex-1 flex-col border-s px-[22px] py-[18px]"
    >
      <div className="flex shrink-0 items-center gap-2.5">
        <KindTile>{row.kind === "file" && row.value.type === "dir" ? <Folder /> : ICON[row.kind]}</KindTile>
        <span className="flex min-w-0 flex-col gap-px">
          <span className="text-foreground truncate text-[13px] font-semibold">{heading}</span>
          {sub ? <span className="text-muted-foreground/80 truncate text-[11.5px]">{sub}</span> : null}
        </span>
        <span className="flex-1" />
        {chips ? <span className="flex shrink-0 items-center gap-1.5">{chips}</span> : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {body}
        {facts.length ? (
          <dl className="border-border/80 mt-5 grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-2.5 border-t pt-3.5 text-[12.5px] leading-[18px]">
            {facts.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground/80">{label}</dt>
                <dd className="text-foreground/80 m-0 line-clamp-2 leading-[1.45]">{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-2 pt-4">
        <button type="button" onClick={onOpen} className={cn(BUTTON.secondary, "gap-2 pr-2.5 pl-3")}>
          {OPEN_LABEL[row.kind]}
          <Key className="text-muted-foreground">↵</Key>
        </button>
        <span className="flex-1" />
        {action ? (
          <button
            type="button"
            aria-label={copied === "yes" ? "Copied" : copied === "no" ? "Couldn't copy" : action.label}
            title={copied === "yes" ? "Copied" : copied === "no" ? "Couldn't copy" : action.label}
            // Only says "copied" when the clipboard said so — a button that reports success it
            // did not have is how a paste of nothing ends up in a message.
            onClick={() => void copyText(action.text).then((ok) => setCopied(ok ? "yes" : "no"))}
            className="text-muted-foreground hover:bg-muted/50 hover:text-foreground flex size-8 items-center justify-center rounded-lg transition-colors"
          >
            {copied === "yes" ? <Check className="text-roam size-[15px]" /> : <Copy className="size-[15px]" />}
          </button>
        ) : null}
      </div>
    </article>
  );
}

/** The selected thing's text in full, matches bold, line breaks kept. */
function Text({ children, words, serif, small }: { children: string; words: string[]; serif?: boolean; small?: boolean }) {
  return (
    <p
      className={cn(
        "text-foreground/80 mt-[18px] whitespace-pre-line",
        small ? "mt-2 text-[13px] leading-[1.6]" : "text-sm leading-[1.65]",
        serif && "font-serif text-[15px] [font-family:ui-serif,'New_York',Georgia,serif]",
      )}
    >
      <Marked text={children} words={words} />
    </p>
  );
}
