import { useEffect, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { Download, FileCode2, FileImage, FileText, FileType2, X } from "lucide-react";
import { MermaidDiagram } from "@/components/assistant-ui/mermaid-diagram";
import { copyText } from "@/lib/files";
import { cn } from "@/lib/utils";
import { Code } from "./code-block";
import { IconAction } from "./icon-action";
import { classify, looksBinary } from "./kinds";
import type { Kind } from "./kinds";
import { HtmlCanvas } from "@/components/assistant-ui/html-canvas";
import { Markdown } from "./markdown";
import { HostActions, ImageBody, Loading, NeedsAnApp, PdfBody } from "./media";

/* ── The preview dialog ─────────────────────────────────────────────────── */

/** Opens a file the way you'd expect to read it — Markdown rendered (with a
 * Rendered/Source switch), code syntax-highlighted, everything else as plain
 * text — in a roomy modal. Shared by the workspace browser and task
 * deliverables; pass `content: null` while it's still being fetched. */
export function FileViewer({
  open,
  onOpenChange,
  name,
  title,
  content,
  error,
  badge,
  onDownload,
  onOpenOnHost,
  projectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** File name or path — decides how the body is rendered. */
  name: string;
  /** Heading. Defaults to the file's base name. */
  title?: string;
  /** `null` while loading. */
  content: string | null;
  error?: string;
  /** Extra chip beside the kind (e.g. "deliverable"). */
  badge?: ReactNode;
  onDownload?: () => void;
  /** The project `name` belongs to, if any — a relative path only lands in the right
   *  folder when the request says which project it is relative *to*. Omit for anything
   *  that already lives in the global workspace root (a chat attachment, the file browser). */
  projectId?: number | null;
  /** Copy the file onto this machine and open it — or reveal it in the file manager.
   *  Absent for anything that isn't one of his sandbox files. */
  onOpenOnHost?: (reveal: boolean) => Promise<unknown>;
}) {
  const kind = classify(name);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<"rendered" | "source">("rendered");
  useEffect(() => setView("rendered"), [name]);

  const media = kind.type === "image" || kind.type === "pdf";
  // An SVG is a picture and a document at once, so it gets the toggle: the markup is
  // frequently the thing being checked. A PNG has no source to show.
  const hasSource =
    kind.type === "markdown" || kind.type === "diagram" || kind.label === "SVG";

  const base = name.split("/").pop() || name;
  const heading = title || base;
  const stats =
    content && !media
      ? `${content.split("\n").length.toLocaleString()} lines · ${content.length.toLocaleString()} chars`
      : "";

  // Escape closes it. A Dialog gave this for free; a page has to say so, and a full-window
  // view you cannot dismiss from the keyboard is worse than the dialog it replaced.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // One Escape, one layer. The control panel also closes on Escape, from a listener on
      // `window` — and `document` bubbles to `window`, so without this the same keypress
      // shut the viewer *and* the panel behind it, which is how you lose your place in a
      // file list. The panel's own guard for "a dialog is open" cannot save it: closing
      // this flushes React synchronously, so the element it looks for is already gone by
      // the time it looks.
      event.stopPropagation();
      onOpenChange(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={heading}
      className="fixed inset-0 z-50 flex flex-col bg-background"
    >
      {/* header — identity on the left, controls on the right. It is the topmost bar in the
          window while this is open, so it takes over the title bar's jobs: draggable, and
          holding a gap where the traffic lights are drawn. */}
      <div className="window-drag-region window-controls-gap flex shrink-0 items-start gap-3 border-b border-border/60 bg-muted/25 px-4 py-3">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-background/70 text-sky-500 ring-1 ring-border/60">
          {kind.type === "image" ? (
            <FileImage className="size-4" />
          ) : kind.type === "pdf" ? (
            <FileType2 className="size-4" />
          ) : kind.type === "code" ? (
            <FileCode2 className="size-4" />
          ) : (
            <FileText className="size-4" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[15px] leading-tight font-semibold">{heading}</h2>
          <div className="text-muted-foreground mt-1 flex min-w-0 items-center gap-1.5 text-[11px]">
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-medium tracking-wide text-muted-foreground">
              {kind.label}
            </span>
            {badge}
            {heading !== base || name.includes("/") ? (
              <span className="min-w-0 truncate font-mono">{name}</span>
            ) : null}
            {stats ? <span className="shrink-0 tabular-nums">· {stats}</span> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {hasSource && content != null ? (
            <div className="inline-flex rounded-lg border border-border/60 bg-background/70 p-0.5 text-xs">
              {(["rendered", "source"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={cn(
                    "rounded-md px-2.5 py-1 capitalize transition-colors",
                    view === v
                      ? "bg-accent font-medium text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {v}
                </button>
              ))}
            </div>
          ) : null}
          {content != null ? (
            <IconAction label="Copy" onClick={() => copyText(content)} copyIcon />
          ) : null}
          {onOpenOnHost ? <HostActions onOpenOnHost={onOpenOnHost} /> : null}
          {onDownload ? (
            <IconAction
              label="Download"
              onClick={onDownload}
              icon={<Download className="size-4" />}
            />
          ) : null}
          <IconAction
            label="Close"
            onClick={() => onOpenChange(false)}
            icon={<X className="size-4" />}
          />
        </div>
      </div>

      {/* body */}
      <div ref={bodyRef} className="min-h-0 flex-1 overflow-auto bg-background">
        {media && view === "rendered" ? (
          // Shown as itself, from its own bytes — so `content` and `error` are not
          // consulted at all. The three callers skip the text fetch for these
          // (`showsAsMedia`), and a picture that arrived as a failed UTF-8 decode is
          // exactly how a screenshot used to be reported as needing another application.
          kind.type === "pdf" ? (
            <PdfBody path={name} projectId={projectId} />
          ) : (
            <ImageBody path={name} alt={base} projectId={projectId} />
          )
        ) : error && looksBinary(error) ? (
          // Not an error — an xlsx simply isn't text, and saying so in red while
          // hiding the useful action in a 16px icon was the wrong way round.
          <NeedsAnApp name={base} onOpenOnHost={onOpenOnHost} />
        ) : error ? (
          <p className="p-5 text-sm text-destructive">{error}</p>
        ) : content == null ? (
          <Loading label="Reading it…" />
        ) : (
          <FileBody kind={kind} content={content} view={view} scroller={bodyRef} />
        )}
      </div>
    </div>
  );
}

function FileBody({
  kind,
  content,
  view,
  scroller,
}: {
  kind: Kind;
  content: string;
  view: "rendered" | "source";
  scroller: RefObject<HTMLDivElement | null>;
}) {
  if (!content.trim()) return <p className="p-5 text-sm text-muted-foreground">(empty file)</p>;
  // An SVG viewed as source. Highlighted as XML, which is what it is — the fallback `<pre>`
  // would show a wall of undifferentiated angle brackets.
  if (kind.type === "image") return <Code code={content} language="xml" numbered />;
  if (kind.type === "markdown" && view === "rendered") {
    const headings = outlineOf(content);
    return (
      <div className="mx-auto flex max-w-[68rem] items-start gap-8 px-6 py-6">
        {/* The prose keeps its measure and the width goes to navigation instead. A wide
            window was leaving a stark empty margin beside a 48rem column — the answer to
            "use the space" on a document is not longer lines, which are harder to read,
            it is a way to move around the document. Hidden below xl, where there is no
            spare width to spend, and below three headings, where a list of two is not an
            outline. */}
        {headings.length >= 3 ? <Outline headings={headings} scroller={scroller} /> : null}
        <div className="min-w-0 max-w-[48rem] flex-1">
          <Markdown>{content}</Markdown>
        </div>
      </div>
    );
  }
  if (kind.type === "markdown") return <Code code={content} language="markdown" numbered />;
  // An .html file is a thing to look at, so it runs. This is the surface that matters most:
  // asked for something animated he writes a page to his folder and shells out to `open`, which
  // hands it to a browser outside this app. Rendering it here is the same drawing without the
  // detour — and under the same seal a canvas in a reply gets.
  if (kind.type === "page" && view === "rendered") {
    return (
      <div className="mx-auto max-w-[68rem] px-6 py-6">
        <HtmlCanvas code={content} fallback={<Code code={content} language="xml" numbered />} />
      </div>
    );
  }
  if (kind.type === "page") return <Code code={content} language="xml" numbered />;
  // A .mmd is a picture written down, so the picture is the default and the source is the
  // toggle — the same call the SVG beside it already makes.
  if (kind.type === "diagram" && view === "rendered") {
    return (
      <div className="mx-auto max-w-[68rem] px-6 py-6">
        <MermaidDiagram
          code={content}
          fallback={<Code code={content} language="markdown" numbered />}
        />
      </div>
    );
  }
  if (kind.type === "diagram") return <Code code={content} language="markdown" numbered />;
  if (kind.type === "code") return <Code code={content} language={kind.lang} numbered />;
  return (
    <pre className="mx-auto max-w-[80rem] overflow-x-auto whitespace-pre-wrap break-words p-5 font-mono text-[13px] leading-relaxed">
      {content}
    </pre>
  );
}

/** One heading in a markdown file: its depth and its text, in document order. */
type Heading = { depth: number; text: string };

/**
 * The headings of a markdown file, read from the source.
 *
 * Fenced code is skipped, which is the whole reason this is a loop and not one regex: a
 * shell block with a `# comment` in it would otherwise become a chapter of the document.
 * Inline markdown is stripped so "The **big** idea" reads as "The big idea".
 */
function outlineOf(markdown: string): Heading[] {
  const found: Heading[] = [];
  let fenced = false;
  for (const line of markdown.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    const match = /^(#{1,4})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!match) continue;
    const text = match[2]
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/[*_]([^*_]+)[*_]/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .trim();
    if (text) found.push({ depth: match[1].length, text });
  }
  return found;
}

/**
 * Where you are in the document, and how to get somewhere else in it.
 *
 * Scrolling is done by *position*, not by anchor ids. Ids would have to be generated on
 * both sides — slugged from the source here and from the rendered children there — and the
 * two disagree the moment a heading contains inline markdown or two headings share a name.
 * Counting heading elements in the rendered body cannot drift: each heading line renders
 * exactly one heading element, in the same order the source lists them.
 *
 * The active item is tracked on scroll, because an outline that does not know where you are
 * is a table of contents, and the point of one of these is telling you where you got to.
 */
function Outline({
  headings,
  scroller,
}: {
  headings: Heading[];
  scroller: RefObject<HTMLDivElement | null>;
}) {
  const [active, setActive] = useState(0);

  useEffect(() => {
    const box = scroller.current;
    if (!box) return;
    const onScroll = () => {
      const marks = box.querySelectorAll("h1, h2, h3, h4");
      const top = box.getBoundingClientRect().top;
      let current = 0;
      marks.forEach((mark, index) => {
        // A heading counts as reached once its top passes a little below the viewport's
        // top edge — not at exactly 0, or the heading you just scrolled to flickers
        // between itself and the one above it.
        if (mark.getBoundingClientRect().top - top < 80) current = index;
      });
      setActive(current);
    };
    onScroll();
    box.addEventListener("scroll", onScroll, { passive: true });
    return () => box.removeEventListener("scroll", onScroll);
  }, [scroller, headings]);

  const go = (index: number) => {
    const box = scroller.current;
    const mark = box?.querySelectorAll<HTMLElement>("h1, h2, h3, h4")[index];
    if (!box || !mark) return;
    // A plain scrollTop assignment, and deliberately no smooth scrolling of either kind.
    //
    // `scrollIntoView({behavior: "smooth"})` did nothing at all when I tried it — not
    // instant, nothing. So I moved the animation to CSS instead, and that was worse: with
    // `scroll-behavior: smooth` on the container, this assignment *also* routes through the
    // same animation path and is swallowed too. Both were verified failing in a real
    // browser, and either can be turned off under the reader's own motion settings. A
    // jump-to-heading that silently does not jump is a broken feature; one that does not
    // animate is a working feature that is slightly less pretty.
    //
    // offsetTop is measured from the positioned dialog root, so the body's own offset — the
    // header's height — comes back off. The extra few pixels stop the heading sitting flush
    // against the top edge.
    box.scrollTop = mark.offsetTop - box.offsetTop - 12;
  };

  return (
    <nav aria-label="Outline" className="sticky top-0 hidden w-52 shrink-0 self-start xl:block">
      <p className="text-muted-foreground/50 mb-2 text-[10px] font-medium tracking-wide uppercase">
        In this file
      </p>
      <ul className="border-border/50 space-y-0.5 border-s">
        {headings.map((heading, index) => (
          <li key={`${index}-${heading.text}`}>
            <button
              type="button"
              onClick={() => go(index)}
              className={cn(
                "-ms-px block w-full border-s-2 py-0.5 pe-1 text-left text-[11.5px] leading-snug transition-colors",
                index === active
                  ? "border-kith text-foreground"
                  : "text-muted-foreground/70 hover:text-foreground border-transparent",
              )}
              // Depth as indentation, so the shape of the document is visible at a glance
              // rather than being a flat list of every heading in it.
              style={{ paddingInlineStart: `${(heading.depth - 1) * 0.6 + 0.6}rem` }}
            >
              {heading.text}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
