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
