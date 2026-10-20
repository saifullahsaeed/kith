"use client";

/**
 * The result cards for tools whose bodies used to be wrong, raw, or missing.
 *
 * Measured on his own transcripts, not guessed: `edit_files` (1,037 calls) printed its diff as an
 * uncoloured `<pre>` inside a key/value card; `check_code` said "1 line to fix" for every object
 * result, clean or not; `list_schedules` titled every row with raw JSON; `find_icons` printed SVG
 * source; the Browser plugin (560 calls) showed the command's name and nothing it returned — not
 * the page, not the screenshot, not what a failed click could have clicked instead; and a rule
 * saying no inside a successful envelope (`update_task`'s "1 of 9 requirements not met") drew as
 * a neutral card, indistinguishable from it working.
 *
 * Each card here is keyed on the tool name, because these shapes are each unique to one tool.
 * The shape-driven default in `tool-result.tsx` still handles everything else.
 */

import { useState, type FC, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUpRight, Clock, FileCode2, GitBranch, Globe, Repeat } from "lucide-react";

import { CodeBlock } from "@/components/files";
import { useMedia } from "@/components/files/media";
import { useFileViewer } from "@/lib/files";
import { useOpenConversation } from "@/lib/open-conversation";
import { useProjectRoot } from "@/lib/project-root";
import { shortPath } from "@/lib/tool-language";
import { diffStat, splitDiff } from "@/lib/tool-row";
import { cn } from "@/lib/utils";

type Obj = Record<string, unknown>;
type Args = Record<string, unknown>;

const isObj = (value: unknown): value is Obj =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);
const s = (value: unknown) => (value == null ? "" : String(value));

/* ── primitives ─────────────────────────────────────────────────────────── */

const Card: FC<{ children: ReactNode; className?: string }> = ({ children, className }) => (
  <div className={cn("flex flex-col gap-2 rounded-lg p-2.5 ring-1 ring-border/60", className)}>
    {children}
  </div>
);

const Caption: FC<{ children: ReactNode }> = ({ children }) => (
  <span className="text-[11px] font-medium text-muted-foreground/60">{children}</span>
);

const Chip: FC<{ children: ReactNode; tone?: "good" | "bad" | "warn" | "plain" }> = ({
  children,
  tone = "plain",
}) => (
  <span
    className={cn(
      "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums",
      tone === "good" && "bg-emerald-500/15 text-emerald-500",
      tone === "bad" && "bg-destructive/15 text-destructive",
      tone === "warn" && "bg-kith/15 text-kith",
      tone === "plain" && "bg-muted text-muted-foreground",
    )}
  >
    {children}
  </span>
);

/** A path that opens the file viewer, shown relative to the project. */
const PathButton: FC<{ path: string; line?: number | string; className?: string }> = ({
  path,
  line,
  className,
}) => {
  const open = useFileViewer((state) => state.open);
  const root = useProjectRoot();
  return (
    <button
      type="button"
      onClick={() => void open(path)}
      title={path}
      className={cn(
        "min-w-0 truncate text-start font-mono text-[11px] text-sky-400 hover:underline",
        className,
      )}
    >
      {shortPath(path, root)}
      {line !== undefined ? <span className="text-muted-foreground">:{s(line)}</span> : null}
    </button>
  );
};

/** Unified-diff lines, coloured — without the `---`/`+++` headers, which the section names. */
const DiffLines: FC<{ lines: string[] }> = ({ lines }) => (
  <pre className="max-h-96 overflow-auto bg-muted/20 p-2 font-mono text-[11.5px] leading-relaxed">
    {lines.map((line, i) => {
      const kind = line.startsWith("@@")
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
            "-mx-2 px-2",
            kind === "add" && "bg-emerald-500/12 text-emerald-400",
            kind === "del" && "bg-destructive/12 text-destructive",
            kind === "hunk" && "text-sky-400/80",
            kind === "same" && "text-foreground/70",
          )}
        >
          {line || " "}
        </div>
      );
    })}
  </pre>
);

/* ── edit_files ─────────────────────────────────────────────────────────── */

export const EditsCard: FC<{ value: Obj | string }> = ({ value }) => {
  const diff = typeof value === "string" ? value : s(value.diff);
  const note = isObj(value) && typeof value.note === "string" ? value.note : "";
  const files = splitDiff(diff);
  if (!files.length) {
    return <p className="text-xs text-muted-foreground">{diff || "No change."}</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {files.map((file, i) => {
        const { plus, minus } = diffStat(file.lines.join("\n"));
        return (
          <div key={`${file.path}-${i}`} className="overflow-hidden rounded-lg ring-1 ring-border/60">
            <div className="flex items-center gap-2 border-b border-border/50 bg-muted/30 px-2.5 py-1.5">
              <FileCode2 className="size-3.5 shrink-0 text-amber-400/80" />
              <PathButton path={file.path} className="flex-1" />
              <span className="shrink-0 font-mono text-[11px]">
                <span className="text-emerald-400">+{plus}</span>{" "}
                <span className="text-destructive">−{minus}</span>
              </span>
            </div>
            <DiffLines lines={file.lines} />
          </div>
        );
      })}
      {note ? <p className="text-[11px] leading-relaxed text-muted-foreground">{note}</p> : null}
    </div>
  );
};

/* ── write_file ─────────────────────────────────────────────────────────── */

const PREVIEW_LINES = 8;

/** A file he wrote: its name, how long it is, and the top of it — never the whole thing twice.
 *  The arguments panel used to print every byte of `content` above a result that said "wrote
 *  3046 bytes", so a 200-line script was a 200-line row. */
export const WroteCard: FC<{ args: Args; value: unknown }> = ({ args, value }) => {
  const path = s(args.path);
  const content = s(args.content);
  const lines = content.replace(/\n$/, "").split("\n");
  const removed = /this removed (\d+) definitions?/.exec(s(value))?.[1];
  const ext = path.split(".").pop() ?? "";
  return (
    <div className="flex flex-col gap-1.5">
      <CodeBlock
        code={lines.slice(0, PREVIEW_LINES).join("\n")}
        language={ext}
        label={`${lines.length.toLocaleString()} lines`}
      />
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <PathButton path={path} />
        {lines.length > PREVIEW_LINES ? <span>· first {PREVIEW_LINES} lines shown</span> : null}
        {removed ? <Chip tone="warn">removed {removed} definitions</Chip> : null}
      </div>
    </div>
  );
};

/* ── check_code ─────────────────────────────────────────────────────────── */

export const ProblemsCard: FC<{ args: Args; value: Obj }> = ({ args, value }) => {
  const engine = s(value.server || value.checker || value.engine);
  if (value.checked === false) {
    return <p className="text-xs text-muted-foreground">{s(value.note) || "No checker for this."}</p>;
  }
  const problems = Array.isArray(value.problems)
    ? (value.problems as unknown[]).filter(isObj)
    : s(value.problems)
        .split("\n")
        .filter((line) => line.trim())
        .map((line) => ({ message: line }) as Obj);
  if (value.clean === true || !problems.length) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Chip tone="good">No problems</Chip>
        {engine}
      </p>
    );
  }
  const file = s(value.path || args.path);
  return (
    <Card>
      <div className="flex items-center gap-2">
        <Caption>{engine}</Caption>
        <span className="flex-1" />
        {typeof value.errors === "number" && value.errors ? <Chip tone="bad">{value.errors} errors</Chip> : null}
        {typeof value.warnings === "number" && value.warnings ? <Chip tone="warn">{value.warnings} warnings</Chip> : null}
      </div>
      <ul className="flex max-h-72 flex-col gap-1 overflow-auto">
        {problems.map((one, i) => (
          <li key={i} className="flex items-baseline gap-2 text-xs">
            <span
              className={cn(
                "mt-1 size-1.5 shrink-0 rounded-full",
                one.severity === "warning" ? "bg-kith" : one.severity === "error" ? "bg-destructive" : "bg-muted-foreground/60",
              )}
            />
            {one.line !== undefined ? (
              <PathButton path={s(one.path || file)} line={`${s(one.line)}${one.column ? `:${s(one.column)}` : ""}`} className="shrink-0" />
            ) : null}
            <span className="min-w-0 text-foreground/85">{s(one.message)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
};

/* ── run_tests ──────────────────────────────────────────────────────────── */

export const TestsCard: FC<{ value: Obj }> = ({ value }) => {
  const [showLog, setShowLog] = useState(false);
  const failures = Array.isArray(value.failures) ? value.failures.map(String) : [];
  const running = value.status === "running";
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-1.5">
        <Caption>{s(value.runner) || "tests"}</Caption>
        <span className="flex-1" />
        {running ? <Chip tone="warn">still running · {s(value.for)}</Chip> : null}
        {typeof value.passed === "number" ? <Chip tone="good">{value.passed} passed</Chip> : null}
        {typeof value.failed === "number" && value.failed ? <Chip tone="bad">{value.failed} failed</Chip> : null}
        {typeof value.errors === "number" && value.errors ? <Chip tone="bad">{value.errors} errors</Chip> : null}
        {typeof value.skipped === "number" && value.skipped ? <Chip>{value.skipped} skipped</Chip> : null}
      </div>
      {value.ran ? (
        <code className="truncate font-mono text-[11px] text-muted-foreground" title={s(value.ran)}>
          {s(value.ran)}
        </code>
      ) : null}
      {running && value.note ? <p className="text-[11px] text-muted-foreground">{s(value.note)}</p> : null}
      {failures.length ? (
        <ul className="flex flex-col gap-1">
          {failures.map((one, i) => (
            <li key={i} className="flex gap-2 text-xs">
              <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-destructive" />
              <span className="min-w-0 font-mono text-[11px] whitespace-pre-wrap text-foreground/85 wrap-break-word">{one}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {value.log ? (
        <>
          <button
            type="button"
            onClick={() => setShowLog((was) => !was)}
            className="w-fit text-[11px] text-muted-foreground hover:text-foreground"
          >
            {showLog ? "Hide the log" : "Show the log"}
          </button>
          {showLog ? (
            <pre className="max-h-80 overflow-auto rounded-md bg-muted/30 p-2 font-mono text-[11px] whitespace-pre-wrap">
              {s(value.log)}
            </pre>
          ) : null}
        </>
      ) : null}
    </Card>
  );
};

/* ── processes ──────────────────────────────────────────────────────────── */

const shellCommand = (command: string) => command.replace(/^cd\s+\S+\s*&&\s*/, "");

export const ProcessCard: FC<{ value: Obj }> = ({ value }) => {
  if (Array.isArray(value.running)) {
    const rows = (value.running as unknown[]).filter(isObj);
    if (!rows.length) return <p className="text-xs text-muted-foreground">Nothing running.</p>;
    return (
      <Card className="gap-1">
        {rows.map((one, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className={cn("size-1.5 shrink-0 rounded-full", one.alive ? "bg-roam" : "bg-muted-foreground/50")} />
            <span className="shrink-0 text-foreground">{s(one.name)}</span>
            <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
              {shellCommand(s(one.command))}
            </code>
            <span className="shrink-0 text-[11px] text-muted-foreground tabular-nums">
              {one.alive ? `up ${s(one.for)}` : "exited"}
            </span>
          </div>
        ))}
      </Card>
    );
  }
  const stopped = typeof value.stopped === "string";
  const alive = Boolean(value.alive) && !stopped;
  const output = s(value.output ?? value.lastOutput);
  return (
    <Card>
      <div className="flex items-center gap-2 text-xs">
        <span className={cn("size-1.5 shrink-0 rounded-full", alive ? "bg-roam" : "bg-muted-foreground/50")} />
        <span className="shrink-0 font-medium text-foreground">{s(value.name ?? value.stopped)}</span>
        <span className="flex-1" />
        <span className="text-[11px] text-muted-foreground tabular-nums">
          {stopped
            ? `stopped after ${s(value.ranFor)}`
            : alive
              ? `running · ${s(value.for)}`
              : value.exitCode !== undefined
                ? `exited ${s(value.exitCode)} after ${s(value.for)}`
                : "done"}
        </span>
      </div>
      {value.command ? (
        <code className="truncate font-mono text-[11px] text-muted-foreground" title={s(value.command)}>
          $ {shellCommand(s(value.command))}
        </code>
      ) : null}
      {output.trim() ? (
        <pre className="max-h-64 overflow-auto rounded-md bg-muted/30 p-2 font-mono text-[11px] whitespace-pre-wrap">
          {output}
        </pre>
      ) : value.note ? (
        <p className="text-[11px] text-muted-foreground">{s(value.note)}</p>
      ) : null}
    </Card>
  );
};

/* ── a rule said no ─────────────────────────────────────────────────────── */

/** `{blocked, next, not_met, checked}` or `{ok: false, error}` inside a successful envelope. */
export const RefusalCard: FC<{ value: Obj }> = ({ value }) => {
  const notMet = Array.isArray(value.not_met) ? value.not_met.map(String) : [];
  const unchecked = Array.isArray(value.unchecked_checklist_items)
    ? value.unchecked_checklist_items.map(String)
    : [];
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-kith/8 p-2.5 ring-1 ring-kith/25">
      <p className="text-xs leading-relaxed text-foreground/90">{s(value.blocked || value.error)}</p>
      {notMet.length || unchecked.length ? (
        <ul className="flex flex-col gap-0.5">
          {[...notMet, ...unchecked].map((one, i) => (
            <li key={i} className="flex items-baseline gap-1.5 text-xs text-foreground/80">
              <span className="font-mono text-[10px] text-kith">✗</span>
              {one}
            </li>
          ))}
        </ul>
      ) : null}
      {value.next ? (
        <p className="text-[11px] text-muted-foreground">
          <span className="font-medium text-kith/90">Next · </span>
          {s(value.next)}
        </p>
      ) : null}
      {typeof value.checked === "string" && value.checked ? (
        <div className="max-h-72 overflow-auto rounded-md bg-muted/30 p-2 text-xs leading-relaxed text-foreground/80 [&_p]:mb-1.5 [&_strong]:text-foreground">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{value.checked}</ReactMarkdown>
        </div>
      ) : null}
    </div>
  );
};

/* ── the Browser plugin ─────────────────────────────────────────────────── */

const Screenshot: FC<{ path: string; width?: unknown; height?: unknown }> = ({ path, width, height }) => {
  const open = useFileViewer((state) => state.open);
  const { url, error, loading } = useMedia(path);
  return (
    <button
      type="button"
      onClick={() => void open(path)}
      title="Open the screenshot"
      className="border-border/60 bg-muted/20 hover:border-kith/40 max-w-full self-start overflow-hidden rounded-lg border p-1 transition-colors"
    >
      {loading ? (
        <span className="block px-3 py-6 text-[11px] text-muted-foreground/60">loading…</span>
      ) : error || !url ? (
        <span className="block px-3 py-6 text-[11px] text-muted-foreground/60">can't preview it here</span>
      ) : (
        <img src={url} alt={`Screenshot, ${s(width)}×${s(height)}`} className="max-h-64 max-w-full rounded object-contain" />
      )}
    </button>
  );
};

export const BrowserCard: FC<{ command: string; args: Args; value: Obj }> = ({ command, args, value }) => {
  const url = s(value.url);
  const title = s(value.title);
  const options = s(value.options);
  return (
    <div className="flex flex-col gap-2">
      {url || title ? (
        <div className="flex items-center gap-2 text-xs">
          <Globe className="size-3.5 shrink-0 text-sky-400/80" />
          <span className="min-w-0 truncate text-foreground/90">{title || url}</span>
          {title && url ? (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="min-w-0 truncate font-mono text-[11px] text-muted-foreground hover:underline"
            >
              {url}
            </a>
          ) : null}
        </div>
      ) : null}
      {value.failure ? <p className="text-xs text-destructive">{s(value.failure)}</p> : null}
      {command === "click" && !value.clicked ? (
        <p className="text-xs text-muted-foreground">
          Nothing called “{s(args.text ?? args.selector)}” to click
          {options ? (
            <>
              {" "}— the page offers <span className="text-foreground/85">{options}</span>
            </>
          ) : null}
          .
        </p>
      ) : null}
      {command === "read" && value.text ? (
        <pre className="max-h-72 overflow-auto rounded-md bg-muted/30 p-2 text-[11.5px] leading-relaxed whitespace-pre-wrap text-foreground/80">
          {s(value.text)}
        </pre>
      ) : null}
      {command === "look" && typeof value.shot === "string" ? (
        <Screenshot path={value.shot} width={value.width} height={value.height} />
      ) : null}
      {command === "resize" && value.width ? (
        <p className="text-xs text-muted-foreground">
          Laid out at {s(value.width)} × {s(value.height)}
          {typeof value.scale === "number" ? `, shown at ${Math.round(value.scale * 100)}%` : ""}.
        </p>
      ) : null}
    </div>
  );
};

/* ── time ───────────────────────────────────────────────────────────────── */

/** What he has waiting. Reminder and schedule rows have no goal, title or name, so the generic
 *  row titled every one of them with its own JSON. */
export const SchedulesCard: FC<{ value: Obj }> = ({ value }) => {
  const items = Array.isArray(value.items) ? (value.items as unknown[]).filter(isObj) : [];
  if (!items.length) return <p className="text-xs text-muted-foreground">Nothing waiting.</p>;
  return (
    <Card className="gap-1">
      {items.map((one, i) => {
        const repeating = one.kind === "repeating" || one.every_minutes || one.daily_at;
        const Icon = repeating ? Repeat : Clock;
        return (
          <div key={i} className="flex items-center gap-2 text-xs">
            <Icon className="size-3.5 shrink-0 text-orange-400/80" />
            <span className="min-w-0 flex-1 truncate text-foreground/90">{s(one.note)}</span>
            <span className="shrink-0 text-[11px] text-muted-foreground">{s(one.fires ?? one.at)}</span>
            {one.id !== undefined ? (
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground/60">#{s(one.id)}</span>
            ) : null}
          </div>
        );
      })}
    </Card>
  );
};

export const ScheduledCard: FC<{ value: Obj }> = ({ value }) => (
  <p className="flex items-center gap-2 text-xs text-foreground/85">
    {value.kind === "repeating" ? (
      <Repeat className="size-3.5 shrink-0 text-orange-400/80" />
    ) : (
      <Clock className="size-3.5 shrink-0 text-orange-400/80" />
    )}
    <span>
      Comes back to him <span className="font-medium">{s(value.fires)}</span>
      {value.note ? <> — {s(value.note)}</> : null}
    </span>
  </p>
);

/* ── design ─────────────────────────────────────────────────────────────── */

/** The icons, drawn. As an `<img>` from a data URI, so markup that came out of a tool result can
 *  never run as part of this page. `currentColor` means nothing inside an image, so it is swapped
 *  for a grey that reads on both themes. */
export const IconsCard: FC<{ value: Obj }> = ({ value }) => {
  const icons = Array.isArray(value.icons) ? (value.icons as unknown[]).filter(isObj) : [];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {icons.map((one, i) =>
          one.icon && typeof one.svg === "string" ? (
            <span
              key={i}
              className="flex w-20 flex-col items-center gap-1 rounded-md bg-muted/30 px-1.5 py-2 ring-1 ring-border/40"
              title={`${s(one.for)} → ${s(one.icon)}`}
            >
              <img
                alt={s(one.icon)}
                className="size-5"
                src={`data:image/svg+xml;utf8,${encodeURIComponent(one.svg.replaceAll("currentColor", "#9a9186"))}`}
              />
              <span className="w-full truncate text-center font-mono text-[10px] text-muted-foreground">
                {s(one.icon)}
              </span>
            </span>
          ) : (
            <span
              key={i}
              className="flex w-20 flex-col items-center justify-center gap-1 rounded-md px-1.5 py-2 text-center text-[10px] text-muted-foreground ring-1 ring-dashed ring-border/60"
              title={s(one.note)}
            >
              no “{s(one.for)}”
            </span>
          ),
        )}
      </div>
      {value.note ? <p className="text-[11px] text-muted-foreground">{s(value.note)}</p> : null}
    </div>
  );
};

/** A design system, as its colours rather than as a JSON object of hex strings. */
export const DesignSystemCard: FC<{ value: Obj }> = ({ value }) => {
  if (value.system === "none") {
    return <p className="text-xs text-muted-foreground">{s(value.note) || "No design system yet."}</p>;
  }
  const colours = isObj(value.colors) ? Object.entries(value.colors) : [];
  const listed = Array.isArray(value.colors) ? value.colors.map(String) : [];
  return (
    <Card>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground">{s(value.name)}</span>
        {value.wrote || value.path ? (
          <PathButton path={s(value.wrote || value.path)} className="text-muted-foreground" />
        ) : null}
      </div>
      {colours.length ? (
        <div className="flex flex-wrap gap-1.5">
          {colours.map(([name, hex]) => (
            <span key={name} className="flex items-center gap-1.5 rounded bg-muted/30 py-0.5 ps-0.5 pe-1.5" title={`${name} — ${s(hex)}`}>
              <span className="size-4 rounded-sm ring-1 ring-border/60" style={{ background: /^#[0-9a-f]{3,8}$/i.test(s(hex)) ? s(hex) : undefined }} />
              <span className="font-mono text-[10px] text-muted-foreground">{name}</span>
            </span>
          ))}
        </div>
      ) : null}
      {listed.length ? (
        <p className="text-[11px] text-muted-foreground">
          Colours: <span className="font-mono">{listed.join(", ")}</span>
        </p>
      ) : null}
      {value.note ? <p className="text-[11px] leading-relaxed text-muted-foreground">{s(value.note)}</p> : null}
    </Card>
  );
};

/* ── memory, symbols, plans, lists ──────────────────────────────────────── */

export const MemoriesCard: FC<{ items: Obj[] }> = ({ items }) => (
  <Card className="max-h-96 gap-2 overflow-auto">
    {items.map((one, i) => (
      <div key={i} className="flex flex-col gap-0.5">
        <p className="line-clamp-3 text-xs leading-relaxed text-foreground/85">{s(one.content)}</p>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground/70">
          {one.id !== undefined ? <span className="font-mono">#{s(one.id)}</span> : null}
          {one.level ? <span>{s(one.level)}</span> : null}
          {Array.isArray(one.tags) && one.tags.length ? <span>{one.tags.map(String).join(" · ")}</span> : null}
          {typeof one.score === "number" ? (
            <span className="ms-auto flex items-center gap-1">
              <span className="h-1 w-10 overflow-hidden rounded-full bg-muted">
                <span className="block h-full rounded-full bg-violet-400/70" style={{ width: `${Math.round(Math.min(1, one.score) * 100)}%` }} />
              </span>
              {one.score.toFixed(2)}
            </span>
          ) : null}
        </div>
      </div>
    ))}
  </Card>
);

export const SymbolsCard: FC<{ value: Obj }> = ({ value }) => {
  const base = s(value.searched);
  const join = (path: string) => (path.startsWith("/") || !base ? path : `${base.replace(/\/[^/]*\.[^/]*$/, "")}/${path}`);
  const section = (label: string, rows: unknown) => {
    const list = Array.isArray(rows) ? (rows as unknown[]).filter(isObj) : [];
    if (!list.length) return null;
    return (
      <div className="flex flex-col gap-0.5">
        <Caption>
          {label} · {list.length}
        </Caption>
        {list.map((one, i) => (
          <div key={i} className="flex items-baseline gap-2 text-xs">
            <PathButton path={join(s(one.path))} line={s(one.line)} className="shrink-0" />
            <code className="min-w-0 truncate font-mono text-[11px] text-foreground/80">{s(one.text)}</code>
          </div>
        ))}
      </div>
    );
  };
  const nothing = !Array.isArray(value.definitions) || (!value.definitions.length && !(Array.isArray(value.references) && value.references.length));
  return (
    <Card>
      <div className="flex items-center gap-2">
        {value.engine ? <Chip>{s(value.engine)}</Chip> : null}
        {typeof value.filesSearched === "number" ? (
          <span className="text-[11px] text-muted-foreground">{value.filesSearched} files searched</span>
        ) : null}
      </div>
      {nothing ? (
        <p className="text-xs text-muted-foreground">{s(value.unavailable || value.error) || "Nothing found."}</p>
      ) : null}
      {section("defined", value.definitions)}
      {section("used", value.references)}
    </Card>
  );
};

export const PlanCard: FC<{ value: Obj }> = ({ value }) => {
  const tasks = Array.isArray(value.tasks) ? (value.tasks as unknown[]).filter(isObj) : [];
  const refused = Array.isArray(value.refused) ? (value.refused as unknown[]).filter(isObj) : [];
  return (
    <Card className="gap-1.5">
      {tasks.map((one, i) => (
        <div key={i} className="flex items-baseline gap-2 text-xs">
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">#{s(one.id)}</span>
          <span className="min-w-0 flex-1 text-foreground/90">{s(one.goal)}</span>
          {one.steps !== undefined ? (
            <span className="shrink-0 text-[11px] text-muted-foreground">{s(one.steps)} steps</span>
          ) : null}
        </div>
      ))}
      {refused.map((one, i) => (
        <div key={`r${i}`} className="flex items-baseline gap-2 text-xs">
          <Chip tone="warn">refused</Chip>
          <span className="min-w-0 text-muted-foreground">
            {one.goal ? `${s(one.goal)} — ` : ""}
            {s(one.why)}
          </span>
        </div>
      ))}
      {value.note ? <p className="text-[11px] text-muted-foreground">{s(value.note)}</p> : null}
    </Card>
  );
};

export const ChecklistCard: FC<{ value: Obj }> = ({ value }) => {
  const added = Array.isArray(value.added) ? (value.added as unknown[]).filter(isObj) : [];
  const checked = Array.isArray(value.checked) ? (value.checked as unknown[]).filter(isObj) : [];
  const single = !added.length && !checked.length && value.text ? [value] : [];
  const rows = [
    ...checked.map((one) => ({ one, mark: "✓", tone: "text-emerald-500" })),
    ...added.map((one) => ({ one, mark: "+", tone: "text-sky-400" })),
    ...single.map((one) => ({ one, mark: one.done ? "✓" : "+", tone: one.done ? "text-emerald-500" : "text-sky-400" })),
  ];
  return (
    <ul className="flex flex-col gap-0.5">
      {rows.map(({ one, mark, tone }, i) => (
        <li key={i} className="flex items-baseline gap-2 text-xs text-foreground/85">
          <span className={cn("w-3 shrink-0 font-mono text-[11px]", tone)}>{mark}</span>
          <span className="min-w-0">{s(one.text)}</span>
        </li>
      ))}
    </ul>
  );
};

/** What `glob` found: one openable row per file, under the folder it searched. */
export const FilesCard: FC<{ text: string }> = ({ text }) => {
  const rows = text.split("\n").filter((line) => line.trim());
  const header = /^under (.+)$/.exec(rows[0] ?? "");
  const base = header?.[1] ?? "";
  const files = header ? rows.slice(1) : rows;
  if (!files.length) return <p className="text-xs text-muted-foreground">{text || "Nothing."}</p>;
  return (
    <Card className="max-h-80 gap-0.5 overflow-auto">
      {files.map((one, i) => {
        const path = one.trim();
        const full = path.startsWith("/") || path.startsWith("~") || !base ? path : `${base.replace(/\/$/, "")}/${path}`;
        return <PathButton key={i} path={full} />;
      })}
    </Card>
  );
};

/** Search that could not search. One note, said as a note — not "1 item" and a field called note. */
export const SearchDownCard: FC<{ note: string }> = ({ note }) => (
  <p className="rounded-lg bg-destructive/8 p-2.5 text-xs leading-relaxed text-foreground/85 ring-1 ring-destructive/20">
    {note}
  </p>
);

/**
 * The agents a `start_agents` call started: each one's name — a way into its conversation — its
 * branch, and the opening of the task it was given, or why it could not be started.
 *
 * The row above already names them; this is for what the row cannot carry. A task is the whole
 * brief, often a page long, and printing it as the call's arguments (JSON, every agent's in one
 * blob) was the reason this card exists: two lines of each is enough to tell them apart, and the
 * rest is the first message in the agent's own conversation, one click away.
 */
export const StartedAgentsCard: FC<{ args: Args; value: Obj }> = ({ args, value }) => {
  const open = useOpenConversation();
  const started = Array.isArray(value.agents) ? (value.agents as unknown[]).filter(isObj) : [];
  const asked = Array.isArray(args.agents) ? (args.agents as unknown[]).filter(isObj) : [];
  const taskOf = (name: string) => s(asked.find((one) => s(one.name) === name)?.task);
  if (!started.length) return null;
  return (
    <Card className="gap-1.5">
      {started.map((one, i) => {
        const name = s(one.name);
        const id = s(one.id);
        const error = typeof one.error === "string" ? one.error : "";
        const task = taskOf(name);
        return (
          <div key={`${name}-${i}`} className="flex flex-col gap-0.5 text-xs">
            <div className="flex min-w-0 items-center gap-2">
              <span
                aria-hidden
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  error ? "bg-destructive/70" : "bg-teal-500 dark:bg-teal-400",
                )}
              />
              {id && !error ? (
                <button
                  type="button"
                  onClick={() => open(id)}
                  title={`Open ${name}'s conversation`}
                  className="text-foreground flex min-w-0 cursor-pointer items-center gap-1 font-medium hover:underline"
                >
                  <span className="truncate">{name}</span>
                  <ArrowUpRight className="size-3 shrink-0 opacity-60" aria-hidden />
                </button>
              ) : (
                <span className="text-foreground truncate font-medium">{name}</span>
              )}
              {one.branch ? (
                <span className="text-muted-foreground flex min-w-0 items-center gap-1 font-mono text-[11px]">
                  <GitBranch className="size-3 shrink-0" aria-hidden />
                  <span className="truncate">{s(one.branch)}</span>
                </span>
              ) : one.workspace === "shared" ? (
                <span className="text-muted-foreground text-[11px]">in this folder</span>
              ) : null}
            </div>
            {error ? (
              <p className="text-destructive/90 ps-3.5 text-[11px] leading-snug">{error}</p>
            ) : task ? (
              <p className="text-muted-foreground line-clamp-2 ps-3.5 text-[11px] leading-snug">{task}</p>
            ) : null}
          </div>
        );
      })}
      {typeof value.note === "string" && value.note ? (
        <p className="text-muted-foreground border-border/60 border-t pt-1.5 text-[11px] leading-snug">
          {value.note}
        </p>
      ) : null}
    </Card>
  );
};
