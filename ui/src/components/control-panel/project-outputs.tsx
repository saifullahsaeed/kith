/**
 * Everything the project's tasks produced, in one place — a grid you can recognise things in, and
 * the selected one previewed beside it.
 *
 * It was only ever reachable one task at a time, which answers "what did this task make" and not
 * "what has this project made" — and the second is the one you ask when you come back to a project
 * after a week. Then it was a table of 57 paths, 54 of them `.kith/screens/NN-something.html`,
 * which is a list you read rather than look at. A design is a picture; the grid draws it as one,
 * with the same renderer the canvas uses, so what you see here is what opening it shows.
 */
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { DropdownMenu } from "radix-ui";
import {
  ArrowDownWideNarrow,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FileCode2,
  FileText,
  Folder,
  Globe,
  LayoutGrid,
  Link2,
  List,
  PanelsTopLeft,
  StickyNote,
  X,
} from "lucide-react";
import { DesignView } from "@/components/design/design-view";
import { FileViewer, Markdown, skipTextRead } from "@/components/files";
import { classify } from "@/components/files/kinds";
import { useMedia } from "@/components/files/media";
import { Layer, useLayer } from "@/hooks/use-layer";
import { fetchWorkspaceFile, type Project, type ProjectOutput } from "@/lib/backend/brain";
import { formatBytes } from "@/lib/bytes";
import { useBoardOverlay } from "@/lib/design/board-overlay";
import { frameAttribute, readFrame } from "@/lib/design/frame";
import { copyText, openWorkspaceFile } from "@/lib/files";
import { cn } from "@/lib/utils";
import { BUTTON, Empty, KindTile, Label, Segmented } from "./chrome";
import {
  clock,
  dayAndClock,
  extensionLabel,
  fileName,
  homeShort,
  insideProject,
  linkDomain,
  outputKind,
  outputTime,
  shortDay,
  sortOutputs,
  type OutputFilter,
  type OutputKind,
  type OutputSort,
} from "./project-page-model";

/** Cards before "Show N more": three rows of four, what the grid holds at the window's height. */
const GRID_PAGE = 12;

const FILTERS: { value: OutputFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "design", label: "Designs" },
  { value: "file", label: "Files" },
  { value: "link", label: "Links" },
  { value: "note", label: "Notes" },
];

const SORTS: { value: OutputSort; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "name", label: "By name" },
  { value: "largest", label: "Largest first" },
];

/** What a thumbnail is fitted into: the card's 164px well, and the preview's 404px one. */
const CARD_BOX = { w: 208, h: 145 };
const PREVIEW_BOX = { w: 360, h: 380 };

/** A card's time: the clock when it was today, the day otherwise. */
const cardWhen = (iso: string) => (shortDay(iso) === "Today" ? clock(iso) : shortDay(iso));

export function ProjectOutputs({
  items,
  loading,
  project,
  onOpenTask,
}: {
  items: ProjectOutput[];
  loading: boolean;
  project: Project;
  onOpenTask: (id: number) => void;
}) {
  const [filter, setFilter] = useState<OutputFilter>("all");
  const [sort, setSort] = useState<OutputSort>("newest");
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [chosen, setChosen] = useState<number | null>(null);
  const [closed, setClosed] = useState(false);
  const [all, setAll] = useState(false);
  const [refused, setRefused] = useState("");
  const [viewing, setViewing] = useState<ProjectOutput | null>(null);
  const showCanvas = useBoardOverlay((state) => state.show);

  /* The canvas opens over this panel, and both listen for Escape on the window. Registering as
   * the frontmost layer while it is up is what makes Escape close the canvas and leave you here,
   * instead of closing the canvas *and* the panel behind it in one keystroke. */
  const canvasOpen = useBoardOverlay((state) => state.open != null);
  useLayer(Layer.Overlay, canvasOpen);

  const count = (kind: OutputKind) => items.filter((one) => outputKind(one) === kind).length;
  const counts: Record<OutputFilter, number> = {
    all: items.length,
    design: count("design"),
    file: count("file"),
    link: count("link"),
    note: count("note"),
  };
  const shown = sortOutputs(filter === "all" ? items : items.filter((one) => outputKind(one) === filter), sort);
  const visible = layout === "list" || all ? shown : shown.slice(0, GRID_PAGE);
  // The first one stands in until you pick, so the preview is there when you arrive.
  const selected = closed ? null : (shown.find((one) => one.id === chosen) ?? shown[0] ?? null);
  const position = selected ? shown.indexOf(selected) : -1;

  const select = (id: number) => {
    setChosen(id);
    setClosed(false);
  };
  const go = (step: number) => {
    if (!shown.length) return;
    const next = shown[Math.max(0, Math.min(shown.length - 1, (position < 0 ? 0 : position) + step))];
    if (shown.indexOf(next) >= GRID_PAGE) setAll(true);
    select(next.id);
  };

  /* The bytes, not a description of them — the same route and the same reasons as the task
   * page's download: `/api/workspace/raw` serves the file with its real type, and a failure says
   * so rather than saving a text file holding the path. A note is his text, saved as text. */
  const download = async (one: ProjectOutput) => {
    setRefused("");
    let blob: Blob;
    let name: string;
    if (one.kind === "text") {
      blob = new Blob([one.content], { type: "text/plain;charset=utf-8" });
      name = `${(one.title || "note").replace(/\s+/g, "_")}.txt`;
    } else {
      const response = await fetch(
        `/api/workspace/raw?path=${encodeURIComponent(one.content)}&projectId=${project.id}`,
      ).catch(() => null);
      if (!response?.ok) {
        setRefused(`Couldn't read ${one.content} — it may have been moved or renamed.`);
        return;
      }
      blob = await response.blob();
      name = fileName(one.content) || one.title || "output";
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };

  const reveal = (one: ProjectOutput) => {
    setRefused("");
    openWorkspaceFile(one.content, true, project.id).catch((err: unknown) =>
      setRefused(err instanceof Error ? err.message : "Couldn't show it in its folder."),
    );
  };

  /** The one thing a double-click or Enter does: what the preview's amber button does. */
  const primary = (one: ProjectOutput) => {
    const kind = outputKind(one);
    if (kind === "design") showCanvas({ taskId: one.task_id, projectId: project.id, title: one.task_goal });
    else if (kind === "link") window.open(one.content, "_blank", "noreferrer");
    else if (kind === "file") setViewing(one);
    else onOpenTask(one.task_id);
  };

  const onGridKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const columns = selected ? 4 : 5;
    const step: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -columns, ArrowDown: columns };
    if (event.key in step && layout === "grid") {
      event.preventDefault();
      go(step[event.key]);
    } else if ((event.key === "ArrowUp" || event.key === "ArrowDown") && layout === "list") {
      event.preventDefault();
      go(event.key === "ArrowUp" ? -1 : 1);
    } else if (event.key === "Enter" && selected) {
      event.preventDefault();
      primary(selected);
    }
  };

  // Keep the keyboard on the card the arrows moved to.
  const grid = useRef<HTMLDivElement>(null);
  const selectedId = selected?.id;
  useEffect(() => {
    if (selectedId == null || !grid.current?.contains(document.activeElement)) return;
    grid.current.querySelector<HTMLElement>(`[data-output="${selectedId}"]`)?.focus();
  }, [selectedId]);

  const hidden = shown.slice(GRID_PAGE);

  return (
    <div className="mt-3.5 -mb-1 flex min-h-0 flex-1 gap-6">
      <section aria-label="Outputs" className="flex min-w-0 flex-1 flex-col gap-3.5 overflow-hidden">
        <div className="flex h-9 shrink-0 items-center gap-3">
          <Segmented
            label="Filter by kind"
            value={filter}
            onChange={(value) => {
              setFilter(value);
              setAll(false);
              setChosen(null);
            }}
            options={FILTERS.map((one) => ({
              value: one.value,
              // Empty kinds stay, quiet, so the row always reads the same and 0 is an answer
              // rather than a missing button.
              label: (
                <span className={cn("flex items-center", !counts[one.value] && filter !== one.value && "text-muted-foreground/50")}>
                  {one.label}
                  <span className={cn("ms-1.5 tabular-nums", counts[one.value] ? "text-muted-foreground/80" : "text-muted-foreground/50")}>
                    {counts[one.value]}
                  </span>
                </span>
              ),
            }))}
          />
          <span className="flex-1" />
          <SortMenu value={sort} onChange={setSort} />
          <div role="group" aria-label="Layout" className="border-border bg-sidebar flex gap-0.5 rounded-lg border p-0.5">
            {(["grid", "list"] as const).map((one) => (
              <button
                key={one}
                type="button"
                aria-label={one === "grid" ? "Grid" : "List"}
                aria-pressed={layout === one}
                onClick={() => setLayout(one)}
                className={cn(
                  "flex h-[26px] w-7 items-center justify-center rounded-md transition-colors",
                  layout === one ? "bg-muted text-foreground" : "text-muted-foreground/80 hover:text-foreground",
                )}
              >
                {one === "grid" ? <LayoutGrid className="size-3.5" /> : <List className="size-3.5" />}
              </button>
            ))}
          </div>
        </div>

        {refused ? (
          <p className="text-destructive border-destructive/30 bg-destructive/10 shrink-0 rounded-lg border px-3 py-2 text-[12.5px]">
            {refused}
          </p>
        ) : null}

        {loading ? null : shown.length === 0 ? (
          <Empty title={items.length === 0 ? "Nothing produced on this project yet." : "Nothing of that kind."}>
            {items.length === 0
              ? "What his tasks file — designs, documents, links, notes — collects here as he works."
              : "Pick another kind above, or All."}
          </Empty>
        ) : layout === "grid" ? (
          <>
            <div
              ref={grid}
              onKeyDown={onGridKey}
              className={cn(
                "grid min-h-0 auto-rows-[226px] gap-3.5 overflow-y-auto",
                selected ? "grid-cols-4" : "grid-cols-5",
                all && "pe-1",
              )}
            >
              {visible.map((one) => (
                <OutputCard
                  key={one.id}
                  output={one}
                  project={project}
                  selected={one.id === selected?.id}
                  onSelect={() => select(one.id)}
                  onOpen={() => primary(one)}
                />
              ))}
            </div>
            {hidden.length && !all ? (
              <button
                type="button"
                onClick={() => setAll(true)}
                className="border-border/80 text-foreground/80 hover:bg-muted/50 flex h-9 shrink-0 items-center gap-2.5 rounded-[10px] border px-3.5 text-[12.5px] transition-colors"
              >
                <span className="shrink-0">Show {hidden.length} more</span>
                <span className="text-muted-foreground/80 min-w-0 truncate text-xs">
                  {hidden[0].title}
                  {hidden.length > 1 ? ` back to ${hidden[hidden.length - 1].title}` : ""}
                </span>
                <span className="flex-1" />
                <ChevronDown className="text-muted-foreground/80 size-3.5 shrink-0" />
              </button>
            ) : null}
          </>
        ) : (
          <OutputTable
            items={visible}
            directory={project.directory}
            selectedId={selected?.id ?? null}
            onSelect={select}
            onOpen={primary}
            onOpenTask={onOpenTask}
            onDownload={(one) => void download(one)}
            onKeyDown={onGridKey}
          />
        )}
      </section>

      {selected ? (
        <Preview
          output={selected}
          project={project}
          position={position}
          total={shown.length}
          onPrev={() => go(-1)}
          onNext={() => go(1)}
          onClose={() => setClosed(true)}
          onPrimary={() => primary(selected)}
          onDownload={() => void download(selected)}
          onReveal={() => reveal(selected)}
          onOpenTask={() => onOpenTask(selected.task_id)}
        />
      ) : null}

      {viewing ? (
        <OutputViewer
          output={viewing}
          projectId={project.id}
          onClose={() => setViewing(null)}
          onDownload={() => void download(viewing)}
        />
      ) : null}
    </div>
  );
}

const MENU =
  "bg-popover/95 text-popover-foreground border-border/70 animate-in fade-in-0 zoom-in-95 z-50 min-w-40 overflow-hidden rounded-xl border p-1 shadow-xl backdrop-blur-xl duration-100";
const MENU_ITEM =
  "data-[highlighted]:bg-accent flex cursor-default items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] outline-none select-none";

function SortMenu({ value, onChange }: { value: OutputSort; onChange: (value: OutputSort) => void }) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label="Sort outputs"
          className="border-input bg-card text-foreground/80 hover:text-foreground flex h-[30px] items-center gap-2 rounded-lg border ps-3 pe-2.5 text-[12.5px] transition-colors"
        >
          <ArrowDownWideNarrow className="text-muted-foreground/80 size-[13px]" />
          {SORTS.find((one) => one.value === value)?.label}
          <ChevronDown className="text-muted-foreground/80 size-3" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={6} className={MENU}>
          <DropdownMenu.RadioGroup value={value} onValueChange={(next) => onChange(next as OutputSort)}>
            {SORTS.map((one) => (
              <DropdownMenu.RadioItem key={one.value} value={one.value} className={MENU_ITEM}>
                <span className="flex size-4 items-center justify-center">
                  <DropdownMenu.ItemIndicator>
                    <Check className="text-kith size-3.5" />
                  </DropdownMenu.ItemIndicator>
                </span>
                {one.label}
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/**
 * One output in the grid: a picture of it, its name and when, and the task it came from.
 *
 * The click is a transparent button laid over the card rather than the card itself. A design is
 * drawn with its own markup, and his screens are full of `<button>`s — inside a card that was a
 * button, that is a button in a button, which HTML does not allow and React says so about. The
 * picture is inert and hidden from the accessibility tree; the overlay is the one control.
 */
function OutputCard({
  output,
  project,
  selected,
  onSelect,
  onOpen,
}: {
  output: ProjectOutput;
  project: Project;
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
}) {
  const kind = outputKind(output);
  return (
    <div
      className={cn(
        "relative flex h-[226px] min-w-0 flex-col overflow-hidden rounded-[14px] border transition-colors",
        selected
          ? "bg-muted/60 border-[color-mix(in_oklab,var(--foreground)_46%,transparent)]"
          : "bg-card border-border hover:border-input",
      )}
    >
      <span
        aria-hidden
        className="bg-background border-border/80 flex h-[164px] w-full shrink-0 items-center justify-center overflow-hidden border-b"
      >
        <Thumb output={output} kind={kind} projectId={project.id} box={CARD_BOX} />
      </span>
      <span className="flex min-h-0 w-full flex-1 flex-col gap-1 px-3 pt-[11px]">
        <span className="flex items-baseline gap-2">
          <span className="text-foreground min-w-0 flex-1 truncate text-[13px] leading-4 font-medium">{output.title}</span>
          <span className="text-muted-foreground/80 shrink-0 text-[11px] leading-4 tabular-nums">
            {cardWhen(output.created_at)}
          </span>
        </span>
        <span className="text-muted-foreground/80 truncate text-[11.5px] leading-[14px]">
          <span className="text-muted-foreground font-mono text-[10.5px]">#{output.task_id}</span> {output.task_goal}
        </span>
      </span>
      <button
        type="button"
        data-output={output.id}
        aria-current={selected || undefined}
        aria-label={`${output.title}, from task ${output.task_id}`}
        onClick={onSelect}
        onFocus={onSelect}
        onDoubleClick={onOpen}
        title={`${output.title} — filed ${dayAndClock(output.created_at)}. Double-click to open.`}
        className="focus-visible:ring-ring/50 absolute inset-0 rounded-[14px] outline-none focus-visible:ring-2"
      />
    </div>
  );
}

/** What stands for an output where a picture goes: the design itself, or a tile for its kind. */
function Thumb({
  output,
  kind,
  projectId,
  box,
  large,
}: {
  output: ProjectOutput;
  kind: OutputKind;
  projectId: number;
  box: { w: number; h: number };
  large?: boolean;
}) {
  if (kind === "design") return <DesignThumb path={output.content} projectId={projectId} box={box} lifted={large} />;
  if (kind === "link") {
    return (
      <span className="flex flex-col items-center gap-3">
        <span className={cn("border-input bg-muted/60 text-muted-foreground flex items-center justify-center rounded-xl border", large ? "size-20" : "size-14")}>
          <Globe className={large ? "size-8" : "size-6"} strokeWidth={1.6} />
        </span>
        <span className="text-muted-foreground max-w-[200px] truncate font-mono text-[11px]">{linkDomain(output.content)}</span>
      </span>
    );
  }
  if (kind === "note") {
    return (
      <span
        className={cn(
          "border-input bg-muted/60 text-muted-foreground/80 overflow-hidden rounded-lg border p-3 text-left text-[9.5px] leading-[1.45]",
          large ? "h-[300px] w-[300px] text-[11.5px]" : "h-[120px] w-[150px]",
        )}
      >
        <StickyNote className="text-muted-foreground mb-1.5 size-3.5" />
        {output.content.slice(0, large ? 1200 : 240)}
      </span>
    );
  }
  return <FileTile path={output.content} bytes={output.bytes} large={large} />;
}

/** A document, as a document: a page with a folded corner and its extension on it. */
function FileTile({ path, bytes, large }: { path: string; bytes?: number; large?: boolean }) {
  return (
    <span className="flex flex-col items-center gap-3">
      <span
        className={cn(
          "border-input bg-muted/60 relative flex flex-col gap-[5px] rounded-lg border",
          large ? "h-[108px] w-[87px] px-3.5 pt-6" : "h-[72px] w-[58px] px-[9px] pt-4",
        )}
      >
        {/* The fold: a corner in the page's own background colour, with the hairline drawn on it. */}
        <span className="bg-background absolute -top-px -right-px size-3.5 rounded-bl-[5px] shadow-[-1px_1px_0_var(--input)]" />
        <span className="bg-foreground/15 block h-[3px] w-[52%] rounded-sm" />
        <span className="bg-foreground/15 block h-[3px] w-[66%] rounded-sm" />
        <span className="bg-foreground/15 block h-[3px] w-[41%] rounded-sm" />
        <span className={cn("text-foreground/80 absolute font-mono font-bold tracking-[0.04em]", large ? "bottom-2.5 left-3 text-xs" : "bottom-[7px] left-2 text-[10px]")}>
          {extensionLabel(path)}
        </span>
      </span>
      <span className="text-muted-foreground max-w-[200px] truncate font-mono text-[11px] whitespace-nowrap">
        {fileName(path)}
        {bytes !== undefined ? (
          <>
            <span className="text-muted-foreground/50"> · </span>
            <span className="text-muted-foreground/80">{formatBytes(bytes)}</span>
          </>
        ) : null}
      </span>
    </span>
  );
}

/**
 * A design, drawn small: the real file, through the canvas's own renderer, scaled to fit.
 *
 * Under the same cache key the chat's canvas strip uses, so a design already drawn there is not
 * fetched again here. Scaled from the design's *own* frame — a phone at 390×844 and a desktop at
 * 1440×900 each fit the well by their own shape, rather than one constant squeezing both. Inert:
 * the click belongs to the card around it.
 */
function DesignThumb({
  path,
  projectId,
  box,
  lifted,
}: {
  path: string;
  projectId: number;
  box: { w: number; h: number };
  lifted?: boolean;
}) {
  const file = useQuery({
    queryKey: ["design", path, projectId],
    queryFn: () => fetchWorkspaceFile(path, projectId),
    staleTime: 60_000,
  });
  const frame = readFrame(frameAttribute(file.data?.content ?? ""));
  const scale = Math.min(box.h / frame.height, box.w / frame.width);
  const tall = frame.height > frame.width;
  return (
    <span
      className="relative block shrink-0 overflow-hidden bg-white"
      style={{
        width: Math.round(frame.width * scale),
        height: Math.round(frame.height * scale),
        // A phone's own corners, at the scale it is drawn; a page gets a sheet's.
        borderRadius: tall ? 46 * scale : 6,
        boxShadow: `0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent), 0 ${lifted ? "10px 28px" : "4px 12px"} rgb(0 0 0 / 0.38)`,
      }}
    >
      {file.data?.content ? (
        <span
          aria-hidden
          className="pointer-events-none absolute top-0 left-0 block origin-top-left"
          style={{ transform: `scale(${scale})`, width: frame.width }}
        >
          <DesignView html={file.data.content} projectId={projectId} />
        </span>
      ) : (
        <span className="flex h-full items-center justify-center px-2 text-center text-[10px] text-neutral-500">
          {file.isError ? "couldn't read it" : ""}
        </span>
      )}
    </span>
  );
}

/** The current table, restyled: for when you want names and sizes in columns, not pictures. */
function OutputTable({
  items,
  directory,
  selectedId,
  onSelect,
  onOpen,
  onOpenTask,
  onDownload,
  onKeyDown,
}: {
  items: ProjectOutput[];
  /** The project's folder: a path in it reads relative to it, so the part that differs shows. */
  directory: string | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onOpen: (one: ProjectOutput) => void;
  onOpenTask: (id: number) => void;
  onDownload: (one: ProjectOutput) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
}) {
  const cols = "grid-cols-[minmax(0,5fr)_minmax(0,4fr)_4.5rem_6.5rem_2rem]";
  return (
    <div className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-[14px] border" onKeyDown={onKeyDown}>
      <div
        className={cn(
          "border-border/80 text-muted-foreground/80 grid h-9 shrink-0 items-center gap-4 border-b px-4 text-[10.5px] font-semibold tracking-[0.1em] uppercase",
          cols,
        )}
      >
        <span>Output</span>
        <span>From task</span>
        <span>Size</span>
        <span>Updated</span>
        <span />
      </div>
      <ul className="m-0 flex min-h-0 list-none flex-col gap-0.5 overflow-y-auto p-1.5">
        {items.map((one) => {
          const kind = outputKind(one);
          const when = outputTime(one);
          const selected = one.id === selectedId;
          return (
            <li
              key={one.id}
              className={cn(
                "grid h-[52px] shrink-0 items-center gap-4 rounded-[10px] px-2.5 transition-colors",
                cols,
                selected ? "bg-muted" : "hover:bg-muted/50",
              )}
            >
              <button
                type="button"
                data-output={one.id}
                onClick={() => onSelect(one.id)}
                onFocus={() => onSelect(one.id)}
                onDoubleClick={() => onOpen(one)}
                className="flex min-w-0 items-center gap-3 text-left outline-none"
                title={`${one.title} — double-click to open`}
              >
                <KindTile>
                  {kind === "link" ? <Link2 /> : kind === "note" ? <FileText /> : kind === "design" ? <PanelsTopLeft /> : <FileCode2 />}
                </KindTile>
                <span className="min-w-0">
                  <span className="text-foreground block truncate text-[13px]">{one.title}</span>
                  {kind !== "note" ? (
                    <span className="text-muted-foreground/80 block truncate font-mono text-[11px]" title={one.content}>
                      {kind === "link" ? one.content : insideProject(one.content, directory)}
                    </span>
                  ) : null}
                </span>
              </button>
              <button
                type="button"
                onClick={() => onOpenTask(one.task_id)}
                className="text-muted-foreground hover:text-foreground min-w-0 truncate text-left text-xs transition-colors"
                title={one.task_goal}
              >
                <span className="font-mono text-[11px]">#{one.task_id}</span> {one.task_goal}
              </button>
              <span className="text-muted-foreground/80 text-xs tabular-nums">
                {one.bytes !== undefined ? formatBytes(one.bytes) : kind === "note" ? `${one.content.length.toLocaleString()} ch` : "—"}
              </span>
              <span className="text-muted-foreground/80 text-xs" title={dayAndClock(when)}>
                {shortDay(when)}
              </span>
              {kind === "link" ? (
                <a
                  href={one.content}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`Open ${one.title}`}
                  className="text-muted-foreground hover:text-foreground hover:bg-muted flex size-8 items-center justify-center rounded-md transition-colors"
                >
                  <ArrowUpRight className="size-4" />
                </a>
              ) : (
                <button
                  type="button"
                  onClick={() => onDownload(one)}
                  aria-label={`Download ${one.title}`}
                  title="Download"
                  className="text-muted-foreground hover:text-foreground hover:bg-muted flex size-8 items-center justify-center rounded-md transition-colors"
                >
                  <Download className="size-4" />
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The selected output, larger, with what it is and what can be done with it. */
function Preview({
  output,
  project,
  position,
  total,
  onPrev,
  onNext,
  onClose,
  onPrimary,
  onDownload,
  onReveal,
  onOpenTask,
}: {
  output: ProjectOutput;
  project: Project;
  position: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  onPrimary: () => void;
  onDownload: () => void;
  onReveal: () => void;
  onOpenTask: () => void;
}) {
  const kind = outputKind(output);
  const [copied, setCopied] = useState<"" | "yes" | "no">("");
  useEffect(() => setCopied(""), [output.id]);
  const copy = (text: string) => void copyText(text).then((ok) => setCopied(ok ? "yes" : "no"));
  const icon = "flex size-7 items-center justify-center rounded-[7px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:opacity-40";

  return (
    <aside
      aria-label="Preview"
      className="border-border bg-card flex w-[420px] shrink-0 flex-col overflow-hidden rounded-[14px] border px-4 pb-4"
    >
      <div className="flex h-12 shrink-0 items-center gap-2">
        <Label count={`${position + 1} of ${total}`}>Preview</Label>
        <span className="flex-1" />
        <button type="button" aria-label="Previous output" onClick={onPrev} disabled={position <= 0} className={icon}>
          <ChevronLeft className="size-[15px]" />
        </button>
        <button type="button" aria-label="Next output" onClick={onNext} disabled={position >= total - 1} className={icon}>
          <ChevronRight className="size-[15px]" />
        </button>
        <button type="button" aria-label="Close preview" onClick={onClose} className={icon}>
          <X className="size-[15px]" />
        </button>
      </div>

      <div className="bg-background border-border/80 flex h-[404px] shrink-0 items-center justify-center overflow-hidden rounded-xl border">
        <PreviewBody output={output} kind={kind} projectId={project.id} />
      </div>

      <div className="mt-4 flex shrink-0 flex-col gap-1">
        <h2 className="text-foreground m-0 truncate text-[15px] font-semibold" title={output.title}>
          {output.title}
        </h2>
        <button
          type="button"
          onClick={onOpenTask}
          title="Open the task that made it"
          className="text-muted-foreground hover:text-foreground line-clamp-2 text-left text-[12.5px] leading-normal transition-colors"
        >
          From <span className="text-foreground/80 font-mono text-[11.5px]">#{output.task_id}</span>{" "}
          <span className="text-foreground/80">{output.task_goal}</span>
        </button>
      </div>

      <Facts output={output} kind={kind} projectId={project.id} />

      <div className="mt-auto flex shrink-0 flex-col gap-2 pt-4">
        <div className="flex gap-2">
          <button type="button" onClick={onPrimary} className={cn(BUTTON.primary, "flex-1 gap-2")}>
            {kind === "design" ? (
              <>
                <PanelsTopLeft className="size-3.5" />
                Open in canvas
              </>
            ) : kind === "link" ? (
              <>
                <ArrowUpRight className="size-3.5" />
                Open link
              </>
            ) : kind === "file" ? (
              <>
                <FileText className="size-3.5" />
                Open
              </>
            ) : (
              <>
                <ArrowUpRight className="size-3.5" />
                Open its task
              </>
            )}
          </button>
          {kind !== "link" ? (
            <button type="button" onClick={onDownload} className={cn(BUTTON.secondary, "gap-[7px]")}>
              <Download className="text-muted-foreground size-3.5" />
              Download
            </button>
          ) : null}
        </div>
        <div className="flex gap-2">
          {kind === "design" || kind === "file" ? (
            <button type="button" onClick={onReveal} className={cn(BUTTON.secondary, "flex-1 gap-[7px]")}>
              <Folder className="text-muted-foreground size-3.5" />
              Show in Files
            </button>
          ) : (
            <span className="flex-1" />
          )}
          {/* Says whether the copy really happened — see `copyText` for the version that lied. */}
          <button type="button" onClick={() => copy(output.content)} className={cn(BUTTON.ghost, "gap-[7px]")}>
            {copied === "yes" ? <Check className="text-roam size-3.5" /> : <Copy className="size-3.5" />}
            {copied === "yes"
              ? "Copied"
              : copied === "no"
                ? "Couldn't copy"
                : kind === "link"
                  ? "Copy link"
                  : kind === "note"
                    ? "Copy text"
                    : "Copy path"}
          </button>
        </div>
      </div>
    </aside>
  );
}

/** The larger picture: the design at the preview's size, an image as itself, a text file's
 *  opening lines, a note as his Markdown. */
function PreviewBody({ output, kind, projectId }: { output: ProjectOutput; kind: OutputKind; projectId: number }) {
  if (kind === "design") return <DesignThumb path={output.content} projectId={projectId} box={PREVIEW_BOX} lifted />;
  if (kind === "note") {
    return (
      <div className="text-muted-foreground h-full w-full overflow-y-auto p-5 text-[12.5px] leading-relaxed">
        <Markdown>{output.content}</Markdown>
      </div>
    );
  }
  if (kind === "file") {
    const type = classify(output.content).type;
    if (type === "image") return <ImagePreview path={output.content} projectId={projectId} />;
    if (!skipTextRead(output.content)) return <TextPreview output={output} projectId={projectId} />;
  }
  return <Thumb output={output} kind={kind} projectId={projectId} box={PREVIEW_BOX} large />;
}

function ImagePreview({ path, projectId }: { path: string; projectId: number }) {
  const { url, error } = useMedia(path, projectId);
  if (error) return <p className="text-muted-foreground/80 px-6 text-center text-[12.5px]">{error}</p>;
  if (!url) return null;
  return <img src={url} alt="" className="max-h-full max-w-full object-contain" />;
}

/** A text file's first screenful, as text — a page you can read the top of. */
function TextPreview({ output, projectId }: { output: ProjectOutput; projectId: number }) {
  const file = useQuery({
    queryKey: ["workspace", output.content, "content", projectId],
    queryFn: () => fetchWorkspaceFile(output.content, projectId),
    staleTime: 30_000,
  });
  if (file.isError) return <FileTile path={output.content} bytes={output.bytes} large />;
  return (
    <pre className="text-muted-foreground m-0 h-full w-full overflow-hidden p-4 font-mono text-[11px] leading-[1.55] whitespace-pre-wrap [mask-image:linear-gradient(to_bottom,black_80%,transparent)]">
      {(file.data?.content ?? "").slice(0, 4000)}
    </pre>
  );
}

/** Kind, path, size, when — each omitted rather than faked when it is not known. */
function Facts({ output, kind, projectId }: { output: ProjectOutput; kind: OutputKind; projectId: number }) {
  const rows: { term: string; value: ReactNode; mono?: boolean }[] = [];
  rows.push({ term: "Kind", value: <KindLabel output={output} kind={kind} projectId={projectId} /> });
  if (kind === "link") rows.push({ term: "Link", value: output.content, mono: true });
  else if (kind !== "note") rows.push({ term: "Path", value: homeShort(output.content), mono: true });
  if (output.bytes !== undefined) rows.push({ term: "Size", value: formatBytes(output.bytes) });
  else if (kind === "note") rows.push({ term: "Size", value: `${output.content.length.toLocaleString()} characters` });
  else if (kind !== "link") rows.push({ term: "Size", value: "Not found where it was filed" });
  rows.push({ term: "Filed", value: dayAndClock(output.created_at) });
  // Only when he came back and rewrote it — on most outputs the file and the filing are one moment.
  if (output.modified && new Date(output.modified).getTime() - new Date(output.created_at).getTime() > 60_000) {
    rows.push({ term: "Changed", value: dayAndClock(output.modified) });
  }
  return (
    <dl className="m-0 mt-3.5 grid shrink-0 grid-cols-[64px_minmax(0,1fr)] gap-x-3 gap-y-[7px] text-xs leading-[17px]">
      {rows.map((row) => (
        <div key={row.term} className="contents">
          <dt className="text-muted-foreground/80">{row.term}</dt>
          <dd className={cn("text-foreground/80 m-0 min-w-0 tabular-nums", row.mono && "font-mono text-[11px] break-all")}>
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** "HTML design · 390 × 844 mobile frame", "Markdown file", "Link". The frame is read from the
 *  design's own declaration, under the cache key its thumbnail already filled. */
function KindLabel({ output, kind, projectId }: { output: ProjectOutput; kind: OutputKind; projectId: number }) {
  const file = useQuery({
    queryKey: ["design", output.content, projectId],
    queryFn: () => fetchWorkspaceFile(output.content, projectId),
    enabled: kind === "design",
    staleTime: 60_000,
  });
  if (kind === "link") return <>Link · {linkDomain(output.content)}</>;
  if (kind === "note") return <>Note</>;
  if (kind === "file") return <>{classify(output.content).label} file</>;
  const frame = readFrame(frameAttribute(file.data?.content ?? ""));
  const named = frame.name !== "custom" && frame.name !== "default" ? ` ${frame.name}` : "";
  return (
    <>
      HTML design{file.data ? ` · ${frame.width} × ${frame.height}${named} frame` : ""}
    </>
  );
}

/** A file, opened in the app's own viewer — the same dialog the task page's preview uses. */
function OutputViewer({
  output,
  projectId,
  onClose,
  onDownload,
}: {
  output: ProjectOutput;
  projectId: number;
  onClose: () => void;
  onDownload: () => void;
}) {
  const [body, setBody] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    // A screenshot is shown by the viewer from its own bytes; reading it as text only ever
    // produced a decode error.
    if (skipTextRead(output.content)) return;
    fetchWorkspaceFile(output.content, projectId)
      .then((file) => setBody(file.content))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "couldn't read that file"));
  }, [output.content, projectId]);
  return (
    <FileViewer
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      name={output.content}
      title={output.title || fileName(output.content)}
      content={body}
      error={error || undefined}
      badge={<span className="bg-kith-soft text-kith shrink-0 rounded px-1.5 py-0.5 font-medium">output</span>}
      onDownload={onDownload}
      projectId={projectId}
      onOpenOnHost={(reveal) => openWorkspaceFile(output.content, reveal, projectId)}
    />
  );
}
