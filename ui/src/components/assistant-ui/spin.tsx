import { cn } from "@/lib/utils";

/**
 * The ring spinner every live thing in the thread uses — a turn working, a call running, a
 * reconnect.
 *
 * One component because the design draws one: a two-pixel ring in a faint tint of its colour
 * with the leading quarter in the full colour. `LoaderIcon` spinning is a different glyph (eight
 * spokes), and three different spinners on one screen read as three different kinds of waiting.
 * The colour is the caller's, by meaning: green for him working, teal for agents, amber for
 * something that could still go wrong.
 */
export function Spin({
  className,
  tone = "roam",
}: {
  className?: string;
  tone?: "roam" | "kith" | "teal" | "blue" | "muted";
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-3.5 shrink-0 animate-spin rounded-full border-2 [animation-duration:0.9s]",
        tone === "roam" && "border-roam/25 border-t-roam",
        tone === "kith" && "border-kith/25 border-t-kith",
        tone === "teal" && "border-teal-400/25 border-t-teal-400",
        tone === "blue" && "border-blue-400/25 border-t-blue-400",
        tone === "muted" && "border-muted-foreground/25 border-t-muted-foreground",
        className,
      )}
    />
  );
}
