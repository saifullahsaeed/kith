/** Pieces the Reminders and Schedules screens share: the composer card, the time field, a small
 *  menu, the chat a thing posts to, and an action that can say it failed. */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Clock } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useQuery } from "@tanstack/react-query";

import { fetchConversations } from "@/lib/backend/conversations";
import { keys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/** The design's strong hairline, for a field that holds what you are typing. */
export const STRONG_BORDER = "border-[color-mix(in_oklab,var(--foreground)_22%,transparent)]";

/** The composer: what · when · go, in one card. Same grammar on both screens. */
export function ComposerCard({
  label,
  htmlFor,
  onSubmit,
  children,
  ariaLabel,
}: {
  label: string;
  htmlFor: string;
  onSubmit: () => void;
  children: ReactNode;
  ariaLabel: string;
}) {
  return (
    <form
      aria-label={ariaLabel}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      className="bg-card border-border flex shrink-0 flex-col gap-2.5 rounded-[14px] border px-4 pt-3.5 pb-4"
    >
      <label htmlFor={htmlFor} className="text-muted-foreground/80 text-[10.5px] leading-[1.2] font-semibold tracking-[0.1em] uppercase">
        {label}
      </label>
      {children}
    </form>
  );
}

/** The primary button's return-key hint. */
export function EnterHint() {
  return (
    <span
      aria-hidden
      className="h-4 rounded-[5px] border border-current/30 px-1 font-mono text-[11px] leading-[14px] font-medium opacity-75"
    >
      ↵
    </span>
  );
}

/** A clock typed as `HH:MM`, in the design's small field. */
export function TimeField({
  id,
  value,
  onChange,
  onBlur,
  invalid,
  label = "Time",
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  invalid?: boolean;
  label?: string;
}) {
  return (
    <span
      className={cn(
        "bg-background border-input focus-within:border-ring flex h-[34px] shrink-0 items-center gap-[7px] rounded-lg border px-2.5 transition-colors",
        invalid && "border-destructive/60",
      )}
    >
      <Clock aria-hidden className="text-muted-foreground/80 size-[13px] shrink-0" />
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        aria-invalid={invalid || undefined}
        className="text-foreground h-[30px] w-11 bg-transparent font-mono text-[13px] tabular-nums outline-none"
      />
    </span>
  );
}

/* ── A small menu: Snooze, Remind again, a schedule's overflow ──────────── */

export const Menu = DropdownMenu.Root;
export const MenuTrigger = DropdownMenu.Trigger;

export function MenuContent({ children, align = "end" }: { children: ReactNode; align?: "start" | "end" }) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        align={align}
        sideOffset={4}
        className="animate-in fade-in-0 zoom-in-95 border-border/70 bg-popover/95 text-popover-foreground z-50 min-w-48 overflow-hidden rounded-xl border p-1 shadow-xl backdrop-blur-xl duration-100"
      >
        {children}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  );
}

export function MenuItem({
  children,
  onSelect,
  hint,
  danger,
  icon,
}: {
  children: ReactNode;
  onSelect: () => void;
  hint?: string;
  danger?: boolean;
  icon?: ReactNode;
}) {
  return (
    <DropdownMenu.Item
      onSelect={onSelect}
      className={cn(
        "flex cursor-default items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] outline-none select-none",
        "data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
        danger && "text-destructive data-[highlighted]:bg-destructive/10 data-[highlighted]:text-destructive",
      )}
    >
      {icon ? <span className="flex size-4 shrink-0 items-center justify-center [&_svg]:size-3.5">{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint ? <span className="text-muted-foreground/70 shrink-0 font-mono text-[10.5px]">{hint}</span> : null}
    </DropdownMenu.Item>
  );
}

export function MenuSeparator() {
  return <DropdownMenu.Separator className="bg-border/60 -mx-1 my-1 h-px" />;
}

/* ── Which chat a thing posts to ────────────────────────────────────────── */

export interface ChatInfo {
  title: string;
  projectId: number | null;
}

/**
 * Chat titles by id, for "he'll pick it up in the … chat".
 *
 * The board snapshot carries only the id. The history list carries the titles, and the cap is the
 * route's own 500 — reminders reach back to August, well past the 50 the chat panel asks for.
 */
export function useChats(): (conversationId: string | null | undefined) => ChatInfo | undefined {
  const list = useQuery({
    queryKey: keys.conversations(500, ""),
    queryFn: () => fetchConversations(500),
    staleTime: 60_000,
  });
  const byId = useMemo(() => {
    const map = new Map<string, ChatInfo>();
    for (const chat of list.data?.conversations ?? []) map.set(chat.id, { title: chat.title, projectId: chat.projectId });
    return map;
  }, [list.data]);
  return useCallback((id) => (id ? byId.get(id) : undefined), [byId]);
}

/* ── An action that can fail out loud ───────────────────────────────────── */

/**
 * Runs one write at a time per key and remembers what went wrong with it.
 *
 * The panel's `create`/`update` throw when the server says no, and before this the screens fired
 * them and forgot — a refused edit looked exactly like a slow one.
 */
export function useActions() {
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const run = useCallback(async (key: string, work: () => unknown, failure: string): Promise<boolean> => {
    setBusy(key);
    setErrors(({ [key]: _gone, ...rest }) => rest);
    try {
      await Promise.resolve(work());
      return true;
    } catch {
      setErrors((current) => ({ ...current, [key]: failure }));
      return false;
    } finally {
      setBusy((current) => (current === key ? null : current));
    }
  }, []);
  const clear = useCallback((key: string) => setErrors(({ [key]: _gone, ...rest }) => rest), []);
  return { busy, errors, run, clear };
}
