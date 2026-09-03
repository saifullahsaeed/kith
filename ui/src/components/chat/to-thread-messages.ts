import type { ThreadMessageLike } from "@assistant-ui/react";

import { USAGE_PART, type ContextLedger, type StoredTurn } from "@/lib/backend";

/**
 * A stored conversation, rebuilt as the thread saw it.
 *
 * Not just the words: the reasoning blocks he opened, the prose between tool rounds, and
 * each call with the result it got. Reopening a conversation should show you the one you
 * had — a paragraph where six rounds of work used to be is a summary, and no amount of
 * cleverness reconstructs the shape once it is gone.
 */
export function toThreadMessages(timeline: StoredTurn[]): ThreadMessageLike[] {
  const out: unknown[] = [];
  // A running count of tool calls seen so far in this conversation, not the backend's own
  // id. `${turnIndex}-${part.id}` was the earlier fix and is still right for the ordinary
  // case, but it assumes the backend's per-turn ids are actually unique within whatever
  // `timeline()` groups as one turn — true for a turn that is one `stream_agent` call, and
  // false for older conversations where a reminder continued the same conversation_id
  // with no new user message in between: two separate turns, each restarting its own ids at
  // c1, land in the transcript with nothing to tell `timeline()` to split them, so "c9" can
  // appear twice *inside* one rendered turn. No amount of scoping by turn index fixes a
  // collision that happens within a single turn index — only something that can never repeat
  // does, so this counts instead of reading anything the backend assigned.
  let callSeq = 0;
  for (const turn of timeline) {
    const content: unknown[] = [];
    // Collected rather than pushed as they arrive. One of these is recorded per model request,
    // and pushing each as its own data part is what made a reopened conversation a column of
    // six-figure token counts — "328,422 tokens", "165,292 tokens", a dozen deep — threaded
    // through the reply. The live stream has always accumulated them into one; a resumed turn
    // has to read the same, or reopening a conversation does not show you the one you had.
    //
    // It also cost the tool grouping: a data part between two tool calls breaks the run, so
    // eight consecutive calls rendered as eight separate "1 tool call" rows instead of one
    // line saying what he touched.
    const rounds: { uncached: number; cached: number; out: number }[] = [];
    // One per turn at most, recorded when it ended — see the `context` branch in
    // `services/conversations`. Collected the same way the counts are, so it lands in the same
    // footer instead of somewhere in the middle of the reply.
    let context: ContextLedger | undefined;
    let baseline: ContextLedger | undefined;
    let folded = false;
    let retried = 0;
    for (const part of turn.parts) {
      if (part.kind === "text") content.push({ type: "text", text: part.text });
      else if (part.kind === "reasoning")
        content.push({ type: "reasoning", text: part.text });
      else if (part.kind === "tool") {
        content.push({
          type: "tool-call",
          // The backend resets its own tool-call ids to c1 at the start of every turn, so the
          // raw id repeats across nearly every message in a long conversation — 209 times for
          // "c9" alone in one real conversation, which is what actually crashed the thread on
          // reopening it: two different messages both offering a tool call keyed "c9" collided
          // in assistant-ui's own resource cache. A counter rather than the backend's id or
          // even `${turnIndex}-${part.id}`: those still collide on a turn that is really two
          // continuations glued together with no user message between them (older
          // conversations, from before this stopped happening unaskedsation on their own) —
          // both restart their own ids at c1, landing two "c9"s inside what `timeline()`
          // reads as one turn. This never repeats, by construction, regardless of what the
          // backend assigned or how the transcript is shaped.
          toolCallId: `call-${callSeq++}`,
          toolName: part.name,
          args: part.arguments,
          argsText: JSON.stringify(part.arguments),
          result: part.result,
        });
      } else if (part.kind === "context") {
        context = part.context;
        // `{}` — not absent — on a turn recorded before this field existed; `window` is
        // always present on a real reading, never on that placeholder. `turn-usage.tsx`
        // falls back to `context` when this is undefined.
        baseline = part.baseline?.window
          ? (part.baseline as ContextLedger)
          : undefined;
        folded = part.folded;
        retried = part.retried ?? 0;
      } else {
        rounds.push({
          uncached: part.uncached,
          cached: part.cached,
          out: part.out,
        });
      }
    }
    // Last, so the figure lands at the foot of the turn and nothing is split around it.
    // Token counts ride back as the same data part the live stream uses, so the footer reads
    // the same on a resumed turn as it did on a fresh one.
    if (rounds.length || context) {
      content.push({
        type: "data",
        name: USAGE_PART,
        data: { rounds, context, baseline, folded, retried },
      });
    }
    // `createdAt` from the transcript, never left to default. A `ThreadMessageLike` without one
    // is stamped with the moment it was converted, so every turn in a conversation reopened now
    // would read as having happened now — a clock that is wrong on exactly the messages it is
    // there to date. Omitted rather than guessed when the turn predates the field.
    const at = turn.at ? new Date(turn.at) : null;
    if (content.length) {
      out.push({
        role: turn.role,
        content,
        ...(at && !Number.isNaN(at.getTime()) ? { createdAt: at } : {}),
      });
    }
  }
  // One cast, at the boundary: the shapes above are the library's own, and its content
  // union narrows by role in a way that defeats inference through a map.
  return out as ThreadMessageLike[];
}
