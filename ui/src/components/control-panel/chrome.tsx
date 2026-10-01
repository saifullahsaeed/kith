/** Chrome shared by every Control Panel page — the nav, the page frames, the small
 *  primitives every list is built out of. */
import type { ReactNode } from "react";
import { Sparkles, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { CHIP } from "./types";

/* ── Navigation ─────────────────────────────────────────────────────────── */

export function NavGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <div className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/50">
        {label}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

export function TabButton({
  icon,
  count,
  active,
  onClick,
  children,
}: {
  icon: ReactNode;
  count?: number;
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "group relative flex w-full items-center gap-2.5 rounded-md py-1.5 pr-2 pl-3 text-left text-sm transition-colors",
        active
          ? "bg-accent font-medium text-foreground"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
    >
      <span
        className={cn(
          "absolute top-1/2 left-0 h-4 w-0.5 -translate-y-1/2 rounded-full bg-kith transition-opacity",
          active ? "opacity-100" : "opacity-0",
        )}
      />
      <span
        className={cn(
          "shrink-0 transition-colors",
          active ? "text-kith" : "text-muted-foreground group-hover:text-foreground",
        )}
      >
        {icon}
      </span>
      <span className="flex-1 truncate">{children}</span>
      {count !== undefined ? (
        <span
          className={cn(
            "text-[11px] tabular-nums transition-colors",
            active ? "text-foreground" : "text-muted-foreground/70",
          )}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

/* ── Shared page furniture ──────────────────────────────────────────────── */

export function PageHeader({
  icon,
  color,
  title,
  subtitle,
  count,
  children,
}: {
  icon: ReactNode;
  color: keyof typeof CHIP;
  title: string;
  subtitle?: string;
  count?: number;
  children?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-center gap-x-3.5 gap-y-3 border-b border-border/60 pb-5">
      <span
        className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg", CHIP[color])}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <h2 className="flex items-baseline gap-2 text-[15px] font-semibold tracking-tight">
          {title}
          {count !== undefined ? (
            <span className="text-xs font-normal tabular-nums text-muted-foreground">{count}</span>
          ) : null}
        </h2>
        {subtitle ? <p className="mt-0.5 text-[13px] text-muted-foreground">{subtitle}</p> : null}
      </div>
      {children ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{children}</div>
      ) : null}
    </div>
  );
}

/** A rounded "compose" pill that holds an input + controls + Add button. */
export function Composer({ children, onSubmit }: { children: ReactNode; onSubmit?: () => void }) {
  return (
    <div
      className="mb-6 flex flex-wrap items-center gap-2 rounded-xl border border-border/70 bg-card/40 p-2 pl-3.5 shadow-sm transition-colors focus-within:border-ring/60 focus-within:bg-card/70"
      onKeyDown={(e) => {
        if (e.key === "Enter" && onSubmit) onSubmit();
      }}
    >
      {children}
    </div>
  );
}

export function SectionLabel({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-3 flex items-baseline gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {children}
      </h3>
      {hint ? <span className="text-xs text-muted-foreground/60">{hint}</span> : null}
    </div>
  );
}

/* ── Primitives ─────────────────────────────────────────────────────────── */

export function Badge({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-md border border-border/70 bg-muted/40 px-1.5 py-0.5 text-[11px] ${className}`}
    >
      {children}
    </span>
  );
}

export function DeleteButton({ onClick }: { onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-7 shrink-0 text-muted-foreground opacity-60 transition-opacity hover:text-destructive hover:opacity-100"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      aria-label="Delete"
    >
      <Trash2 className="size-4" />
    </Button>
  );
}

export function EmptyState({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border/70 bg-card/20 px-6 py-16 text-center">
      <span className="flex size-11 items-center justify-center rounded-full bg-muted/50 text-muted-foreground">
        {icon ?? <Sparkles className="size-5" />}
      </span>
      <p className="max-w-sm text-sm text-muted-foreground">{children}</p>
    </div>
  );
}

/* ── The 2026-09-30 redesign's furniture ────────────────────────────────────
 *
 * Everything below is drawn from the "Kith Control Panel" design canvas, one board per screen,
 * at the window's real size. The rules it commits to are the reason these exist rather than
 * the older pieces above:
 *
 *   - colour means state, never domain: amber is "needs you" (or him), green is live or done,
 *     everything else is neutral. The old per-page violet/sky/orange chips are gone.
 *   - one page title per screen, at the size the design gives it, with the subtitle saying what
 *     the screen is for;
 *   - lists are dense rows on neutral surfaces; cards only where a thing is a unit of its own.
 *
 * The values are the app's theme tokens, not the board's hexes, so the light theme follows.
 */

/** A label inside the left nav: WORK, MIND, LIBRARY. */
export function NavLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-muted-foreground/80 px-2.5 pt-4 pb-1.5 text-[10.5px] font-semibold tracking-[0.1em] uppercase">
      {children}
    </div>
  );
}

/** One nav item: icon, label, an optional amber count of what waits on you, and a quiet total. */
export function NavItem({
  icon,
  active,
  onClick,
  count,
  alert,
  alertLabel,
  children,
}: {
  icon: ReactNode;
  active: boolean;
  onClick: () => void;
  count?: number;
  /** What here waits on you. Amber, and only when there is some. */
  alert?: number;
  alertLabel?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "mb-0.5 flex h-[34px] w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[13px] transition-colors",
        active
          ? "bg-muted text-foreground font-medium"
          : "text-foreground/80 hover:bg-muted/50 hover:text-foreground",
      )}
    >
      <span className={cn("flex shrink-0 [&_svg]:size-4", active ? "text-kith" : "text-muted-foreground/80")}>
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {alert ? (
        <span
          title={alertLabel}
          aria-label={alertLabel}
          className="bg-kith/15 text-kith flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1.5 text-[11px] font-bold tabular-nums"
        >
          {alert}
        </span>
      ) : null}
      {count ? <span className="text-muted-foreground/80 text-[11.5px] tabular-nums">{count}</span> : null}
    </button>
  );
}

/** The page's title row: 24px title with a quiet count, one line of what it is for, actions right. */
export function PageTitle({
  title,
  count,
  subtitle,
  children,
}: {
  title: ReactNode;
  count?: ReactNode;
  subtitle?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-end gap-4">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h1 className="flex items-baseline gap-2 text-2xl font-[650] tracking-[-0.01em]">
          <span className="truncate">{title}</span>
          {count !== undefined && count !== null ? (
            <span className="text-muted-foreground/80 text-[15px] font-normal tabular-nums">{count}</span>
          ) : null}
        </h1>
        {subtitle ? <p className="text-muted-foreground m-0 text-[13px]">{subtitle}</p> : null}
      </div>
      {children ? <div className="flex shrink-0 items-center gap-2">{children}</div> : null}
    </div>
  );
}

/** A segmented filter: a row of pills, one pressed. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  label,
}: {
  options: readonly { value: T; label: ReactNode; count?: number }[];
  value: T;
  onChange: (value: T) => void;
  size?: "sm" | "md";
  label?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn(
        "border-border flex shrink-0 gap-0.5 border",
        size === "sm" ? "bg-background rounded-lg p-0.5" : "bg-sidebar gap-1 rounded-[10px] p-[3px]",
      )}
    >
      {options.map((option) => {
        const on = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(option.value)}
            className={cn(
              "flex items-center whitespace-nowrap transition-colors",
              size === "sm" ? "h-6 rounded-md px-2.5 text-xs" : "h-7 rounded-[7px] px-3 text-[12.5px]",
              on ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
            {option.count !== undefined ? (
              <span className="text-muted-foreground/80 ms-1.5 tabular-nums">{option.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** A unit on the page: neutral, hairline-bordered, 14px corners. `tone="attention"` is amber. */
export function Card({
  children,
  className,
  tone = "neutral",
  label,
}: {
  children: ReactNode;
  className?: string;
  tone?: "neutral" | "attention";
  label?: string;
}) {
  return (
    <section
      aria-label={label}
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-[14px] border",
        tone === "attention" ? "border-kith/25 bg-kith/[0.05]" : "border-border bg-card",
        className,
      )}
    >
      {children}
    </section>
  );
}

/** A card's head: 46px, title, quiet count, an action on the right. */
export function CardHead({
  children,
  count,
  dot,
  action,
  tone = "neutral",
}: {
  children: ReactNode;
  count?: ReactNode;
  dot?: "attention" | "live";
  action?: ReactNode;
  tone?: "neutral" | "attention";
}) {
  return (
    <div
      className={cn(
        "flex h-[46px] shrink-0 items-center gap-2 border-b px-4",
        tone === "attention" ? "border-kith/15" : "border-border/80",
      )}
    >
      {dot === "attention" ? <span className="bg-kith size-[7px] shrink-0 rounded-full" /> : null}
      {dot === "live" ? <LiveDot /> : null}
      <span className="text-[13px] font-semibold">{children}</span>
      {count !== undefined ? (
        <span className={cn("text-xs tabular-nums", tone === "attention" ? "text-kith/80" : "text-muted-foreground/80")}>
          {count}
        </span>
      ) : null}
      <span className="flex-1" />
      {action}
    </div>
  );
}

/** The breathing green dot: something is happening right now. */
export function LiveDot({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("kith-breathe bg-roam size-[7px] shrink-0 rounded-full", className)}
      style={{ animationDuration: "1.6s" }}
    />
  );
}

/** An uppercase section label, with a dim count after it. */
export function Label({ children, count, className }: { children: ReactNode; count?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-baseline gap-2", className)}>
      <span className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">{children}</span>
      {count !== undefined ? <span className="text-muted-foreground/50 text-[11px] tabular-nums">{count}</span> : null}
    </div>
  );
}

/** A small pill. `attention` is amber, `live` green; neutral otherwise. */
export function Chip({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "attention" | "live";
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-[22px] shrink-0 items-center rounded-full border px-2 text-[11px] whitespace-nowrap tabular-nums",
        tone === "attention"
          ? "border-kith/25 bg-kith/10 text-kith"
          : tone === "live"
            ? "border-roam/25 bg-roam/10 text-roam"
            : "border-border bg-muted/60 text-foreground/80",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A key, as a key. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="text-muted-foreground/80 inline-flex h-[18px] items-center rounded-[5px] border border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-[5px] font-mono text-[11px] leading-none">
      {children}
    </kbd>
  );
}

/** A small kind tile: a neutral 28px square holding a 14px icon. */
export function KindTile({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "attention" }) {
  return (
    <span
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-lg [&_svg]:size-3.5",
        tone === "attention" ? "bg-kith/15 text-kith" : "bg-muted/70 text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

/** The design's buttons. Primary is the one amber thing on a surface; never a red fill. */
export const BUTTON = {
  primary:
    "bg-kith text-primary-foreground hover:bg-kith/90 inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-lg px-3.5 text-[13px] font-semibold transition-colors disabled:opacity-50",
  secondary:
    "border-input bg-muted/70 text-foreground hover:bg-muted inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-lg border px-3 text-[13px] transition-colors disabled:opacity-50",
  ghost:
    "text-foreground/80 hover:bg-muted/50 hover:text-foreground inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-lg px-3 text-[13px] transition-colors",
  danger:
    "text-destructive hover:bg-destructive/10 inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-lg px-3 text-[13px] transition-colors",
  small: "h-7 rounded-[7px] px-3 text-[12.5px]",
} as const;

/** What an empty list says: one sentence, and the next thing to do. */
export function Empty({
  title,
  children,
  action,
  className,
}: {
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-1.5 rounded-xl border border-dashed border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-4 py-[18px]",
        className,
      )}
    >
      {title ? <span className="text-foreground text-[13px] font-semibold">{title}</span> : null}
      {children ? <span className="text-muted-foreground/80 text-[12.5px] leading-[1.45]">{children}</span> : null}
      {action ? <div className="mt-1.5">{action}</div> : null}
    </div>
  );
}
