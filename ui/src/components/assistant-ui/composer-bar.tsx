"use client";

/**
 * The composer's own controls: how hard he thinks, what he may do, which model, how full the
 * window is — and, while he works, whether what you type steers him now or waits for the turn.
 *
 * **Why these moved here from the title bar.** Thinking, access and model are settings for the
 * *next message*, so they belong where the next message is written. In the title bar they were
 * three unrelated shapes a long way from the input, and a control you have to go looking for is
 * a control you leave on whatever it was. The title bar keeps navigation and presence.
 *
 * The data is the same data the title bar used — `useServerConfig` for the model and the effort,
 * `usePermissions` for the mode and what is waiting — written the same way, so moving the
 * control changed nothing about what it does.
 */

import { useAuiState } from "@assistant-ui/react";
import {
  Check,
  ChevronDown,
  Layers,
  Lightbulb,
  Maximize2,
  Shield,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { latestUsage, shortModelName } from "@/components/assistant-ui/turn-usage";
import { EFFORTS } from "@/components/shell/header-controls";
import { usePermissions } from "@/hooks/use-permissions";
import { MODE_LABELS, patchServerConfig, type PermissionMode } from "@/lib/backend";
import { byGroup, FOLDS_AT } from "@/lib/context-groups";
import { foldNow } from "@/lib/commands";
import { useConversationId } from "@/lib/conversation";
import type { ContextLedger } from "@/lib/backend/types";
import { Go } from "@/lib/go";
import { pathForContext, pathForSettings } from "@/lib/router";
import { useServerConfig } from "@/lib/server-config";
import { cn } from "@/lib/utils";

/* ── a chip and the menu it opens ─────────────────────────────────────────────────────── */

const chip =
  "flex h-7 shrink-0 items-center gap-1.5 rounded-lg px-[9px] text-xs transition-colors";

/** Open state for one menu, closed by Escape or a press anywhere else. */
function useMenu(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Claimed, so the composer's Escape (stop the turn) never sees the one that closed a menu.
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    };
    const onDown = () => setOpen(false);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);
  return [open, setOpen];
}

function Menu({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      role="menu"
      onPointerDown={(event) => event.stopPropagation()}
      className={cn(
        "border-border bg-popover text-popover-foreground absolute bottom-full start-0 z-50 mb-2 overflow-hidden rounded-xl border shadow-xl",
        "animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-1 duration-150",
        className,
      )}
    >
      {children}
    </div>
  );
}

function MenuLabel({ children, note }: { children: ReactNode; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3.5 pt-3 pb-1.5">
      <span className="text-muted-foreground/75 text-[10px] font-semibold tracking-[0.12em] uppercase">
        {children}
      </span>
      {note ? <span className="text-muted-foreground/75 text-[11px]">{note}</span> : null}
    </div>
  );
}

/* ── Thinking ─────────────────────────────────────────────────────────────────────────── */

/**
 * How hard he thinks before answering. Hidden when the model's provider has said it takes no
 * reasoning parameter — sending one anyway is a 400 — and offered while that is still unknown,
 * which is the normal state right after a switch.
 */
export function ThinkingChip() {
  const { config, onSaveConfig } = useServerConfig();
  const [open, setOpen] = useMenu();
  if (!config) return null;
  const supports = !config.capabilities?.known || config.capabilities.reasoning;
  if (!supports) return null;
  const effort = config.effort ?? "";
  const current = EFFORTS.find((one) => one.value === effort) ?? EFFORTS[0];

  const choose = (next: string) => {
    onSaveConfig({ ...config, effort: next });
    void patchServerConfig({ effort: next });
    setOpen(false);
  };

  return (
    <div className="relative">
      <button
        type="button"
        title="How hard he thinks before answering"
        aria-haspopup="menu"
        aria-expanded={open}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => setOpen(!open)}
        className={cn(
          chip,
          "text-foreground/75 hover:text-foreground hover:bg-accent/60",
          open && "bg-accent text-foreground",
        )}
      >
        <Lightbulb className="size-[13px]" aria-hidden />
        {current.label}
      </button>
      {open ? (
        <Menu className="w-[34rem] max-w-[calc(100vw-2rem)]">
          <MenuLabel note="how much he reasons before answering">Thinking</MenuLabel>
          <div className="flex flex-col px-1.5 pb-1.5">
            {EFFORTS.map((one) => {
              const selected = one.value === effort;
              return (
                <button
                  key={one.value || "default"}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  onClick={() => choose(one.value)}
                  className={cn(
                    "hover:bg-accent/70 flex items-baseline gap-3 rounded-lg px-2 py-1.5 text-left",
                    selected && "bg-accent/70",
                  )}
                >
                  <span className="flex w-4 shrink-0 justify-center self-center">
                    {selected ? <Check className="text-kith size-3.5" /> : null}
                  </span>
                  <span className="text-foreground w-[4.5rem] shrink-0 text-[13px] font-medium">
                    {one.label}
                  </span>
                  <span className="text-muted-foreground min-w-0 text-xs">{one.hint}</span>
                </button>
              );
            })}
          </div>
          <div className="border-border text-muted-foreground/75 border-t px-3.5 py-2 text-[11px]">
            Applies from your next message
          </div>
        </Menu>
      ) : null}
    </div>
  );
}

/* ── Access ───────────────────────────────────────────────────────────────────────────── */

const MODE_ICON: Record<PermissionMode, typeof Shield> = {
  ask: Shield,
  auto: ShieldCheck,
  bypass: ShieldAlert,
};

/**
 * What he may do on this computer without asking — red while nothing is checked, with a count
 * of the requests already waiting on you, because that count is the reason to look at it.
 */
export function AccessChip() {
  const { mode, pending, setMode } = usePermissions();
  const [open, setOpen] = useMenu();
  const Icon = MODE_ICON[mode];
  const bypass = mode === "bypass";
  const waiting = pending.length;

  return (
    <div className="relative">
      <button
        type="button"
        title="What he may do on this computer without asking"
        aria-haspopup="menu"
        aria-expanded={open}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => setOpen(!open)}
        className={cn(
          chip,
          bypass
            ? "border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/15 border"
            : "text-foreground/75 hover:text-foreground hover:bg-accent/60",
          open && !bypass && "bg-accent text-foreground",
        )}
      >
        <Icon className="size-[13px]" aria-hidden />
        {MODE_LABELS[mode].label}
        {waiting ? (
          <span
            className="bg-kith text-primary-foreground flex size-4 items-center justify-center rounded-full text-[10px] font-bold tabular-nums"
            aria-label={`${waiting} waiting on you`}
          >
            {waiting}
          </span>
        ) : null}
      </button>
      {open ? (
        <Menu className="w-[24rem] max-w-[calc(100vw-2rem)]">
          {waiting ? (
            <div className="border-kith/30 bg-kith-soft/60 m-1.5 mb-0 flex items-center gap-2.5 rounded-lg border px-3 py-2 text-[13px]">
              <span className="bg-kith text-primary-foreground flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold tabular-nums">
                {waiting}
              </span>
              <span className="text-foreground">
                {waiting === 1 ? "request" : "requests"} waiting on you
              </span>
            </div>
          ) : null}
          <MenuLabel>Access</MenuLabel>
          <div className="flex flex-col gap-0.5 px-1.5 pb-1.5">
            {(["ask", "auto", "bypass"] as PermissionMode[]).map((one) => {
              const selected = one === mode;
              const OptionIcon = MODE_ICON[one];
              const red = one === "bypass";
              return (
                <button
                  key={one}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  onClick={() => {
                    setMode(one);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex items-start gap-2.5 rounded-lg border border-transparent px-2.5 py-2 text-left",
                    selected
                      ? red
                        ? "border-destructive/30 bg-destructive/10"
                        : "bg-accent/70"
                      : "hover:bg-accent/60",
                  )}
                >
                  <OptionIcon
                    className={cn(
                      "mt-0.5 size-3.5 shrink-0",
                      red ? "text-destructive" : "text-muted-foreground",
                    )}
                    aria-hidden
                  />
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "block text-[13px] font-medium",
                        red && selected ? "text-destructive" : "text-foreground",
                      )}
                    >
                      {MODE_LABELS[one].label}
                    </span>
                    <span className="text-muted-foreground block text-xs leading-snug">
                      {MODE_LABELS[one].hint}
                    </span>
                  </span>
                  {selected ? (
                    <Check
                      className={cn("mt-0.5 size-3.5 shrink-0", red ? "text-destructive" : "text-kith")}
                    />
                  ) : null}
                </button>
              );
            })}
          </div>
          <Go>
            {(go) => (
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  go(pathForSettings("permissions"));
                }}
                className="border-border text-muted-foreground/75 hover:text-foreground w-full border-t px-3.5 py-2 text-left text-[11px] transition-colors"
              >
                Standing grants live in Settings → Permissions
              </button>
            )}
          </Go>
        </Menu>
      ) : null}
    </div>
  );
}

/* ── Model ────────────────────────────────────────────────────────────────────────────── */

/** Which model answers, without the vendor prefix, one click from where it is chosen. */
export function ModelChip() {
  const { config } = useServerConfig();
  if (!config?.model) return null;
  return (
    <Go>
      {(go) => (
        <button
          type="button"
          title={`${config.model} — click to change`}
          onClick={() => go(pathForSettings("model"))}
          className={cn(
            chip,
            // The one chip that gives way when the row runs out of room — the full name is on
            // hover and in Settings, and a Stop button pushed out of the composer is worse.
            "text-foreground/75 hover:text-foreground hover:bg-accent/60 max-w-[14rem] min-w-9 shrink gap-1 font-mono",
          )}
        >
          <span className="truncate">{shortModelName(config.model)}</span>
          <ChevronDown className="size-[11px] shrink-0" aria-hidden />
        </button>
      )}
    </Go>
  );
}

/* ── Context ──────────────────────────────────────────────────────────────────────────── */

/**
 * How full the window is — the one place it is shown.
 *
 * It used to be in five: this ring, a card in Activity, a Context tab beside it, a meter that
 * appeared under the composer after a fold, and "412k in, 71% cached" under every single answer,
 * so a long chat said the same number fifteen times down the page. Now the ring is the reading and
 * a click opens the breakdown above it — what is in the window by group, the fold, and a button
 * to the full screen for anything deeper.
 *
 * Read off the latest turn's own usage rather than fetched: the number already exists, live, the
 * instant a round reports it. A fold's own reading wins until the next turn measures again — a
 * fold takes no reading of its own, so without it the ring would describe the conversation from
 * before the fold.
 */
/** "90k", "1.05M", "940" — a count you read at a glance, which a full "1,048,576" is not. */
function compact(count: number): string {
  if (count < 1_000) return String(count);
  if (count < 1_000_000) return `${Math.round(count / 1_000)}k`;
  return `${(count / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
}

export function ContextRing({ afterFold }: { afterFold?: ContextLedger }) {
  const conversationId = useConversationId();
  const running = useAuiState((s) => s.thread.isRunning);
  const [open, setOpen] = useMenu();
  const [folded, setFolded] = useState<ContextLedger | undefined>();
  const [folding, setFolding] = useState(false);
  const [note, setNote] = useState("");
  const encoded = useAuiState((s) => {
    const usage = latestUsage(s.thread.messages);
    const reading = usage?.context ?? usage?.baseline;
    return reading ? JSON.stringify({ reading, folded: Boolean(usage?.folded) }) : "";
  });
  // A new turn measures the window itself, so a fold's reading stops being the latest one.
  useEffect(() => {
    if (running) {
      setFolded(undefined);
      setNote("");
    }
  }, [running]);

  const live = encoded ? (JSON.parse(encoded) as { reading: ContextLedger; folded: boolean }) : null;
  const reading = folded ?? afterFold ?? live?.reading;
  if (!reading) return null;
  if (!reading.window) {
    return <span className="text-muted-foreground/60 px-2 text-[11px]">Context unknown</span>;
  }
  const wasFolded = !folded && !afterFold && live?.folded;
  const percent = Math.round(reading.share * 100);
  const near = reading.share >= FOLDS_AT;
  const tone = wasFolded ? "text-destructive" : near ? "text-kith" : "text-blue-500 dark:text-blue-400";
  const circumference = 2 * Math.PI * 14;
  const groups = byGroup(reading.lines ?? [], reading.used);

  const fold = async () => {
    if (!conversationId || folding) return;
    setFolding(true);
    setNote("Folding…");
    const result = await foldNow(conversationId);
    setNote(result.note);
    if (result.reading) setFolded(result.reading);
    setFolding(false);
  };

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Context ${percent}% full, folds at ${Math.round(FOLDS_AT * 100)}% — show what is in it`}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => setOpen(!open)}
        className={cn(
          "text-muted-foreground hover:text-foreground hover:bg-accent/60 flex h-7 items-center gap-1.5 rounded-lg px-2 text-[11px] tabular-nums transition-colors",
          open && "bg-accent text-foreground",
        )}
      >
        <svg width="16" height="16" viewBox="0 0 36 36" aria-hidden className="shrink-0">
          <circle cx="18" cy="18" r="14" fill="none" stroke="currentColor" strokeWidth="5" className="text-foreground/12" />
          <circle
            cx="18"
            cy="18"
            r="14"
            fill="none"
            stroke="currentColor"
            strokeWidth="5"
            strokeDasharray={`${Math.max(1.5, reading.share * circumference)} ${circumference}`}
            transform="rotate(-90 18 18)"
            className={tone}
          />
        </svg>
        {percent}%
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label="What is in his context"
          onPointerDown={(event) => event.stopPropagation()}
          className={cn(
            "border-border bg-popover text-popover-foreground absolute end-0 bottom-full z-50 mb-2 flex w-[22rem] max-w-[calc(100vw-2rem)] flex-col gap-3 rounded-xl border p-3.5 shadow-xl",
            "animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-1 duration-150",
          )}
        >
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">Context</span>
            <span className="text-muted-foreground text-xs tabular-nums">
              {compact(reading.used)} of {compact(reading.window)} · {percent}%
            </span>
            <span className="flex-1" />
            <Go>
              {(go) => (
                <button
                  type="button"
                  aria-label="Open the full context breakdown"
                  title="Full screen"
                  onClick={() => {
                    setOpen(false);
                    go(pathForContext());
                  }}
                  className="text-muted-foreground hover:text-foreground hover:bg-accent/60 flex size-7 items-center justify-center rounded-lg transition-colors"
                >
                  <Maximize2 className="size-3.5" aria-hidden />
                </button>
              )}
            </Go>
          </div>
          {/* Against the whole window, with the fold drawn in, so how close it is reads at a glance. */}
          <div className="bg-muted relative flex h-2 gap-0.5 overflow-hidden rounded-full">
            {groups.map((group) => (
              <span
                key={group.key}
                className={group.swatch}
                style={{ width: `${(group.tokens / reading.window) * 100}%` }}
              />
            ))}
            <span
              aria-hidden
              className="bg-foreground/50 absolute inset-y-0 w-0.5"
              style={{ left: `${FOLDS_AT * 100}%` }}
            />
          </div>
          <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] tabular-nums">
            {groups.map((group) => (
              <li key={group.key} className="text-muted-foreground flex items-center gap-1.5" title={group.hint}>
                <span className={cn("size-2 shrink-0 rounded-[2px]", group.swatch)} aria-hidden />
                <span className="min-w-0 truncate">{group.label}</span>
                <span className="flex-1" />
                <span className="text-foreground/80">{compact(group.tokens)}</span>
              </li>
            ))}
          </ul>
          <div className="border-border/60 flex items-center gap-2 border-t pt-3">
            <span className="text-muted-foreground min-w-0 flex-1 text-[11px] leading-snug">
              {note ||
                (wasFolded
                  ? "Earlier steps were folded to make room."
                  : near
                    ? "Near the fold — older turns will be summarised soon."
                    : `Folds at ${Math.round(FOLDS_AT * 100)}%.`)}
            </span>
            <button
              type="button"
              onClick={() => void fold()}
              disabled={running || folding || !conversationId}
              title={
                running
                  ? "Wait for the turn to finish — a fold rewrites the history it is reading."
                  : "Summarise the older turns now, instead of waiting for the window to fill"
              }
              className="border-border text-foreground/85 hover:bg-accent/60 flex h-7 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 text-xs transition-colors disabled:opacity-50"
            >
              <Layers className="size-3.5" aria-hidden />
              {folding ? "Folding…" : "Fold now"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
