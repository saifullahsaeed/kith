/** The Files screen's preview pane: what the selected file is, without opening it. */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, Copy, FileCode2, FileImage, FileText, Folder, FolderOpen, PanelRightClose } from "lucide-react";

import { skipTextRead } from "@/components/files";
import { classify, looksBinary, looksTooBig } from "@/components/files/kinds";
import { useMedia } from "@/components/files/media";
import type { WorkspaceEntry } from "@/lib/backend/brain";
import { formatSize } from "@/lib/files";
import { escapeHtml, grammarFor, highlight } from "@/lib/highlight";
import { cn } from "@/lib/utils";
import { BUTTON } from "./chrome";
import { useFileText, useFolder } from "./files-data";
import { homeShort, lineTotal, modifiedLabel, sortEntries } from "./library";

/** How many lines of a file the preview draws. The rest is one click away in the viewer, and
 *  colouring a 40,000-line log to show the first screen of it is a stall nobody asked for. */
const PREVIEW_LINES = 2000;


/** A file's kind as an icon — the same one in the list, the grid and the preview. */
export function EntryIcon({ entry }: { entry: WorkspaceEntry }): ReactNode {
  if (entry.type === "dir") return <Folder />;
  const kind = classify(entry.name);
  if (kind.type === "image") return <FileImage />;
  if (kind.type === "code" || kind.type === "page") return <FileCode2 />;
  return <FileText />;
}

/** Settle on a value once it has stopped changing — so holding ↓ through a folder reads the file
 *  you stop on, not every one you pass. */
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const wait = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(wait);
  }, [value, ms]);
  return settled;
}

export function Preview({
  entry,
  path,
  hostPath,
  onOpen,
  onReveal,
  onCopy,
  onHide,
}: {
  entry: WorkspaceEntry | null;
  path: string;
  hostPath: string;
  onOpen: () => void;
  onReveal: () => void;
  onCopy: () => Promise<boolean>;
  onHide: () => void;
}) {
  const settledPath = useSettled(path, 90);
  const [copied, setCopied] = useState<"" | "yes" | "no">("");
  useEffect(() => setCopied(""), [path]);

  if (!entry) {
    return (
      <section aria-label="Preview" className="border-border flex min-h-0 min-w-0 items-center justify-center border-l">
        <p className="text-muted-foreground/80 text-[13px]">Nothing selected.</p>
      </section>
    );
  }

  const isDir = entry.type === "dir";
  const kind = isDir ? null : classify(entry.name);
  const text = !isDir && !skipTextRead(entry.name) && kind?.label !== "SVG";

  return (
    <section aria-label={`Preview of ${entry.name}`} className="border-border flex min-h-0 min-w-0 flex-col border-l">
      <div className="flex shrink-0 items-center gap-3 pt-4 pr-4 pb-3.5 pl-5">
        <span aria-hidden className="bg-muted/60 text-muted-foreground flex size-[34px] shrink-0 items-center justify-center rounded-[9px] [&_svg]:size-4">
          <EntryIcon entry={entry} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
          <span className="truncate text-[15px] font-semibold">{entry.name}</span>
          <span className="text-muted-foreground/80 truncate font-mono text-[11.5px]" title={hostPath}>
            {homeShort(hostPath)}
          </span>
        </div>
        <button
          type="button"
          onClick={onHide}
          aria-label="Hide preview"
          title="Hide preview"
          className="text-muted-foreground/80 hover:bg-muted/50 hover:text-foreground flex size-7 shrink-0 items-center justify-center rounded-[7px] transition-colors"
        >
          <PanelRightClose className="size-[15px]" />
        </button>
      </div>

      <div className="bg-background border-border mx-4 flex min-h-0 flex-1 flex-col overflow-hidden rounded-[10px] border">
        {settledPath !== path ? (
          <Quiet>…</Quiet>
        ) : isDir ? (
          <FolderPreview path={path} />
        ) : kind?.type === "image" ? (
          <ImagePreview path={path} name={entry.name} />
        ) : text ? (
          <TextPreview path={path} name={entry.name} label={kind?.label ?? "Text"} />
        ) : (
          <Quiet>
            No preview for {kind?.label ?? "this"} files here — Open shows it, or hands it to the app that does.
          </Quiet>
        )}
      </div>

      <dl className="m-0 flex shrink-0 flex-col px-5 pt-3 pb-2">
        <Meta label={isDir ? "Kind" : "Size"}>
          {isDir ? (
            "Folder"
          ) : (
            <>
              {formatSize(entry.size)} <span className="text-muted-foreground/80">· {entry.size.toLocaleString()} bytes</span>
            </>
          )}
        </Meta>
        <Meta label="Modified">{modifiedLabel(entry.modified ?? 0) || "—"}</Meta>
        {isDir ? <FolderCount path={path} /> : text ? <LineCount path={path} /> : <Meta label="Kind">{kind?.label ?? "File"}</Meta>}
      </dl>

      <div className="border-border/80 flex shrink-0 items-center gap-2 border-t px-5 pt-3 pb-4">
        <button type="button" onClick={onOpen} className={cn(BUTTON.primary, "gap-2")}>
          Open
          <span aria-hidden className="font-mono text-[11px] font-medium opacity-60">
            ↵
          </span>
        </button>
        <button type="button" onClick={onReveal} className={cn(BUTTON.secondary, "gap-[7px]")}>
          <FolderOpen className="text-muted-foreground size-3.5" />
          Show in Finder
        </button>
        <button
          type="button"
          onClick={() =>
            void onCopy().then((ok) => {
              setCopied(ok ? "yes" : "no");
              window.setTimeout(() => setCopied(""), 1600);
            })
          }
          className={cn(BUTTON.ghost, "gap-[7px] px-2.5")}
        >
          {copied === "yes" ? <Check className="text-roam size-3.5" /> : <Copy className="size-3.5" />}
          {copied === "yes" ? "Copied" : copied === "no" ? "Couldn't copy" : "Copy path"}
        </button>
      </div>
    </section>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex h-7 items-center text-xs">
      <dt className="text-muted-foreground/80 w-24 shrink-0">{label}</dt>
      <dd className="text-foreground/80 m-0 min-w-0 truncate tabular-nums">{children}</dd>
    </div>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-8 text-center">
      <p className="text-muted-foreground/80 text-[12.5px] leading-[1.5]">{children}</p>
    </div>
  );
}

function LineCount({ path }: { path: string }) {
  const { data } = useFileText(path);
  return <Meta label="Lines">{data === undefined ? "—" : lineTotal(data).toLocaleString()}</Meta>;
}

function FolderCount({ path }: { path: string }) {
  const { data } = useFolder(path, true);
  const n = data?.filter((e) => !e.name.startsWith(".")).length;
  return <Meta label="Holds">{n === undefined ? "—" : `${n} item${n === 1 ? "" : "s"}`}</Meta>;
}

function FolderPreview({ path }: { path: string }) {
  const { data, isPending } = useFolder(path, true);
  if (isPending) return <Quiet>Looking…</Quiet>;
  const shown = sortEntries((data ?? []).filter((e) => !e.name.startsWith(".")), "name");
  if (!shown.length) return <Quiet>This folder is empty.</Quiet>;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-2">
      {shown.slice(0, 200).map((e) => (
        <div key={e.name} className="text-foreground/80 flex h-7 items-center gap-2 px-3 text-xs">
          {e.type === "dir" ? (
            <Folder className="text-muted-foreground/80 size-3.5 shrink-0" />
          ) : (
            <FileText className="text-muted-foreground/80 size-3.5 shrink-0" />
          )}
          <span className="min-w-0 flex-1 truncate">{e.name}</span>
          <span className="text-muted-foreground/80 tabular-nums">{e.type === "dir" ? "" : formatSize(e.size)}</span>
        </div>
      ))}
    </div>
  );
}

function ImagePreview({ path, name }: { path: string; name: string }) {
  const { url, error, loading } = useMedia(path);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  if (error) return <Quiet>{error}</Quiet>;
  if (loading) return <Quiet>Loading it…</Quiet>;
  return (
    <>
      <div className="border-border/80 text-muted-foreground/80 flex h-8 shrink-0 items-center border-b px-3 text-[11px]">
        <span>{classify(name).label}</span>
        <span className="flex-1" />
        {size ? <span className="tabular-nums">{`${size.w.toLocaleString()} × ${size.h.toLocaleString()}`}</span> : null}
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center p-4">
        <img
          src={url}
          alt={name}
          onLoad={(event) => setSize({ w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight })}
          className="checkerboard max-h-full max-w-full rounded-md object-contain"
        />
      </div>
    </>
  );
}

/* The design's code palette: neutral, with one colour for strings and one for numbers.
 *
 * The app's highlight theme in `index.css` is the chat's, with a hue per token class; the board
 * draws code on this screen almost monochrome — keywords by weight, comments by style — so the
 * file reads as a document rather than a rainbow. Those rules are unlayered, and an unlayered
 * rule beats any utility however specific, hence the `!` on each of these. */
const NEUTRAL_CODE = [
  "text-foreground/80",
  "[&_.hljs-keyword]:text-foreground/90! [&_.hljs-keyword]:font-bold",
  "[&_.hljs-literal]:text-foreground/90! [&_.hljs-literal]:font-bold",
  "[&_.hljs-built_in]:text-foreground/90! [&_.hljs-type]:text-foreground/90! [&_.hljs-section]:text-foreground/90!",
  "[&_.hljs-string]:text-sky-700! dark:[&_.hljs-string]:text-[#8DBBF3]! [&_.hljs-regexp]:text-sky-700! dark:[&_.hljs-regexp]:text-[#8DBBF3]!",
  "[&_.hljs-number]:text-teal-700! dark:[&_.hljs-number]:text-[#7FE0D2]!",
  "[&_.hljs-comment]:text-muted-foreground/80! [&_.hljs-comment]:italic [&_.hljs-quote]:text-muted-foreground/80!",
  "[&_.hljs-punctuation]:text-muted-foreground/80! [&_.hljs-meta]:text-muted-foreground!",
  "[&_.hljs-title]:text-foreground/80! [&_.hljs-attr]:text-foreground/80! [&_.hljs-attribute]:text-foreground/80!",
  "[&_.hljs-params]:text-foreground/80! [&_.hljs-variable]:text-foreground/80! [&_.hljs-property]:text-foreground/80!",
  "[&_.hljs-tag]:text-foreground/80! [&_.hljs-name]:text-foreground/90! [&_.hljs-selector-class]:text-foreground/90!",
  "[&_.hljs-symbol]:text-teal-700! dark:[&_.hljs-symbol]:text-[#7FE0D2]! [&_.hljs-bullet]:text-muted-foreground!",
  "[&_.hljs-addition]:text-roam! [&_.hljs-deletion]:text-destructive!",
].join(" ");

const LINE = 19;

function TextPreview({ path, name, label }: { path: string; name: string; label: string }) {
  const { data, error, isPending } = useFileText(path);
  const scroller = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<[number, number]>([1, 1]);

  const total = data === undefined ? 0 : lineTotal(data);
  const shown = useMemo(() => {
    if (data === undefined) return { html: "", lines: 0 };
    const lines = data.replace(/\n$/, "").split("\n").slice(0, PREVIEW_LINES);
    const code = lines.join("\n");
    const kind = classify(name);
    const language =
      kind.type === "code" ? kind.lang : kind.type === "markdown" ? "markdown" : kind.type === "page" ? "xml" : undefined;
    return {
      html: grammarFor(language) ? highlight(code, language) : escapeHtml(code),
      lines: lines.length,
    };
  }, [data, name]);

  const measure = useCallback(() => {
    const box = scroller.current;
    if (!box) return;
    const first = Math.min(shown.lines, Math.floor(box.scrollTop / LINE) + 1);
    const last = Math.min(shown.lines, Math.floor((box.scrollTop + box.clientHeight - 16) / LINE));
    setView([Math.max(1, first), Math.max(first, last)]);
  }, [shown.lines]);
  useEffect(() => {
    measure();
    const box = scroller.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(measure);
    watch.observe(box);
    return () => watch.disconnect();
  }, [measure]);
  // A new file starts at its top.
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = 0;
  }, [path]);

  if (isPending) return <Quiet>Reading it…</Quiet>;
  if (error) {
    const said = error instanceof Error ? error.message : String(error);
    if (looksBinary(said)) return <Quiet>It isn't text, so there's nothing to show here — Open hands it to the app that reads it.</Quiet>;
    if (looksTooBig(said)) return <Quiet>Too big to preview here — Open hands it to an app that can.</Quiet>;
    return <Quiet>{said}</Quiet>;
  }
  if (!total) return <Quiet>This file is empty.</Quiet>;

  return (
    <>
      <div className="border-border/80 text-muted-foreground/80 flex h-8 shrink-0 items-center border-b px-3 text-[11px]">
        <span>{label}</span>
        <span className="flex-1" />
        <span className="tabular-nums">
          Lines {view[0]}–{view[1]} of {total.toLocaleString()}
        </span>
      </div>
      <div ref={scroller} onScroll={measure} className="min-h-0 flex-1 overflow-auto py-2 font-mono text-[11.5px] leading-[19px]">
        <div className="flex min-w-max">
          {/* Two columns rather than numbers woven into the markup — the highlighted HTML is one
              blob whose spans cross lines, as `Code` in files/code-block.tsx explains. Sticky, so
              the numbers stay put while a long line scrolls under them. */}
          <pre
            aria-hidden
            className="bg-background text-muted-foreground/50 sticky left-0 m-0 w-[44px] shrink-0 pr-3.5 text-right font-mono tabular-nums select-none"
          >
            {Array.from({ length: shown.lines }, (_, i) => i + 1).join("\n")}
          </pre>
          <pre className="m-0 pr-4 font-mono">
            <code className={NEUTRAL_CODE} dangerouslySetInnerHTML={{ __html: shown.html }} />
          </pre>
        </div>
        {total > PREVIEW_LINES ? (
          <p className="text-muted-foreground/80 sticky left-0 px-[58px] pt-2 font-sans text-[11.5px]">
            The first {PREVIEW_LINES.toLocaleString()} lines — Open shows all {total.toLocaleString()}.
          </p>
        ) : null}
      </div>
    </>
  );
}
