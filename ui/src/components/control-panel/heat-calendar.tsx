/** A month as a heat map: one square a day, tinted by how much happened on it.
 *
 * The Journal's left rail and Lifetime's "Jump to date" are the same question — which day do you
 * want, and which days had anything — so they are the same calendar. A plain date picker answers
 * only the first half; this one shows you that 1 September had 44 entries before you click it. */
import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { dayKey, heatLevel, monthCells, monthTitle, plural } from "./day-format";
import type { MonthIndex } from "./day-format";

/** The five steps of `heatLevel`, as fills of the text colour — so the light theme tints dark. */
const TINT = [
  "bg-foreground/[0.03]",
  "bg-foreground/[0.09]",
  "bg-foreground/[0.16]",
  "bg-foreground/[0.25]",
  "bg-foreground/[0.38]",
] as const;

const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];

export function HeatCalendar({
  month,
  onMonth,
  counts,
  selected,
  onPick,
  min,
  max,
  noun,
  className,
}: {
  month: MonthIndex;
  onMonth: (month: MonthIndex) => void;
  /** How much happened per local day, keyed `YYYY-MM-DD`. */
  counts: ReadonlyMap<string, number>;
  /** The day being looked at. */
  selected?: string | null;
  onPick: (key: string) => void;
  /** The earliest and latest months worth paging to. */
  min: MonthIndex;
  max: MonthIndex;
  /** What is being counted: ["entry", "entries"]. */
  noun: readonly [string, string];
  className?: string;
}) {
  const year = Math.floor(month / 12);
  const m = month % 12;
  const title = monthTitle(year, m);
  const today = dayKey(Date.now());
  const monthName = new Date(year, m, 1).toLocaleDateString(undefined, { month: "long" });

  let total = 0;
  let days = 0;
  const cells = monthCells(year, m).map((day) => {
    if (day == null) return null;
    const key = dayKey(new Date(year, m, day).getTime());
    const n = counts.get(key) ?? 0;
    total += n;
    if (n) days += 1;
    return { day, key, n };
  });

  return (
    <section
      aria-label={title}
      className={cn("bg-card border-border flex shrink-0 flex-col gap-2.5 rounded-[14px] border px-3.5 pt-3.5 pb-3", className)}
    >
      <div className="flex items-center gap-1">
        <div className="flex min-w-0 flex-1 flex-col gap-px">
          <span className="text-sm font-semibold">{title}</span>
          <span className="text-muted-foreground/80 text-[11.5px] tabular-nums">
            {total ? `${plural(total, noun[0], noun[1])} on ${plural(days, "day")}` : `No ${noun[1]} this month`}
          </span>
        </div>
        <MonthButton label="Previous month" disabled={month <= min} onClick={() => onMonth(month - 1)}>
          <ChevronLeft />
        </MonthButton>
        <MonthButton label="Next month" disabled={month >= max} onClick={() => onMonth(month + 1)}>
          <ChevronRight />
        </MonthButton>
      </div>

      <div
        aria-hidden
        className="text-muted-foreground/80 grid grid-cols-[repeat(7,32px)] justify-between gap-1 text-center text-[10.5px] font-semibold"
      >
        {WEEKDAYS.map((day, index) => (
          <span key={index}>{day}</span>
        ))}
      </div>

      <div className="grid grid-cols-[repeat(7,32px)] justify-between gap-1">
        {cells.map((cell, index) =>
          cell ? (
            <button
              key={cell.key}
              type="button"
              // A day with nothing on it has nowhere to go; it says so by not being a target.
              disabled={!cell.n}
              aria-label={`${cell.day} ${monthName}${cell.key === today ? ", today" : ""}, ${
                cell.n ? plural(cell.n, noun[0], noun[1]) : `no ${noun[1]}`
              }`}
              aria-pressed={cell.key === selected}
              onClick={() => onPick(cell.key)}
              className={cn(
                "focus-visible:ring-ring flex size-8 items-center justify-center rounded-[7px] p-0 text-xs tabular-nums outline-none transition-shadow focus-visible:ring-2 disabled:cursor-default",
                TINT[heatLevel(cell.n)],
                cell.n ? "text-foreground hover:ring-foreground/30 hover:ring-1" : "text-muted-foreground/50",
                cell.key === selected && "border-foreground border-[1.5px] font-semibold",
              )}
            >
              {cell.day}
            </button>
          ) : (
            <span key={`blank-${index}`} className="size-8" />
          ),
        )}
      </div>

      <div aria-hidden className="text-muted-foreground/80 flex items-center gap-1 pt-0.5 text-[11px]">
        <span className="me-1">Fewer</span>
        {TINT.map((tint, index) => (
          <span key={tint} className={cn("size-2.5 rounded-[3px]", tint, index === 0 && "border-border border")} />
        ))}
        <span className="ms-1">More</span>
      </div>
    </section>
  );
}

function MonthButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="text-muted-foreground hover:bg-muted/50 hover:text-foreground disabled:text-muted-foreground/50 flex size-7 items-center justify-center rounded-[7px] transition-colors disabled:cursor-default disabled:hover:bg-transparent [&_svg]:size-[15px]"
    >
      {children}
    </button>
  );
}
