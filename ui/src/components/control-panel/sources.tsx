/**
 * Sources: what he has been given to read, and the copy he kept of each.
 *
 * Drawn from the "Sources" board of the 2026-09-30 control panel design. Three things, top to
 * bottom: a place to feed him (a link, some text, a file dropped on it), what he is reading right
 * now, and a master–detail of everything he has — the list on the left, the selected source in
 * full on the right, with the passages he recalls by numbered in the gutter.
 *
 * The full text is not in the snapshot — a page is up to 20,000 characters and the list only
 * needs its length — so the reader asks `GET /api/sources/<id>` for the one that is open.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { AlignLeft, ExternalLink, File, Info, Link as LinkIcon, MessageSquare, Search, Upload } from "lucide-react";

import { fetchSource } from "@/lib/backend/brain";
import type { BrainSnapshot, Source } from "@/lib/backend/brain";
import { activeComposer } from "@/lib/active-composer";
import { keys } from "@/lib/query-keys";
import { pathForHome } from "@/lib/router";
import { cn } from "@/lib/utils";
import { Layer, useLayer } from "@/hooks/use-layer";
import { BUTTON, Kbd, Label, LiveDot, PageTitle, Segmented } from "./chrome";
import { matches } from "./format";
import {
  askLine,
  bareUrl,
  blocksOf,
  domainOf,
  longDate,
  passagesOf,
  shortDate,
  sourceKind,
  type SourceKind,
} from "./library";
import type { Handlers } from "./types";

type Ingest = (input: { url?: string; text?: string; title?: string }) => Promise<void>;
type Filter = "all" | SourceKind;

const KIND_ICON: Record<SourceKind, ReactNode> = {
  link: <LinkIcon />,
  text: <AlignLeft />,
  file: <File />,
};

/** What a row says under its title: the site for a link, what kind of thing it was otherwise. */
function metaFor(source: Source, kind: SourceKind): string {
  const where = kind === "link" ? domainOf(source.origin) : kind === "file" ? "Dropped file" : "Pasted text";
  return `${where} · ${source.chars.toLocaleString()} chars`;
}

/**
 * A file dragged over this screen is for him to read, anywhere on it.
 *
 * The window has its own drop handler: it attaches the file to the focused chat and puts "Drop to
 * attach" over everything. Here that would be the wrong answer twice — the file would go to a chat
 * you are not looking at, and not to him — so the drag is stopped before it reaches the window.
 * Only drags carrying files: text dragged out of the reader is not a drop.
 */
function stopDrag(event: React.DragEvent): boolean {
  if (!Array.from(event.dataTransfer.types).includes("Files")) return false;
  event.preventDefault();
  event.stopPropagation();
  return true;
}

/** Past this a dropped file is not something to read into memory to find out it is too long —
 *  the server keeps the first 20,000 characters of anything regardless. */
const MAX_DROP_BYTES = 2_000_000;

/** Read a dropped file as text, or say why not. Binary is told by a NUL in its first bytes,
 *  which no text file has and nearly every binary format does. */
async function readTextFile(file: globalThis.File): Promise<string> {
  if (file.size > MAX_DROP_BYTES) throw new Error(`${file.name} is too big to read here.`);
  const head = new Uint8Array(await file.slice(0, 4096).arrayBuffer());
  if (head.includes(0)) throw new Error(`Only text can be fed here — ${file.name} isn't text.`);
  const text = await file.text();
  if (!text.trim()) throw new Error(`${file.name} is empty.`);
  return text;
}

export function Sources({
  snap,
  query,
  remove,
  ingest,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
  ingest: Ingest;
}) {
  const layer = useLayer(Layer.Panel);
  const ask = useAsk();
  const [askNote, setAskNote] = useState("");
  const askAbout = (source: Source) => {
    setAskNote(ask(source) ? "" : "Open a chat first — there's no conversation here to ask in.");
  };
  const [filter, setFilter] = useState<Filter>("all");
  // Null means "the first one": after feeding him something, the newest source is selected the
  // moment the snapshot brings it back, without anything having to wait for the refetch.
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [reading, setReading] = useState<{ label: string; since: number } | null>(null);
  const [error, setError] = useState("");
  // How deep a file drag is inside the screen; enter and leave fire for every child crossed.
  const [dragging, setDragging] = useState(0);

  const all = useMemo(
    () => snap.sources.filter((s) => matches(query, s.title, s.origin)).map((s) => ({ ...s, kind: sourceKind(s) })),
    [snap.sources, query],
  );
  const items = filter === "all" ? all : all.filter((s) => s.kind === filter);
  const selected = items.find((s) => s.id === selectedId) ?? items[0] ?? null;
  const count = (kind: SourceKind) => all.filter((s) => s.kind === kind).length;

  /** Feed him one thing. A link if it is one, text otherwise; `title` names a dropped file. */
  const feed = useCallback(
    async (raw: string, title?: string) => {
      const value = raw.trim();
      if (!value || reading) return false;
      const isUrl = /^https?:\/\/\S+$/i.test(value);
      setError("");
      setReading({ label: isUrl ? bareUrl(value) : title ?? "what you pasted", since: Date.now() });
      try {
        await ingest(isUrl ? { url: value } : { text: value, title });
        setSelectedId(null);
        setFilter("all");
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "He couldn't read that.");
        return false;
      } finally {
        setReading(null);
      }
    },
    [ingest, reading],
  );

  const feedFile = useCallback(
    async (file: globalThis.File) => {
      try {
        await feed(await readTextFile(file), file.name);
      } catch (e) {
        setError(e instanceof Error ? e.message : "He couldn't read that file.");
      }
    },
    [feed],
  );

  /* ⌘V anywhere on the screen feeds him what you copied — the board's promise, and the fastest
   * way to do the one thing this screen is for. Not inside a field: pasting into the ingest box,
   * the find box or the panel search is pasting, and it stays that. */
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable || ["INPUT", "TEXTAREA"].includes(target.tagName))
      )
        return;
      if (!layer.frontmost()) return;
      const file = event.clipboardData?.files?.[0];
      const text = event.clipboardData?.getData("text/plain") ?? "";
      if (!file && !text.trim()) return;
      event.preventDefault();
      if (file) void feedFile(file);
      else void feed(text);
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [feed, feedFile, layer]);

  const removeOne = async (source: Source) => {
    const at = items.findIndex((s) => s.id === source.id);
    if (await remove("source", source.id, source.title)) {
      const next = items[at + 1] ?? items[at - 1];
      setSelectedId(next ? next.id : null);
    }
  };

  return (
    <div
      onDragEnter={(event) => stopDrag(event) && setDragging((d) => d + 1)}
      onDragOver={(event) => {
        if (stopDrag(event)) event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => stopDrag(event) && setDragging((d) => Math.max(0, d - 1))}
      onDrop={(event) => {
        if (!stopDrag(event)) return;
        setDragging(0);
        const file = event.dataTransfer.files[0];
        if (file) void feedFile(file);
      }}
      className="flex h-[calc(100vh-100px)] min-h-[620px] flex-col gap-[22px]"
    >
      <PageTitle
        title="Sources"
        count={all.length}
        subtitle="He reads it, remembers it, and recalls it on demand."
      >
        <span className="text-muted-foreground/80 flex items-center gap-[7px] pb-0.5 text-xs">
          <Kbd>⌘V</Kbd>anywhere here feeds him what you copied
        </span>
      </PageTitle>

      <div className="flex shrink-0 flex-col gap-2.5">
        <IngestZone busy={reading !== null} dragging={dragging > 0} onFeed={feed} />
        {reading ? <ReadingNow label={reading.label} since={reading.since} /> : null}
        {error ? (
          <p role="alert" className="text-destructive px-1 text-[12.5px]">
            {error}
          </p>
        ) : null}
      </div>

      {snap.sources.length === 0 ? (
        // The ingest box above is the action; this only says what will appear here.
        <div className="flex min-h-0 flex-1 items-center justify-center rounded-[14px] border border-dashed border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-6 text-center">
          <p className="text-muted-foreground/80 max-w-[460px] text-[13px] leading-[1.55]">
            Nothing to read yet — give him a link, some text or a file above, and the passages that
            matter come back to him when a task touches them.
          </p>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[452px_minmax(0,1fr)] gap-5">
          <div className="flex min-h-0 min-w-0 flex-col gap-[22px]">
            <section
              aria-labelledby="lib-h"
              className="bg-card border-border flex min-h-0 flex-col overflow-hidden rounded-[14px] border"
            >
              <div className="border-border/80 flex h-12 shrink-0 items-center gap-2 border-b pr-2 pl-4">
                <h2 id="lib-h" className="m-0 text-[13px] font-semibold">
                  Library
                </h2>
                <span className="flex-1" />
                <Segmented
                  size="sm"
                  label="Show"
                  value={filter}
                  onChange={(v) => {
                    setFilter(v);
                    setSelectedId(null);
                  }}
                  options={[
                    { value: "all", label: "All", count: all.length },
                    { value: "link", label: "Links", count: count("link") },
                    { value: "text", label: "Text", count: count("text") },
                    { value: "file", label: "Files", count: count("file") },
                  ]}
                />
              </div>
              <SourceList
                items={items}
                selected={selected}
                onSelect={(s) => {
                  setSelectedId(s.id);
                  setAskNote("");
                }}
                onAsk={(s) => askAbout(s)}
                onRemove={(s) => void removeOne(s)}
                empty={query ? "Nothing here matches the search." : "None of this kind yet."}
              />
              <div className="border-border/80 text-muted-foreground/80 flex h-10 shrink-0 items-center gap-3.5 border-t px-4 text-[11.5px]">
                <span className="flex items-center gap-1.5">
                  <Kbd>↑↓</Kbd>move
                </span>
                <span className="flex items-center gap-1.5">
                  <Kbd>↵</Kbd>ask about it
                </span>
                <span className="flex items-center gap-1.5">
                  <Kbd>⌫</Kbd>remove
                </span>
              </div>
            </section>
            <Keeps sources={snap.sources} />
          </div>

          {selected ? (
            <Reader
              key={selected.id}
              source={selected}
              kind={selected.kind}
              snap={snap}
              onAsk={() => askAbout(selected)}
              askNote={askNote}
              onRemove={() => void removeOne(selected)}
            />
          ) : (
            <div className="border-border text-muted-foreground/80 flex items-center justify-center rounded-[14px] border text-[13px]">
              Pick a source to read it.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Asking about one ───────────────────────────────────────────────────── */

/**
 * Put "About source #12 ("…"): " in the chat you were last in, and go there.
 *
 * The id rather than the text: he has `read_source`, and a source is up to 20,000 characters that
 * would otherwise land in the transcript as a stale copy. The composer is the focused chat's —
 * the same slot a dropped file and a design reference use, because the panel sits above every
 * chat pane and none of them is reachable by a hook from here. Appended, never assigned: you may
 * have been halfway through a sentence.
 */
function useAsk() {
  const navigate = useNavigate();
  return (source: Source): boolean => {
    const composer = activeComposer();
    if (!composer) return false;
    const next = askLine(composer.getState().text, source);
    if (next !== null) composer.setText(next);
    navigate(pathForHome());
    return true;
  };
}

/* ── Feeding him ────────────────────────────────────────────────────────── */

function IngestZone({
  busy,
  dragging,
  onFeed,
}: {
  busy: boolean;
  /** A file is being dragged over the screen, and letting go will feed it. */
  dragging: boolean;
  onFeed: (value: string) => Promise<boolean>;
}) {
  const [value, setValue] = useState("");
  const lines = value ? value.split("\n").length : 0;

  const field = useRef<HTMLTextAreaElement>(null);
  const submit = async () => {
    // Nothing typed: the button says where to put it rather than doing nothing.
    if (!value.trim()) return field.current?.focus();
    if (await onFeed(value)) setValue("");
  };

  return (
    <form
      aria-label="Feed him a source"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      className={cn(
        "bg-sidebar flex flex-col gap-3 rounded-[14px] border border-dashed px-[18px] pt-[18px] pb-4 transition-colors",
        dragging
          ? "border-kith/55 bg-kith/[0.05]"
          : "border-[color-mix(in_oklab,var(--foreground)_18%,transparent)]",
      )}
    >
      <div className="flex items-center gap-3.5">
        <span className="bg-muted/60 text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-[10px]">
          <Upload className="size-[18px]" />
        </span>
        <label htmlFor="feed" className="sr-only">
          Paste a link or text, or drop a file
        </label>
        <div className="bg-background border-input focus-within:border-ring flex h-12 min-w-0 flex-1 items-center gap-2.5 rounded-[10px] border pr-2 pl-3.5 transition-colors">
          {/* A one-line textarea, not an input: an input drops the newlines from a paste, and the
              paragraph breaks are where his passages end. */}
          <textarea
            ref={field}
            id="feed"
            rows={1}
            value={value}
            disabled={busy}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder={dragging ? "Let go to feed him this file" : "Paste a link or text, or drop a file"}
            className="text-foreground placeholder:text-muted-foreground/80 h-[22px] min-w-0 flex-1 resize-none overflow-hidden bg-transparent text-sm leading-[22px] whitespace-pre outline-none disabled:opacity-60"
          />
          {lines > 1 ? (
            <span className="text-muted-foreground/80 shrink-0 text-xs tabular-nums">
              {lines.toLocaleString()} lines · {value.length.toLocaleString()} chars
            </span>
          ) : null}
          <button type="submit" disabled={busy} className={cn(BUTTON.primary, "gap-2 pr-2.5 pl-3.5")}>
            Feed him
            <span className="flex h-4 items-center rounded-[5px] border border-current/30 px-1 font-mono text-[11px] leading-none font-medium opacity-75">
              ↵
            </span>
          </button>
        </div>
      </div>
      <div className="text-muted-foreground/80 flex items-center gap-5 pl-[54px] text-xs whitespace-nowrap">
        <span className="flex items-center gap-1.5 [&_svg]:size-[13px]">
          <LinkIcon />
          <span>
            <span className="text-foreground/80">A link</span> — he fetches the page and reads it
          </span>
        </span>
        <span className="flex items-center gap-1.5 [&_svg]:size-[13px]">
          <AlignLeft />
          <span>
            <span className="text-foreground/80">Text</span> — anything you paste
          </span>
        </span>
        <span className="flex items-center gap-1.5 [&_svg]:size-[13px]">
          <File />
          <span>
            <span className="text-foreground/80">A file</span> — drop it anywhere on this box
          </span>
        </span>
        <span className="flex-1" />
        <span className="truncate">He splits each one into passages, and recalls the ones that matter.</span>
      </div>
    </form>
  );
}

/**
 * What he is reading right now.
 *
 * The board draws "12 of 40 passages" and a Stop button, and neither is available to draw: the
 * ingest is one request that answers when he has fetched, split and embedded the whole thing. So
 * the bar is indeterminate and the number is how long it has taken — both true — and there is no
 * Stop, because cancelling the request would not stop the server storing what it read.
 */
function ReadingNow({ label, since }: { label: string; since: number }) {
  const bar = useRef<HTMLSpanElement>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, []);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const run = bar.current?.animate?.(
      [{ transform: "translateX(-100%)" }, { transform: "translateX(340%)" }],
      { duration: 1400, iterations: Infinity, easing: "ease-in-out" },
    );
    return () => run?.cancel();
  }, []);
  const seconds = Math.max(0, Math.round((now - since) / 1000));

  return (
    <div
      role="status"
      className="bg-card border-border flex h-12 items-center gap-3 rounded-xl border pr-4 pl-4"
    >
      <LiveDot />
      <span className="text-foreground flex min-w-0 shrink items-center text-[13px] whitespace-nowrap">
        Reading&nbsp;
        <span className="text-foreground/80 truncate font-mono text-[12.5px]">{label}</span>…
      </span>
      <span className="flex min-w-20 flex-1 items-center px-2">
        <span className="bg-muted relative block h-1 w-full overflow-hidden rounded-full">
          <span ref={bar} className="bg-roam absolute inset-y-0 left-0 w-[30%] rounded-full" />
        </span>
      </span>
      <span className="text-muted-foreground text-xs whitespace-nowrap tabular-nums">
        {seconds < 2 ? "Just started" : `${seconds}s`}
      </span>
    </div>
  );
}

/* ── The list ───────────────────────────────────────────────────────────── */

function SourceList({
  items,
  selected,
  onSelect,
  onAsk,
  onRemove,
  empty,
}: {
  items: (Source & { kind: SourceKind })[];
  selected: Source | null;
  onSelect: (source: Source) => void;
  onAsk: (source: Source) => void;
  onRemove: (source: Source) => void;
  empty: string;
}) {
  const list = useRef<HTMLDivElement>(null);
  const at = selected ? items.findIndex((s) => s.id === selected.id) : -1;

  // The selected row stays in view as the keys move it. Keyed on the id, not the row: the
  // snapshot hands back a new object for the same source on every refetch, and following that
  // would pull the list back to the selection whenever anything anywhere changed.
  const selectedId = selected?.id;
  useEffect(() => {
    if (selectedId == null) return;
    list.current?.querySelector(`[data-id="${selectedId}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    const move = (to: number) => {
      event.preventDefault();
      const next = items[Math.max(0, Math.min(items.length - 1, to))];
      if (next) onSelect(next);
    };
    if (event.key === "ArrowDown") return move(at + 1);
    if (event.key === "ArrowUp") return move(at - 1);
    if (event.key === "Home") return move(0);
    if (event.key === "End") return move(items.length - 1);
    if (event.key === "Enter" && selected) {
      event.preventDefault();
      return onAsk(selected);
    }
    if ((event.key === "Backspace" || event.key === "Delete") && selected) {
      event.preventDefault();
      return onRemove(selected);
    }
  };

  if (items.length === 0) {
    return <p className="text-muted-foreground/80 px-4 py-6 text-[12.5px]">{empty}</p>;
  }

  return (
    <div
      ref={list}
      role="listbox"
      tabIndex={0}
      aria-label="Sources"
      aria-activedescendant={selected ? `source-${selected.id}` : undefined}
      onKeyDown={onKeyDown}
      className="focus-visible:ring-ring/40 flex min-h-0 flex-col gap-0.5 overflow-y-auto p-1.5 outline-none focus-visible:ring-2 focus-visible:ring-inset"
    >
      {items.map((s) => {
        const on = s.id === selected?.id;
        return (
          <div
            key={s.id}
            id={`source-${s.id}`}
            data-id={s.id}
            role="option"
            aria-selected={on}
            onClick={() => {
              onSelect(s);
              list.current?.focus();
            }}
            className={cn(
              "flex h-[60px] shrink-0 cursor-default items-center gap-3 rounded-[10px] pr-3 pl-2.5 transition-colors",
              on ? "bg-muted" : "hover:bg-muted/50",
            )}
          >
            <span className="bg-muted/60 text-muted-foreground flex size-8 shrink-0 items-center justify-center rounded-lg [&_svg]:size-3.5">
              {KIND_ICON[s.kind]}
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="text-foreground truncate text-[13px] font-medium">{s.title}</span>
              <span className="text-muted-foreground/80 truncate text-[11.5px] tabular-nums">{metaFor(s, s.kind)}</span>
            </span>
            <span className="text-muted-foreground/80 shrink-0 text-[11.5px] whitespace-nowrap">
              {shortDate(s.created_at)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** WHAT HE KEEPS — how much there is, in the units he recalls by. */
function Keeps({ sources }: { sources: Source[] }) {
  const chars = sources.reduce((sum, s) => sum + s.chars, 0);
  // Passages only when the server counts them. The split is exact only on the text, and the list
  // has lengths, not text — a number worked out from a length would be a guess printed as a fact.
  const known = sources.length > 0 && sources.every((s) => typeof s.passages === "number");
  const passages = known ? sources.reduce((sum, s) => sum + (s.passages ?? 0), 0) : null;
  const tiles: [string, string][] = [
    [sources.length.toLocaleString(), sources.length === 1 ? "source" : "sources"],
    ...(passages !== null ? [[passages.toLocaleString(), "passages for recall"] as [string, string]] : []),
    [chars.toLocaleString(), "characters"],
  ];
  return (
    <section aria-labelledby="keep-h" className="flex shrink-0 flex-col gap-3 px-1">
      <h2 id="keep-h" className="m-0">
        <Label>What he keeps</Label>
      </h2>
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${tiles.length}, minmax(0, 1fr))` }}>
        {tiles.map(([value, label]) => (
          <div key={label} className="bg-sidebar border-border/80 flex flex-col gap-0.5 rounded-[10px] border px-3 py-2.5">
            <span className="text-xl font-[650] tabular-nums">{value}</span>
            <span className="text-muted-foreground/80 text-[11.5px]">{label}</span>
          </div>
        ))}
      </div>
      <p className="text-muted-foreground m-0 text-[12.5px] leading-[1.55]">
        When a task or a question touches one of these, he pulls in the passages that match — not the
        whole page. Ask about a source to point him straight at it.
      </p>
    </section>
  );
}

/* ── The reader ─────────────────────────────────────────────────────────── */

function Reader({
  source,
  kind,
  snap,
  onAsk,
  askNote,
  onRemove,
}: {
  source: Source;
  kind: SourceKind;
  snap: BrainSnapshot;
  onAsk: () => void;
  /** Said when there was no chat to put the question in. */
  askNote: string;
  onRemove: () => void;
}) {
  const detail = useQuery({
    queryKey: keys.source(source.id),
    queryFn: () => fetchSource(source.id),
    // A stored source does not change; only removing it does, and that leaves this screen.
    staleTime: Infinity,
  });
  const full = detail.data;
  const { passages, total } = useMemo(() => passagesOf(full?.content ?? ""), [full?.content]);
  const project = full?.project_id != null ? snap.projects.find((p) => p.id === full.project_id) : undefined;

  // Which passages are on screen, for "Passages 14–17 of 38".
  const body = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<[number, number] | null>(null);
  const measure = useCallback(() => {
    const box = body.current;
    if (!box) return;
    const top = box.scrollTop;
    const bottom = top + box.clientHeight;
    let first = 0;
    let last = 0;
    for (const el of Array.from(box.querySelectorAll<HTMLElement>("[data-passage]"))) {
      const a = el.offsetTop - box.offsetTop;
      const b = a + el.offsetHeight;
      if (b > top + 4 && a < bottom - 4) {
        const n = Number(el.dataset.passage);
        if (!first) first = n;
        last = n;
      }
    }
    setRange(first ? [first, last] : null);
  }, []);
  useEffect(() => {
    measure();
    const box = body.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(measure);
    watch.observe(box);
    return () => watch.disconnect();
  }, [measure, passages]);

  // Find in this source. ⌘F while a source is open — it is the find you would want here, and the
  // panel deliberately leaves ⌘F alone everywhere else.
  const findBox = useRef<HTMLInputElement>(null);
  const [find, setFind] = useState("");
  const [hit, setHit] = useState(0);
  const [hits, setHits] = useState(0);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        findBox.current?.focus();
        findBox.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    const marks = Array.from(body.current?.querySelectorAll<HTMLElement>("mark[data-find]") ?? []);
    setHits(marks.length);
    marks.forEach((mark, i) => mark.toggleAttribute("data-current", i === hit));
    marks[hit]?.scrollIntoView({ block: "center" });
  }, [find, hit, passages]);
  const step = (by: number) => hits && setHit((h) => (h + by + hits) % hits);

  const added = longDate(source.created_at);
  const visible = range
    ? range[0] === range[1]
      ? `Passage ${range[0]} of ${total}`
      : `Passages ${range[0]}–${range[1]} of ${total}`
    : total
      ? `${total} passage${total === 1 ? "" : "s"}`
      : "";

  return (
    <article
      aria-labelledby="rd-h"
      className="bg-card border-border flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[14px] border"
    >
      <div className="flex items-start gap-4 pt-5 pr-4 pl-6">
        <span className="bg-muted/60 text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-[10px] [&_svg]:size-[17px]">
          {KIND_ICON[kind]}
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <h2 id="rd-h" className="m-0 truncate text-xl leading-[1.25] font-[650] tracking-[-0.01em]">
            {source.title}
          </h2>
          {kind === "link" ? (
            <a
              href={source.origin}
              target="_blank"
              rel="noreferrer"
              className="text-muted-foreground hover:text-foreground flex min-w-0 items-center gap-1.5 font-mono text-xs transition-colors"
            >
              <span className="truncate">{bareUrl(source.origin)}</span>
              <ExternalLink className="text-muted-foreground/80 size-3 shrink-0" />
            </a>
          ) : (
            <span className="text-muted-foreground font-mono text-xs">
              {kind === "file" ? "Dropped file, read as text" : "Pasted text"}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onAsk}
            className={cn(BUTTON.primary, "gap-[7px] pr-3.5 pl-3")}
          >
            <MessageSquare className="size-3.5" />
            Ask about this
          </button>
          {kind === "link" ? (
            <a href={source.origin} target="_blank" rel="noreferrer" className={BUTTON.secondary}>
              Open original
            </a>
          ) : null}
          <span className="bg-border mx-0.5 h-5 w-px" aria-hidden />
          <button type="button" onClick={onRemove} className={cn(BUTTON.danger, "px-2.5")}>
            Remove
          </button>
        </div>
      </div>
      {askNote ? <p className="text-kith mt-2 mr-4 ml-20 text-[12.5px]">{askNote}</p> : null}

      <dl className="bg-background border-border/80 mt-[18px] mr-6 ml-20 grid grid-cols-4 overflow-hidden rounded-[10px] border">
        <Fact label="Added">{added || "—"}</Fact>
        <Fact label="Size">
          <span className="tabular-nums">{source.chars.toLocaleString()} characters</span>
        </Fact>
        <Fact label="For recall">
          {full ? `Kept as ${total} passage${total === 1 ? "" : "s"}` : "…"}
        </Fact>
        <Fact label="Project">
          {project ? project.name : <span className="text-muted-foreground/80">{full ? "None" : "…"}</span>}
        </Fact>
      </dl>

      <div className="border-border/80 mt-[18px] flex h-11 shrink-0 items-center gap-2.5 border-y pr-4 pl-6">
        <Label>Reading</Label>
        <span className="text-muted-foreground text-xs tabular-nums">{visible}</span>
        <span className="flex-1" />
        <label htmlFor="find" className="sr-only">
          Find in this source
        </label>
        <span className="bg-background border-border focus-within:border-ring flex h-7 w-[240px] items-center gap-[7px] rounded-[7px] border px-2 transition-colors">
          <Search className="text-muted-foreground/80 size-3 shrink-0" />
          <input
            ref={findBox}
            id="find"
            value={find}
            onChange={(event) => {
              setFind(event.target.value);
              setHit(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                step(event.shiftKey ? -1 : 1);
              }
              if (event.key === "Escape") {
                event.preventDefault();
                setFind("");
                event.currentTarget.blur();
              }
            }}
            placeholder="Find in this source"
            className="text-foreground placeholder:text-muted-foreground/80 h-[26px] min-w-0 flex-1 bg-transparent text-xs outline-none"
          />
          {find.trim() ? (
            <span className="text-muted-foreground/80 shrink-0 text-[11px] tabular-nums">
              {hits ? `${hit + 1}/${hits}` : "0"}
            </span>
          ) : (
            <Kbd>⌘F</Kbd>
          )}
        </span>
      </div>

      {/* The stored copy, with the passage seams he recalls by in the gutter. */}
      <div
        ref={body}
        onScroll={measure}
        className="relative flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 pt-[18px] pb-6"
      >
        {detail.isPending ? (
          <p className="text-muted-foreground/80 pl-14 text-[13px]">Opening his copy…</p>
        ) : detail.isError ? (
          <p className="text-destructive pl-14 text-[13px]">Couldn't open this one — {String(detail.error?.message ?? "")}</p>
        ) : !full ? (
          <p className="text-muted-foreground/80 pl-14 text-[13px]">This source is gone.</p>
        ) : (
          passages.map((passage) => (
            <div
              key={passage.n}
              data-passage={passage.n}
              className="grid grid-cols-[40px_minmax(0,700px)] gap-4"
            >
              <span className="text-muted-foreground/50 pt-[3px] text-right font-mono text-[11px] tabular-nums select-none">
                {passage.n}
              </span>
              <div className="flex min-w-0 flex-col gap-2.5">
                {blocksOf(passage.text).map((block, i) =>
                  block.type === "code" ? (
                    <pre
                      key={i}
                      className="bg-background border-border/80 text-foreground/80 m-0 overflow-x-auto rounded-lg border px-3.5 py-2.5 font-mono text-xs leading-[1.6]"
                    >
                      <Marked text={block.text} find={find} />
                    </pre>
                  ) : block.type === "heading" ? (
                    <h3 key={i} className="text-foreground m-0 text-[13.5px] font-semibold">
                      <Inline text={block.text} find={find} />
                    </h3>
                  ) : (
                    <p key={i} className="text-foreground/80 m-0 text-[13.5px] leading-[1.65] break-words whitespace-pre-line">
                      <Inline text={block.text} find={find} />
                    </p>
                  ),
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="border-border/80 text-muted-foreground/80 flex h-10 shrink-0 items-center gap-2 border-t px-6 text-[11.5px]">
        <Info className="size-[13px] shrink-0" />
        <span className="truncate">
          {kind === "link"
            ? `This is the copy he read on ${added}. The live page may have changed since — Open original to compare.`
            : `This is the text he was given on ${added}, as he keeps it.`}
        </span>
      </div>
    </article>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-border/80 flex min-w-0 flex-col gap-[3px] px-3.5 py-[9px] [&+&]:border-l">
      <dt className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">{label}</dt>
      <dd className="text-foreground m-0 truncate text-[13px] whitespace-nowrap">{children}</dd>
    </div>
  );
}

/** Text with `inline code` drawn as code, and the find term marked. */
function Inline({ text, find }: { text: string; find: string }) {
  const parts = text.split(/(`[^`\n]+`)/);
  return (
    <>
      {parts.map((part, i) =>
        part.length > 2 && part.startsWith("`") && part.endsWith("`") ? (
          <code key={i} className="bg-muted/60 text-foreground rounded px-[5px] py-px font-mono text-xs">
            <Marked text={part.slice(1, -1)} find={find} />
          </code>
        ) : (
          <Marked key={i} text={part} find={find} />
        ),
      )}
    </>
  );
}

/** `text`, with every occurrence of `find` (any case) in a `<mark>` the reader can step through. */
function Marked({ text, find }: { text: string; find: string }) {
  const want = find.trim();
  if (!want) return <>{text}</>;
  const pattern = new RegExp(`(${want.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
  return (
    <>
      {text.split(pattern).map((part, i) =>
        i % 2 === 1 ? (
          <mark
            key={i}
            data-find
            className="bg-kith/25 text-foreground data-[current]:bg-kith/60 rounded-[3px]"
          >
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}
