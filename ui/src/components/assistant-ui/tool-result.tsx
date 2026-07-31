"use client";

import { memo, type FC, type ReactNode } from "react";

import { CodeBlock } from "@/components/file-view";
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

const Shell: FC<{ command: string; output: string; code: number }> = ({
  command,
  output,
  code,
}) => (
  <div className="overflow-hidden rounded-lg ring-1 ring-border/60">
    <div className="flex items-center gap-2 border-b border-border/50 bg-muted/40 px-2.5 py-1.5">
      <span className="font-mono text-[11px] text-muted-foreground">$</span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{command}</span>
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

/** One record from a listing, on one line: what it is, then what it says.
 *
 * A stack of labelled fields per row turns a list of ten tasks into eighty lines. The
 * identifying fields go first and small, the human one takes the rest of the width.
 */
const Row: FC<{ value: Record<string, unknown> }> = ({ value }) => {
  const id = value.id ?? value.name;
  const title =
    value.goal ?? value.title ?? value.name ?? value.topic ?? value.entry ?? value.content;
  const status = value.status ?? value.priority;
  return (
    <div className="flex min-w-0 items-baseline gap-2 text-xs">
      {id !== undefined && value.id !== undefined ? (
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">#{String(id)}</span>
      ) : null}
      <span className="min-w-0 flex-1 truncate">
        {String(title ?? JSON.stringify(value)).slice(0, 160)}
      </span>
      {status ? (
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {String(status)}
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
      return (
        <pre className="max-h-96 overflow-auto rounded-lg bg-muted/30 p-2.5 text-[11.5px] leading-relaxed whitespace-pre-wrap ring-1 ring-border/60">
          {result}
        </pre>
      );
    }

    if (Array.isArray(result)) {
      if (!result.length) return <p className="text-xs text-muted-foreground">(none)</p>;
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

export const ToolArgs: FC<{ argsText?: string }> = ({ argsText }) => {
  const parsed = parseArgs(argsText);
  const args = Object.fromEntries(
    Object.entries(parsed).filter(
      ([key, value]) =>
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
