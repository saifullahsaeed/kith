import { useEffect, useRef } from "react";
import { Brain, PanelRightClose, Play, Square, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { useAutonomy } from "@/hooks/use-autonomy";
import type { ActivityItem } from "@/lib/backend/autonomy";

type Autonomy = ReturnType<typeof useAutonomy>;

const KIND: Record<ActivityItem["kind"], { mark: string; tone: string; head?: boolean }> = {
  // "head" kinds begin a tick — each becomes a titled block so you can see every
  // distinct thing he set out to do, and why.
  start: { mark: "▸", tone: "bg-muted text-blue-500", head: true },
  reply: { mark: "↩", tone: "bg-muted text-pink-500", head: true },
  reflect: { mark: "❋", tone: "bg-muted text-kith", head: true },
  curious: { mark: "✦", tone: "bg-muted text-teal-500", head: true },
  consolidate: { mark: "☾", tone: "bg-muted text-indigo-500", head: true },
  breakout: { mark: "⎋", tone: "bg-muted text-orange-500", head: true },
  tool: { mark: "→", tone: "text-muted-foreground" },
  thought: { mark: "·", tone: "text-muted-foreground/60" },
  reminder: { mark: "⏰", tone: "text-orange-500" },
  done: { mark: "·", tone: "text-muted-foreground/50" },
  error: { mark: "⚠", tone: "text-destructive" },
  status: { mark: "•", tone: "text-muted-foreground" },
};

// Turn a raw tool call ("recall(query=sam)") into plain English so you can see
// when he reaches for memory, notes, tasks, the web, etc.
const TOOL_VERB: Record<string, string> = {
  recall: "searched his memory", remember: "saved a memory", forget: "let a memory go",
  set_memory_level: "re-shelved a memory",
  take_note: "wrote a note", read_notes: "read his notes", update_note: "edited a note",
  journal: "journalled", read_journal: "re-read his journal",
  add_task: "set himself a task", list_tasks: "checked his tasks", update_task: "updated a task",
  wonder: "noted a curiosity", list_curiosities: "reviewed his curiosities", update_curiosity: "updated a curiosity",
  set_reminder: "set a reminder", list_reminders: "checked reminders", cancel_reminder: "cleared a reminder",
  schedule: "set a standing job", list_schedules: "checked schedules", cancel_schedule: "cleared a schedule",
  set_mood: "named how he feels", set_identity: "reshaped who he is", note_about_self: "noted something about himself",
  note_about: "noted something about you", recall_person: "recalled who you are",
  reach_out: "reached out to you",
  search_sources: "searched what you gave him", read_source: "read a source you gave him",
  web_search: "searched the web", fetch_url: "read a web page",
  shell: "ran a command", read_file: "read a file", write_file: "wrote a file", list_files: "looked through files",
  create_tool: "built himself a tool", list_tools: "checked his tools", delete_tool: "removed a tool",
};

function humanizeTool(text: string): string {
  const name = text.split("(")[0];
  return TOOL_VERB[name] ?? text;
}

type Tick = { head: ActivityItem; items: ActivityItem[] };

/** Group the flat activity stream into ticks — one titled block per self-directed
 * step, with its tool calls and thoughts nested beneath. */
function groupTicks(activity: ActivityItem[]): Tick[] {
  const ticks: Tick[] = [];
  for (const item of activity) {
    if (item.kind === "done") continue; // end-of-tick marker; the next head is the divider
    const meta = KIND[item.kind] ?? KIND.status;
    if (meta.head || ticks.length === 0) ticks.push({ head: item, items: [] });
    else ticks[ticks.length - 1].items.push(item);
  }
  return ticks;
}

/** The Mind window: watch Kith think and act on his own, and turn him loose. */
export function MindPanel({ autonomy, width, onClose }: { autonomy: Autonomy; width: number; onClose: () => void }) {
  const { status, activity, start, stop, tick } = autonomy;
  const running = status?.running ?? false;

  const feedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activity.length]);

  const ticks = groupTicks(activity);

  return (
    <aside style={{ width }} className="flex h-full shrink-0 flex-col border-l border-border/60 bg-background/45 backdrop-blur-md">
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
                <span className="size-1.5 animate-pulse rounded-full bg-roam" />roaming
              </span>
            ) : null}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {status?.current || (running ? `thinking every ${status?.intervalSeconds}s` : "idle — resting")}
          </div>
        </div>
        <Button variant="ghost" size="icon" className="size-7 text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="Collapse Mind">
          <PanelRightClose className="size-4" />
        </Button>
      </div>

      {/* controls */}
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        {running ? (
          <Button size="sm" variant="outline" onClick={() => void stop()}>
            <Square className="size-3.5" />Stop
          </Button>
        ) : (
          <Button size="sm" onClick={() => void start()}>
            <Play className="size-3.5" />Let it roam
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => void tick()} disabled={status?.ticking}>
          <Zap className={cn("size-3.5", status?.ticking && "animate-pulse")} />Run once
        </Button>
        <div className="flex-1" />
        <span className="text-[11px] tabular-nums text-muted-foreground">
          {ticks.length ? `${ticks.length} step${ticks.length === 1 ? "" : "s"}` : ""}
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
              His mind is quiet. Hit <span className="font-medium text-foreground">Run once</span> to watch him take a
              self-directed step, or <span className="font-medium text-foreground">Let it roam</span> to set him loose.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {ticks.map((t, i) => <TickBlock key={i} tick={t} />)}
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
        <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md text-xs leading-none", meta.tone)}>
          {meta.mark}
        </span>
        <div className="min-w-0 flex-1">
          <span className="break-words text-sm font-medium leading-snug whitespace-pre-wrap">{tick.head.text}</span>
          <span className="ml-1.5 align-middle text-[10px] tabular-nums text-muted-foreground/70">{time(tick.head.at)}</span>
        </div>
      </div>
      {tick.items.length ? (
        <ul className="mt-2.5 ml-[11px] space-y-2 border-l border-border/60 pl-3.5">
          {tick.items.map((item, i) => {
            const m = KIND[item.kind] ?? KIND.status;
            const text = item.kind === "tool" ? humanizeTool(item.text) : item.text;
            return (
              <li key={i} className="flex items-start gap-2 text-xs">
                <span className={cn("shrink-0 leading-relaxed", m.tone)}>{m.mark}</span>
                <span className={cn("min-w-0 flex-1 break-words leading-relaxed whitespace-pre-wrap", item.kind === "thought" ? "text-foreground/90 italic" : "text-muted-foreground")}>
                  {text}
                </span>
                <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/50">{time(item.at)}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function time(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}
