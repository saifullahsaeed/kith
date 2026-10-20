"use client";

/**
 * One of his turns, as the design draws it: a run strip of beats, then the answer.
 *
 * **Why a strip, and why beats.** A real turn is eighteen calls, ten reasoning blocks and four
 * sentences (the median of his last 181). Drawn part by part, at one weight, that is a wall of
 * apparatus with the answer at the bottom of it — and the earlier fix, one collapsed row per
 * chain of calls, still put a dozen rows of "3 commands · thought" between the sentences. The
 * strip says the whole turn in one line (Summary), one line per thing he *said* (Story), or
 * the calls under one of those lines (Detail). The reader picks the depth; nothing is hidden.
 *
 * **What is a beat** is decided in `lib/turn-plan.ts`, once per shape of the message rather than
 * once per token. This file only draws it.
 *
 * **Rendered by index, not by `GroupedParts`.** Grouping follows part order, and his thinking
 * arrives *before* the sentence it produced — so a beat's reasoning would be drawn above its own
 * row. `PartByIndex` lets the layout put each part where it reads, with the library still doing
 * everything per part: tool-UI lookup by name, the part scope `MarkdownText` reads, data renderers.
 */

import {
  MessagePrimitive,
  useAuiState,
  type DataMessagePartComponent,
  type ReasoningMessagePartComponent,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { Check, ChevronDown, CornerDownRight, Lightbulb, X } from "lucide-react";
import { useMemo, useState, type FC, type MouseEvent, type ReactNode } from "react";

import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { PluginRunOutcome } from "@/components/assistant-ui/plugin-outcome";
import {
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/reasoning";
import { TurnStatus, type TurnUsage } from "@/components/assistant-ui/turn-usage";
import { STEER_PART, TURN_TIMING, USAGE_PART, type TurnTiming } from "@/lib/backend/adapter";
import { useProjectRoot } from "@/lib/project-root";
import { callText, describeCall, summariseRun, summaryLine } from "@/lib/tool-language";
import { outcomeOf } from "@/lib/tool-row";
import {
  formatClock,
  formatWorked,
  type Beat,
  type PartKind,
  type Section,
  type TurnPlan,
} from "@/lib/turn-plan";
import { cn } from "@/lib/utils";
import { useTicking } from "@/lib/use-ticking";

import { Spin } from "./spin";

export type PartComponents = MessagePrimitive.PartByIndex.Props["components"];

/* ── the part renderers every index goes through ──────────────────────────────────────── */

/** Below this many characters a reasoning block is a one-line narration, not a chain of
 *  thought, and a disclosure in front of three words is a worse deal than the three words. */
const REASONING_INLINE_MAX = 160;

/**
 * A reasoning block inside a beat: short ones as a quiet italic line with a bulb, long ones as
 * the "Thought" disclosure — open, and following the newest line, while it streams.
 */
export const ThoughtPart: ReasoningMessagePartComponent = ({ text, status }) => {
  const running = status?.type === "running";
  if (!running && text.length <= REASONING_INLINE_MAX) {
    if (!text.trim()) return null;
    return (
      <div className="text-muted-foreground/75 flex items-start gap-2 px-2 py-[3px] text-xs leading-relaxed italic">
        <Lightbulb className="mt-[3px] size-3 shrink-0" aria-hidden />
        <div className="min-w-0 [&_p]:my-0">
          <MarkdownText />
        </div>
      </div>
    );
  }
  return (
    <ReasoningRoot streaming={running} variant="ghost" className="mb-0 py-0.5 ps-1">
      <ReasoningTrigger active={running} />
      <ReasoningContent aria-busy={running}>
        <ReasoningText>
          <MarkdownText />
        </ReasoningText>
      </ReasoningContent>
    </ReasoningRoot>
  );
};

/**
 * Something you said into a turn that was already running, drawn where it landed.
 *
 * Marked as yours and set apart, because it is the one thing inside his turn that he did not
 * write. Placed at the round it actually entered the prompt rather than at the top — a steer
 * that arrived after four tool calls did not influence those four, and showing it above them
 * would claim it did.
 */
export const SteeredIn: FC<{ text: string }> = ({ text }) => (
  <div className="border-kith/30 bg-kith-soft/40 my-1 rounded-lg border px-3 py-2">
    <div className="text-kith mb-0.5 flex items-center gap-1.5 text-[10px] font-semibold tracking-[0.1em] uppercase">
      <CornerDownRight className="size-3" aria-hidden />
      You, mid-turn
    </div>
    <p className="text-foreground/90 text-[13px] wrap-break-word whitespace-pre-wrap">{text}</p>
  </div>
);

const SteerPart: DataMessagePartComponent<{ text?: string }> = ({ data }) => {
  const said = data?.text ?? "";
  return said ? <SteeredIn text={said} /> : null;
};

/** The usage part is metadata about the turn, drawn in the footer — never in the body. */
const Nothing: FC = () => null;

/** The renderers for every part of an assistant message, with the thread's tool fallback. */
export function usePartComponents(
  ToolFallback: ToolCallMessagePartComponent,
): PartComponents {
  return useMemo(
    () => ({
      Text: MarkdownText,
      Reasoning: ThoughtPart,
      tools: { Fallback: ToolFallback },
      data: {
        by_name: { [STEER_PART]: SteerPart, [USAGE_PART]: Nothing },
        // Anybody else's data part keeps the library's registry; with nothing registered it is
        // nothing, which is what the old `part.dataRendererUI` rendered too.
        Fallback: Nothing,
      },
    }),
    [ToolFallback],
  );
}

/* ── small hooks ──────────────────────────────────────────────────────────────────────── */


/** This turn's usage part. Serialised because the selector compares by identity. */
export function useTurnUsage(): TurnUsage | undefined {
  const encoded = useAuiState((s) => {
    const part = s.message.parts.find((one) => one.type === "data" && one.name === USAGE_PART) as
      | { data?: unknown }
      | undefined;
    return part?.data ? JSON.stringify(part.data) : "";
  });
  return useMemo(() => {
    if (!encoded) return undefined;
    try {
      return JSON.parse(encoded) as TurnUsage;
    } catch {
      return undefined;
    }
  }, [encoded]);
}

/** When this window watched the turn start and end — see `TURN_TIMING` for why only then. */
export function useTurnTiming(): TurnTiming | undefined {
  const encoded = useAuiState((s) => {
    const custom = (s.message.metadata as { custom?: Record<string, unknown> } | undefined)?.custom;
    const timing = custom?.[TURN_TIMING] as TurnTiming | undefined;
    return timing?.startedAt ? `${timing.startedAt}:${timing.endedAt ?? ""}` : "";
  });
  return useMemo(() => {
    if (!encoded) return undefined;
    const [start, end] = encoded.split(":");
    return { startedAt: Number(start), ...(end ? { endedAt: Number(end) } : {}) };
  }, [encoded]);
}

interface CallFacts {
  names: string[];
  /** Calls that failed, and whether every one of them was followed by the same tool working. */
  failed: number;
  recovered: boolean;
  /** Calls a permission answer refused. */
  refused: number;
  /** The phrase of the call in flight, present tense — "Editing webapp/static/app.js". */
  running: string;
  /** An `ask` is waiting on the person, and how many questions it holds. */
  waiting: boolean;
  questions: number;
  /** Questions asked by every `ask` in these calls, answered or not. */
  asked: number;
}

/** What a set of calls came to, as one string-compared selector. */
function useCallFacts(calls: readonly number[]): CallFacts {
  const root = useProjectRoot();
  const key = calls.join(",");
  const encoded = useAuiState((s) => {
    const parts = s.message.parts;
    const live = s.message.status?.type === "running";
    const out: CallFacts = {
      names: [],
      failed: 0,
      recovered: true,
      refused: 0,
      running: "",
      waiting: false,
      questions: 0,
      asked: 0,
    };
    for (const index of key ? key.split(",").map(Number) : []) {
      const part = parts[index];
      if (!part || part.type !== "tool-call") continue;
      out.names.push(part.toolName);
      if (part.toolName === "ask") {
        const questions = (part.args as { questions?: unknown[] } | undefined)?.questions;
        out.asked += Array.isArray(questions) ? questions.length : 1;
      }
      if (part.result === undefined) {
        if (!live) continue;
        if (part.toolName === "ask") {
          out.waiting = true;
          const asked = (part.args as { questions?: unknown[] } | undefined)?.questions;
          out.questions += Array.isArray(asked) ? asked.length : 1;
        } else if (!out.running) {
          const call = describeCall(part.toolName, part.args as Record<string, unknown>, root);
          const phrase = [call.doing, call.subject].filter(Boolean).join(" ");
          out.running = summaryLine(phrase.charAt(0).toUpperCase() + phrase.slice(1)) || callText(call);
        }
        continue;
      }
      const outcome = outcomeOf(part.toolName, part.result);
      if (outcome.state === "denied") out.refused += 1;
      if (outcome.state !== "failed") continue;
      out.failed += 1;
      // Recovered when a later call of the same tool, anywhere in the turn, worked — the same
      // rule the row itself uses to wear its "recovered" chip.
      let fixed = false;
      for (let later = index + 1; later < parts.length; later++) {
        const next = parts[later];
        if (next?.type !== "tool-call" || next.toolName !== part.toolName || next.result === undefined) continue;
        fixed = outcomeOf(next.toolName, next.result).state === "done";
        break;
      }
      if (!fixed) out.recovered = false;
    }
    return JSON.stringify(out);
  });
  return useMemo(() => JSON.parse(encoded) as CallFacts, [encoded]);
}

/* ── the turn ─────────────────────────────────────────────────────────────────────────── */

/** Whether a run did any work — one that only thought is drawn as the thought, not as a strip. */
const didWork = (section: Section) =>
  section.kind === "run" && section.beats.some((beat) => beat.calls.length > 0);

/**
 * A turn with work in it, top to bottom: each run of work as a strip, and what he said between
 * them at reading size.
 *
 * One strip per run rather than one per turn, because he answers mid-turn — the pricing, then an
 * `ask`; the architecture, then he starts building — and a single strip had to clamp those replies
 * into two-line beats. The *last* strip that did work speaks for the turn: its state, its clock,
 * how long it worked, how many steps. The ones above it are finished stretches of work and say
 * only what they did.
 */
export function TurnSections({ plan, components }: { plan: TurnPlan; components: PartComponents }) {
  let voice = -1;
  plan.sections.forEach((section, index) => {
    if (didWork(section)) voice = index;
  });
  return (
    <>
      {plan.sections.map((section, index) => {
        if (section.kind === "output") {
          return <AnswerProse key={`output-${section.index}`} index={section.index} components={components} />;
        }
        const first = section.beats[0];
        const key = `run-${first?.head ?? first?.items[0] ?? index}`;
        if (!didWork(section)) {
          // Only thinking, and maybe a sentence: drawn as it is, like a turn with no calls.
          return (
            <div key={key} className="flex flex-col gap-2">
              {section.beats.flatMap((beat) => [
                ...beat.items.map((item) => (
                  <MessagePrimitive.PartByIndex key={item} index={item} components={components} />
                )),
                ...(beat.head !== null
                  ? [<AnswerProse key={beat.head} index={beat.head} components={components} />]
                  : []),
              ])}
            </div>
          );
        }
        return <RunStrip key={key} beats={section.beats} turn={index === voice} components={components} />;
      })}
    </>
  );
}

/* ── the strip ────────────────────────────────────────────────────────────────────────── */

type StripState = "working" | "waiting" | "done" | "stopped" | "failed";

/**
 * A run: a header that says it in one line, and the beats under it.
 *
 * The run that speaks for the turn (`turn`) opens at Story for the turn at the bottom of the thread
 * and folds to Summary for the ones you have scrolled past — the design's "a finished turn you
 * scrolled past" is one line. A run above an answer he gave mid-turn starts folded: the answer
 * under it is the point, and the run is how he got there. Either way your own click takes over for
 * as long as the message is on screen.
 */
export function RunStrip({
  beats,
  turn,
  components,
}: {
  beats: Beat[];
  /** Whether this run carries the turn's own state — the last run that did work. */
  turn: boolean;
  components: PartComponents;
}) {
  const status = useAuiState((s) =>
    s.message.status?.type === "incomplete"
      ? `incomplete:${s.message.status.reason}`
      : (s.message.status?.type ?? "complete"),
  );
  const isLast = useAuiState((s) => s.message.isLast);
  const turnRunning = status === "running" || status === "requires-action";
  /** A run above a later answer is finished even while the turn goes on. */
  const running = turn && turnRunning;
  const allCalls = useMemo(() => beats.flatMap((beat) => beat.calls), [beats]);
  const facts = useCallFacts(allCalls);
  const usage = useTurnUsage();
  const timing = useTurnTiming();
  const now = useTicking(running && Boolean(timing));

  const state: StripState = running
    ? facts.waiting
      ? "waiting"
      : "working"
    : !turn
      ? "done"
      : status === "incomplete:cancelled"
        ? "stopped"
        : status.startsWith("incomplete")
          ? "failed"
          : "done";

  const [asked, setAsked] = useState<boolean | null>(null);
  const expanded = asked ?? (turn && (running || isLast));

  /* An `ask` is said as what it was — questions — rather than as the "message" its tool group
   * counts it as: "1 message" in a run he spent waiting on your answer reads as something he sent. */
  const run = summariseRun(facts.names.filter((name) => name !== "ask"));
  const questions = facts.asked ? `asked ${facts.asked} question${facts.asked === 1 ? "" : "s"}` : "";
  const steps = turn ? (usage?.rounds?.length ?? 0) : 0;
  const worked =
    turn && timing?.endedAt && timing.startedAt ? formatWorked(timing.endedAt - timing.startedAt) : "";
  const clock = running && timing ? formatClock(now - timing.startedAt) : "";

  const summary = [
    state === "waiting" ? questions : "",
    run.text ? `${run.text}${running ? " so far" : ""}` : "",
    state === "waiting" ? "" : questions,
  ]
    .filter(Boolean)
    .join(" · ");

  /* A run that does not speak for the turn is named by what it did: "3 searches · 1 skill". */
  const label = !turn
    ? summary || "Worked"
    : state === "working"
      ? "Working"
      : state === "waiting"
        ? "Waiting for you"
        : state === "stopped"
          ? worked
            ? `Stopped after ${worked}`
            : "Stopped"
          : state === "failed"
            ? "Didn't finish"
            : worked
              ? `Worked ${worked}`
              : "Worked";
  const detail = turn ? summary : "";

  return (
    <section
      aria-label={running ? "What he is doing" : "What he did"}
      data-slot="kith_run-strip"
      data-state={state}
      className={cn(
        "bg-card overflow-hidden rounded-[14px] border",
        state === "working"
          ? "border-roam/30"
          : state === "waiting"
            ? "border-kith/35"
            : state === "failed"
              ? "border-destructive/30"
              : "border-border",
      )}
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setAsked(!expanded)}
        className="text-foreground hover:bg-muted/30 flex h-10 w-full items-center gap-2.5 px-3.5 text-left text-[13px] transition-colors"
      >
        <StateGlyph state={state} />
        <span
          className={cn(
            "font-semibold",
            turn ? "shrink-0" : "min-w-0 truncate",
            state === "waiting" && "text-kith",
            state === "failed" && "text-destructive",
          )}
        >
          {label}
        </span>
        {clock ? (
          <span
            className={cn(
              "shrink-0 tabular-nums",
              state === "waiting" ? "text-muted-foreground" : "text-roam",
            )}
          >
            {clock}
          </span>
        ) : null}
        {detail ? (
          <>
            <span className="text-muted-foreground/75 shrink-0">·</span>
            <span className="text-muted-foreground flex min-w-0 items-center gap-[0.35em] truncate">
              <span className="truncate">{detail}</span>
            </span>
          </>
        ) : null}
        <span className="flex-1" />
        {steps ? (
          <span className="text-muted-foreground/75 shrink-0 text-xs tabular-nums">
            {steps} {steps === 1 ? "step" : "steps"}
          </span>
        ) : null}
        {running ? null : (
          <ChevronDown
            aria-hidden
            className={cn(
              "text-muted-foreground/75 size-3.5 shrink-0 transition-transform duration-200",
              expanded && "rotate-180",
            )}
          />
        )}
      </button>

      {expanded ? (
        <div className="border-border/80 flex flex-col gap-0.5 border-t px-2 pt-2 pb-2.5">
          {beats.map((beat, index) => (
            <div key={beat.head ?? `lead-${beat.items[0] ?? index}`} className="flex flex-col">
              <BeatRow
                beat={beat}
                live={running && index === beats.length - 1}
                components={components}
              />
            </div>
          ))}
          {running && usage ? (
            <div className="ps-2 pt-1 empty:hidden">
              <TurnStatus usage={usage} />
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function StateGlyph({ state }: { state: StripState }) {
  if (state === "working") return <Spin />;
  if (state === "waiting") {
    return (
      <span className="bg-kith-soft text-kith flex size-[18px] shrink-0 items-center justify-center rounded-full text-[11px] font-bold">
        ?
      </span>
    );
  }
  if (state === "stopped") {
    return (
      <span className="bg-muted flex size-[18px] shrink-0 items-center justify-center rounded-full">
        <span className="bg-muted-foreground size-2 rounded-[2px]" />
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className="bg-destructive/15 text-destructive flex size-[18px] shrink-0 items-center justify-center rounded-full">
        <X className="size-[11px]" strokeWidth={3} />
      </span>
    );
  }
  return (
    <span className="bg-roam-soft text-roam flex size-[18px] shrink-0 items-center justify-center rounded-full">
      <Check className="size-[11px]" strokeWidth={3} />
    </span>
  );
}

/* ── a beat ───────────────────────────────────────────────────────────────────────────── */

/** Clicks that land on something of their own — a file chip, a link, a copy button — are that
 *  thing's, not the row's; and a drag that selected text is a selection, not a toggle. */
function isOwnClick(event: MouseEvent<HTMLElement>): boolean {
  const target = event.target as HTMLElement;
  const control = target.closest("a, button, input, textarea, [role='button']");
  if (control && control !== event.currentTarget && event.currentTarget.contains(control)) return true;
  return (window.getSelection()?.toString() ?? "").length > 0;
}

/**
 * One beat: the sentence he said, what it came to, and — opened — the calls that followed it.
 *
 * The live beat of a running turn opens by itself and follows the work (Detail); every other
 * beat sits at Story until you open it, and your choice holds from then on.
 */
function BeatRow({
  beat,
  live,
  components,
}: {
  beat: Beat;
  live: boolean;
  components: PartComponents;
}) {
  const [asked, setAsked] = useState<boolean | null>(null);
  const facts = useCallFacts(beat.calls);
  const root = useProjectRoot();
  const open = asked ?? live;
  const hasBody = beat.items.length > 0;
  const toggle = () => hasBody && setAsked(!open);

  /* A beat with no sentence — work he did before saying anything — is named by that work: the
   * one call's own phrase when there is one, the counts otherwise. */
  const soleCall = useAuiState((s) => {
    if (beat.head !== null || beat.calls.length !== 1) return "";
    const part = s.message.parts[beat.calls[0]!];
    if (part?.type !== "tool-call") return "";
    return summaryLine(callText(describeCall(part.toolName, part.args as Record<string, unknown>, root)));
  });
  const run = summariseRun(facts.names);
  const label = soleCall || run.text || (live ? "Thinking" : "Thought it through");

  const trouble = facts.failed > 0 && !facts.recovered;
  const dot = facts.waiting
    ? "bg-kith"
    : trouble
      ? "bg-destructive"
      : "bg-roam";

  return (
    <div className="flex flex-col" data-slot="kith_beat" data-open={open || undefined}>
      <div
        onClick={(event) => {
          if (!isOwnClick(event)) toggle();
        }}
        className={cn(
          "group/beat flex items-start gap-2.5 rounded-[9px] px-2 py-[7px] text-[13px] leading-[1.45] transition-colors",
          hasBody && "cursor-pointer",
          open ? "bg-muted/70" : hasBody && "hover:bg-muted/35",
          live ? "text-foreground" : "text-foreground/85",
        )}
      >
        <span className="flex h-5 w-3.5 shrink-0 items-center justify-center" aria-hidden>
          <span
            className={cn(
              "rounded-full",
              dot,
              live && !facts.waiting ? "size-2 animate-[kith-breathe_1.4s_ease-in-out_infinite]" : "size-[7px]",
            )}
          />
        </span>
        <div className="min-w-0 flex-1">
          {beat.head !== null ? (
            <BeatSentence index={beat.head} clamp={!open} components={components} />
          ) : (
            <span className="text-foreground/85">{label}</span>
          )}
        </div>
        <span className="text-muted-foreground/75 flex shrink-0 items-center gap-1.5 pt-0.5 text-[11px] whitespace-nowrap">
          <BeatMeta facts={facts} running={live} repeats={beat.head === null ? label : ""} />
          {hasBody ? (
            <button
              type="button"
              data-beat-toggle
              aria-expanded={open}
              aria-label={open ? "Fold these steps" : "Show these steps"}
              onClick={(event) => {
                event.stopPropagation();
                toggle();
              }}
              className="hover:text-foreground -m-1 rounded p-1 transition-colors"
            >
              <ChevronDown
                className={cn("size-3 transition-transform duration-200", open && "rotate-180")}
              />
            </button>
          ) : null}
        </span>
      </div>

      {open && hasBody ? (
        <div className="border-border mt-0.5 mb-1.5 ms-[31px] flex min-w-0 flex-col gap-0.5 border-s ps-3">
          {beat.items.map((index) => (
            <MessagePrimitive.PartByIndex key={index} index={index} components={components} />
          ))}
        </div>
      ) : null}

      {/* A way into a plugin's tab, outside the fold: the point of a plugin call is usually its
          surface, not its return value, and a button behind two clicks is a button nobody finds. */}
      <div className="ms-[31px] empty:hidden">
        <PluginRunOutcome names={facts.names} />
      </div>
    </div>
  );
}

function BeatMeta({
  facts,
  running,
  repeats,
}: {
  facts: CallFacts;
  running: boolean;
  /** The row's own label, when it is already the count — so it is not said twice. */
  repeats: string;
}): ReactNode {
  if (facts.waiting) {
    return (
      <span className="text-kith">
        asked {facts.questions || 1} question{facts.questions === 1 ? "" : "s"}
      </span>
    );
  }
  if (facts.running && running) {
    return (
      <span className="text-roam flex max-w-[16rem] items-center gap-1.5">
        <Spin className="size-2.5 border-[1.5px]" />
        <span className="truncate">{facts.running}</span>
      </span>
    );
  }
  if (facts.failed || facts.refused) {
    return (
      <>
        {facts.failed ? <span className="text-destructive">{facts.failed} failed</span> : null}
        {facts.failed && facts.refused ? <span>·</span> : null}
        {facts.refused ? <span className="text-destructive">{facts.refused} refused</span> : null}
        {facts.failed && facts.recovered ? (
          <>
            <span>·</span>
            <span className="text-roam">recovered</span>
          </>
        ) : null}
      </>
    );
  }
  const run = summariseRun(facts.names.filter((name) => name !== "ask"));
  const text = [
    run.text,
    facts.asked ? `asked ${facts.asked} question${facts.asked === 1 ? "" : "s"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return text && text !== repeats ? <span>{text}</span> : null;
}

/** The sentence at the head of a beat, at the strip's size. Clamped while the beat is folded,
 *  because a sentence is what the row is for and a paragraph pushes the next beat off screen. */
function BeatSentence({
  index,
  clamp,
  components,
}: {
  index: number;
  clamp: boolean;
  components: PartComponents;
}) {
  return (
    <div
      data-slot="aui_assistant-message-content"
      className={cn(
        "wrap-break-word [&_.aui-md]:leading-[1.45] [&_h1]:text-[13px] [&_h2]:text-[13px] [&_h3]:text-[13px] [&_h4]:text-[13px] [&_li]:leading-[1.45] [&_p]:my-0 [&_p]:leading-[1.45] [&_p+p]:mt-1.5",
        // Two lines at most, open or folded. The strip holds the work — the calls and the
        // thinking — and a sentence here is the step's heading, not the place he says things;
        // the answer is drawn below the strip at full size.
        clamp ? "line-clamp-2" : "line-clamp-3",
      )}
    >
      <MessagePrimitive.PartByIndex index={index} components={components} />
    </div>
  );
}

/* ── no strip: a turn that thought and answered ────────────────────────────────────────── */

/**
 * A turn with no calls in it, drawn as it is: the thinking, then the prose. No strip — "1 step"
 * above a reply is apparatus with nothing in it — and while it runs, one live line in place of
 * the header it does not have.
 */
export function FlatTurn({
  plan,
  kinds,
  components,
}: {
  plan: TurnPlan;
  kinds: readonly PartKind[];
  components: PartComponents;
}) {
  const running = useAuiState((s) => s.message.status?.type === "running");
  const usage = useTurnUsage();
  const timing = useTurnTiming();
  const now = useTicking(running && Boolean(timing));
  const lastKind = plan.flat.length ? kinds[plan.flat[plan.flat.length - 1]!] : undefined;
  const prose = (kind: PartKind | undefined) => kind === "text" || kind === "output";
  const status = usage ? <TurnStatus usage={usage} /> : null;

  return (
    <>
      {plan.flat.map((index) =>
        prose(kinds[index]) ? (
          <AnswerProse key={index} index={index} components={components} />
        ) : (
          <MessagePrimitive.PartByIndex key={index} index={index} components={components} />
        ),
      )}
      {running && !prose(lastKind) ? (
        <div className="flex flex-col gap-2">
          <div
            data-slot="aui_assistant-message-indicator"
            aria-label="He is working"
            className="text-muted-foreground flex items-center gap-2 text-[13px]"
          >
            <Spin />
            <span className="text-foreground font-semibold">Working</span>
            {timing ? (
              <span className="text-roam tabular-nums">{formatClock(now - timing.startedAt)}</span>
            ) : null}
          </div>
          {status}
        </div>
      ) : null}
    </>
  );
}

/** The answer: the hero of the turn, at reading size. */
export function AnswerProse({ index, components }: { index: number; components: PartComponents }) {
  return (
    <div
      data-slot="aui_assistant-message-content"
      data-kith-answer
      className="text-foreground/95 px-0.5 pt-0.5 text-[15px] leading-[1.62] wrap-break-word"
    >
      <MessagePrimitive.PartByIndex index={index} components={components} />
    </div>
  );
}
