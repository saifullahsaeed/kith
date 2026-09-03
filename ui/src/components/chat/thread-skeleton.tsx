import type { FC } from "react";

/**
 * What a conversation looks like while it is being fetched.
 *
 * The gap this fills is not a slow render — it is the several hundred milliseconds between
 * clicking a conversation and having anything to render at all. Before this, `openConversation`
 * awaited the whole payload before it changed any state, so the click left the *previous*
 * conversation on screen, unchanged and interactive, until the new one arrived and replaced it
 * whole. From a chair that is the app ignoring you and then jumping.
 *
 * Shaped like a conversation rather than a spinner on purpose: alternating turns at plausible
 * widths, so the thing that arrives lands in roughly the outline already there instead of
 * replacing a circle. A spinner says "wait"; this says "your conversation, shortly", and it is
 * the difference between the wait feeling like work and feeling like a stall.
 *
 * `aria-hidden` with a live-region label beside it: forty shimmering rectangles are noise to a
 * screen reader, and "Loading the conversation" is the whole of what they mean.
 */
export const ThreadSkeleton: FC = () => (
  <div className="flex h-full flex-col gap-6 overflow-hidden px-4 py-8">
    <span className="sr-only" role="status">
      Loading the conversation
    </span>
    <div aria-hidden className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      {SHAPES.map((shape, index) => (
        <div
          key={index}
          className={shape.mine ? "flex justify-end" : "flex flex-col gap-2"}
        >
          {shape.lines.map((width, line) => (
            <div
              key={line}
              style={{ width }}
              className={
                shape.mine
                  ? "shimmer bg-muted/70 h-8 rounded-2xl motion-reduce:animate-none"
                  : "shimmer bg-muted/50 h-4 rounded motion-reduce:animate-none"
              }
            />
          ))}
        </div>
      ))}
    </div>
  </div>
);

/* Fixed rather than random. A skeleton that reshuffles on every render is a second animation
 * competing with the shimmer, and the widths only have to be plausible once. */
const SHAPES: { mine: boolean; lines: string[] }[] = [
  { mine: true, lines: ["42%"] },
  { mine: false, lines: ["88%", "94%", "61%"] },
  { mine: true, lines: ["28%"] },
  { mine: false, lines: ["92%", "76%", "85%", "39%"] },
  { mine: false, lines: ["70%", "48%"] },
];
