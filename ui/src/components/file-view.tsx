import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
  type RefObject,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/common";
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FileBox,
  FileCode2,
  FileText,
  FolderOpen,
  Loader2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/* ── Extension → language table ─────────────────────────────────────────── */

type Lang = { id: string; label: string };
const BY_EXT: Record<string, Lang> = {
  js: { id: "javascript", label: "JavaScript" },
  mjs: { id: "javascript", label: "JavaScript" },
  cjs: { id: "javascript", label: "JavaScript" },
  jsx: { id: "javascript", label: "JSX" },
  ts: { id: "typescript", label: "TypeScript" },
  tsx: { id: "typescript", label: "TSX" },
  py: { id: "python", label: "Python" },
  pyw: { id: "python", label: "Python" },
  rb: { id: "ruby", label: "Ruby" },
  go: { id: "go", label: "Go" },
  rs: { id: "rust", label: "Rust" },
  java: { id: "java", label: "Java" },
  kt: { id: "kotlin", label: "Kotlin" },
  swift: { id: "swift", label: "Swift" },
  c: { id: "c", label: "C" },
  h: { id: "c", label: "C" },
  cpp: { id: "cpp", label: "C++" },
  cc: { id: "cpp", label: "C++" },
  cxx: { id: "cpp", label: "C++" },
  hpp: { id: "cpp", label: "C++" },
  cs: { id: "csharp", label: "C#" },
  php: { id: "php", label: "PHP" },
  sh: { id: "bash", label: "Shell" },
  bash: { id: "bash", label: "Shell" },
  zsh: { id: "bash", label: "Shell" },
  json: { id: "json", label: "JSON" },
  jsonc: { id: "json", label: "JSON" },
  yml: { id: "yaml", label: "YAML" },
  yaml: { id: "yaml", label: "YAML" },
  toml: { id: "ini", label: "TOML" },
  ini: { id: "ini", label: "INI" },
  cfg: { id: "ini", label: "Config" },
  conf: { id: "ini", label: "Config" },
  env: { id: "bash", label: "Env" },
  xml: { id: "xml", label: "XML" },
  html: { id: "xml", label: "HTML" },
  htm: { id: "xml", label: "HTML" },
  svg: { id: "xml", label: "SVG" },
  css: { id: "css", label: "CSS" },
  scss: { id: "scss", label: "SCSS" },
  less: { id: "less", label: "Less" },
  sql: { id: "sql", label: "SQL" },
  graphql: { id: "graphql", label: "GraphQL" },
  gql: { id: "graphql", label: "GraphQL" },
  diff: { id: "diff", label: "Diff" },
  patch: { id: "diff", label: "Diff" },
  dockerfile: { id: "dockerfile", label: "Dockerfile" },
  makefile: { id: "makefile", label: "Makefile" },
  lua: { id: "lua", label: "Lua" },
  r: { id: "r", label: "R" },
  pl: { id: "perl", label: "Perl" },
  scala: { id: "scala", label: "Scala" },
};
const BY_NAME: Record<string, Lang> = {
  dockerfile: BY_EXT.dockerfile,
  makefile: BY_EXT.makefile,
  ".gitignore": { id: "bash", label: "gitignore" },
  ".dockerignore": { id: "bash", label: "dockerignore" },
  ".env": BY_EXT.env,
  "requirements.txt": { id: "bash", label: "requirements" },
};
const MARKDOWN = new Set(["md", "markdown", "mdx"]);

type Kind =
  | { type: "markdown"; label: string }
  | { type: "code"; lang: string; label: string }
  | { type: "plain"; label: string };

function classify(name: string): Kind {
  const base = (name.split("/").pop() ?? name).toLowerCase();
  const ext = base.includes(".") ? base.split(".").pop()! : "";
  if (MARKDOWN.has(ext)) return { type: "markdown", label: "Markdown" };
  const named = BY_NAME[base];
  if (named) return { type: "code", lang: named.id, label: named.label };
  const byExt = BY_EXT[ext];
  if (byExt) return { type: "code", lang: byExt.id, label: byExt.label };
  return { type: "plain", label: ext ? ext.toUpperCase() : "Text" };
}

/** The server says a file isn't text when it can't be decoded — which for a workbook
 *  or an image is the normal case, not a failure. */
function looksBinary(error: string): boolean {
  return /binary file/i.test(error);
}

/**
 * What to show instead of a text body for a file this window can't render.
 *
 * The whole point of the viewer is to see the work, and for a spreadsheet the honest
 * answer is "not here — in the app you already use for spreadsheets". So this is a
 * real invitation with the application named, rather than a red line of prose next to
 * an icon nobody would think to press.
 */
function NeedsAnApp({
  name,
  onOpenOnHost,
}: {
  name: string;
  onOpenOnHost?: (reveal: boolean) => Promise<unknown>;
}) {
  const [app, setApp] = useState<string | null>(null);
  const [busy, setBusy] = useState<"open" | "reveal" | null>(null);
  const [failed, setFailed] = useState("");
  // Set after a handoff that widened to the folder, so the note can say so — a page
  // arriving with its stylesheet is the difference between working and looking broken.
  const [handedFolder, setHandedFolder] = useState(false);

  // Asked by extension, so nothing is copied out of the sandbox just to label a button.
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/workspace/opens-with?path=${encodeURIComponent(name)}`)
      .then((response) => response.json())
      .then((body: { opensWith?: string | null }) => {
        if (!cancelled) setApp(body.opensWith ?? null);
      })
      .catch(() => {
        /* the generic label is a fine fallback */
      });
    return () => {
      cancelled = true;
    };
  }, [name]);

  const run = (reveal: boolean) => {
    if (!onOpenOnHost) return;
    setBusy(reveal ? "reveal" : "open");
    setFailed("");
    onOpenOnHost(reveal)
      .then((result) => {
        const handoff = result as { folderHandedOver?: boolean } | undefined;
        if (handoff?.folderHandedOver) setHandedFolder(true);
      })
      .catch((err: unknown) => setFailed(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-12 text-center">
      <span className="mb-2 flex size-11 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        <FileBox className="size-5" />
      </span>
      <p className="text-sm font-medium">This one needs its own application</p>
      <p className="text-muted-foreground max-w-sm text-xs leading-relaxed">
        {app
          ? `It's not text, so there's nothing to show here. ${app} is what this machine opens it with.`
          : "It's not text, so there's nothing to show here. It'll open in whatever you normally use for this kind of file."}
      </p>

      {onOpenOnHost ? (
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          <Button onClick={() => run(false)} disabled={busy !== null}>
            {busy === "open" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <ExternalLink className="size-4" />
            )}
            {app ? `Open in ${app}` : "Open in another app"}
          </Button>
          <Button variant="outline" onClick={() => run(true)} disabled={busy !== null}>
            {busy === "reveal" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <FolderOpen className="size-4" />
            )}
            Show in folder
          </Button>
        </div>
      ) : null}

      {failed ? <p className="text-destructive mt-3 max-w-sm text-xs">{failed}</p> : null}
      <p className="text-muted-foreground/60 mt-3 text-[11px]">
        {handedFolder
          ? "Its whole folder was copied to your Kith files, so anything it loads alongside it came too."
          : "A copy is placed in your Kith files folder first."}
      </p>
    </div>
  );
}

/** Getting the file out of the sandbox and into an app you already have.
 *
 * Two actions rather than one: "open" is what you want for a spreadsheet or a PDF the
 * viewer can't render well, and "show in folder" is what you want when you're about to
 * do something else with it — or when it's a script, which the server reveals rather
 * than runs.
 */
function HostActions({ onOpenOnHost }: { onOpenOnHost: (reveal: boolean) => Promise<unknown> }) {
  const [busy, setBusy] = useState<"open" | "reveal" | null>(null);
  const [failed, setFailed] = useState("");

  const run = (reveal: boolean) => {
    setBusy(reveal ? "reveal" : "open");
    setFailed("");
    onOpenOnHost(reveal)
      .catch((err: unknown) => setFailed(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  return (
    <>
      {failed ? (
        <span className="text-destructive max-w-56 truncate text-[11px]" title={failed}>
          {failed}
        </span>
      ) : null}
      <IconAction
        label="Open in another app"
        onClick={() => run(false)}
        icon={
          busy === "open" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <ExternalLink className="size-4" />
          )
        }
      />
      <IconAction
        label="Show in folder"
        onClick={() => run(true)}
        icon={
          busy === "reveal" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <FolderOpen className="size-4" />
          )
        }
      />
    </>
  );
}

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
  /** Copy the file onto this machine and open it — or reveal it in the file manager.
   *  Absent for anything that isn't one of his sandbox files. */
  onOpenOnHost?: (reveal: boolean) => Promise<unknown>;
}) {
  const kind = classify(name);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<"rendered" | "source">("rendered");
  useEffect(() => setView("rendered"), [name]);

  const base = name.split("/").pop() || name;
  const heading = title || base;
  const stats = content
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
          {kind.type === "code" ? (
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
          {kind.type === "markdown" && content != null ? (
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
        {error && looksBinary(error) ? (
          // Not an error — an xlsx simply isn't text, and saying so in red while
          // hiding the useful action in a 16px icon was the wrong way round.
          <NeedsAnApp name={base} onOpenOnHost={onOpenOnHost} />
        ) : error ? (
          <p className="p-5 text-sm text-destructive">{error}</p>
        ) : content == null ? (
          <p className="flex items-center gap-2 p-5 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Reading it…
          </p>
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

function IconAction({
  label,
  onClick,
  icon,
  copyIcon,
}: {
  label: string;
  onClick: () => void;
  icon?: ReactNode;
  copyIcon?: boolean;
}) {
  const [hit, setHit] = useState(false);
  return (
    <button
      onClick={() => {
        onClick();
        if (copyIcon) {
          setHit(true);
          setTimeout(() => setHit(false), 1600);
        }
      }}
      title={label}
      aria-label={label}
      className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/25 focus-visible:outline-none"
    >
      {copyIcon ? hit ? <Check className="size-4 text-kith" /> : <Copy className="size-4" /> : icon}
    </button>
  );
}

function copyText(text: string) {
  if (typeof navigator === "undefined" || !navigator.clipboard) return;
  navigator.clipboard.writeText(text).catch(() => {});
}

/* ── Markdown renderer (react-markdown + gfm + highlighted code) ─────────── */

export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>
        {children}
      </ReactMarkdown>
    </div>
  );
});

/** Markdown on one line.
 *
 * The block renderer wraps everything in `<p>` with margins, which in a timeline row
 * or beside a timestamp pushes the rest of the line away and breaks the layout. This
 * keeps emphasis, code and links — the things he actually uses in a short line — and
 * renders paragraphs as spans.
 *
 * Headings and lists are deliberately flattened rather than honoured: a heading inside
 * a one-line summary is a mistake in the text, not something to lay out.
 */
export const MarkdownInline = memo(function MarkdownInline({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_INLINE}>
      {children}
    </ReactMarkdown>
  );
});

const MD_INLINE: Components = {
  p: (p) => <span {...p} />,
  h1: (p) => <span className="font-semibold" {...p} />,
  h2: (p) => <span className="font-semibold" {...p} />,
  h3: (p) => <span className="font-semibold" {...p} />,
  ul: (p) => <span {...p} />,
  ol: (p) => <span {...p} />,
  li: (p) => <span className="before:content-['·_']" {...p} />,
  strong: (p) => <strong className="font-semibold" {...p} />,
  em: (p) => <em className="italic" {...p} />,
  code: (p) => <code className="bg-muted rounded px-1 py-0.5 font-mono text-[0.9em]" {...p} />,
  pre: (p) => <span {...p} />,
  a: ({ href, ...rest }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-kith underline decoration-dotted"
      {...rest}
    />
  ),
  blockquote: (p) => <span className="text-muted-foreground italic" {...p} />,
  hr: () => null,
  br: () => <> </>,
};

const MD: Components = {
  h1: (p) => <h1 className="mt-7 mb-3 text-xl font-semibold tracking-tight first:mt-0" {...p} />,
  h2: (p) => (
    <h2
      className="mt-7 mb-2.5 border-b border-border/50 pb-1.5 text-lg font-semibold tracking-tight first:mt-0"
      {...p}
    />
  ),
  h3: (p) => <h3 className="mt-5 mb-2 text-base font-semibold first:mt-0" {...p} />,
  h4: (p) => <h4 className="mt-4 mb-1.5 text-sm font-semibold first:mt-0" {...p} />,
  h5: (p) => <h5 className="mt-3 mb-1 text-sm font-semibold first:mt-0" {...p} />,
  h6: (p) => (
    <h6
      className="mt-3 mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground first:mt-0"
      {...p}
    />
  ),
  p: (p) => <p className="my-3 leading-relaxed first:mt-0 last:mb-0" {...p} />,
  a: ({ href, ...p }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-kith underline underline-offset-2 hover:text-kith/80"
      {...p}
    />
  ),
  ul: (p) => <ul className="my-3 ms-5 list-disc space-y-1 marker:text-muted-foreground" {...p} />,
  ol: (p) => (
    <ol className="my-3 ms-5 list-decimal space-y-1 marker:text-muted-foreground" {...p} />
  ),
  li: (p) => <li className="leading-relaxed" {...p} />,
  blockquote: (p) => (
    <blockquote
      className="my-3 border-s-2 border-kith/40 ps-4 text-muted-foreground italic"
      {...p}
    />
  ),
  hr: () => <hr className="my-6 border-border" />,
  strong: (p) => <strong className="font-semibold" {...p} />,
  em: (p) => <em className="italic" {...p} />,
  img: ({ alt, ...p }) => (
    <img alt={alt} className="my-3 max-w-full rounded-lg border border-border/60" {...p} />
  ),
  table: (p) => (
    <div className="my-4 overflow-x-auto rounded-xl border border-border/60">
      <table className="w-full border-collapse text-sm" {...p} />
    </div>
  ),
  thead: (p) => <thead className="bg-muted/50" {...p} />,
  th: (p) => <th className="border-b border-border/60 px-3 py-2 text-start font-medium" {...p} />,
  td: (p) => <td className="border-b border-border/40 px-3 py-2 align-top" {...p} />,
  pre: ({ children }) => <>{children}</>, // CodeBlock renders its own <pre>
  code: ({
    className,
    children,
    ...props
  }: ComponentPropsWithoutRef<"code"> & { className?: string }) => {
    const text = String(children ?? "").replace(/\n$/, "");
    const match = /language-([\w-]+)/.exec(className ?? "");
    const isBlock = !!match || text.includes("\n");
    if (!isBlock) {
      return (
        <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[0.85em]" {...props}>
          {children}
        </code>
      );
    }
    const lang = match?.[1];
    return (
      <div className="my-4 overflow-hidden rounded-xl border border-border/60 bg-muted/15">
        <CodeBlock code={text} language={lang} label={lang} />
      </div>
    );
  },
};

/* ── Syntax-highlighted code ────────────────────────────────────────────── */

/** Code with a small toolbar (language + copy). Used for his self-made tools
 * and for fenced blocks inside rendered Markdown. */
export function CodeBlock({
  code,
  language,
  label,
}: {
  code: string;
  language?: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    copyText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div>
      <div className="flex items-center justify-between border-b border-border/50 bg-muted/30 px-3 py-1.5">
        <span className="font-mono text-[11px] lowercase text-muted-foreground">
          {label || language || "text"}
        </span>
        <button
          onClick={copy}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-label="Copy code"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <Code code={code} language={language} />
    </div>
  );
}

function Code({
  code,
  language,
  numbered = false,
}: {
  code: string;
  language?: string;
  /** A gutter of line numbers. For a whole file, not for a fenced snippet in chat. */
  numbered?: boolean;
}) {
  const html = useMemo(() => highlight(code, language), [code, language]);
  const numbers = useMemo(
    () => (numbered ? code.split("\n").map((_, index) => index + 1) : []),
    [code, numbered],
  );

  if (!numbered) {
    return (
      <pre className="overflow-x-auto p-4 text-[13px] leading-relaxed">
        <code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    );
  }

  // Two columns rather than numbers woven into the markup. The highlighted HTML is one
  // blob whose spans cross line boundaries, so splitting it per line to prefix a number
  // means repairing tags — and getting that subtly wrong colours the rest of the file
  // wrong. A parallel column with the same line-height needs no surgery at all, and it
  // stays put while long lines scroll under it. It holds because the code pre does not
  // wrap: one source line is always exactly one row.
  return (
    <div className="flex min-w-0 items-start font-mono text-[13px] leading-relaxed">
      <pre
        aria-hidden
        className="text-muted-foreground/40 shrink-0 border-r border-border/40 py-4 pr-3 pl-5 text-right tabular-nums select-none"
      >
        {numbers.join("\n")}
      </pre>
      <pre className="min-w-0 flex-1 overflow-x-auto py-4 pr-5 pl-4">
        <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}

function highlight(code: string, language?: string): string {
  try {
    if (code.length > 300_000) return escapeHtml(code);
    if (language && hljs.getLanguage(language)) {
      return hljs.highlight(code, { language, ignoreIllegals: true }).value;
    }
    return hljs.highlightAuto(code).value;
  } catch {
    return escapeHtml(code);
  }
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}
