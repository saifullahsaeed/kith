import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowRight,
  Braces,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Filter,
  Loader2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { fetchContextMessage, type RoleTotal, type Sent, type SentMessage } from "@/lib/backend";
import { copyText } from "@/lib/files";
import { formatCompact, formatTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";

/**
 * The prompt itself — every message, in order, and the one you clicked, whole.
 *
 * The context screen answered in categories first: eleven lines, "code he has read: 260k". That
 * is the ledger's shape and not the question's. Nobody opens this to learn that a category is
 * large; they open it to see what is actually being sent, and to read the message that looks
 * wrong. A category cannot say which messages it is made of, in what order, or what any of them
 * says.
 *
 * The list carries previews only. One real conversation here is 2.36M tokens of transcript, so
 * shipping every message's text in order to draw a list of previews would be a several-megabyte
 * response for a screen that shows one message at a time — the whole text is fetched for the row
 * you select, and only that one.
 */

/** Each role's own colour, so a row is identifiable before it is read. Deliberately the app's own
 *  tokens rather than new hues: these mark a handful of rows, not a categorical scale, and the
 *  context bar above already owns the five-colour palette. */
const ROLE_TONE: Record<string, string> = {
  system: "text-kith bg-kith-soft",
  user: "text-roam bg-roam-soft",
  assistant: "text-foreground/80 bg-muted",
  tool: "text-muted-foreground bg-muted/60",
};

export function MessageExplorer({
  conversationId,
  sent,
}: {
  conversationId: string;
  sent: Sent;
}) {
  const messages = sent.messages;
  // Open on the newest question, not on message 1. Message 1 is the persona — byte-identical on
  // every request since the app was installed, and the one message nobody opens this screen to
  // read. What changed is at the other end.
  const [selected, setSelected] = useState(
    () =>
      [...messages].reverse().find((message) => message.role === "user")?.index ??
      messages[messages.length - 1]?.index ??
      1,
  );
  const [onlyNew, setOnlyNew] = useState(false);
  const current = messages.find((message) => message.index === selected) ?? messages[0];
  const shown = onlyNew ? messages.filter((message) => message.change !== "kept") : messages;

  if (messages.length === 0) return null;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
        <div>
          <h2 className="text-muted-foreground/60 text-[11px] font-semibold tracking-[0.14em] uppercase">
            The prompt
          </h2>
          <p className="text-muted-foreground/50 mt-1 text-[11px]">
            {messages.length} message{messages.length === 1 ? "" : "s"} ·{" "}
            {formatTokens(sent.tokens)} tokens — what the next turn sends, in order
          </p>
        </div>
        <div className="flex-1" />
        {/* Only offered when there is something to filter to. A toggle that can only ever show
            the same list is a control that teaches you it does nothing. */}
        {sent.hasPrevious && sent.addedTokens > 0 ? (
          <Button
            variant="ghost"
            size="xs"
            aria-pressed={onlyNew}
            onClick={() => {
              const next = !onlyNew;
              setOnlyNew(next);
              // Carry the selection into the filtered list. Without this, filtering to the new
              // messages while an old one is selected leaves the list with nothing highlighted
              // and the pane showing something that is no longer on screen.
              if (next && current && current.change === "kept") {
                const first = messages.find((message) => message.change !== "kept");
                if (first) setSelected(first.index);
              }
            }}
            className={cn(
              "h-6 gap-1.5 px-2 text-[11px]",
              onlyNew
                ? "bg-accent text-foreground"
                : "text-muted-foreground/70 hover:text-foreground",
            )}
          >
            <Filter className="size-3" />
            {onlyNew ? "Showing new only" : "Only what is new"}
          </Button>
        ) : null}
      </div>

      <SinceLastTurn sent={sent} />

      <div className="flex flex-col gap-1.5">
        <PerMessageBar messages={messages} onPick={setSelected} selected={selected} />
        {/* The bar is turns and the row under it is roles, and without a word for each the
            second reads as the first's legend and its colours look wrong. */}
        <p className="text-muted-foreground/40 text-[10px]">
          every turn in the conversation, oldest to newest, sized by what it costs
        </p>
      </div>

      <div className="flex flex-col gap-1.5">
        <p className="text-muted-foreground/40 text-[10px]">and what it is made of, by role</p>
        <ByRole rows={sent.byRole} total={sent.tokens} />
      </div>

      {/* Side by side on a wide window, stacked on a narrow one — the list is a table of
          contents and the pane is what you came to read, so neither wants a scrollbar of its
          own on a phone-width column.
          The list column is fixed rather than fractional: it holds a role chip, a token count
          and two lines of preview, and none of that gets better with more room, whereas the
          pane is holding a seventeen-thousand-character system prompt and every pixel helps.
          Both are short when stacked and tall only side by side. At 62vh each, a stacked layout
          put the pane entirely below the fold — you would pick a message and the thing you
          picked it to read was off-screen, which is the one thing this screen must not do. */}
      <div className="grid min-h-0 gap-4 lg:grid-cols-[24rem_minmax(0,1fr)]">
        <MessageList messages={shown} selected={selected} onSelect={setSelected} />
        {current ? (
          <MessageBody
            conversationId={conversationId}
            message={current}
            call={callBehind(messages, current)}
            count={messages.length}
            onStep={(by) =>
              setSelected((was) => Math.min(messages.length, Math.max(1, was + by)))
            }
          />
        ) : null}
      </div>
    </section>
  );
}

/**
 * What the last turn did to the context, in one line.
 *
 * The question people actually have about context is not "what is in it" but "why does it keep
 * growing, and what is doing it" — and that is only answerable by comparison. One question
 * produces an assistant message, a tool call and a tool result, and all three are in every
 * request from then on. Seeing that once explains more than any total does.
 *
 * Both sides are computed from the transcript rather than stored: the prompt is a function of the
 * conversation, so the last turn's prompt is that function over the conversation as it stood when
 * that turn began. Neither side costs a model call.
 */
function SinceLastTurn({ sent }: { sent: Sent }) {
  if (!sent.hasPrevious) {
    return (
      <p className="text-muted-foreground/50 text-[11px]">
        First turn — there is nothing before this to compare against.
      </p>
    );
  }
  const growth = sent.tokens - sent.previousTokens;
  const liveDelta =
    sent.messages.reduce((sum, m) => sum + (m.live ? m.tokens : 0), 0) - sent.previousLiveTokens;

  return (
    <div className="border-border/60 bg-card/30 flex flex-wrap items-center gap-x-2 gap-y-2 rounded-lg border px-4 py-2.5 text-[12px]">
      <span className="text-muted-foreground/60">Last turn sent</span>
      <span className="font-mono tabular-nums">{formatTokens(sent.previousTokens)}</span>
      <ArrowRight aria-hidden className="text-muted-foreground/30 size-3.5" />
      <span className="text-muted-foreground/60">next sends</span>
      <span className="font-mono tabular-nums">{formatTokens(sent.tokens)}</span>
      <span
        className={cn(
          "rounded px-1.5 py-0.5 font-mono text-[11px] tabular-nums",
          growth > 0 ? "bg-kith-soft text-kith" : "bg-muted text-muted-foreground",
        )}
      >
        {growth >= 0 ? "+" : "−"}
        {formatTokens(Math.abs(growth))}
      </span>

      <span className="text-muted-foreground/20">·</span>

      {/* The three things that move a prompt between turns, named. Anything that does not apply
          is left out rather than shown as a zero — "0 dropped" reads as a measurement, and on a
          conversation that has never folded it is just noise. */}
      {sent.addedTokens > 0 ? (
        <span className="text-muted-foreground">
          <span className="font-mono tabular-nums">+{formatCompact(sent.addedTokens)}</span> from
          the last turn&rsquo;s own work
        </span>
      ) : null}
      {sent.droppedTokens > 0 ? (
        <span className="text-muted-foreground">
          <span className="font-mono tabular-nums">−{formatCompact(sent.droppedTokens)}</span>{" "}
          folded away
        </span>
      ) : null}
      {liveDelta !== 0 ? (
        <span className="text-muted-foreground/60">
          <span className="font-mono tabular-nums">
            {liveDelta > 0 ? "+" : "−"}
            {formatCompact(Math.abs(liveDelta))}
          </span>{" "}
          in the block rewritten each turn
        </span>
      ) : null}
    </div>
  );
}

/** What the last turn carried and this one will not — the half of context management that is not
 *  growth. Only rendered when a fold or a trim actually removed something. */
export function DroppedSince({ sent }: { sent: Sent }) {
  if (sent.dropped.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <div>
        <h2 className="text-muted-foreground/60 text-[11px] font-semibold tracking-[0.14em] uppercase">
          Left behind since the last turn
        </h2>
        <p className="text-muted-foreground/50 mt-1 text-[11px]">
          {sent.dropped.length} message{sent.dropped.length === 1 ? "" : "s"} ·{" "}
          {formatTokens(sent.droppedTokens)} tokens the last prompt carried and the next will not —
          folded into the brief, or trimmed for age.
        </p>
      </div>
      <ul className="border-border/60 flex max-h-72 flex-col overflow-y-auto rounded-lg border">
        {sent.dropped.map((message, index) => (
          <li
            key={`${message.role}:${message.tool}:${index}`}
            className="border-border/40 flex items-start gap-2 border-b px-2.5 py-2 last:border-b-0"
          >
            <span
              className={cn(
                "shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] line-through opacity-60",
                ROLE_TONE[message.role] ?? "bg-muted text-muted-foreground",
              )}
            >
              {message.tool || message.role}
            </span>
            <span className="text-muted-foreground/50 min-w-0 flex-1 truncate text-[11px]">
              {message.preview || "—"}
            </span>
            <span className="text-muted-foreground/40 shrink-0 font-mono text-[10px] tabular-nums">
              {formatCompact(message.tokens)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * One bar per message, width proportional to what it costs.
 *
 * The chart OpenRouter draws, and the reason it is worth drawing: a prompt's problem is almost
 * never spread evenly. One system block or one tool result is usually most of it, and that is
 * visible here in a glance and invisible in a list of forty rows.
 *
 * Minimum width of a pixel, so a 12-token message is still a target rather than a rounding error
 * that silently cannot be clicked.
 */
function PerMessageBar({
  messages,
  selected,
  onPick,
}: {
  messages: SentMessage[];
  selected: number;
  onPick: (index: number) => void;
}) {
  // One segment per *turn*, not per message. Per message it was 1,502 marks across a 1,900px
  // strip — a barcode, unreadable and effectively unclickable at roughly one pixel each. Per
  // turn it is forty, which is a shape you can read and a target you can hit, and it is the same
  // unit the list below is grouped by, so pointing at a block in one finds the block in the
  // other. Left in prompt order here — this is the one place the whole conversation is visible
  // at once, and a timeline reads left to right.
  const turns = useMemo(() => intoTurns(messages).slice().reverse(), [messages]);
  const total = turns.reduce((sum, turn) => sum + turn.tokens, 0) || 1;

  return (
    <div className="flex h-10 w-full gap-px overflow-hidden rounded-md">
      {turns.map((turn) => {
        const holds = turn.messages.some((message) => message.index === selected);
        return (
          <button
            key={turn.key}
            type="button"
            onClick={() => onPick(turn.messages[0].index)}
            title={`${turn.kind === "turn" ? `Turn ${turn.turnNumber}` : turn.label} — ${formatTokens(turn.tokens)} tokens across ${turn.messages.length} message${turn.messages.length === 1 ? "" : "s"}${turn.kind === "turn" ? `\n${turn.label}` : ""}`}
            aria-label={`${turn.kind === "turn" ? `Turn ${turn.turnNumber}` : turn.label}, ${turn.tokens} tokens, ${turn.messages.length} messages`}
            style={{ width: `${Math.max((turn.tokens / total) * 100, 0.4)}%` }}
            className={cn(
              "min-w-px transition-opacity hover:opacity-100",
              turn.kind === "live"
                ? "bg-muted-foreground/30"
                : turn.kind === "preamble"
                  ? "bg-kith"
                  : "bg-foreground/70",
              turn.hasNew && "bg-roam",
              holds ? "ring-foreground/70 z-10 opacity-100 ring-2" : "opacity-60",
            )}
          />
        );
      })}
    </div>
  );
}

/**
 * Four marks, and they have to be told apart.
 *
 * Two accents and two neutrals rather than four hues: the context bar higher up the page owns
 * the five-colour categorical palette, and a second scale in the same colours a few hundred
 * pixels below it would make "orange" mean two different things on one screen.
 *
 * The neutrals are separated by *lightness*, not by hue, and by more than looks necessary on
 * paper. The first version had `foreground/60` against `muted-foreground/50` — legibly different
 * as swatches, and indistinguishable once they were 8px-wide marks on a dark surface. Those two
 * roles are 81% of this bar between them, so a bar that cannot separate them is a bar that shows
 * nothing about the split that matters.
 */
const BAR_TONE: Record<string, string> = {
  system: "bg-kith",
  user: "bg-roam",
  assistant: "bg-foreground/80",
  tool: "bg-muted-foreground/30",
};

/** Which role the prompt is actually made of. "Why is this so big" is almost always one row. */
function ByRole({ rows, total }: { rows: RoleTotal[]; total: number }) {
  if (rows.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-1.5">
      {rows.map((row) => (
        <li key={row.role} className="flex items-baseline gap-1.5 text-[11px]">
          <span
            aria-hidden
            className={cn("size-2 shrink-0 rounded-[2px]", BAR_TONE[row.role] ?? "bg-muted")}
          />
          <span className="text-muted-foreground">{row.role}</span>
          {/* A decimal below one percent. `user` on a long coding conversation is a couple of
              hundred tokens against sixty thousand — real, and rounded to a flat "0%" it reads
              as "none", which is a different claim. */}
          <span className="font-mono tabular-nums">
            {row.share >= 0.01 ? Math.round(row.share * 100) : (row.share * 100).toFixed(1)}%
          </span>
          <span className="text-muted-foreground/50 font-mono tabular-nums">
            {formatCompact(row.tokens)} · {row.count}
          </span>
        </li>
      ))}
      <li className="text-muted-foreground/40 ms-auto font-mono text-[11px] tabular-nums">
        {formatTokens(total)} total
      </li>
    </ul>
  );
}

/**
 * One turn's worth of the prompt: the question, and everything answering it produced.
 *
 * A turn is the unit people think in — "what did asking that cost me" — and the prompt has no
 * other structure. 1,518 flat rows is not a list anyone navigates; 76 turns is.
 */
interface TurnGroup {
  key: string;
  kind: "live" | "turn" | "preamble";
  /** The question that started it, or what the block is when it is not a turn. */
  label: string;
  turnNumber: number;
  messages: SentMessage[];
  tokens: number;
  hasNew: boolean;
}

/**
 * Cut the prompt into turns, newest first.
 *
 * **Newest first because that is the priority order.** Sent order puts the persona at the top,
 * which is the one message nobody opened this screen to read — it is identical on every request
 * and has been since the app was installed. What changed is at the far end, past 1,500 rows of
 * scrolling.
 *
 * Three kinds of group, because the prompt genuinely has three parts. A turn starts at a `user`
 * message and runs to the next one. Anything before the first user message is the preamble — the
 * persona, and the folded brief if there is one — which is not a turn and never was. The live
 * block at the tail is its own thing for the same reason: it belongs to no turn, and it is
 * rewritten on every one.
 */
function intoTurns(messages: SentMessage[]): TurnGroup[] {
  const groups: TurnGroup[] = [];
  let current: SentMessage[] = [];
  let turns = 0;

  const flush = (kind: TurnGroup["kind"], label: string, turnNumber: number) => {
    if (current.length === 0) return;
    groups.push({
      key: `${kind}-${turnNumber}-${current[0].index}`,
      kind,
      label,
      turnNumber,
      messages: current,
      tokens: current.reduce((sum, message) => sum + message.tokens, 0),
      hasNew: current.some((message) => message.change === "added"),
    });
    current = [];
  };

  for (const message of messages) {
    if (message.live) {
      flush(turns === 0 ? "preamble" : "turn", labelOf(current), turns);
      current = [message];
      flush("live", "Right now", turns + 1);
      continue;
    }
    if (message.role === "user") {
      flush(turns === 0 ? "preamble" : "turn", labelOf(current), turns);
      turns += 1;
    }
    current.push(message);
  }
  flush(turns === 0 ? "preamble" : "turn", labelOf(current), turns);

  return groups.reverse();
}

/**
 * The call that produced this result, if it is one.
 *
 * The pane is handed a single message, but a tool result on its own is half the story: the
 * command that asked for it is a separate message, sent and billed, and reading a result without
 * it is reading an answer with the question torn off.
 */
function callBehind(messages: SentMessage[], message: SentMessage): SentMessage | undefined {
  if (message.role !== "tool") return undefined;
  const before = messages[messages.findIndex((one) => one.index === message.index) - 1];
  return before?.calls.length === 1 && before.calls[0].name === message.tool ? before : undefined;
}

/** A turn is named by the question that started it — the only part of it a person recognises. */
function labelOf(messages: SentMessage[]): string {
  const asked = messages.find((message) => message.role === "user");
  if (asked) return asked.preview || "(empty)";
  return "Before the conversation";
}

/**
 * One line of the list: a message, or a tool call and its result together.
 *
 * `lead` is what selecting the row opens — for a pair that is the *result*, because the result is
 * the part with anything in it.
 */
interface Row {
  key: string;
  lead: SentMessage;
  /** The assistant turn that carried the call, when this row is a pair. */
  call?: SentMessage;
  tokens: number;
  messages: SentMessage[];
}

/**
 * Fold each tool call into its result.
 *
 * A tool use is two messages in the prompt and one thing that happened. The first is an assistant
 * turn with no content at all — it exists only to carry `tool_calls` — so on its own it renders as
 * a row saying "calls check_process" worth 37 tokens, which is the envelope rather than the thing.
 * On a conversation that is 60% tool results by weight and 1,004 tool messages by count, that
 * doubles the length of the list to say nothing twice.
 *
 * **Safe because the pairing is structural, not guessed.** `conversations.full_messages` rebuilds
 * every stored result as exactly this pair, in this order, so the call is always the message
 * immediately before its result. Matched on the tool's name as well as the shape, so a row is only
 * ever folded when both halves agree about which tool ran.
 *
 * Nothing is hidden: the row carries both messages, its token count is the pair's real combined
 * cost, and the turn header above still counts messages rather than rows.
 */
function intoRows(messages: SentMessage[]): Row[] {
  const rows: Row[] = [];
  for (let at = 0; at < messages.length; at += 1) {
    const message = messages[at];
    const next = messages[at + 1];
    // Matched on the call's own name, not on its preview text. `calls a, b` — one assistant turn
    // carrying two calls answered by two results — is left alone: folding the first result into
    // it would silently attach the row to the wrong call.
    const single =
      message.calls.length === 1 && next?.role === "tool" && message.calls[0].name === next.tool;
    if (single) {
      rows.push({
        key: `pair-${message.index}`,
        lead: next,
        call: message,
        tokens: message.tokens + next.tokens,
        messages: [message, next],
      });
      at += 1;
      continue;
    }
    rows.push({
      key: `one-${message.index}`,
      lead: message,
      tokens: message.tokens,
      messages: [message],
    });
  }
  return rows;
}

function MessageList({
  messages,
  selected,
  onSelect,
}: {
  messages: SentMessage[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  const row = useRef<HTMLButtonElement | null>(null);
  const all = useMemo(() => intoTurns(messages), [messages]);
  // Pinned out of the scrolling list rather than sitting at the top of it. It is present on every
  // single request and it is one of the largest things in the prompt, so it is the one row you
  // want a fixed place for — scrolling to turn 12 should not mean losing sight of the block that
  // is rewritten under you every turn.
  const live = all.find((group) => group.kind === "live");
  const groups = useMemo(() => all.filter((group) => group.kind !== "live"), [all]);

  // Open the newest turn, and whichever one holds the selection. Everything else starts shut:
  // the point of grouping 1,518 rows is that you see 76 headings, not 1,518 rows with dividers.
  const holding = groups.find((group) =>
    group.messages.some((message) => message.index === selected),
  );
  const [open, setOpen] = useState<Set<string>>(
    () => new Set([groups[0]?.key, holding?.key].filter(Boolean) as string[]),
  );
  useEffect(() => {
    if (holding && !open.has(holding.key)) {
      setOpen((was) => new Set(was).add(holding.key));
    }
    // `open` is deliberately not a dependency — this only ever adds, and watching it would
    // re-run on every toggle to conclude there is nothing to add.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holding?.key]);

  // Follow the selection. The bar above and the pane's own prev/next both move it, and without
  // this the list simply stayed where it was — click the 150th segment of the bar and the row it
  // selected is six hundred pixels below the fold, so the list is showing you message 3 while the
  // pane shows message 150. "nearest" rather than "center" so clicking a visible row never yanks
  // the list out from under the pointer.
  useEffect(() => {
    row.current?.scrollIntoView({ block: "nearest" });
  }, [selected, open]);

  /** Arrow keys walk the rows that are actually on screen, in the order they are on screen —
   *  which is newest first, and skips whatever is collapsed. Stepping by message index would
   *  move the selection backwards through a list that reads forwards. */
  const visible = groups.flatMap((group) => (open.has(group.key) ? intoRows(group.messages) : []));
  const onKeyDown = (event: KeyboardEvent) => {
    const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    let target: Row | undefined;
    if (step) {
      const at = visible.findIndex((one) => one.messages.some((m) => m.index === selected));
      target = visible[Math.min(visible.length - 1, Math.max(0, at + step))];
    } else if (event.key === "Home") target = visible[0];
    else if (event.key === "End") target = visible[visible.length - 1];
    else return;
    event.preventDefault();
    if (target) onSelect(target.lead.index);
    // Move focus with the selection, or the next arrow press comes from the old row.
    window.requestAnimationFrame(() => row.current?.focus());
  };

  return (
    <div className="flex min-h-0 flex-col gap-2">
      {live ? (
        <ul className="border-border/60 shrink-0 overflow-hidden rounded-lg border">
          {intoRows(live.messages).map((one) => renderRow(one, "Rewritten before every request"))}
        </ul>
      ) : null}

      <ul
        onKeyDown={onKeyDown}
        className="border-border/60 h-80 lg:h-[62vh] lg:min-h-104 overflow-y-auto rounded-lg border"
      >
        {groups.map((group) => (
          <li key={group.key}>
            <TurnHeader
              group={group}
              open={open.has(group.key)}
              onToggle={() =>
                setOpen((was) => {
                  const next = new Set(was);
                  if (next.has(group.key)) next.delete(group.key);
                  else next.add(group.key);
                  return next;
                })
              }
            />
            {open.has(group.key) ? (
              <ul>{intoRows(group.messages).map((one) => renderRow(one))}</ul>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );

  function renderRow(one: Row, note?: string) {
    const { lead, call } = one;
    const holds = one.messages.some((message) => message.index === selected);
    const command = call?.calls[0]?.args;
    return (
      <li key={one.key}>
        <button
          type="button"
          ref={holds ? row : undefined}
          onClick={() => onSelect(lead.index)}
          aria-current={holds}
          className={cn(
            "border-border/40 relative flex w-full flex-col gap-1 border-b py-2 pe-2.5 ps-3 text-left transition-colors last:border-b-0",
            holds ? "bg-accent/60" : "hover:bg-accent/30",
          )}
        >
          {/* New reads as an edge, not a chip. It applies to whole runs of consecutive rows, and
              a badge repeated down twenty of them is louder than the rows it is marking. */}
          <span
            aria-hidden
            className={cn(
              "absolute inset-y-0 inset-s-0 w-0.5",
              lead.change === "added" ? "bg-kith" : "bg-transparent",
            )}
          />
          <span className="flex items-baseline gap-1.5">
            <span
              className={cn(
                "shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px]",
                ROLE_TONE[lead.role] ?? "bg-muted text-muted-foreground",
              )}
            >
              {lead.tool || lead.role}
            </span>
            {lead.change === "rewritten" ? (
              <span
                className="text-muted-foreground/40 shrink-0 text-[9px] tracking-wide uppercase"
                title="Rewritten before every request — which is why the tail of a prompt is never cached"
              >
                rewritten
              </span>
            ) : null}
            <span className="flex-1" />
            {/* Both numbers, together and never wrapping. The index span used to break across two
                lines in its own narrow column, which made every tool row two lines taller than it
                needed to be. */}
            <span
              className="text-muted-foreground/25 shrink-0 font-mono text-[10px] whitespace-nowrap tabular-nums"
              title={call ? `Messages ${call.index} and ${lead.index}` : `Message ${lead.index}`}
            >
              {call ? `${call.index}–${lead.index}` : lead.index}
            </span>
            <span
              className="text-muted-foreground/50 w-10 shrink-0 text-end font-mono text-[10px] tabular-nums"
              title={
                call
                  ? `${formatTokens(one.tokens)} tokens — ${formatTokens(call.tokens)} for the call, ${formatTokens(lead.tokens)} for the result`
                  : `${formatTokens(one.tokens)} tokens`
              }
            >
              {formatCompact(one.tokens)}
            </span>
          </span>

          {note ? (
            <span className="text-muted-foreground/40 text-[10px] italic">{note}</span>
          ) : null}

          {/* The command, on its own line. It is what the row *is* — "grep" names the tool and
              the pattern names the call — and it is sent, so a screen about what gets sent has to
              show it. The first version folded the pair and printed only the result, which is
              exactly the thing this screen exists not to do. */}
          {command ? (
            <span className="text-foreground/70 truncate font-mono text-[10.5px] leading-snug">
              {command}
            </span>
          ) : null}
          <span
            className={cn(
              "line-clamp-2 text-[11px] leading-snug",
              command ? "text-muted-foreground/45" : "text-muted-foreground/70",
            )}
          >
            {lead.preview || "—"}
          </span>
        </button>
      </li>
    );
  }
}

/**
 * The heading you actually navigate by: the question, what answering it cost, and whether any of
 * it is new. Sticky, so scrolling inside a long turn never loses which turn you are in.
 */
function TurnHeader({
  group,
  open,
  onToggle,
}: {
  group: TurnGroup;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="bg-card/95 border-border/60 hover:bg-accent/40 sticky top-0 z-10 flex w-full items-center gap-2 border-b px-2.5 py-2 text-left backdrop-blur transition-colors"
    >
      <ChevronRight
        aria-hidden
        className={cn(
          "text-muted-foreground/40 size-3 shrink-0 transition-transform",
          open && "rotate-90",
        )}
      />
      <span className="text-muted-foreground/50 shrink-0 font-mono text-[10px] tabular-nums">
        {group.kind === "turn" ? `turn ${group.turnNumber}` : group.kind === "live" ? "now" : "base"}
      </span>
      <span
        className={cn(
          "min-w-0 flex-1 truncate text-[11px]",
          group.kind === "turn" ? "text-foreground/85" : "text-muted-foreground/60 italic",
        )}
      >
        {group.label}
      </span>
      {group.hasNew ? (
        <span className="bg-kith-soft text-kith shrink-0 rounded px-1 py-0.5 text-[9px] font-medium tracking-wide uppercase">
          new
        </span>
      ) : null}
      {/* Messages, not rows. A tool use is folded into one row below but is two messages in the
          prompt, and this column is about the prompt. */}
      <span
        className="text-muted-foreground/30 shrink-0 font-mono text-[10px] tabular-nums"
        title={`${group.messages.length} message${group.messages.length === 1 ? "" : "s"} in the prompt`}
      >
        {group.messages.length}
      </span>
      <span
        className="text-muted-foreground/50 w-12 shrink-0 text-end font-mono text-[10px] tabular-nums"
        title={`${formatTokens(group.tokens)} tokens`}
      >
        {formatCompact(group.tokens)}
      </span>
    </button>
  );
}

/**
 * How big a result may be before it is left alone.
 *
 * Pretty-printing walks the whole value and builds a much longer string, and a quarter-megabyte
 * tool result rendered to a few thousand DOM lines janks the pane for no benefit — nobody reads
 * the ten-thousandth line of a JSON blob.
 */
const PRETTY_CEILING = 200_000;

/**
 * A tool result, laid out to be read.
 *
 * **The trade this makes, stated plainly.** Everything else on this screen is byte-exact — the
 * point of it is that what you see is what gets sent. This is the one place that is not, so it is
 * a mode rather than a replacement: `Raw` is one click away, it is labelled while it is on, and
 * Copy always copies what was actually sent rather than what is on screen.
 *
 * Worth the trade because the alternative is unreadable. A shell result arrives as
 * `{"ok": true, "result": {"exitCode": 0, "output": "## main...\n M .kith/..."}}` — one line, with
 * every newline in the output escaped — so the thing you opened the row to read is the thing the
 * format hides.
 *
 * Deliberately not valid JSON on the way out: a string carrying newlines is printed as the lines
 * it actually contains. Keeping it parseable would mean keeping `\n` escaped, which is exactly
 * the part that made it unreadable.
 */
function prettify(text: string): string | null {
  if (text.length > PRETTY_CEILING) return null;
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  // A bare string or number is already as readable as it gets, and indenting it says nothing.
  if (value === null || typeof value !== "object") return null;
  return render(value, 0);
}

function render(value: unknown, depth: number): string {
  const pad = "  ".repeat(depth);
  const inner = "  ".repeat(depth + 1);

  if (typeof value === "string") {
    if (!value.includes("\n")) return JSON.stringify(value);
    // The whole reason this exists: print the lines the string actually holds.
    const lines = value.split("\n").map((line) => `${inner}${line}`);
    return `⏎\n${lines.join("\n")}`;
  }
  if (value === null || typeof value !== "object") return JSON.stringify(value);

  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    const items = value.map((item) => `${inner}${render(item, depth + 1)}`);
    return `[\n${items.join(",\n")}\n${pad}]`;
  }

  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return "{}";
  const rows = entries.map(([key, item]) => `${inner}${JSON.stringify(key)}: ${render(item, depth + 1)}`);
  return `{\n${rows.join(",\n")}\n${pad}}`;
}

/** A label over one half of a paired call, so the two are never mistaken for one block of text. */
function Half({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex items-baseline gap-2 px-3 pt-2.5 pb-1">
      <span className="text-muted-foreground/50 text-[10px] font-semibold tracking-[0.12em] uppercase">
        {title}
      </span>
      <span className="text-muted-foreground/30 font-mono text-[10px] tabular-nums">{detail}</span>
    </div>
  );
}

/** The selected message, whole. Fetched on selection — see the file docstring for why the list
 *  does not carry it. */
function MessageBody({
  conversationId,
  message,
  call,
  count,
  onStep,
}: {
  conversationId: string;
  message: SentMessage;
  /** The assistant turn that asked for this, when the message is a tool result. */
  call?: SentMessage;
  count: number;
  onStep: (by: number) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [asked, setAsked] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [raw, setRaw] = useState(false);

  // Computed for both halves, so the toggle is offered only when it would change something.
  const prettyText = useMemo(() => (text === null ? null : prettify(text)), [text]);
  const prettyAsked = useMemo(() => (asked === null ? null : prettify(asked)), [asked]);
  const canPretty = prettyText !== null || prettyAsked !== null;
  const shown = !raw && prettyText !== null ? prettyText : text;
  const shownAsked = !raw && prettyAsked !== null ? prettyAsked : asked;

  const index = message.index;
  const callIndex = call?.index;
  useEffect(() => {
    let live = true;
    setText(null);
    setAsked(null);
    void fetchContextMessage(conversationId, index).then((body) => {
      if (live) setText(body.text);
    });
    // Both halves, because both are sent. A result read without the command that asked for it is
    // an answer with the question torn off — and the command is itself a message in the prompt,
    // costed and billed like any other.
    if (callIndex !== undefined) {
      void fetchContextMessage(conversationId, callIndex).then((body) => {
        if (live) setAsked(body.text);
      });
    }
    return () => {
      live = false;
    };
  }, [conversationId, index, callIndex]);

  const copy = async () => {
    if (text === null) return;
    // Copies the pair when it is a pair — what was asked and what came back, in that order,
    // which is what someone pasting this into a bug report needs.
    const whole = asked ? `${asked}\n\n${text}` : text;
    // Through `copyText`, and only claim it on a `true`. Writing straight to
    // `navigator.clipboard` and then setting `copied` regardless showed a tick over an empty
    // clipboard whenever `writeText` rejected — which it does in an Electron webview — and you
    // found out from the paste, not the button.
    if (!(await copyText(whole))) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  const label = useMemo(
    () => `${message.role}${message.tool ? ` · ${message.tool}` : ""}`,
    [message.role, message.tool],
  );

  return (
    <div className="border-border/60 flex h-80 lg:h-[62vh] lg:min-h-104 min-w-0 flex-col rounded-lg border">
      <div className="border-border/60 flex items-center gap-2 border-b px-3 py-2">
        <span
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-[10px]",
            ROLE_TONE[message.role] ?? "bg-muted text-muted-foreground",
          )}
        >
          {label}
        </span>
        {/* The pair's total when it is a pair, matching the row that opened it. Showing only the
            result's here read as a different number from the one in the list, for the same thing.
            Each half still prints its own count over its own block below. */}
        <span className="text-muted-foreground/50 font-mono text-[11px] tabular-nums">
          {formatTokens(message.tokens + (call?.tokens ?? 0))} tokens ·{" "}
          {formatTokens(message.chars + (call?.chars ?? 0))} chars
          {call ? " · call and result" : ""}
        </span>
        <div className="flex-1" />
        {/* Only offered where it changes something, and it says which way it is pointing rather
            than what it would do — this is the one part of the screen that is not byte-exact, so
            it has to be obvious when it is on. */}
        {canPretty ? (
          <Button
            variant="ghost"
            size="xs"
            aria-pressed={!raw}
            onClick={() => setRaw((was) => !was)}
            title={
              raw
                ? "Showing exactly what was sent"
                : "Formatted for reading — newlines inside strings are printed as lines. Click for exactly what was sent."
            }
            className={cn(
              "h-6 gap-1 px-1.5 text-[11px]",
              raw
                ? "text-muted-foreground/70 hover:text-foreground"
                : "bg-accent/60 text-foreground",
            )}
          >
            <Braces className="size-3" />
            {raw ? "Raw" : "Formatted"}
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="xs"
          className="text-muted-foreground/70 hover:text-foreground gap-1 px-1.5"
          onClick={copy}
          disabled={text === null}
          title="Copies what was sent, not what is displayed"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          <span className="text-[11px]">{copied ? "Copied" : "Copy"}</span>
        </Button>
        <span className="text-muted-foreground/40 font-mono text-[11px] tabular-nums">
          {index} of {count}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="text-muted-foreground/70 hover:text-foreground size-6"
          onClick={() => onStep(-1)}
          disabled={index <= 1}
          aria-label="Previous message"
        >
          <ChevronLeft className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="text-muted-foreground/70 hover:text-foreground size-6"
          onClick={() => onStep(1)}
          disabled={index >= count}
          aria-label="Next message"
        >
          <ChevronRight className="size-3.5" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {text === null ? (
          <span className="text-muted-foreground/50 flex items-center gap-2 p-3 text-[12px]">
            <Loader2 className="size-3.5 animate-spin" />
            reading it…
          </span>
        ) : (
          <>
            {/* Both halves, labelled, in the order they are sent. The call is a message in the
                prompt — its own tokens, its own line in the bill — so a pane that showed only the
                result would be quietly under-reporting what went. */}
            {call ? (
              <section className="border-border/40 border-b">
                <Half
                  title="what he asked for"
                  detail={`message ${call.index} · ${formatTokens(call.tokens)} tokens`}
                />
                <pre className="text-foreground/75 px-3 pb-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap wrap-break-word">
                  {shownAsked === null ? "…" : shownAsked || "(empty)"}
                </pre>
              </section>
            ) : null}
            {call ? (
              <Half
                title="what came back"
                detail={`message ${message.index} · ${formatTokens(message.tokens)} tokens`}
              />
            ) : null}
            {/* `pre` rather than prose: this is the literal text a provider receives, and a
                markdown renderer here would show something the model never saw. */}
            <pre
              className={cn(
                "text-muted-foreground/90 px-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap wrap-break-word",
                call ? "" : "pt-3",
              )}
            >
              {shown || "(empty)"}
            </pre>
            {/* Said once at the bottom rather than badged on every block: the toggle above is
                already lit, and this is the sentence that keeps the screen honest about the one
                place it renders rather than reproduces. */}
            {!raw && canPretty ? (
              <p className="text-muted-foreground/30 px-3 pt-2 pb-3 text-[10px] italic">
                Formatted for reading. What was sent is one line with its newlines escaped — Raw
                shows it, and Copy always takes it.
              </p>
            ) : (
              <div className="pb-3" />
            )}
          </>
        )}
      </div>
    </div>
  );
}
