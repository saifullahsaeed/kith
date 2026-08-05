"use client";

import { memo, useState, type FC, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { BookOpenText, Check, Copy } from "lucide-react";

import { CodeBlock, copyText } from "@/components/file-view";
import { useFileViewer } from "@/lib/files";
import { cn } from "@/lib/utils";

/**
 * What a tool call looks like in the chat.
 *
 * Everything used to go through one `JSON.stringify(result, null, 2)` in a `<pre>`, so a
 * diff arrived as a string containing `\n`, a shell run as `{"exitCode":0,"output":"…"}`,
 * and a task list as forty lines of braces. The information was all there and none of it was
 * readable, which is a particular kind of bad: it looks like transparency and functions as
 * noise, so you stop opening it, and then you stop noticing when a call did nothing.
 *
 * Two layers, deliberately in this order.
 *
 * **A shape-driven default.** An object becomes labelled rows, an array becomes a count and
 * its items, a `diff` field becomes a diff, a long string becomes text. That lifts all
 * fifty-five tools at once and it lifts the fifty-sixth for free. Per-tool work alone would
 * have left most of them raw, which is how you end up with six pretty tools and a pile.
 *
 * **Then overrides**, only where the generic version wastes the space — a diff, a terminal,
 * a `file:line` list worth clicking.
 */

/* ── one-line summaries ─────────────────────────────────────────────────── */

type Args = Record<string, unknown>;

/**
 * Peel the transport envelope off a result.
 *
 * A stored tool result is `{ok: true, result: {...}}` — the live stream and the transcript
 * do not agree on this, and reading the outer object is why a shell run with plenty of output
 * rendered as "no output": `output` was one level down the whole time. Unwrapping in one place
 * means every renderer below sees what the tool actually returned, and `ok: false` stops being
 * invisible.
 */
export function unwrap(result: unknown): { value: unknown; failed: boolean } {
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const outer = result as Record<string, unknown>;
    if ("ok" in outer && "result" in outer) {
      return { value: outer.result, failed: outer.ok === false };
    }
    if ("ok" in outer && "error" in outer) {
      return { value: outer.error, failed: outer.ok === false };
    }
  }
  return { value: result, failed: false };
}

/** The line you read without opening anything. */
export function summarise(name: string, args: Args, wrapped: unknown): string {
  const { value: result, failed } = unwrap(wrapped);
  if (failed)
    return `failed · ${String((result as { message?: string })?.message ?? result ?? "").slice(0, 70)}`;
  const r = (result ?? {}) as Record<string, unknown>;
  const s = (v: unknown) => (v == null ? "" : String(v));
  const short = (v: unknown, n = 44) => {
    const t = s(v).replace(/\s+/g, " ").trim();
    return t.length > n ? `${t.slice(0, n - 1)}…` : t;
  };
  const lines = (v: unknown) => s(v).split("\n").length;

  switch (name) {
    case "shell": {
      const code = Number(r.exitCode ?? 0);
      const out = s(r.output).trim();
      const tail = out ? ` · ${lines(out)} line${lines(out) === 1 ? "" : "s"}` : " · no output";
      return `${short(args.command, 52)} · ${code === 0 ? "ok" : `exit ${code}`}${tail}`;
    }
    case "edit_file": {
      const diff = s(result);
      const plus = (diff.match(/^\+(?!\+)/gm) || []).length;
      const minus = (diff.match(/^-(?!-)/gm) || []).length;
      return `${short(args.path)} · +${plus} −${minus}`;
    }
    case "write_file":
      return `${short(args.path)} · ${s(args.content).length.toLocaleString()} chars`;
    case "read_file": {
      if (r.note) return `${short(args.path)} · ${short(r.note, 40)}`;
      if (r.image || r.data) return `${short(args.path)} · looked at it`;
      return `${short(args.path)} · ${lines(result).toLocaleString()} lines`;
    }
    case "delete_file":
      return `${short(r.trashed ?? args.path)} · to the Trash`;
    case "grep": {
      const hits = s(result)
        .split("\n")
        .filter((l) => l.includes(":")).length;
      return `${short(args.pattern, 30)} · ${hits || "no"} match${hits === 1 ? "" : "es"}`;
    }
    case "glob": {
      const found = Array.isArray(r.files) ? r.files.length : lines(result);
      return `${short(args.pattern, 30)} · ${found} file${found === 1 ? "" : "s"}`;
    }
    case "check_code": {
      const text = s(r.output ?? result).trim();
      return text ? `${lines(text)} line${lines(text) === 1 ? "" : "s"} to fix` : "clean";
    }
    case "changes": {
      const diff = s(r.diff ?? result);
      const files = (diff.match(/^diff --git/gm) || []).length;
      return files ? `${files} file${files === 1 ? "" : "s"} changed` : "nothing uncommitted";
    }
    case "commit":
      return r.committed === false ? "nothing to record" : short(r.changed ?? args.message, 52);
    case "add_task":
      return `#${s(r.id)} ${short(args.goal ?? r.goal)}`;
    case "update_task":
      return `#${s(args.id)}${args.status ? ` → ${s(args.status)}` : ""}`;
    case "add_milestone":
      return `${short(args.title)}${r.note ? " · already there" : ""}`;
    case "order_milestones":
      return `${(Array.isArray(args.ids) ? args.ids : []).join(" → ")}`;
    case "create_project":
      return `#${s(r.id)} ${short(args.name)}`;
    case "add_deliverable":
      return short(args.title ?? args.content);
    case "ask_on_task":
      return `#${s(args.id)} · ${short(args.question, 48)}`;
    case "read_skill":
      return s(args.name);
    case "journal":
      return short(args.entry, 56);
    default:
      break;
  }
  // The generic line: the most identifying argument, then what came back.
  //
  // Never key names. The first version ended with `Object.keys(r).slice(0,3).join(", ")`,
  // so four consecutive list_tasks calls each read "· items, total, showing" — the shape of
  // the answer instead of the answer, repeated, which is worse than saying nothing because
  // it looks like content. If there is no value worth printing, print none.
  const subject = args.path ?? args.name ?? args.title ?? args.goal ?? args.query ?? args.id;
  const answer = describe(result);
  return [short(subject), answer].filter(Boolean).join(" · ");
}

/** A paged listing: `{items, total, showing}`. The commonest result in the system. */
function paged(value: unknown): { items: unknown[]; total: number } | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  if (!Array.isArray(v.items) || typeof v.total !== "number") return null;
  return { items: v.items, total: v.total };
}

/** What came back, in a few words, or "" when there is nothing worth saying. */
function describe(result: unknown): string {
  const page = paged(result);
  if (page) return page.total === 0 ? "none" : `${page.total.toLocaleString()}`;
  if (Array.isArray(result)) {
    return result.length ? `${result.length.toLocaleString()}` : "none";
  }
  if (typeof result === "string") {
    const n = result.split("\n").length;
    return result.trim() ? `${n.toLocaleString()} line${n === 1 ? "" : "s"}` : "nothing";
  }
  if (result && typeof result === "object") {
    const r = result as Record<string, unknown>;
    // A value, not a key. `ok: true` on its own says nothing anyone needs.
    for (const key of ["note", "status", "message", "id", "committed", "trashed"]) {
      if (r[key] !== undefined && r[key] !== null && r[key] !== "") {
        return String(r[key]).replace(/\s+/g, " ").slice(0, 48);
      }
    }
  }
  return "";
}

/* ── the formatted body ─────────────────────────────────────────────────── */

// Not uppercase. LIMIT / OFFSET / CONTAINS / ITEMS / TOTAL / SHOWING read as a database
// dump rather than as an answer, and shouting six field names at someone is not clarity.
const Label: FC<{ children: ReactNode }> = ({ children }) => (
  <span className="shrink-0 text-[11px] font-medium text-muted-foreground/60">{children}</span>
);

/** The command line's own copy button — small enough to sit inline in the terminal header
 *  without competing with the exit-code badge for attention. Same honest-about-success
 *  `copyText` as everywhere else that copies. */
const CopyCommand: FC<{ command: string }> = ({ command }) => {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() =>
        void copyText(command).then((ok) => {
          if (!ok) return;
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        })
      }
      title="Copy command"
      aria-label="Copy command"
      className="shrink-0 rounded p-0.5 text-muted-foreground/70 hover:bg-accent hover:text-foreground"
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
    </button>
  );
};

const Shell: FC<{ command: string; output: string; code: number }> = ({
  command,
  output,
  code,
}) => (
  <div className="overflow-hidden rounded-lg ring-1 ring-border/60">
    <div className="flex items-center gap-2 border-b border-border/50 bg-muted/40 px-2.5 py-1.5">
      <span className="font-mono text-[11px] text-muted-foreground">$</span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{command}</span>
      <CopyCommand command={command} />
      <span
        className={cn(
          "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums",
          code === 0 ? "bg-emerald-500/15 text-emerald-500" : "bg-destructive/15 text-destructive",
        )}
      >
        {code === 0 ? "exit 0" : `exit ${code}`}
      </span>
    </div>
    {output.trim() ? (
      <pre className="max-h-80 overflow-auto bg-background/40 p-2.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
        {output}
      </pre>
    ) : (
      <p className="px-2.5 py-2 text-xs text-muted-foreground">no output</p>
    )}
  </div>
);

/** A unified diff, coloured. The one shape where plain text is genuinely unreadable. */
const Diff: FC<{ text: string }> = ({ text }) => (
  <div className="max-h-96 overflow-auto rounded-lg bg-muted/30 ring-1 ring-border/60">
    <pre className="p-2.5 font-mono text-[11.5px] leading-relaxed">
      {text.split("\n").map((line, i) => {
        const kind =
          line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ")
            ? "meta"
            : line.startsWith("@@")
              ? "hunk"
              : line.startsWith("+")
                ? "add"
                : line.startsWith("-")
                  ? "del"
                  : "same";
        return (
          <div
            key={i}
            className={cn(
              "px-1 -mx-1",
              kind === "add" && "bg-emerald-500/12 text-emerald-400",
              kind === "del" && "bg-destructive/12 text-destructive",
              kind === "hunk" && "text-sky-400",
              kind === "meta" && "text-muted-foreground/70",
            )}
          >
            {line || " "}
          </div>
        );
      })}
    </pre>
  </div>
);

/** Light markdown for a tool result whose text is prose meant to be read structured — a
 *  skill's instructions, a source's content — not chat prose (that has its own renderer one
 *  level up) and not code (that's `CodeBlock`). Deliberately plain rather than a port of the
 *  chat's full markdown component set: this lives inside an already-small card. */
const proseComponents: Components = {
  h1: ({ children }) => <p className="mt-2 text-sm font-semibold text-foreground first:mt-0">{children}</p>,
  h2: ({ children }) => <p className="mt-2 text-sm font-semibold text-foreground first:mt-0">{children}</p>,
  h3: ({ children }) => <p className="mt-2 text-xs font-semibold text-foreground first:mt-0">{children}</p>,
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 list-disc space-y-0.5 ps-4 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal space-y-0.5 ps-4 last:mb-0">{children}</ol>,
  code: ({ children }) => (
    <code className="rounded bg-muted/60 px-1 py-0.5 font-mono text-[11px]">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="my-2 overflow-auto rounded-md bg-muted/40 p-2 font-mono text-[11px] leading-relaxed">
      {children}
    </pre>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-kith underline decoration-dotted underline-offset-2"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => <strong className="font-medium text-foreground">{children}</strong>,
};

const Prose: FC<{ text: string; className?: string }> = ({ text, className }) => (
  <div className={cn("text-xs leading-relaxed text-foreground/80", className)}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={proseComponents}>
      {text}
    </ReactMarkdown>
  </div>
);

/** A skill, `read_skill`'s result. The instructions are the point and used to render as one
 *  unstyled `pre` block — no headings, no bullets, just the raw markdown source of a document
 *  meant to be read, not looked at. Resources are clickable (they're paths inside `directory`,
 *  which nothing did anything with before); `note`, when present, is the "you already opened
 *  this" or "that's a lot of skills this turn" warning and reads as one, ahead of the body. */
function looksLikeSkill(value: Record<string, unknown>): boolean {
  return (
    typeof value.name === "string" &&
    typeof value.instructions === "string" &&
    Array.isArray(value.resources)
  );
}

const Skill: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const open = useFileViewer((s) => s.open);
  const directory = typeof value.directory === "string" ? value.directory : "";
  const resources = Array.isArray(value.resources) ? value.resources : [];
  const moreResources = typeof value.moreResources === "number" ? value.moreResources : 0;
  const allowedTools = Array.isArray(value.allowedTools) ? value.allowedTools : [];
  const note = typeof value.note === "string" ? value.note : "";
  const instructions = typeof value.instructions === "string" ? value.instructions : "";

  return (
    <div className="flex flex-col gap-2 rounded-lg p-2.5 ring-1 ring-border/60">
      <div className="flex items-center gap-1.5">
        <BookOpenText className="text-kith/70 size-3.5 shrink-0" />
        <span className="text-sm font-medium text-foreground">{String(value.name)}</span>
      </div>
      {note ? (
        <p className="rounded-md bg-muted/40 p-2 text-[11px] leading-relaxed text-muted-foreground">
          {note}
        </p>
      ) : null}
      {instructions ? (
        <div className="max-h-80 overflow-auto rounded-md bg-muted/30 p-2.5 ring-1 ring-border/40">
          <Prose text={instructions} />
        </div>
      ) : null}
      {resources.length ? (
        <div className="flex flex-col gap-1">
          <Label>resources</Label>
          <div className="flex flex-wrap gap-1.5">
            {resources.map((resource, i) => {
              const rel = typeof resource === "string" ? resource : JSON.stringify(resource);
              return (
                <button
                  key={i}
                  onClick={() => void open(directory ? `${directory}/${rel}` : rel)}
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10.5px] text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  {rel}
                </button>
              );
            })}
            {moreResources > 0 ? (
              <span className="text-muted-foreground/60 px-1.5 py-0.5 text-[10.5px]">
                +{moreResources} more
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
      {allowedTools.length ? (
        <div className="flex flex-wrap gap-1">
          {allowedTools.map((toolName, i) => (
            <span key={i} className="bg-kith/10 text-kith rounded px-1.5 py-0.5 text-[10px]">
              {String(toolName)}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
};

/** A source, `read_source`'s result — the full text of something a person handed him to read.
 *  `content` is the whole document; a `pre` block was the same "readable format shown
 *  unformatted" problem as a skill's instructions. */
function looksLikeSource(value: Record<string, unknown>): boolean {
  return typeof value.title === "string" && typeof value.content === "string" && "origin" in value;
}

const Source: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const origin = typeof value.origin === "string" ? value.origin : "";
  const isUrl = /^https?:\/\//.test(origin);
  return (
    <div className="flex flex-col gap-2 rounded-lg p-2.5 ring-1 ring-border/60">
      <span className="min-w-0 truncate text-sm font-medium text-foreground">
        {String(value.title)}
      </span>
      {origin ? (
        isUrl ? (
          <a
            href={origin}
            target="_blank"
            rel="noreferrer"
            className="text-kith truncate text-[11px] underline decoration-dotted underline-offset-2"
          >
            {origin}
          </a>
        ) : (
          <span className="truncate text-[11px] text-muted-foreground/70">{origin}</span>
        )
      ) : null}
      <div className="max-h-80 overflow-auto rounded-md bg-muted/30 p-2.5 ring-1 ring-border/40">
        <Prose text={String(value.content ?? "")} />
      </div>
    </div>
  );
};

/** A search hit — `web_search`'s and `search_sources`' shared shape: a title, a snippet, and
 *  either a `url` (the web) or an `origin` (an ingested source). Each item of the array used to
 *  go through `Fields`, which turned five results into fifteen labelled rows of the same three
 *  keys repeated; this is what one hit actually is — a link and a sentence about it. */
function looksLikeSearchHit(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.title === "string" &&
    typeof v.snippet === "string" &&
    (typeof v.url === "string" || typeof v.origin === "string")
  );
}

const SearchHit: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const url = typeof value.url === "string" ? value.url : "";
  const origin = typeof value.origin === "string" ? value.origin : "";
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline gap-2">
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="text-kith min-w-0 truncate text-xs font-medium underline decoration-dotted underline-offset-2"
          >
            {String(value.title)}
          </a>
        ) : (
          <span className="min-w-0 truncate text-xs font-medium text-foreground">
            {String(value.title)}
          </span>
        )}
        {value.id !== undefined ? (
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
            #{String(value.id)}
          </span>
        ) : null}
      </div>
      {url || origin ? (
        <span className="text-muted-foreground/60 truncate text-[10.5px]">{url || origin}</span>
      ) : null}
      <p className="text-[11.5px] leading-relaxed text-foreground/75">{String(value.snippet)}</p>
    </div>
  );
};

/** `repo_map`'s rendered text: one section per file (`path  (N lines)`), each followed by its
 *  definitions in `outline`'s own row format — so the same parser handles both, just applied
 *  per file instead of once. A trailing, unindented line that isn't a file header is the
 *  overall summary ("`root` — 12 of 40 source files"); shown as a caption rather than folded
 *  into the listing. */
function parseRepoMap(text: string): {
  files: { header: string; rows: { line: string; depth: number; signature: string }[]; note: string }[];
  footer: string;
} {
  const files: { header: string; rows: { line: string; depth: number; signature: string }[]; note: string }[] = [];
  let footer = "";
  for (const raw of text.split("\n")) {
    if (!raw.trim()) continue;
    const isHeader = !/^\s/.test(raw) && /\(\d+ lines?\)$/.test(raw);
    if (isHeader) {
      files.push({ header: raw, rows: [], note: "" });
      continue;
    }
    const current = files[files.length - 1];
    const match = current ? OUTLINE_ROW.exec(raw) : null;
    if (current && match) {
      const [, num, gap, signature] = match;
      current.rows.push({
        line: num.trim(),
        depth: Math.max(0, Math.floor((gap.length - 2) / 2)),
        signature,
      });
    } else if (current && /^\s/.test(raw)) {
      current.note = raw.trim();
    } else {
      footer = raw.trim();
    }
  }
  return { files, footer };
}

const RepoMap: FC<{ text: string }> = ({ text }) => {
  const open = useFileViewer((s) => s.open);
  const { files, footer } = parseRepoMap(text);
  if (!files.length) {
    return <p className="text-xs text-muted-foreground">{footer || text}</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex max-h-96 flex-col divide-y divide-border/40 overflow-auto rounded-lg ring-1 ring-border/60">
        {files.map((file, fi) => (
          <div key={fi} className="flex flex-col">
            <button
              onClick={() => void open(file.header.split("  (")[0])}
              className="px-2.5 py-1.5 text-left font-mono text-[11px] font-medium text-foreground/90 hover:bg-accent/50"
            >
              {file.header}
            </button>
            {file.rows.map((row, ri) => (
              <button
                key={ri}
                onClick={() => void open(file.header.split("  (")[0])}
                className="flex w-full items-baseline gap-2.5 px-2.5 py-0.5 text-left hover:bg-accent/50"
              >
                <span className="w-6 shrink-0 text-end font-mono text-[11px] text-muted-foreground/60">
                  {row.line}
                </span>
                <span
                  className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-foreground/85"
                  style={{ paddingLeft: `${row.depth * 0.9}em` }}
                >
                  {row.signature}
                </span>
              </button>
            ))}
            {file.note ? (
              <p className="text-muted-foreground/60 px-2.5 py-1 ps-8 text-[10.5px] italic">
                {file.note}
              </p>
            ) : null}
          </div>
        ))}
      </div>
      {footer ? <p className="text-[11px] text-muted-foreground/70">{footer}</p> : null}
    </div>
  );
};

/** `file:line: text` rows, each one openable. What grep and glob actually produce. */
const Locations: FC<{ text: string }> = ({ text }) => {
  const open = useFileViewer((s) => s.open);
  const rows = text.split("\n").filter((l) => l.trim());
  return (
    <div className="max-h-80 divide-y divide-border/40 overflow-auto rounded-lg ring-1 ring-border/60">
      {rows.map((row, i) => {
        const match = /^([^\s:]+):(\d+):?\s?(.*)$/.exec(row);
        if (!match) {
          return (
            <button
              key={i}
              onClick={() => void open(row.trim())}
              className="block w-full truncate px-2.5 py-1.5 text-left font-mono text-[11.5px] hover:bg-accent/50"
            >
              {row.trim()}
            </button>
          );
        }
        const [, file, line, rest] = match;
        return (
          <button
            key={i}
            onClick={() => void open(file)}
            className="flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left hover:bg-accent/50"
          >
            <span className="shrink-0 font-mono text-[11px] text-sky-400">
              {file}
              <span className="text-muted-foreground">:{line}</span>
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-foreground/80">
              {rest}
            </span>
          </button>
        );
      })}
    </div>
  );
};

/** `outline`'s rendered text, one row per definition: a right-aligned line number, then the
 *  signature indented by nesting depth. Parsed back out of `services/code/outline.render`'s
 *  own format (`" 27  class Foo"`, two spaces per depth) rather than given structured, since
 *  the plain-text form is also what he reads — one format, not two that can drift apart. */
const OUTLINE_ROW = /^(\s*\d+)(\s\s+)(.*)$/;

const Outline: FC<{ text: string; path: string }> = ({ text, path }) => {
  const open = useFileViewer((s) => s.open);
  const lines = text.split("\n");
  const rows = lines.slice(1).map((line) => {
    const match = OUTLINE_ROW.exec(line);
    if (!match) return { line: "", depth: 0, signature: line };
    const [, num, gap, signature] = match;
    return { line: num.trim(), depth: Math.max(0, Math.floor((gap.length - 2) / 2)), signature };
  });
  if (!rows.length || !rows.some((r) => r.line)) {
    return <p className="text-xs text-muted-foreground">{lines[0] ?? text}</p>;
  }
  return (
    <div className="max-h-96 divide-y divide-border/40 overflow-auto rounded-lg ring-1 ring-border/60">
      {rows.map((row, i) => (
        <button
          key={i}
          onClick={() => void open(path)}
          className="flex w-full items-baseline gap-2.5 px-2.5 py-1 text-left hover:bg-accent/50"
        >
          <span className="w-6 shrink-0 text-end font-mono text-[11px] text-muted-foreground/60">
            {row.line}
          </span>
          <span
            className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-foreground/85"
            style={{ paddingLeft: `${row.depth * 0.9}em` }}
          >
            {row.signature}
          </span>
        </button>
      ))}
    </div>
  );
};

/** One record from a listing, on one line: what it is, then what it says.
 *
 * A stack of labelled fields per row turns a list of ten tasks into eighty lines. The
 * identifying fields go first and small, the human one takes the rest of the width.
 */
const Row: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const id = value.id ?? value.name;
  const title =
    value.goal ?? value.title ?? value.name ?? value.topic ?? value.entry ?? value.content;
  const status = typeof value.status === "string" ? value.status : undefined;
  const other = status ? undefined : value.priority;
  return (
    <div className="flex min-w-0 items-baseline gap-2 text-xs">
      {id !== undefined && value.id !== undefined ? (
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">#{String(id)}</span>
      ) : null}
      <span className="min-w-0 flex-1 truncate">
        {String(title ?? JSON.stringify(value)).slice(0, 160)}
      </span>
      {/* Coloured when it's a real status word this palette knows about — a task, milestone
       *  or project row then reads the same colour it would in its own card, or on the
       *  Kanban board. Anything else (a priority, an unrecognised value) stays a plain
       *  neutral chip rather than guessing at a colour for it. */}
      {status ? <StatusBadge status={status} /> : null}
      {other ? (
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {String(other)}
        </span>
      ) : null}
    </div>
  );
};

/** Anything else: labelled rows rather than braces. */
const Fields: FC<{ value: Record<string, unknown> }> = ({ value }) => (
  <div className="flex flex-col gap-1.5">
    {Object.entries(value).map(([key, raw]) => {
      const text =
        typeof raw === "string"
          ? raw
          : typeof raw === "number" || typeof raw === "boolean" || raw === null
            ? String(raw)
            : JSON.stringify(raw, null, 2);
      const long = text.includes("\n") || text.length > 90;
      return (
        <div key={key} className={cn("gap-2", long ? "flex flex-col" : "flex items-baseline")}>
          <Label>{key.replace(/_/g, " ")}</Label>
          {long ? (
            <pre className="max-h-72 overflow-auto rounded-md bg-muted/40 p-2 text-[11.5px] whitespace-pre-wrap">
              {text}
            </pre>
          ) : (
            <span className="min-w-0 break-words text-xs text-foreground/90">{text || "—"}</span>
          )}
        </div>
      );
    })}
  </div>
);

/** Status → colour, matching the Kanban board and the Projects tab so a status reads the same
 *  wherever it shows up — a task's "done", a milestone's "done" and a project's "done" are one
 *  colour, not three different greens someone has to learn are the same thing. Covers every
 *  status across tasks, milestones and projects; the three vocabularies don't collide because
 *  each object only ever has one of them. */
const STATUS_TONE: Record<string, string> = {
  backlog: "bg-muted text-muted-foreground",
  todo: "bg-sky-500/15 text-sky-500",
  doing: "bg-kith/15 text-kith",
  review: "bg-violet-500/15 text-violet-400",
  waiting: "bg-orange-500/15 text-orange-500",
  done: "bg-emerald-500/15 text-emerald-500",
  dropped: "bg-muted text-muted-foreground/60 line-through",
  active: "bg-kith/15 text-kith",
  paused: "bg-orange-500/15 text-orange-500",
  archived: "bg-muted text-muted-foreground/60",
};

const StatusBadge: FC<{ status: string }> = ({ status }) => (
  <span
    className={cn(
      "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium capitalize",
      STATUS_TONE[status] ?? "bg-muted text-muted-foreground",
    )}
  >
    {status}
  </span>
);

/** A task — but two different shapes of it. `add_task`/`update_task` echo the bare row (goal,
 *  status, priority, no checklist, no milestone title); `view_task` returns the richer joined
 *  `task_detail` (checklist, comments, milestone_title). `priority` is on both and belongs to
 *  nothing else in the system, so it's the one field that reliably picks out either shape —
 *  checking for `checklist`/`milestone_title` alone missed the bare row entirely. */
function looksLikeTask(value: Record<string, unknown>): boolean {
  return typeof value.goal === "string" && typeof value.status === "string" && "priority" in value;
}

const Task: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const status = String(value.status ?? "");
  const priority = String(value.priority ?? "");
  const checklist = Array.isArray(value.checklist) ? value.checklist : [];
  const done = checklist.filter(
    (item) => item && typeof item === "object" && (item as { done?: boolean }).done,
  ).length;
  const comments = Array.isArray(value.comments) ? value.comments.length : 0;
  const deliverables = Array.isArray(value.deliverables) ? value.deliverables.length : 0;
  const description = typeof value.description === "string" ? value.description.trim() : "";

  return (
    <div className="flex flex-col gap-2 rounded-lg p-2.5 ring-1 ring-border/60">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
          {String(value.goal)}
        </span>
        {status ? <StatusBadge status={status} /> : null}
        {priority === "high" ? (
          <span className="shrink-0 rounded bg-destructive/15 px-1.5 py-0.5 text-[10px] font-medium text-destructive">
            high priority
          </span>
        ) : null}
      </div>
      {value.milestone_title ? (
        <span className="text-[11px] text-muted-foreground/70">→ {String(value.milestone_title)}</span>
      ) : null}
      {description ? (
        <p className="text-xs leading-relaxed text-foreground/80">{description}</p>
      ) : null}
      {checklist.length ? (
        <div className="flex flex-col gap-1">
          <Label>
            checklist · {done}/{checklist.length}
          </Label>
          <ul className="flex flex-col gap-0.5">
            {checklist.map((item, i) => {
              const row = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
              const checked = Boolean(row.done);
              return (
                <li
                  key={i}
                  className={cn(
                    "flex items-baseline gap-1.5 text-xs",
                    checked && "text-muted-foreground/60 line-through",
                  )}
                >
                  <span className="shrink-0 font-mono text-[10px]">{checked ? "[x]" : "[ ]"}</span>
                  <span className="min-w-0">{String(row.text ?? row.title ?? "")}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {value.due_at || comments > 0 || deliverables > 0 ? (
        <div className="flex flex-wrap gap-x-3 text-[11px] text-muted-foreground/70">
          {value.due_at ? <span>due {String(value.due_at)}</span> : null}
          {comments > 0 ? <span>{comments} comment{comments === 1 ? "" : "s"}</span> : null}
          {deliverables > 0 ? (
            <span>
              {deliverables} deliverable{deliverables === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

/** A milestone — one step of a project's roadmap. Same problem as a task's old rendering, one
 *  size down: `add_milestone`/`update_milestone` echo back `order_index`, `project_id`,
 *  `created_at`/`updated_at` — bookkeeping nobody asked about — next to the two facts that are
 *  the actual answer, its title and whether it's done. */
function looksLikeMilestone(value: Record<string, unknown>): boolean {
  return (
    typeof value.title === "string" &&
    (value.status === "todo" || value.status === "done") &&
    "order_index" in value &&
    "project_id" in value
  );
}

const Milestone: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const status = String(value.status ?? "");
  return (
    <div className="flex flex-col gap-1 rounded-lg p-2.5 ring-1 ring-border/60">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
          {String(value.title)}
        </span>
        {status ? <StatusBadge status={status} /> : null}
      </div>
      {value.target_at ? (
        <span className="text-[11px] text-muted-foreground/70">target {String(value.target_at)}</span>
      ) : null}
    </div>
  );
};

/** A project — `create_project`/`update_project`'s result, and a `get_project` lookup. The flat
 *  dump used to put `directory` (often empty), raw timestamps and the id ahead of the two things
 *  actually worth reading: what it's called and what it's for. */
function looksLikeProject(value: Record<string, unknown>): boolean {
  return (
    typeof value.name === "string" &&
    typeof value.status === "string" &&
    ["active", "done", "paused", "archived"].includes(value.status)
  );
}

const Project: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const status = String(value.status ?? "");
  const description = typeof value.description === "string" ? value.description.trim() : "";
  const directory = typeof value.directory === "string" ? value.directory.trim() : "";
  return (
    <div className="flex flex-col gap-2 rounded-lg p-2.5 ring-1 ring-border/60">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
          {String(value.name)}
        </span>
        {status ? <StatusBadge status={status} /> : null}
      </div>
      {description ? <p className="text-xs leading-relaxed text-foreground/80">{description}</p> : null}
      {directory ? (
        <span className="truncate font-mono text-[11px] text-muted-foreground/70">{directory}</span>
      ) : null}
    </div>
  );
};

const looksLikeDiff = (text: string) =>
  /^(diff --git|@@ )/m.test(text) || (/^\+/m.test(text) && /^-/m.test(text));
const looksLikeLocations = (text: string) => {
  const rows = text.split("\n").filter((l) => l.trim());
  return rows.length > 0 && rows.filter((l) => /^[^\s:]+:\d+/.test(l)).length >= rows.length / 2;
};

export const ToolResultBody: FC<{ name: string; args: Args; result: unknown }> = memo(
  ({ name, args, result: wrapped }) => {
    const { value: result, failed } = unwrap(wrapped);
    if (result === undefined || result === null) return null;
    if (failed) {
      // A refusal or an error. It used to be a key in a blob you had to open and read; the
      // permission gate in particular says exactly what to do next, which is worth seeing.
      const message =
        typeof result === "string"
          ? result
          : String((result as { message?: string })?.message ?? JSON.stringify(result));
      return (
        <div className="rounded-lg bg-destructive/10 p-2.5 text-xs leading-relaxed text-destructive ring-1 ring-destructive/25">
          {message}
        </div>
      );
    }

    // Shell first: it is the only tool whose result is a command's whole world, and the exit
    // code deserves to be the thing you see rather than a key in an object.
    if (name === "shell") {
      const r = result as { exitCode?: number; output?: string };
      return (
        <Shell
          command={String(args.command ?? "")}
          output={String(r.output ?? "")}
          code={Number(r.exitCode ?? 0)}
        />
      );
    }

    // `outline`'s shape is unique to it (`{path, language, definitions, outline}`), so this
    // is keyed on the tool rather than detected from the shape — nothing else returns a
    // rendered listing-of-definitions string under that key.
    if (name === "outline" && result && typeof result === "object" && "outline" in result) {
      const r = result as { path?: string; outline?: string };
      return <Outline text={String(r.outline ?? "")} path={String(r.path ?? args.path ?? "")} />;
    }

    // Same reasoning as `outline` just above: `repo_map`'s shape is unique to it.
    if (name === "repo_map" && result && typeof result === "object" && "map" in result) {
      const r = result as { map?: string };
      return <RepoMap text={String(r.map ?? "")} />;
    }

    if (typeof result === "string") {
      if (looksLikeDiff(result)) return <Diff text={result} />;
      if (name === "grep" || (name === "glob" && looksLikeLocations(result))) {
        return <Locations text={result} />;
      }
      if (name === "read_file") {
        return (
          <CodeBlock
            code={result}
            language={languageOf(String(args.path ?? ""))}
            label={String(args.path ?? "")}
          />
        );
      }
      if (!result.trim()) return <p className="text-xs text-muted-foreground">(nothing)</p>;
      // A fetched page is prose a person is meant to read, not code — the one string result
      // that reads worse in a monospace block than it would as plain text.
      if (name === "fetch_url" || name === "browse_page") {
        return (
          <div className="max-h-96 overflow-auto rounded-lg bg-muted/30 p-2.5 ring-1 ring-border/60">
            <Prose text={result} />
          </div>
        );
      }
      return (
        <pre className="max-h-96 overflow-auto rounded-lg bg-muted/30 p-2.5 text-[11.5px] leading-relaxed whitespace-pre-wrap ring-1 ring-border/60">
          {result}
        </pre>
      );
    }

    if (Array.isArray(result)) {
      if (!result.length) return <p className="text-xs text-muted-foreground">(none)</p>;
      if (result.every(looksLikeSearchHit)) {
        return (
          <div className="flex max-h-96 flex-col divide-y divide-border/40 overflow-auto rounded-lg p-1 ring-1 ring-border/60">
            {result.map((hit, i) => (
              <div key={i} className="px-2 py-1.5">
                <SearchHit value={hit} />
              </div>
            ))}
          </div>
        );
      }
      return (
        <div className="flex flex-col gap-1.5">
          <Label>
            {result.length} item{result.length === 1 ? "" : "s"}
          </Label>
          <div className="max-h-80 divide-y divide-border/40 overflow-auto rounded-lg ring-1 ring-border/60">
            {result.map((item, i) => (
              <div key={i} className="px-2.5 py-1.5">
                {item && typeof item === "object" ? (
                  <Fields value={item as Record<string, unknown>} />
                ) : (
                  <span className="text-xs">{String(item)}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      );
    }

    // A paged listing. Six labelled rows — LIMIT 100 / OFFSET 0 / CONTAINS — / ITEMS [] /
    // TOTAL 0 / SHOWING none of 0 — to say "there are no tasks" is the generic renderer
    // being technically complete and practically useless. This is the commonest result
    // shape in the system, so it earns a case of its own.
    const page = paged(result);
    if (page) {
      if (!page.items.length) {
        return <p className="text-xs text-muted-foreground">Nothing found.</p>;
      }
      return (
        <div className="flex flex-col gap-1.5">
          {page.total > page.items.length ? (
            <Label>
              {page.items.length} of {page.total.toLocaleString()}
            </Label>
          ) : (
            <Label>
              {page.total.toLocaleString()} {page.total === 1 ? "item" : "items"}
            </Label>
          )}
          <div className="max-h-80 divide-y divide-border/40 overflow-auto rounded-lg ring-1 ring-border/60">
            {page.items.map((item, i) => (
              <div key={i} className="px-2.5 py-1.5">
                {item && typeof item === "object" ? (
                  <Row value={item as Record<string, unknown>} />
                ) : (
                  <span className="text-xs">{String(item)}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      );
    }

    const object = result as Record<string, unknown>;
    if (looksLikeTask(object)) return <Task value={object} />;
    if (looksLikeMilestone(object)) return <Milestone value={object} />;
    if (looksLikeProject(object)) return <Project value={object} />;
    if (looksLikeSkill(object)) return <Skill value={object} />;
    if (looksLikeSource(object)) return <Source value={object} />;
    // A single text-ish field is prose, not a record — printing "output:" above it is noise.
    const keys = Object.keys(object);
    if (keys.length === 1 && typeof object[keys[0]] === "string") {
      const only = String(object[keys[0]]);
      if (looksLikeDiff(only)) return <Diff text={only} />;
    }
    return <Fields value={object} />;
  },
);
ToolResultBody.displayName = "ToolResultBody";

/** Enough to highlight a read. Reuses the viewer's own table by extension. */
function languageOf(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    py: "python",
    rb: "ruby",
    go: "go",
    rs: "rust",
    java: "java",
    kt: "kotlin",
    swift: "swift",
    c: "c",
    h: "c",
    cpp: "cpp",
    cs: "csharp",
    php: "php",
    sh: "bash",
    bash: "bash",
    zsh: "bash",
    json: "json",
    yml: "yaml",
    yaml: "yaml",
    toml: "ini",
    ini: "ini",
    sql: "sql",
    html: "xml",
    xml: "xml",
    css: "css",
    md: "markdown",
    markdown: "markdown",
  };
  return map[ext] ?? "plaintext";
}

/* ── arguments ──────────────────────────────────────────────────────────── */

/** The arguments as they were sent, parsed back. Invalid JSON is normal mid-stream. */
export function parseArgs(argsText?: string): Args {
  if (!argsText) return {};
  try {
    const parsed = JSON.parse(argsText);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Args) : {};
  } catch {
    return {};
  }
}

/**
 * What he asked the tool for.
 *
 * Worth showing rather than hiding, and worth reading rather than decoding: every diagnosis
 * that mattered today came from the arguments. `add_milestone` with `after: [35]` going in and
 * a milestone with no order coming out is only visible if both halves are legible.
 */
//: Arguments that are plumbing rather than intent. `limit: 100, offset: 0, contains: ""` is
//: what the caller did *not* say — the schema's defaults, echoed back — and three rows of it
//: above the answer buries the one argument that mattered.
const PLUMBING = new Set(["limit", "offset", "contains"]);

//: `edit_file`'s `old`/`new` are the whole text either side of the change — worth sending, not
//: worth showing twice. The result below is that same edit as a real diff, red/green, in
//: context; showing the full before-and-after here first is the same information said worse,
//: ahead of the version that actually reads well. `path` and `replace_all` still show: they're
//: not in the diff.
const SUPERSEDED_BY_RESULT: Record<string, Set<string>> = {
  edit_file: new Set(["old", "new"]),
};

export const ToolArgs: FC<{ argsText?: string; toolName?: string }> = ({ argsText, toolName }) => {
  const parsed = parseArgs(argsText);
  const hidden = (toolName && SUPERSEDED_BY_RESULT[toolName]) || undefined;
  const args = Object.fromEntries(
    Object.entries(parsed).filter(
      ([key, value]) =>
        !hidden?.has(key) &&
        !(PLUMBING.has(key) && (value === 0 || value === "" || value === null || value === 100)) &&
        value !== "" &&
        value !== null &&
        value !== undefined,
    ),
  );
  const keys = Object.keys(args);
  if (!keys.length && Object.keys(parsed).length) return null; // only defaults — say nothing
  if (!keys.length) {
    // Unparsed but present: mid-stream, or a tool that takes a bare string.
    return argsText?.trim() ? (
      <pre className="rounded-md bg-muted/40 p-2 text-[11.5px] whitespace-pre-wrap">{argsText}</pre>
    ) : null;
  }
  return <Fields value={args} />;
};
