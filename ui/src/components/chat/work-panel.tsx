import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlarmClock,
  PanelRightClose,
  RotateCw,
  TriangleAlert,
  Undo2,
  type LucideIcon,
} from "lucide-react";

import { WorkingOn } from "@/components/assistant-ui/working-on";
import { Button } from "@/components/ui/button";
import { time } from "@/lib/dates";
import { cn } from "@/lib/utils";
import { formatTokens, realTokens, sumUsage, usageTitle, type Usage } from "@/lib/tokens";
import { describeCall, type DescribedCall } from "@/lib/tool-language";
import type { useActivity } from "@/hooks/use-activity";
import type { ActivityItem } from "@/lib/backend/activity";

type Activity = ReturnType<typeof useActivity>;

const KIND: Record<
  ActivityItem["kind"],
  { icon: LucideIcon | null; tone: string; head?: boolean }
> = {
  // "head" kinds begin a block — each becomes a titled block so you can see every
  // distinct thing he set out to do, and why.
  //
  // Real icons rather than the unicode glyphs that were here (▸ ❋ ✦ ☾ ⎋). Those rendered at
  // whatever weight and baseline the system font felt like, so the column never lined up and
  // the feed read as typographic debris beside the rest of an app that uses lucide
  // throughout. An icon also survives being small, which every one of these is.
  reply: { icon: Undo2, tone: "text-pink-400", head: true },
  // `reflect`, `curious` and `consolidate` had entries here too. Nothing has emitted them
  // since the scheduled inner life was removed, and an icon for a kind that never arrives is
  // a promise the feed cannot keep. The server's live set is start / reply / breakout /
  // tool / thought / reminder / done / error / status / tokens.
  tool: { icon: null, tone: "text-muted-foreground/70" }, // its own icon, by what it touched
  thought: { icon: null, tone: "text-muted-foreground/50" },
  reminder: { icon: AlarmClock, tone: "text-orange-400" },
  done: { icon: null, tone: "text-muted-foreground/40" },
  // Never rendered as a feed line — pulled out in groupTicks and shown as one quiet
  // footer row per block, because a sentence per model request would bury the thinking.
  tokens: { icon: null, tone: "" },
  error: { icon: TriangleAlert, tone: "text-destructive" },
  // The round that dropped and is being sent again. It reads as a warning rather than a note
  // because it is one — the turn is still alive, but it is losing time — and unlike the live
  // line in the message, this one is timestamped and stays, which is the only place afterwards
  // that says the retry happened at all.
  retrying: { icon: RotateCw, tone: "text-amber-500" },
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

type Block = {
  head: ActivityItem;
  items: ActivityItem[];
  usage: Usage[];
  /** Not a step at all — an acknowledgement from the machine, shown as its own line. */
  control?: boolean;
};

/** A status is never a step, so it is never inside one.
 *
 * "working", "resting", "stopping this step" from the controls; "caught up — resting" from a
 * turn that produced nothing; "stopped" from one you interrupted. None of them is work,
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

/** Group the flat activity stream into blocks — one titled block per
 * step, with its tool calls and thoughts nested beneath.
 *
 * Token counts are collected separately rather than left in the item list: one arrives
 * per model request, and a step that took twelve rounds would otherwise read as twelve
 * lines about tokens interleaved with what he was actually doing. */
function groupBlocks(activity: ActivityItem[]): Block[] {
  const blocks: Block[] = [];
  for (const item of activity) {
    if (item.kind === "done") continue; // end-of-block marker; the next head is the divider
    const meta = KIND[item.kind] ?? KIND.status;
    if (isControlLine(item)) {
      blocks.push({ head: item, items: [], usage: [], control: true });
      continue;
    }
    const last = blocks[blocks.length - 1];
    // `!last?.control` matters as much as the head check: without it the first real item
    // after a control line would be nested inside it, which is the same bug pointing the
    // other way.
    if (meta.head || !last || last.control) blocks.push({ head: item, items: [], usage: [] });
    else if (item.kind === "tokens") {
      if (item.tokens) last.usage.push(item.tokens);
    } else last.items.push(item);
  }
  return blocks;
}

/** The Work panel: what he is doing when nobody is talking to him, in the session you
 *  are looking at. Named for what it shows — his memory lives in the control panel. */
export function WorkPanel({
  activity,
  conversationId = "",
  width,
  onClose,
}: {
  activity: Activity;
  /** The conversation on screen. The feed narrows to it, so switching sessions switches
   *  what the panel is about — it used to show every session's work at once, which with two
   *  projects going was one stream of interleaved steps belonging to neither. */
  conversationId?: string;
  width: number;
  onClose: () => void;
}) {
  const { status, activity: lines } = activity;
  // Nothing carries on by itself any more, so the header has no running state to speak
  // for. What it still shows is the feed and what is waiting on you.
  // "Everything" is still available, because watching two projects advance at once is a
  // real thing to want — it is just the wrong default when you are reading one of them.
  const [everything, setEverything] = useState(false);

  const feedRef = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => {
    if (everything || !conversationId) return lines;
    // A line with no conversation is about the machine rather than about one piece of work
    // — a status change, a step run from "Run" with nobody working — so every session
    // shows it. Dropping those would make an idle panel look broken.
    return lines.filter((item) => !item.conversation || item.conversation === conversationId);
  }, [lines, conversationId, everything]);

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length]);

  const blocks = groupBlocks(shown);
  // Steps, not blocks. A control line is drawn in this list but is not work, and counting
  // it made a session that woke up and went straight back to sleep read as thirteen steps.
  const steps = blocks.filter((one) => !one.control).length;
  const lifetime = (status?.tokensUncached ?? 0) + (status?.tokensOut ?? 0);
  const hidden = lines.length - shown.length;

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
          {/* "Work", not "Mind". This panel is rounds, tool calls and token spend — what he
              does when nobody is talking to him. His actual mind (memories, notes, journal)
              is the group of that name in the control panel, and having both called Mind
              meant the word told you nothing about which one you were looking at. */}
          <div className="flex items-center gap-2 text-sm font-semibold">Work</div>
          <div className="truncate text-[11px] text-muted-foreground">
            {steps > 0 ? `${steps} round${steps === 1 ? "" : "s"} this session` : "nothing yet"}
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

      {/* What he is on right now, and how far through it he is.

          It lived above the composer, which put "what is he doing" in the middle of the thing you
          type into — it competed with the composer for the one bit of screen you are always looking
          at, and it is not a message. Here it sits with the round feed, which is the other half of
          the same question, and the checklist ticks over beside the steps that are ticking it. */}
      <WorkingOn conversationId={conversationId} />

      {/* No approval queue, and no review queue.

          Both were the same idea — a count with a Review button that dropped a prompt into the
          thread — and both were removed for the same reason the columns behind them were: a queue
          beside the conversation is a second place to find out something the conversation is
          already telling you. `review` went with the four statuses; this one went once it was
          actually looked at, showing four plans that were all unapprovable (no plan filed on any
          of them) while the real approval happened by typing "ok approved" in chat.

          Approval is a sentence in a conversation. He says the plan is ready and what is in it,
          you say yes or say what to change, and `update_task` refuses `approved` without a plan
          and a checklist — so the enforcement lives where it cannot be missed rather than in a
          badge that can. */}

      {/* controls */}
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        {/* Interrupt and "stop every session" both lived here. Neither has anything to act
            on: a turn is stopped from the thread it is in, which is where you are already
            looking when you want it stopped. */}
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
        {blocks.length === 0 ? (
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
            {/* No button here, and nothing to press. This fills in as a turn runs — yours,
                or one a due reminder started in this conversation. */}
            <p className="max-w-[17rem] text-sm text-muted-foreground">
              {conversationId ? (
                <>Nothing here yet in this conversation. Talk to him, or file a task.</>
              ) : (
                <>Quiet for now. This fills in while he works.</>
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
            {blocks.map((t, i) =>
              t.control ? <ControlLine key={i} item={t.head} /> : <WorkBlock key={i} block={t} />,
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

function WorkBlock({ block }: { block: Block }) {
  const meta = KIND[block.head.kind] ?? KIND.status;
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-3 shadow-sm">
      <div className="flex items-start gap-2.5">
        <span
          className={cn(
            // A block is the unit you scan for, so its mark is the heaviest thing in
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
            {block.head.text}
          </span>
          <span className="ml-1.5 align-middle text-[10px] tabular-nums text-muted-foreground/70">
            {time(block.head.at)}
          </span>
        </div>
        {block.usage.length ? <BlockTokens usage={block.usage} /> : null}
      </div>
      {block.items.length ? (
        <ul className="mt-2.5 ml-[11px] space-y-2 border-l border-border/60 pl-3.5">
          {block.items.map((item, i) => {
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
 * doubles as a sign of life on a turn that is between tool calls and otherwise silent.
 *
 * The per-request numbers are on hover rather than on screen. They were feed lines for
 * one build and it was immediately wrong: a twelve-round step became twelve entries about
 * tokens threaded through what he was actually doing, which is the opposite of the point.
 * The heading is where a total belongs, and the detail is there for whoever wants it. */
function BlockTokens({ usage }: { usage: Usage[] }) {
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

