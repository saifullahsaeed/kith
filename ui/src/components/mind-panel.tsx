import { useCallback, useEffect, useRef } from "react";
import {
  AlarmClock,
  BookOpenText,
  Brain,
  CircleDot,
  Compass,
  FileCode2,
  FolderKanban,
  Globe,
  Heart,
  ListChecks,
  Moon,
  NotebookPen,
  PanelRightClose,
  Play,
  Puzzle,
  Search,
  Send,
  Sparkles,
  Square,
  TerminalSquare,
  TriangleAlert,
  Undo2,
  Unlock,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { fetchPermissions, setPermissionMode } from "@/lib/backend";
import { cn } from "@/lib/utils";
import { formatTokens, realTokens, sumUsage, usageTitle, type Usage } from "@/lib/tokens";
import type { useAutonomy } from "@/hooks/use-autonomy";
import type { ActivityItem } from "@/lib/backend/autonomy";

type Autonomy = ReturnType<typeof useAutonomy>;

const KIND: Record<
  ActivityItem["kind"],
  { icon: LucideIcon | null; tone: string; head?: boolean }
> = {
  // "head" kinds begin a tick — each becomes a titled block so you can see every
  // distinct thing he set out to do, and why.
  //
  // Real icons rather than the unicode glyphs that were here (▸ ❋ ✦ ☾ ⎋). Those rendered at
  // whatever weight and baseline the system font felt like, so the column never lined up and
  // the feed read as typographic debris beside the rest of an app that uses lucide
  // throughout. An icon also survives being small, which every one of these is.
  start: { icon: CircleDot, tone: "text-blue-400", head: true },
  reply: { icon: Undo2, tone: "text-pink-400", head: true },
  reflect: { icon: Compass, tone: "text-kith", head: true },
  curious: { icon: Sparkles, tone: "text-teal-400", head: true },
  consolidate: { icon: Moon, tone: "text-indigo-400", head: true },
  breakout: { icon: Unlock, tone: "text-orange-400", head: true },
  tool: { icon: null, tone: "text-muted-foreground/70" }, // its own icon, by what it touched
  thought: { icon: null, tone: "text-muted-foreground/50" },
  reminder: { icon: AlarmClock, tone: "text-orange-400" },
  done: { icon: null, tone: "text-muted-foreground/40" },
  // Never rendered as a feed line — pulled out in groupTicks and shown as one quiet
  // footer row per tick, because a sentence per model request would bury the thinking.
  tokens: { icon: null, tone: "" },
  error: { icon: TriangleAlert, tone: "text-destructive" },
  status: { icon: null, tone: "text-muted-foreground/60" },
};

/**
 * What kind of thing a tool touched, and the icon for it.
 *
 * The point is scanning rather than decoration. A column of identical arrows tells you
 * nothing; a column where six rows carry a terminal and two carry a globe tells you at a
 * glance that he spent this step building and briefly looked something up — which is the
 * question you actually have when you glance at the panel.
 */
const GROUP: Record<string, { icon: LucideIcon; tone: string }> = {
  memory: { icon: Brain, tone: "text-violet-400/80" },
  writing: { icon: NotebookPen, tone: "text-kith/80" },
  reading: { icon: BookOpenText, tone: "text-kith/70" },
  tasks: { icon: ListChecks, tone: "text-blue-400/80" },
  projects: { icon: FolderKanban, tone: "text-blue-400/70" },
  curiosity: { icon: Sparkles, tone: "text-teal-400/80" },
  self: { icon: Heart, tone: "text-pink-400/70" },
  time: { icon: AlarmClock, tone: "text-orange-400/80" },
  outreach: { icon: Send, tone: "text-pink-400/80" },
  web: { icon: Globe, tone: "text-sky-400/80" },
  shell: { icon: TerminalSquare, tone: "text-emerald-400/80" },
  files: { icon: FileCode2, tone: "text-amber-400/80" },
  search: { icon: Search, tone: "text-sky-400/70" },
  tools: { icon: Wrench, tone: "text-muted-foreground/70" },
  skills: { icon: Puzzle, tone: "text-kith/80" },
};

/**
 * What each tool did, and to what.
 *
 * `verb` is the phrase; `of` names the argument that is the subject of it. Both halves matter
 * and only the first existed: every call was rendered by splitting the raw text on "(" and
 * looking up the bare name, so a whole afternoon of work read as "read a file / wrote a file /
 * ran a command" over and over, with the one useful piece of information — *which* file,
 * *which* command — discarded on the way in.
 *
 * The table is exhaustive on purpose, and a test asserts it stays that way. Seventeen of the
 * fifty-five tools had no entry and fell through to the raw `name(arg=value)` string, so the
 * feed was half plain English and half code, and which half you got depended on which tool he
 * happened to reach for. A missing entry should fail the suite, not quietly print a function
 * call at someone.
 */
const TOOL: Record<string, { verb: string; of?: string; group: keyof typeof GROUP }> = {
  // memory and notes
  recall: { verb: "searched his memory for", of: "query", group: "memory" },
  remember: { verb: "remembered", of: "content", group: "memory" },
  forget: { verb: "let go of a memory", group: "memory" },
  set_memory_level: { verb: "re-shelved a memory", group: "memory" },
  take_note: { verb: "noted", of: "title", group: "writing" },
  read_notes: { verb: "read his notes", group: "reading" },
  update_note: { verb: "edited a note", group: "writing" },
  journal: { verb: "wrote in his journal", group: "writing" },
  read_journal: { verb: "re-read his journal", group: "reading" },
  // tasks and projects
  add_task: { verb: "set himself", of: "goal", group: "tasks" },
  list_tasks: { verb: "looked over his tasks", group: "tasks" },
  update_task: { verb: "updated a task", group: "tasks" },
  view_task: { verb: "opened task", of: "id", group: "tasks" },
  comment_on_task: { verb: "noted on a task", of: "comment", group: "writing" },
  ask_on_task: { verb: "asked you", of: "question", group: "outreach" },
  add_checklist_item: { verb: "added a step", of: "text", group: "tasks" },
  check_item: { verb: "ticked off a step", group: "tasks" },
  add_deliverable: { verb: "handed over", of: "title", group: "tasks" },
  create_project: { verb: "started the project", of: "name", group: "projects" },
  list_projects: { verb: "looked over his projects", group: "projects" },
  update_project: { verb: "updated a project", group: "projects" },
  add_milestone: { verb: "added the milestone", of: "title", group: "projects" },
  update_milestone: { verb: "updated a milestone", group: "projects" },
  order_milestones: { verb: "put the milestones in order", group: "projects" },
  unlink_milestones: { verb: "unlinked two milestones", group: "projects" },
  // curiosity, mood, identity, people
  wonder: { verb: "grew curious about", of: "topic", group: "curiosity" },
  list_curiosities: { verb: "reviewed what he is curious about", group: "curiosity" },
  update_curiosity: { verb: "updated a curiosity", group: "curiosity" },
  set_mood: { verb: "felt", of: "mood", group: "self" },
  set_identity: { verb: "reshaped who he is", group: "self" },
  note_about_self: { verb: "noted about himself", of: "note", group: "self" },
  note_about: { verb: "noted about you", of: "note", group: "self" },
  recall_person: { verb: "recalled who you are", group: "self" },
  // time
  set_reminder: { verb: "set a reminder", of: "note", group: "time" },
  list_reminders: { verb: "checked his reminders", group: "time" },
  cancel_reminder: { verb: "cleared a reminder", group: "time" },
  schedule: { verb: "set a standing job", of: "note", group: "time" },
  list_schedules: { verb: "checked his schedules", group: "time" },
  cancel_schedule: { verb: "cleared a schedule", group: "time" },
  // reaching out
  reach_out: { verb: "reached out to you", group: "outreach" },
  // knowledge and the web
  search_sources: { verb: "searched your sources for", of: "query", group: "search" },
  read_source: { verb: "read a source you gave him", group: "reading" },
  web_search: { verb: "searched the web for", of: "query", group: "search" },
  fetch_url: { verb: "read", of: "url", group: "web" },
  browse_page: { verb: "opened", of: "url", group: "web" },
  // the machine
  shell: { verb: "ran", of: "command", group: "shell" },
  read_file: { verb: "read", of: "path", group: "files" },
  write_file: { verb: "wrote", of: "path", group: "files" },
  edit_file: { verb: "edited", of: "path", group: "files" },
  changes: { verb: "checked what he had changed", group: "files" },
  commit: { verb: "saved a point in history", of: "message", group: "files" },
  check_code: { verb: "checked the code", of: "path", group: "shell" },
  glob: { verb: "looked for files matching", of: "pattern", group: "search" },
  history: { verb: "looked back through his history", group: "reading" },
  delete_file: { verb: "put in the Trash", of: "path", group: "files" },
  list_files: { verb: "looked through", of: "path", group: "files" },
  grep: { verb: "searched files for", of: "pattern", group: "search" },
  // his own tools and skills
  create_tool: { verb: "built himself a tool", of: "name", group: "tools" },
  list_tools: { verb: "checked his own tools", group: "tools" },
  delete_tool: { verb: "removed one of his tools", group: "tools" },
  read_skill: { verb: "opened the skill", of: "name", group: "skills" },
};

/** One feed icon, at the single size this panel uses everywhere. */
function Glyph({ icon: Mark }: { icon: LucideIcon }) {
  return <Mark className="size-3.5" strokeWidth={2} />;
}

/** A tool nobody has written a phrase for: its name, made readable, never raw code. */
function fallbackVerb(name: string): string {
  return name.replace(/_/g, " ");
}

/** The line for one tool call: what he did, the thing he did it to, and an icon for the kind
 *  of work it was. */
function describeTool(item: ActivityItem): {
  verb: string;
  subject: string;
  icon: LucideIcon;
  tone: string;
} {
  // `tool` and `args` are the current shape. `text` is parsed only for events recorded before
  // they existed, so scrolling back through history still reads properly.
  const name = item.tool ?? item.text.split("(")[0];
  const entry = TOOL[name];
  const group = entry ? GROUP[entry.group] : undefined;
  return {
    verb: entry?.verb ?? fallbackVerb(name),
    subject: entry?.of ? (item.args?.[entry.of] ?? "") : "",
    icon: group?.icon ?? Wrench,
    tone: group?.tone ?? "text-muted-foreground/60",
  };
}

type Tick = { head: ActivityItem; items: ActivityItem[]; usage: Usage[] };

/** Group the flat activity stream into ticks — one titled block per self-directed
 * step, with its tool calls and thoughts nested beneath.
 *
 * Token counts are collected separately rather than left in the item list: one arrives
 * per model request, and a step that took twelve rounds would otherwise read as twelve
 * lines about tokens interleaved with what he was actually doing. */
function groupTicks(activity: ActivityItem[]): Tick[] {
  const ticks: Tick[] = [];
  for (const item of activity) {
    if (item.kind === "done") continue; // end-of-tick marker; the next head is the divider
    const meta = KIND[item.kind] ?? KIND.status;
    if (meta.head || ticks.length === 0) ticks.push({ head: item, items: [], usage: [] });
    else if (item.kind === "tokens") {
      if (item.tokens) ticks[ticks.length - 1].usage.push(item.tokens);
    } else ticks[ticks.length - 1].items.push(item);
  }
  return ticks;
}

/** The Mind window: watch Kith think and act on his own, and turn him loose. */
export function MindPanel({
  autonomy,
  width,
  onClose,
}: {
  autonomy: Autonomy;
  width: number;
  onClose: () => void;
}) {
  const { status, activity, start, stop, tick, cancel } = autonomy;
  const running = status?.running ?? false;
  const ticking = status?.ticking ?? false;
  const stopping = status?.stopping ?? false;

  const feedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activity.length]);

  const confirm = useConfirm();

  /**
   * Turning him loose, with the mode question asked out loud.
   *
   * Roaming in ask-mode is a trap: a tick that needs permission records a question and
   * moves on, and at 4am nobody answers it — so he spends the night refusing himself and
   * nothing says why. The server therefore requires at least Auto. Changing someone's
   * safety setting for them without saying so is not a thing to do quietly, so this asks
   * first, in the words of what actually changes.
   */
  const turnLoose = useCallback(async () => {
    const state = await fetchPermissions().catch(() => null);
    if (state?.mode === "ask") {
      const ok = await confirm({
        title: "Switch to Auto and let him roam?",
        description:
          "While roaming he works with nobody watching, so Ask mode would leave him stuck " +
          "on questions until you came back. Auto lets him act outside his folder without " +
          "asking each time — genuinely dangerous things still ask. You can change it back " +
          "from the title bar.",
        confirmLabel: "Switch and roam",
      });
      if (!ok) return;
      await setPermissionMode("auto").catch(() => {});
    }
    await start();
  }, [confirm, start]);

  const ticks = groupTicks(activity);
  const lifetime = (status?.tokensUncached ?? 0) + (status?.tokensOut ?? 0);

  return (
    <aside
      style={{ width }}
      className="flex h-full shrink-0 flex-col border-l border-border/60 bg-background/45 backdrop-blur-md"
    >
      {/* header */}
      <div className="flex items-center gap-2.5 border-b border-border/60 px-4 py-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/70 text-kith">
          <Brain className="size-4" />
        </span>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="flex items-center gap-2 text-sm font-semibold">
            Mind
            {running ? (
              <span className="inline-flex items-center gap-1 text-[11px] font-normal text-roam">
                <span className="size-1.5 animate-pulse rounded-full bg-roam" />
                roaming
              </span>
            ) : null}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {status?.current ||
              (running ? `thinking every ${status?.intervalSeconds}s` : "idle — resting")}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground hover:text-foreground"
          onClick={onClose}
          aria-label="Collapse Mind"
        >
          <PanelRightClose className="size-4" />
        </Button>
      </div>

      {/* controls */}
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        {/* Two independent things, and they were sharing one word. This button decides
            whether there is a NEXT step; the one beside it decides whether THIS step keeps
            going. When both can be stopped, this one says which — a bare "Stop" twice over
            tells you nothing about what you are about to stop. */}
        {running ? (
          <Button size="sm" variant="outline" onClick={() => void stop()}>
            <Square className="size-3.5" />
            {ticking ? "Stop roaming" : "Stop"}
          </Button>
        ) : (
          <Button size="sm" onClick={() => void turnLoose()}>
            <Play className="size-3.5" />
            Let it roam
          </Button>
        )}
        {/* Mid-step this is the only way out, and it used to be greyed out — so watching him
            start down a wrong path meant watching him finish it, up to sixteen rounds later.
            Never disabled while a step runs: that was the whole problem. */}
        {ticking ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void cancel()}
            disabled={stopping}
            title="Stop the step he is taking now. Roaming is left as it is."
          >
            <Square className={cn("size-3.5", stopping && "animate-pulse")} />
            {stopping ? "Stopping…" : "Stop"}
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={() => void tick()}>
            <Zap className="size-3.5" />
            Run once
          </Button>
        )}
        <div className="flex-1" />
        <span
          className="text-[11px] tabular-nums text-muted-foreground"
          title={
            status?.tokensIn
              ? `${formatTokens(status.tokensIn)} shown, ${formatTokens(status.tokensIn - (status.tokensUncached ?? 0))} of it served from cache`
              : undefined
          }
        >
          {[
            ticks.length ? `${ticks.length} step${ticks.length === 1 ? "" : "s"}` : "",
            // Since the server started, and only what a provider actually had to read.
            lifetime ? `${formatTokens(lifetime)} tokens` : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>

      {/* feed */}
      <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {ticks.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-muted/60 text-muted-foreground">
              <Brain className="size-5" />
            </span>
            <p className="max-w-[15rem] text-sm text-muted-foreground">
              His mind is quiet. Hit <span className="font-medium text-foreground">Run once</span>{" "}
              to watch him take a self-directed step, or{" "}
              <span className="font-medium text-foreground">Let it roam</span> to set him loose.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {ticks.map((t, i) => (
              <TickBlock key={i} tick={t} />
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

function TickBlock({ tick }: { tick: Tick }) {
  const meta = KIND[tick.head.kind] ?? KIND.status;
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-3 shadow-sm">
      <div className="flex items-start gap-2.5">
        <span
          className={cn(
            // A tick is the unit you scan for, so its mark is the heaviest thing in
            // the block and readable from the colour alone — blue for work, amber for
            // reflection, indigo for settling.
            "bg-muted/60 ring-border/50 mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md ring-1",
            meta.tone,
          )}
        >
          {meta.icon ? <Glyph icon={meta.icon} /> : null}
        </span>
        <div className="min-w-0 flex-1">
          <span className="break-words text-sm font-medium leading-snug whitespace-pre-wrap">
            {tick.head.text}
          </span>
          <span className="ml-1.5 align-middle text-[10px] tabular-nums text-muted-foreground/70">
            {time(tick.head.at)}
          </span>
        </div>
        {tick.usage.length ? <TickTokens usage={tick.usage} /> : null}
      </div>
      {tick.items.length ? (
        <ul className="mt-2.5 ml-[11px] space-y-2 border-l border-border/60 pl-3.5">
          {tick.items.map((item, i) => {
            const m = KIND[item.kind] ?? KIND.status;
            const call = item.kind === "tool" ? describeTool(item) : null;
            const Icon = call?.icon ?? m.icon;
            return (
              <li key={i} className="flex items-start gap-2 text-xs">
                <span
                  className={cn(
                    "mt-[3px] flex size-3.5 shrink-0 items-center justify-center",
                    call?.tone ?? m.tone,
                  )}
                >
                  {Icon ? (
                    <Glyph icon={Icon} />
                  ) : (
                    // A thought or a status line: a dot, so the column still aligns and the
                    // eye is not drawn to something that is only prose.
                    <span className="bg-current size-1 rounded-full opacity-60" />
                  )}
                </span>
                <span
                  className={cn(
                    "min-w-0 flex-1 leading-relaxed",
                    // A tool call is one row, ellipsised, with the whole value on hover. It
                    // used to wrap, and a long command became three ragged lines broken
                    // mid-word ("test_ta / sk54.py") — which made a step of eight calls a
                    // wall you had to read rather than a list you could scan. Prose still
                    // wraps: a thought is meant to be read.
                    call
                      ? "truncate text-muted-foreground"
                      : cn(
                          "break-words whitespace-pre-wrap",
                          item.kind === "thought"
                            ? "text-foreground/90 italic"
                            : "text-muted-foreground",
                        ),
                  )}
                  title={call ? [call.verb, call.subject].filter(Boolean).join(" ") : undefined}
                >
                  {call ? (
                    <>
                      {call.verb}
                      {call.subject ? (
                        <>
                          {" "}
                          {/* The subject in mono: a path or a command is scannable that way,
                              and obviously a literal rather than part of the sentence. */}
                          <span className="text-foreground/80 font-mono text-[11px]">
                            {call.subject}
                          </span>
                        </>
                      ) : null}
                    </>
                  ) : (
                    item.text
                  )}
                </span>
                <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/50">
                  {time(item.at)}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

/** What a step cost, on its heading.
 *
 * It climbs while the step runs — one model request reports in per round — so this
 * doubles as a sign of life on a tick that is between tool calls and otherwise silent.
 *
 * The per-request numbers are on hover rather than on screen. They were feed lines for
 * one build and it was immediately wrong: a twelve-round step became twelve entries about
 * tokens threaded through what he was actually doing, which is the opposite of the point.
 * The heading is where a total belongs, and the detail is there for whoever wants it. */
function TickTokens({ usage }: { usage: Usage[] }) {
  const total = usage.reduce((sum, one) => sum + realTokens(one), 0);
  const each = usage.map((one, i) => `${i + 1}. ${formatTokens(realTokens(one))}`).join("  ");
  return (
    <span
      className="mt-0.5 shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/50"
      title={`${usage.length} request${usage.length === 1 ? "" : "s"} · ${usageTitle(sumUsage(usage))}\n${each}`}
    >
      {formatTokens(total)}
    </span>
  );
}

function time(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}
