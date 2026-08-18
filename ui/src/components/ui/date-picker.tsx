import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Picking a date, without the native control.
 *
 * `<input type="date">` was here and it is a poor thing in an app like this. It renders in
 * the platform's own idiom rather than the app's — a light box with a grey system calendar in
 * a dark window — you cannot restyle the popup at all, the text field silently accepts
 * nonsense until it loses focus, and the whole affair says "31/07/2026" when what anyone
 * wants to read is "Fri 31 Jul".
 *
 * So: a real popover, in the app's own language. Three things it does that the native one
 * cannot, and each of them is why this was worth writing rather than living with:
 *
 * * **Shortcuts.** Nearly every date anyone sets by hand is today, tomorrow, or a week out.
 *   Those are one click instead of navigating a grid to find a square.
 * * **It can be cleared.** "No due date" is a normal thing to want and the native input
 *   makes it a fight.
 * * **Keyboard.** Arrows move by day and week, Enter picks, Escape closes — the same as
 *   every calendar people already know, which the native one only half provides.
 *
 * Dates are handled as plain `YYYY-MM-DD` strings throughout, never as Date objects in
 * state. A Date is a moment in time, and a due date is not: constructing one puts the value
 * in the local zone, and anyone east of UTC then watches their date move a day when it is
 * serialised. The only Date here is a scratch value for arithmetic on the grid.
 */
const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

export function DatePicker({
  value,
  onChange,
  placeholder = "No date",
  className = "",
  ariaLabel = "Date",
}: {
  /** `YYYY-MM-DD`, or empty for none. */
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(() => value || today());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) setCursor(value || today());
  }, [open, value]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Claim it. An open calendar is the innermost thing on screen, so its Escape is its
        // own — unclaimed, the same key kept travelling up and closed the whole control panel.
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        return;
      }
      const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
      if (step) {
        event.preventDefault();
        setCursor((current) => shift(current, step));
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        onChange(cursor);
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, cursor, onChange]);

  const grid = useMemo(() => monthGrid(cursor), [cursor]);
  const overdue = Boolean(value) && value < today();

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={() => setOpen((was) => !was)}
        className={cn(
          "border-border/60 bg-card/40 hover:border-border focus-visible:border-ring focus-visible:ring-ring/25 flex w-full items-center gap-2 rounded-lg border px-3 py-1.5 text-left text-sm outline-none transition-colors focus-visible:ring-[3px]",
          !value && "text-muted-foreground",
        )}
      >
        <CalendarDays className="text-muted-foreground/70 size-3.5 shrink-0" />
        <span className={cn("min-w-0 flex-1 truncate", overdue && "text-destructive")}>
          {value ? human(value) : placeholder}
        </span>
        {value ? (
          <span
            role="button"
            tabIndex={-1}
            aria-label="Clear the date"
            title="Clear"
            onClick={(event) => {
              // Clearing is not opening: without this the popover flashes open behind it.
              event.stopPropagation();
              onChange("");
            }}
            className="text-muted-foreground/50 hover:text-foreground shrink-0"
          >
            <X className="size-3.5" />
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="border-border/70 bg-popover/95 absolute start-0 top-full z-50 mt-1 w-[17rem] rounded-xl border p-2 shadow-xl backdrop-blur-xl">
          <div className="flex items-center gap-1 px-1 pb-1.5">
            <button
              type="button"
              aria-label="Previous month"
              onClick={() => setCursor(shiftMonth(cursor, -1))}
              className="hover:bg-accent text-muted-foreground hover:text-foreground rounded-md p-1"
            >
              <ChevronLeft className="size-4" />
            </button>
            <span className="flex-1 text-center text-sm font-medium">{monthLabel(cursor)}</span>
            <button
              type="button"
              aria-label="Next month"
              onClick={() => setCursor(shiftMonth(cursor, 1))}
              className="hover:bg-accent text-muted-foreground hover:text-foreground rounded-md p-1"
            >
              <ChevronRight className="size-4" />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-0.5 px-1">
            {WEEKDAYS.map((day) => (
              <span
                key={day}
                className="text-muted-foreground/50 py-1 text-center text-[10px] font-medium"
              >
                {day}
              </span>
            ))}
            {grid.map((day) => {
              const outside = day.slice(0, 7) !== cursor.slice(0, 7);
              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => {
                    onChange(day);
                    setOpen(false);
                  }}
                  className={cn(
                    "h-7 rounded-md text-center text-[12px] tabular-nums transition-colors",
                    outside && "text-muted-foreground/30",
                    !outside && "hover:bg-accent",
                    day === today() && day !== value && "text-kith font-semibold",
                    day === value && "bg-kith text-primary-foreground font-semibold",
                    // The keyboard cursor, shown only when it has moved off the selection —
                    // a ring around the selected day would just read as a rendering fault.
                    day === cursor && day !== value && "ring-ring ring-1",
                  )}
                >
                  {Number(day.slice(8, 10))}
                </button>
              );
            })}
          </div>

          {/* Where most hand-set dates actually land. */}
          <div className="border-border/60 mt-2 flex items-center gap-1 border-t pt-2">
            {(
              [
                ["Today", 0],
                ["Tomorrow", 1],
                ["In a week", 7],
              ] as const
            ).map(([label, days]) => (
              <button
                key={label}
                type="button"
                onClick={() => {
                  onChange(shift(today(), days));
                  setOpen(false);
                }}
                className="hover:bg-accent text-muted-foreground hover:text-foreground rounded-md px-2 py-1 text-[11px]"
              >
                {label}
              </button>
            ))}
            <div className="flex-1" />
            {value ? (
              <button
                type="button"
                onClick={() => {
                  onChange("");
                  setOpen(false);
                }}
                className="text-muted-foreground hover:text-destructive rounded-md px-2 py-1 text-[11px]"
              >
                Clear
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Today as `YYYY-MM-DD`, in the reader's own zone — "today" is a local idea. */
function today(): string {
  const now = new Date();
  return iso(now.getFullYear(), now.getMonth(), now.getDate());
}

function iso(year: number, monthIndex: number, day: number): string {
  return `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** Move by days. Via a local Date on purpose: month lengths and leap years are its problem,
 *  not ours, and it never leaves this function. */
function shift(date: string, days: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const moved = new Date(y, m - 1, d + days);
  return iso(moved.getFullYear(), moved.getMonth(), moved.getDate());
}

/** Move by months, clamping the day. Jan 31 back a month is Feb 28, not Mar 3. */
function shiftMonth(date: string, months: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const target = new Date(y, m - 1 + months, 1);
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  return iso(target.getFullYear(), target.getMonth(), Math.min(d, lastDay));
}

/** Six weeks from the Monday on or before the 1st, so the grid never changes height. */
function monthGrid(date: string): string[] {
  const [y, m] = date.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const weekday = (first.getDay() + 6) % 7; // Monday-first
  const start = new Date(y, m - 1, 1 - weekday);
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
    return iso(day.getFullYear(), day.getMonth(), day.getDate());
  });
}

function monthLabel(date: string): string {
  const [y, m] = date.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

/** "Fri 31 Jul" — or with the year when it is not this one, since a bare "31 Jul" on a date
 *  two years out is the kind of thing nobody notices until it matters. */
function human(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  const when = new Date(y, m - 1, d);
  const sameYear = y === new Date().getFullYear();
  return when.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}
