import { type ReactNode } from "react";

import { cn } from "@/lib/utils";
import type { Tunable } from "@/lib/backend";

/**
 * One setting, in the shape every settings page uses.
 *
 * Twelve pages were twelve layouts: each invented its own spacing, its own idea of where a
 * control sits, and its own answer to "has this been changed". A row that looks the same
 * everywhere is most of what makes a settings screen read as one product rather than as a pile
 * of forms that happen to share a sidebar.
 *
 * The anatomy is fixed and the reasons are worth keeping:
 *
 * - **Name, then what happens if you change it.** The help line is a consequence, never a
 *   restatement of the label. A row with nothing worth saying gets no help line rather than a
 *   filler one, which is why `help` is optional rather than required-and-padded.
 * - **The control sits at a fixed right edge**, so a column of rows has one alignment to read
 *   down instead of one per row.
 * - **A dot for away-from-default.** `isDefault` is on every `Tunable` and was being shown on
 *   exactly one page; "what have I changed" is the question you arrive with on all of them.
 */
export function SettingRow({
  label,
  help,
  changed,
  error,
  disabled,
  disabledWhy,
  children,
}: {
  label: ReactNode;
  help?: ReactNode;
  /** Away from its default. Draws the dot, and counts toward the sidebar's badge. */
  changed?: boolean;
  /** Refused, with the bound and the reason for it — shown in the row, not in a toast that
   *  outlives the mistake. Replaces the help line while it stands. */
  error?: string;
  /** Nothing here would do anything. Dimmed rather than hidden: hiding it leaves someone
   *  hunting for a control they remember seeing. */
  disabled?: boolean;
  /** What would make it work. Required in spirit whenever `disabled` is set — a greyed row
   *  with no explanation is the thing this is meant to prevent. */
  disabledWhy?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "border-border/60 flex items-center gap-5 px-4 py-3.5 transition-colors first:border-t-0 [&+&]:border-t",
        disabled ? "opacity-55" : "hover:bg-accent/30",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[13px]">
          {changed ? (
            <span
              aria-hidden
              title="Changed from its default"
              className="bg-kith size-[5px] shrink-0 rounded-full"
            />
          ) : null}
          {label}
        </div>
        {error ? (
          <p className="text-destructive mt-1 text-[11.5px] leading-snug">{error}</p>
        ) : help || disabledWhy ? (
          <p className="text-muted-foreground mt-0.5 max-w-[58ch] text-[11.5px] leading-[1.45]">
            {disabled && disabledWhy ? disabledWhy : help}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}

/** The card a run of rows sits in. Separate from the row so a page can put anything in one. */
export function SettingRows({ children }: { children: ReactNode }) {
  return (
    <div className="border-border/60 bg-card overflow-hidden rounded-xl border">{children}</div>
  );
}

/** A titled run of rows. `sub` sits beside the title rather than under it: a settings page is a
 *  list of sections, and a two-line heading before every one of them is most of the page. */
export function SettingSection({
  title,
  sub,
  action,
  children,
}: {
  title: string;
  sub?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-6 first:mt-0">
      <div className="mb-2.5 flex items-baseline gap-2.5">
        <h2 className="text-[13px] font-semibold">{title}</h2>
        {sub ? <p className="text-muted-foreground min-w-0 flex-1 text-[11.5px]">{sub}</p> : null}
        {action ? <div className="ms-auto">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

/* ── the four controls ──────────────────────────────────────────────────────────────────── */

/** A number, with its unit *outside* the field — so the number stays selectable and the unit
 *  never scrolls into the value. */
export function NumberField({
  value,
  unit,
  invalid,
  disabled,
  wide,
  onChange,
}: {
  value: number | string;
  unit?: string;
  invalid?: boolean;
  disabled?: boolean;
  /** For a `text` setting. "http://127.0.0.1:11434" and "nomic-embed-text" both truncated to
   *  about half of themselves in a field sized for four digits. */
  wide?: boolean;
  onChange: (raw: string) => void;
}) {
  return (
    <>
      <input
        type="text"
        inputMode="decimal"
        value={String(value)}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={cn(
          "border-border/60 bg-background h-[30px] rounded-[9px] border px-2.5 font-mono text-xs outline-none",
          wide ? "w-[232px] text-left" : "w-[108px] text-right",
          "focus:border-kith/55 focus:ring-kith/18 focus:ring-[3px]",
          invalid && "border-destructive/55 focus:border-destructive/55 focus:ring-destructive/18",
          disabled && "cursor-not-allowed",
        )}
      />
      {/* Fixed width, left-aligned, so every field in a column shares one right edge.
       *
       * Without it the *unit* is what lines up and the fields go ragged — "rounds" and
       * "characters" differ by thirty pixels, so seven rows of numbers step in and out as you
       * read down them. Rendered even when there is no unit, for the same reason. */}
      <span className="text-muted-foreground w-[62px] shrink-0 text-[10.5px]">{unit}</span>
    </>
  );
}

/** Three or four choices. More than that is a select; fewer is a switch. */
export function Segmented<T extends string>({
  options,
  value,
  disabled,
  onChange,
}: {
  options: readonly { value: T; label: string }[];
  value: T;
  disabled?: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <div className="border-border/60 bg-muted flex gap-0.5 rounded-[10px] border p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          disabled={disabled}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cn(
            "h-6 rounded-lg px-2.5 text-[11.5px] transition-colors",
            option.value === value
              ? "bg-background text-foreground shadow-[0_1px_2px_oklch(0_0_0/6%)]"
              : "text-muted-foreground hover:text-foreground",
            disabled && "cursor-not-allowed",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** On or off. A checkbox under the paint, so it is reachable by keyboard and announced. */
export function Switch({
  checked,
  label,
  disabled,
  onChange,
}: {
  checked: boolean;
  /** What it switches — read by a screen reader, since the track says nothing on its own. */
  label: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={cn("relative inline-flex", disabled && "cursor-not-allowed")}>
      <input
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden
        className={cn(
          "border-border/60 peer-focus-visible:ring-kith/60 block h-[21px] w-9 rounded-full border transition-colors peer-focus-visible:ring-2",
          checked ? "bg-kith border-transparent" : "bg-muted",
        )}
      >
        <span
          className={cn(
            "bg-background absolute top-[3px] block size-[15px] rounded-full shadow-[0_1px_2px_oklch(0_0_0/18%)] transition-[left]",
            checked ? "left-[18px]" : "left-[3px]",
          )}
        />
      </span>
    </label>
  );
}

/** A path, and whatever you can do to it. */
export function PathValue({ children }: { children: ReactNode }) {
  return (
    <span
      title={typeof children === "string" ? children : undefined}
      className="border-border/60 bg-muted max-w-[22rem] truncate rounded-lg border px-2 py-1 font-mono text-[11.5px]"
    >
      {children}
    </span>
  );
}

/* ── what a Tunable becomes ─────────────────────────────────────────────────────────────── */

/** The bound, as a sentence, or "" when the value is inside it.
 *
 * Says the bound *and why it exists* where the server gave us enough to: a range with no
 * explanation is a rule you obey without learning anything. */
export function boundError(knob: Tunable, raw: string): string {
  if (knob.kind === "bool" || knob.kind === "text") return "";
  const value = Number(raw);
  if (raw.trim() === "" || Number.isNaN(value)) return "Needs a number.";
  if (knob.min !== null && value < knob.min)
    return `Must be at least ${knob.min}${knob.unit ? ` ${knob.unit}` : ""}.`;
  if (knob.max !== null && value > knob.max)
    return `Must be at most ${knob.max}${knob.unit ? ` ${knob.unit}` : ""}.`;
  return "";
}

/** "Default is 25." — appended to the help so a changed row says what it changed *from*.
 *  Only where it adds something: on a boolean the dot plus the switch already say it. */
export function defaultNote(knob: Tunable): string {
  if (knob.isDefault || knob.kind === "bool") return "";
  return ` Default is ${String(knob.default)}${knob.unit ? ` ${knob.unit}` : ""}.`;
}
