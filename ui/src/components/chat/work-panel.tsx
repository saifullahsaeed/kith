import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlarmClock,
  CircleDot,
  ClipboardCheck,
  FileCheck2,
  PanelRightClose,
  Square,
  TriangleAlert,
  Undo2,
  Unlock,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatTokens, realTokens, sumUsage, usageTitle, type Usage } from "@/lib/tokens";
import { describeCall, type DescribedCall } from "@/lib/tool-language";
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
  breakout: { icon: Unlock, tone: "text-orange-400", head: true },
  // `reflect`, `curious` and `consolidate` had entries here too. Nothing has emitted them
  // since the scheduled inner life was removed, and an icon for a kind that never arrives is
  // a promise the feed cannot keep. The server's live set is start / reply / breakout /
  // tool / thought / reminder / done / error / status / tokens.
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

/* The phrase-and-icon tables that used to sit here now live in `@/lib/tool-language`, because
   the chat thread describes the same calls and was describing them as "1 tool call" and
   "Used tool: read_skill" while this panel said "read ~/Kith/cv.pdf". One table, one
   vocabulary, both surfaces. `server/tests/test_mind_feed.py` reads that file now. */

/** One feed icon, at the single size this panel uses everywhere. */
function Glyph({ icon: Mark }: { icon: LucideIcon }) {
  return <Mark className="size-3.5" strokeWidth={2} />;
}

/** The line for one tool call: what he did, the thing he did it to, and an icon for the kind
 *  of work it was. */
function describeTool(item: ActivityItem): DescribedCall {
  // `tool` and `args` are the current shape. `text` is parsed only for events recorded before
  // they existed, so scrolling back through history still reads properly.
  return describeCall(item.tool ?? item.text.split("(")[0], item.args);
}

type Tick = {
  head: ActivityItem;
  items: ActivityItem[];
  usage: Usage[];
  /** Not a step at all — an acknowledgement from the machine, shown as its own line. */
  control?: boolean;
};

/** A status is never a step, so it is never inside one.
 *
 * "working", "resting", "stopping this step" from the controls; "caught up — resting" from a
 * tick that found nothing to do; "stopped" from one you interrupted. None of them is work,
 * and every one of them used to fall through to "append to whichever block came last" — so
 * two bare `working` rows appeared inside a chat turn that had nothing to do with them,
 * making the turn look like it did something it did not.
 *
 * Drawing them between the blocks rather than inside one also keeps the step count honest:
 * a session that woke up, found an empty board and went back to sleep did not take a step,
 * and a feed that says it did is a feed you cannot use to answer "what has he been doing". */
function isControlLine(item: ActivityItem): boolean {
  return item.kind === "status";
}

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
    if (isControlLine(item)) {
      ticks.push({ head: item, items: [], usage: [], control: true });
      continue;
    }
    const last = ticks[ticks.length - 1];
    // `!last?.control` matters as much as the head check: without it the first real item
    // after a control line would be nested inside it, which is the same bug pointing the
    // other way.
    if (meta.head || !last || last.control) ticks.push({ head: item, items: [], usage: [] });
    else if (item.kind === "tokens") {
      if (item.tokens) last.usage.push(item.tokens);
    } else last.items.push(item);
  }
  return ticks;
}

/** The Work panel: what he is doing when nobody is talking to him, in the session you
 *  are looking at. Named for what it shows — his memory lives in the control panel. */
export function WorkPanel({
  autonomy,
  conversationId = "",
  width,
  onClose,
  onReview,
  onApprove,
}: {
  autonomy: Autonomy;
  /** The conversation on screen. The feed narrows to it, so switching sessions switches
   *  what the panel is about — it used to show every session's work at once, which with two
   *  projects going was one stream of interleaved steps belonging to neither. */
  conversationId?: string;
  width: number;
  onClose: () => void;
  /** Ask him, in the thread, to check the work a tick handed over. A message rather than a step,
   *  because the point of the review column is that the reviewer is not the tick. */
  onReview: (taskIds: number[]) => void;
  /** Same shape, one step earlier: ask him, in the thread, to walk through a plan waiting for
   *  approval — a real conversation about it, not a bare yes/no button. */
  onApprove: (taskIds: number[]) => void;
}) {
  const { status, activity, stop, cancel } = autonomy;
  const toReview = status?.toReview ?? [];
  const toApprove = status?.toApprove ?? [];
  // How many sessions are carrying on by themselves. The panel's header speaks for the
  // machine, so it asks the plural question; stopping *one* lives on the session bar above
  // the thread, next to the session it belongs to.
  const sessions = (status?.working ?? []).length;
  const working = sessions > 0;
  const ticking = status?.ticking ?? false;
  const stopping = status?.stopping ?? false;
  // "Everything" is still available, because watching two projects advance at once is a
  // real thing to want — it is just the wrong default when you are reading one of them.
  const [everything, setEverything] = useState(false);

  const feedRef = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => {
    if (everything || !conversationId) return activity;
    // A line with no conversation is about the machine rather than about one piece of work
    // — a status change, a step run from "Run" with nobody working — so every session
    // shows it. Dropping those would make an idle panel look broken.
    return activity.filter((item) => !item.conversation || item.conversation === conversationId);
  }, [activity, conversationId, everything]);

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length]);

  const ticks = groupTicks(shown);
  // Steps, not blocks. A control line is drawn in this list but is not work, and counting
  // it made a session that woke up and went straight back to sleep read as thirteen steps.
  const steps = ticks.filter((one) => !one.control).length;
  const lifetime = (status?.tokensUncached ?? 0) + (status?.tokensOut ?? 0);
  const hidden = activity.length - shown.length;

  return (
    <aside
      style={{ width }}
      className="flex h-full shrink-0 flex-col border-l border-border/60 bg-background/45 backdrop-blur-md"
    >
      {/* header */}
      <div className="flex items-center gap-2.5 border-b border-border/60 px-4 py-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/70 text-kith">
          <Activity className="size-4" />
        </span>
        <div className="min-w-0 flex-1 leading-tight">
          {/* "Work", not "Mind". This panel is ticks, tool calls and token spend — what he
              does when nobody is talking to him. His actual mind (memories, notes, journal)
              is the group of that name in the control panel, and having both called Mind
              meant the word told you nothing about which one you were looking at. */}
          <div className="flex items-center gap-2 text-sm font-semibold">
            Work
            {working ? (
              <span className="text-roam inline-flex items-center gap-1 text-[11px] font-normal">
                <span className="bg-roam size-1.5 animate-pulse rounded-full" />
                working
              </span>
            ) : null}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {status?.current ||
              (working
                ? `working — ${(status?.working ?? []).length} session${(status?.working ?? []).length === 1 ? "" : "s"}`
                : "idle — resting")}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground hover:text-foreground"
          onClick={onClose}
          aria-label="Collapse the work panel"
        >
          <PanelRightClose className="size-4" />
        </Button>
      </div>

      {/*
        Finished work waiting to be checked, with the button that gets it checked.

        The `review` column and the instruction telling him how to judge it both existed already —
        and neither was visible anywhere. A tick would finish a task, hand it over, and the only way
        to find out was to open the control panel and go looking. That is the same failure as the
        silently parked tasks: a queue nobody is shown is a queue nobody works.

        It goes here, above Run, because this panel is already the thing beside the chat that says
        what he is doing — and because reviewing is *chat's* job. Chat has the conversation the work
        came out of; a tick reviewing its own output is what the column exists to prevent. So the
        button sends a message into the thread rather than starting a step.
      */}
      {toReview.length > 0 ? (
        <div className="border-border/60 bg-kith-soft/40 flex items-start gap-2.5 border-b px-4 py-2.5">
          <ClipboardCheck className="text-kith mt-0.5 size-3.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium">
              {toReview.length} finished — needs your check
            </p>
            <p className="text-muted-foreground truncate text-[11px]" title={toReview.map((t) => `#${t.id} ${t.goal}`).join("\n")}>
              {toReview.map((t) => t.goal).join(" · ")}
            </p>
          </div>
          <Button
            size="xs"
            variant="outline"
            className="shrink-0"
            onClick={() => onReview(toReview.map((t) => t.id))}
            title="Have him check it here, where the conversation is — not in a step marking its own homework."
          >
            Review
          </Button>
        </div>
      ) : null}

      {/*
        Same pattern, one step earlier: a plan drafted with the planning-a-task skill, waiting
        for a look before any implementation starts. Always entered from chat — see
        `TASK_STATUSES` in domain/enums.py — so anything showing up here is a plan somebody
        actually asked to see, never a tick's own initiative.
      */}
      {toApprove.length > 0 ? (
        <div className="border-border/60 bg-kith-soft/40 flex items-start gap-2.5 border-b px-4 py-2.5">
          <FileCheck2 className="text-kith mt-0.5 size-3.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium">
              {toApprove.length} plan{toApprove.length === 1 ? "" : "s"} — need your approval
            </p>
            <p className="text-muted-foreground truncate text-[11px]" title={toApprove.map((t) => `#${t.id} ${t.goal}`).join("\n")}>
              {toApprove.map((t) => t.goal).join(" · ")}
            </p>
          </div>
          <Button
            size="xs"
            variant="outline"
            className="shrink-0"
            onClick={() => onApprove(toApprove.map((t) => t.id))}
            title="Look at the plan here, in the conversation — so you can push back on it, not just approve or not."
          >
            Review
          </Button>
        </div>
      ) : null}

      {/* controls */}
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        {/* Interrupt only, and only while a step is actually running — chat has no manual
            "run one now" trigger anymore, so this slot has nothing to show the rest of the
            time. "Interrupt" rather than "Stop", because it already shares a screen with two
            things called Stop: this session's, on the session bar, and every session's, next
            to it. Stopping is about whether he continues; interrupting is about the step he
            is inside, which a session already working may well follow with another. */}
        {ticking ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void cancel()}
            disabled={stopping}
            // Never disabled while a step runs, which it used to be — so watching him start
            // down a wrong path meant watching him finish it, up to sixteen rounds later.
            title="Cut short the step he is taking now. A session carrying on takes another."
          >
            <Square className={cn("size-3.5", stopping && "animate-pulse")} />
            {stopping ? "Stopping…" : "Interrupt"}
          </Button>
        ) : null}
        {/* The other scope, and deliberately not a second button competing with the first.
            Stopping one session lives on the session bar above the thread, where the session
            is; this is the "all of them" one, so it names how many and stays out of the way
            until there is something to stop. */}
        {sessions > 0 ? (
          <button
            type="button"
            onClick={() => void stop()}
            className="text-muted-foreground/70 hover:text-destructive text-[11px] underline-offset-2 transition-colors hover:underline"
            title="Stop every session from carrying on by itself. A step already running finishes."
          >
            Stop {sessions === 1 ? "the session" : `all ${sessions} sessions`}
          </button>
        ) : null}
        <div className="flex-1" />
        {/* Only offered when narrowing is actually hiding something, so it is a way out of
            a filter rather than a switch to reason about on an empty feed. */}
        {conversationId && (hidden > 0 || everything) ? (
          <button
            type="button"
            onClick={() => setEverything((all) => !all)}
            className={cn(
              "rounded px-1.5 py-0.5 text-[11px] transition-colors",
              everything
                ? "bg-accent/60 text-foreground"
                : "text-muted-foreground/60 hover:text-foreground",
            )}
            title={
              everything
                ? "Showing every session. Click for this conversation only."
                : `${hidden} line${hidden === 1 ? "" : "s"} from other sessions`
            }
          >
            {everything ? "All sessions" : `+${hidden}`}
          </button>
        ) : null}
        <span
          className="text-[11px] tabular-nums text-muted-foreground"
          title={
            status?.tokensIn
              ? `${formatTokens(status.tokensIn)} shown, ${formatTokens(status.tokensIn - (status.tokensUncached ?? 0))} of it served from cache`
              : undefined
          }
        >
          {[
            steps ? `${steps} step${steps === 1 ? "" : "s"}` : "",
            // Since the server started, and only what a provider actually had to read.
            lifetime ? `${formatTokens(lifetime)} tokens` : "",
          ]
            .filter(Boolean)
            .join(" · ") ||
            // Rather than nothing: with both figures empty this row was one button and a
            // stretch of void, which reads as a bar that failed to load.
            "no steps yet"}
        </span>
      </div>

      {/* feed */}
      <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {ticks.length === 0 ? (
          /* Everything in the middle, together.
             This panel is four hundred pixels wide and it is open by default, so an idle feed
             was most of a column of nothing with one sentence adrift in it. The way out of the
             empty state belongs inside the empty state — and when the reason this conversation
             is quiet is that the work is happening in another one, saying so beats saying
             nothing at all. */
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-muted/60 text-muted-foreground">
              <Activity className="size-5" />
            </span>
            {/* No button here — there is no manual trigger to point at any more. This fills
                in on its own: filing a task or a reminder coming due is what starts a
                session, not anything pressed in this panel. */}
            <p className="max-w-[17rem] text-sm text-muted-foreground">
              {conversationId ? (
                <>Nothing here yet in this conversation. Talk to him, or file a task.</>
              ) : (
                <>Quiet for now. This fills in once something sets a session working.</>
              )}
            </p>
            {hidden > 0 && !everything ? (
              <button
                type="button"
                onClick={() => setEverything(true)}
                className="text-muted-foreground/70 hover:text-foreground text-[11px] underline-offset-2 transition-colors hover:underline"
              >
                He is busy elsewhere — {hidden} line{hidden === 1 ? "" : "s"} from other sessions
              </button>
            ) : null}
          </div>
        ) : (
          <div className="space-y-3">
            {ticks.map((t, i) =>
              t.control ? <ControlLine key={i} item={t.head} /> : <TickBlock key={i} tick={t} />,
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

/** An acknowledgement from the machine, between the steps rather than inside one.
 *
 * Deliberately unlike a step: no card, no icon tile, no token figure. It is a marker in the
 * margin, and looking like one is how you can tell at a glance that nothing was done. */
function ControlLine({ item }: { item: ActivityItem }) {
  return (
    <div className="text-muted-foreground/50 flex items-center gap-2 px-1 text-[11px]">
      <span className="bg-border/60 h-px flex-1" />
      <span>{item.text}</span>
      <span className="tabular-nums">{time(item.at)}</span>
      <span className="bg-border/60 h-px w-3" />
    </div>
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
