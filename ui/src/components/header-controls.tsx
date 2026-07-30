import { useEffect, useState } from "react";
import { Check, ChevronDown, Gauge, ShieldCheck, X } from "lucide-react";

import {
  answerPermission,
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
    <div className="flex items-center gap-1">
      {supportsEffort ? (
        <Picker
          icon={<Gauge className="size-3.5" />}
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
        pending={pending}
        onAnswer={(id, allow, scope) => {
          setPending((was) => was.filter((one) => one.id !== id));
          void answerPermission(id, allow, scope).catch(() => {});
        }}
      />
    </div>
  );
}

function Picker({
  icon,
  label,
  badge,
  title,
  options,
  selected,
  onSelect,
  pending = [],
  onAnswer,
}: {
  icon: React.ReactNode;
  label: string;
  badge?: number;
  title: string;
  options: { value: string; label: string; hint: string }[];
  selected: string;
  onSelect: (value: string) => void;
  /** Things he is waiting on. Answered here rather than in the chat, because a tick can
   *  raise one with nobody looking at a conversation. */
  pending?: PermissionRequest[];
  onAnswer?: (id: string, allow: boolean, scope: "session" | "always") => void;
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
          {pending.length > 0 && onAnswer ? (
            <div className="border-border/60 mb-1 border-b pb-1">
              {pending.map((request) => (
                <div key={request.id} className="px-2.5 py-2">
                  <p className="text-[11px] leading-snug">{request.why}</p>
                  <p
                    className="text-muted-foreground/70 mt-0.5 truncate font-mono text-[10px]"
                    title={request.what}
                  >
                    {request.what}
                  </p>
                  <div className="mt-1.5 flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => onAnswer(request.id, true, "session")}
                      className="bg-kith-soft/60 hover:bg-kith-soft rounded px-2 py-0.5 text-[11px]"
                    >
                      Allow once
                    </button>
                    <button
                      type="button"
                      onClick={() => onAnswer(request.id, true, "always")}
                      className="hover:bg-accent rounded px-2 py-0.5 text-[11px]"
                      title="Remember this, across restarts"
                    >
                      Always
                    </button>
                    <button
                      type="button"
                      onClick={() => onAnswer(request.id, false, "session")}
                      className="text-muted-foreground hover:text-destructive ms-auto rounded p-0.5"
                      title="No"
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}

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
