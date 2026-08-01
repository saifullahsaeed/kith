import { useEffect, useState } from "react";
import { Check, ChevronDown, Gauge, ShieldCheck } from "lucide-react";

import {
  fetchPermissions,
  setPermissionMode,
  MODE_LABELS,
  type PermissionMode,
  type PermissionRequest,
} from "@/lib/backend";
import { cn } from "@/lib/utils";

/**
 * The two things worth changing mid-conversation, in the title bar.
 *
 * Both were settings buried three clicks away, and both are decisions you make *about a
 * particular piece of work* rather than once and forever: how much you want him thinking
 * about this, and how much rope he has on your machine right now. A control you have to
 * go looking for is a control you leave on whatever it was.
 *
 * The mode especially. He runs on the real computer now, and "what is he allowed to do"
 * stopped being a preference the moment the container went away — it belongs somewhere you
 * can see without asking for it.
 */
const EFFORTS = [
  { value: "", label: "Default", hint: "Let the model decide — usually the right answer." },
  { value: "low", label: "Low", hint: "Barely thinks. Fast and cheap; fine for small asks." },
  { value: "medium", label: "Medium", hint: "A moderate amount of reasoning before answering." },
  { value: "high", label: "High", hint: "Thinks hard. Slower and dearer; for the difficult ones." },
] as const;

export function HeaderControls({
  effort,
  supportsEffort,
  onEffort,
}: {
  effort: string;
  /** False when the model's provider does not accept a reasoning parameter — the control
   *  is hidden rather than shown broken, because sending it anyway is a 400. */
  supportsEffort: boolean;
  onEffort: (effort: string) => void;
}) {
  const [mode, setMode] = useState<PermissionMode>("ask");
  const [pending, setPending] = useState<PermissionRequest[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchPermissions()
        .then((state) => {
          if (!alive) return;
          setMode(state.mode);
          setPending(state.pending);
        })
        .catch(() => {});
    load();
    // Polled rather than pushed: a request can appear from an autonomy tick with nobody
    // watching, and the count in the header is how you find out.
    const timer = setInterval(load, 4_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex items-center gap-0.5">
      {supportsEffort ? (
        <Picker
          icon={<Gauge className="size-3.5" />}
          // The noun, because the value alone named nothing. A title bar reading "High" next
          // to "Auto" is two words that could be about anything — refresh rate, verbosity,
          // volume — and the one thing they are actually about is what you would have to open
          // the menu to find out.
          noun="Thinking"
          label={EFFORTS.find((one) => one.value === effort)?.label ?? "Default"}
          title="How hard he thinks before answering"
          options={EFFORTS.map((one) => ({ ...one }))}
          selected={effort}
          onSelect={onEffort}
        />
      ) : null}

      <Picker
        icon={
          <ShieldCheck
            className={cn(
              "size-3.5",
              mode === "bypass" && "text-destructive",
              mode === "auto" && "text-roam",
            )}
          />
        }
        noun="Access"
        label={MODE_LABELS[mode].label}
        badge={pending.length > 0 ? pending.length : undefined}
        title="What he may do on this computer without asking"
        options={(["ask", "auto", "bypass"] as PermissionMode[]).map((one) => ({
          value: one,
          label: MODE_LABELS[one].label,
          hint: MODE_LABELS[one].hint,
        }))}
        selected={mode}
        onSelect={(next) => {
          setMode(next as PermissionMode);
          void setPermissionMode(next as PermissionMode).catch(() => {});
        }}
      />
    </div>
  );
}

function Picker({
  icon,
  noun,
  label,
  badge,
  title,
  options,
  selected,
  onSelect,
}: {
  icon: React.ReactNode;
  /** What the value is *about* — "Thinking", "Access". Dropped below `lg`, where the icon has
   *  to carry it and the bar has no room for prose. */
  noun?: string;
  label: string;
  badge?: number;
  title: string;
  options: { value: string; label: string; hint: string }[];
  selected: string;
  onSelect: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);

  // Escape and click-away, because a menu in a title bar that only closes by re-clicking
  // its own button is a menu you end up dragging the window with.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    const onDown = () => setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <div className="relative">
      <button
        type="button"
        title={title}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => setOpen((was) => !was)}
        className="text-muted-foreground hover:text-foreground hover:bg-accent/60 flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] transition-colors"
      >
        {icon}
        {noun ? <span className="text-muted-foreground/55 hidden lg:inline">{noun}</span> : null}
        <span className="font-mono">{label}</span>
        {badge ? (
          <span className="bg-destructive/90 text-destructive-foreground rounded-full px-1.5 text-[10px] leading-4">
            {badge}
          </span>
        ) : null}
        <ChevronDown className="size-3 opacity-50" />
      </button>

      {open ? (
        <div
          onPointerDown={(event) => event.stopPropagation()}
          className="border-border/70 bg-popover/95 absolute end-0 top-full z-50 mt-1 w-72 rounded-xl border p-1 shadow-xl backdrop-blur-xl"
        >
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onSelect(option.value);
                setOpen(false);
              }}
              className="hover:bg-accent flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left"
            >
              <span className="mt-0.5 size-3.5 shrink-0">
                {option.value === selected ? <Check className="text-kith size-3.5" /> : null}
              </span>
              <span className="min-w-0">
                <span className="block text-sm">{option.label}</span>
                <span className="text-muted-foreground/80 block text-[11px] leading-snug">
                  {option.hint}
                </span>
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
