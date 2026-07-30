import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface DropdownOption {
  value: string;
  label: ReactNode;
}

/** A small, theme-aware select. Drop-in for a native <select> (value + onChange),
 * styled to match Presence in both light and dark. Options may be plain strings. */
export function Dropdown({
  value,
  options,
  onChange,
  className = "",
  ariaLabel,
  align = "start",
  variant = "default",
}: {
  value: string;
  options: (DropdownOption | string)[];
  onChange: (value: string) => void;
  className?: string;
  ariaLabel?: string;
  align?: "start" | "end";
  variant?: "default" | "bare";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const opts: DropdownOption[] = options.map((o) => (typeof o === "string" ? { value: o, label: o } : o));
  const current = opts.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={
          variant === "bare"
            ? "flex w-full items-center justify-between gap-0.5 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground"
            : "flex w-full items-center justify-between gap-1 rounded-md border bg-transparent px-2 py-1 text-sm outline-none transition-colors hover:border-ring focus-visible:border-ring"
        }
      >
        <span className="truncate">{current?.label ?? value}</span>
        <ChevronDown className={`size-3 shrink-0 opacity-60 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? (
        <div
          role="listbox"
          className={`absolute z-50 mt-1 max-h-60 min-w-full overflow-auto rounded-lg border bg-popover p-1 shadow-xl ${align === "end" ? "right-0" : "left-0"}`}
        >
          {opts.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent"
            >
              <Check className={`size-3.5 shrink-0 ${o.value === value ? "text-kith" : "opacity-0"}`} />
              <span className="truncate">{o.label}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
