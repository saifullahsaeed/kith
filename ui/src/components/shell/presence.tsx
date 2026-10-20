import { useEffect, useState } from "react";

import { agentCount, clock, type Presence } from "@/components/shell/chat-states";
import { cn } from "@/lib/utils";

/** The little breathing light that stands in for Kith. Amber when he's awake
 * and with you; green while he's working on his own. */
export function PresenceOrb({
  working = false,
  size = 10,
  idle = false,
}: {
  working?: boolean;
  size?: number;
  idle?: boolean;
}) {
  const orb = working ? "var(--roam)" : "var(--kith)";
  return (
    <span
      className="kith-orb inline-block shrink-0"
      data-idle={idle ? "true" : "false"}
      style={{ width: size, height: size, ["--orb" as string]: orb }}
      aria-hidden
    />
  );
}

/**
 * Kith, in the title bar: his name and what he is doing, as one pill.
 *
 * It replaces an orb, the word "here", and two counters beside it — one for turns running in
 * other chats and one for unsent text — because the tabs and the conversation list now carry both
 * of those per chat, where they can be clicked. What is left is the one sentence nothing else
 * says: whether he is with you, working (and for how long), has agents working, or is waiting on
 * you.
 *
 * The colour is the meaning, as everywhere else: green is working, teal is agents, amber is him
 * and anything that needs you. The dot stays amber for agents because it is still *him* doing the
 * waiting — on them; only the words turn teal.
 */
export function PresencePill({
  presence,
  onClick,
}: {
  presence: Presence;
  /** Where the pill takes you, when there is somewhere to go. Absent, it is a status line. */
  onClick?: () => void;
}) {
  const now = useNow(presence.kind === "working");

  const tone =
    presence.kind === "working"
      ? "border-roam/30 bg-roam/10"
      : presence.kind === "agents"
        ? "border-teal-500/30 bg-teal-500/10 dark:border-teal-400/30 dark:bg-teal-400/10"
        : presence.kind === "waiting"
          ? "border-kith/35 bg-kith/10"
          : "border-border bg-transparent";

  const words =
    presence.kind === "working"
      ? `working · ${clock(now - presence.since)}`
      : presence.kind === "agents"
        ? `${agentCount(presence.count)} working`
        : presence.kind === "waiting"
          ? "waiting on you"
          : "here";

  const label =
    presence.kind === "waiting"
      ? `Kith is waiting on you in ${presence.chats} chat${presence.chats === 1 ? "" : "s"}`
      : presence.kind === "agents"
        ? `Kith has ${words}${onClick ? " — show them" : ""}`
        : presence.kind === "working"
          ? `Kith is ${words}`
          : "Kith is here";

  const body = (
    <>
      <span
        aria-hidden
        className={cn(
          "kith-breathe size-2 shrink-0 rounded-full",
          presence.kind === "working" ? "bg-roam" : "bg-kith",
        )}
        style={{
          animationDuration:
            presence.kind === "working" ? "1.4s" : presence.kind === "waiting" ? "2.4s" : "3.2s",
        }}
      />
      <span className="font-semibold">Kith</span>
      {presence.kind === "waiting" ? (
        <span aria-hidden className="text-muted-foreground/80">
          ·
        </span>
      ) : null}
      <span
        className={cn(
          "whitespace-nowrap tabular-nums",
          presence.kind === "working"
            ? "text-roam"
            : presence.kind === "agents"
              ? "text-teal-600 dark:text-teal-400"
              : presence.kind === "waiting"
                ? "text-kith"
                : "text-muted-foreground",
        )}
      >
        {words}
      </span>
    </>
  );

  const shape = cn(
    "text-foreground flex h-7 shrink-0 items-center gap-2 rounded-full border pr-3 pl-2.5 text-xs",
    tone,
  );

  return onClick ? (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        shape,
        "cursor-pointer transition-[filter] hover:brightness-110",
        "focus-visible:ring-kith/60 focus-visible:ring-2 focus-visible:outline-none",
      )}
    >
      {body}
    </button>
  ) : (
    <div role="status" aria-label={label} title={label} className={shape}>
      {body}
    </div>
  );
}

/** The time, ticking once a second while `on` — and not at all otherwise, so an idle pill costs
 *  nothing. */
function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [on]);
  return now;
}
