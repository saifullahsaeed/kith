import {
  memo,
  useEffect,
  useMemo,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/common";
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FileCode2,
  FileText,
  FolderOpen,
  Loader2,
  X,
} from "lucide-react";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
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
export function FilePreviewDialog({
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
  const [view, setView] = useState<"rendered" | "source">("rendered");
  useEffect(() => setView("rendered"), [name]);

  const base = name.split("/").pop() || name;
  const heading = title || base;
  const stats = content
    ? `${content.split("\n").length.toLocaleString()} lines · ${content.length.toLocaleString()} chars`
    : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="flex max-h-[88dvh] min-h-[16rem] w-[min(96vw,64rem)] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none"
      >
        {/* header — identity on the left, controls on the right */}
        <div className="flex items-start gap-3 border-b border-border/60 bg-muted/25 px-4 py-3">
          <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-background/70 text-sky-500 ring-1 ring-border/60">
            {kind.type === "code" ? (
              <FileCode2 className="size-4" />
            ) : (
              <FileText className="size-4" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <DialogTitle className="truncate text-[15px] leading-tight font-semibold">
              {heading}
            </DialogTitle>
            <DialogDescription className="mt-1 flex min-w-0 items-center gap-1.5 text-[11px]">
              <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-medium tracking-wide text-muted-foreground">
                {kind.label}
              </span>
              {badge}
              {heading !== base || name.includes("/") ? (
                <span className="min-w-0 truncate font-mono">{name}</span>
              ) : null}
              {stats ? <span className="shrink-0 tabular-nums">· {stats}</span> : null}
            </DialogDescription>
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
        <div className="min-h-0 flex-1 overflow-auto bg-background">
          {error ? (
            <p className="p-5 text-sm text-destructive">{error}</p>
          ) : content == null ? (
            <p className="flex items-center gap-2 p-5 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Reading it…
            </p>
          ) : (
            <FileBody kind={kind} content={content} view={view} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function FileBody({
  kind,
  content,
  view,
}: {
  kind: Kind;
  content: string;
  view: "rendered" | "source";
}) {
  if (!content.trim()) return <p className="p-5 text-sm text-muted-foreground">(empty file)</p>;
  if (kind.type === "markdown" && view === "rendered") {
    return (
      <div className="mx-auto max-w-[48rem] px-6 py-6">
        <Markdown>{content}</Markdown>
      </div>
    );
  }
  if (kind.type === "markdown") return <Code code={content} language="markdown" />;
  if (kind.type === "code") return <Code code={content} language={kind.lang} />;
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words p-5 font-mono text-[13px] leading-relaxed">
      {content}
    </pre>
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

function Code({ code, language }: { code: string; language?: string }) {
  const html = useMemo(() => highlight(code, language), [code, language]);
  return (
    <pre className="overflow-x-auto p-4 text-[13px] leading-relaxed">
      <code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: html }} />
    </pre>
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
