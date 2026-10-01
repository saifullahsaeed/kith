/**
 * Schedules: standing jobs he runs on a cadence.
 *
 * Drawn from the 2026-09-30 board. The composer is the Reminders one with a cadence where the
 * quick times were; each job is a card that answers "when next, when last, where does it report"
 * at a glance, with pause/resume on the job itself. The right column is the reason most of these
 * should exist: the reminders he keeps being asked for, grouped, each one a job you can add.
 *
 * "Weekdays at" is on the board and is shown, but disabled: the server takes `daily_at` or
 * `every_minutes` and nothing else, and a "weekdays" that quietly ran on Fridays and Saturdays
 * would be worse than one that says it is not there yet.
 */
import { useMemo, useState, type ReactNode } from "react";
import { Bell, Copy, Lightbulb, MoreHorizontal, Plus, Repeat, Trash2, X } from "lucide-react";

import { ItemMenu } from "@/components/ui/item-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useNow } from "@/hooks/use-now";
import { deleteBrainItem, type BrainSnapshot, type Schedule } from "@/lib/backend/brain";
import { copyText } from "@/lib/files";
import { cn } from "@/lib/utils";
import { BUTTON, Chip, Empty, KindTile, Label, PageTitle } from "./chrome";
import { matches } from "./format";
import { suggestSchedules, type Suggestion } from "./reminder-patterns";
import {
  cadenceBadge,
  cadencePreview,
  clock,
  clockText,
  countdown,
  dayMonth,
  dayOffset,
  parseClock,
  weekStrip,
} from "./reminder-time";
import {
  ComposerCard,
  EnterHint,
  Menu,
  MenuContent,
  MenuItem,
  MenuSeparator,
  MenuTrigger,
  TimeField,
  useActions,
  useChats,
} from "./timed-parts";
import type { Handlers } from "./types";

type Cadence = "daily" | "weekdays" | "every";

const ms = (iso: string | null | undefined) => {
  const t = iso ? new Date(iso).getTime() : Number.NaN;
  return Number.isNaN(t) ? null : t;
};

/** Suggestions you waved away stay away — on this machine, which is all a dismissal needs. */
const DISMISSED_KEY = "kith.control-panel.dismissed-schedule-suggestions";

function readDismissed(): string[] {
  try {
    const raw = window.localStorage.getItem(DISMISSED_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((key): key is string => typeof key === "string") : [];
  } catch {
    return [];
  }
}

function writeDismissed(keys: string[]) {
  try {
    window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(keys));
  } catch {
    // A private window: the dismissal lasts as long as the page does.
  }
}

/** "Today 9:00 AM", "Tomorrow 9:00 AM", "Fri 2 Oct 9:00 AM" — the board's way, without "at". */
function dayClock(at: Date, now: Date): string {
  const days = dayOffset(at, now);
  const day = days === 0 ? "Today" : days === 1 ? "Tomorrow" : days === -1 ? "Yesterday" : dayMonth(at);
  return `${day} ${clock(at)}`;
}

export function Schedules({
  snap,
  query,
  remove,
  create,
  update,
  refresh,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const tick = useNow(30_000);
  const now = useMemo(() => new Date(tick), [tick]);
  const chatOf = useChats();
  const actions = useActions();
  const items = snap.schedules.filter((schedule) => matches(query, schedule.note, schedule.status));
  const running = items.filter((schedule) => schedule.status === "active").length;
  const paused = items.length - running;

  /* ── The composer ── */
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<Cadence>("daily");
  const [at, setAt] = useState("09:00");
  const [every, setEvery] = useState("60");
  const minutes = Number(every);
  const preview =
    mode === "every"
      ? cadencePreview({ kind: "every", minutes }, now)
      : mode === "daily"
        ? cadencePreview({ kind: "daily", at }, now)
        : null;

  const add = async () => {
    const text = note.trim();
    if (!text || !preview) return;
    const clockAt = parseClock(at);
    const body =
      mode === "every"
        ? { note: text, every_minutes: Math.round(minutes) }
        : clockAt
          ? { note: text, daily_at: clockText(clockAt.hour, clockAt.minute) }
          : null;
    if (!body) return;
    const ok = await actions.run("compose", () => create("schedule", body), "The server didn’t take it — nothing was added.");
    if (ok) setNote("");
  };

  /* ── Suggestions: from his real reminders, minus what a schedule already covers ── */
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);
  // The suggestion just taken, so its job can say where it came from and offer to undo it.
  const [taken, setTaken] = useState<{ note: string; basis: string; since: number } | null>(null);
  const projectFor = (conversationId: string) => {
    const projectId = chatOf(conversationId)?.projectId;
    return projectId ? snap.projects.find((project) => project.id === projectId)?.name : undefined;
  };
  const suggestions = suggestSchedules(snap.reminders, snap.schedules, projectFor).filter(
    (suggestion) => !dismissed.includes(suggestion.key),
  );
  const pastCount = snap.reminders.filter((reminder) => reminder.status !== "pending").length;

  const takeSuggestion = async (suggestion: Suggestion) => {
    const ok = await actions.run(
      `suggest-${suggestion.key}`,
      () =>
        create("schedule", {
          note: suggestion.note,
          daily_at: suggestion.dailyAt,
          // Where the reminders behind it reported, so the job reports there too.
          ...(suggestion.conversationId ? { conversation_id: suggestion.conversationId } : {}),
        }),
      "The server didn’t take it — nothing was added.",
    );
    if (ok) setTaken({ note: suggestion.note, basis: suggestion.basis, since: Date.now() - 5_000 });
  };
  const dismiss = (key: string) => {
    const next = [...new Set([...dismissed, key])];
    setDismissed(next);
    writeDismissed(next);
  };

  /** Undo is not a delete you are asked about — you made it a second ago, from here. */
  const undo = async (schedule: Schedule) => {
    const ok = await actions.run(`job-${schedule.id}`, () => deleteBrainItem("schedule", schedule.id), "Couldn’t undo it.");
    if (ok) {
      setTaken(null);
      refresh();
    }
  };

  return (
    <div className="flex flex-col gap-[22px]">
      <PageTitle title="Schedules" count={snap.schedules.length} subtitle="Standing jobs he runs on a cadence." />

      {/* ── Composer: what · when · add (the Reminders composer's grammar) ── */}
      <ComposerCard label="Standing job" htmlFor="schedule-note" ariaLabel="New schedule" onSubmit={() => void add()}>
        <input
          id="schedule-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="What should he do each time — e.g. tell me what broke overnight on Sadeef AI"
          autoComplete="off"
          className="bg-background border-input focus-visible:border-ring text-foreground placeholder:text-muted-foreground/80 h-[46px] rounded-[10px] border px-3.5 text-sm outline-none transition-colors"
        />
        <div className="mt-0.5 flex items-center gap-2.5">
          <TooltipProvider delayDuration={200}>
            <div role="group" aria-label="Cadence" className="bg-sidebar border-border flex shrink-0 gap-1 rounded-[10px] border p-[3px]">
              <CadenceButton on={mode === "daily"} onClick={() => setMode("daily")}>
                Every day at
              </CadenceButton>
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* A disabled button gets no pointer events, so the tooltip hangs on its wrapper. */}
                  <span tabIndex={0} className="rounded-[7px]">
                    <CadenceButton on={false} disabled onClick={() => undefined} describedBy="weekdays-soon">
                      Weekdays at
                    </CadenceButton>
                  </span>
                </TooltipTrigger>
                <TooltipContent id="weekdays-soon">Coming soon — for now he can run a job every day, or every N minutes</TooltipContent>
              </Tooltip>
              <CadenceButton on={mode === "every"} onClick={() => setMode("every")}>
                Every N minutes
              </CadenceButton>
            </div>
          </TooltipProvider>

          {mode === "every" ? (
            <span className="bg-background border-input focus-within:border-ring flex h-[34px] shrink-0 items-center gap-[7px] rounded-lg border px-2.5 transition-colors">
              <Repeat aria-hidden className="text-muted-foreground/80 size-[13px]" />
              <label htmlFor="schedule-every" className="sr-only">
                Minutes between runs
              </label>
              <input
                id="schedule-every"
                type="text"
                inputMode="numeric"
                value={every}
                onChange={(event) => setEvery(event.target.value.replace(/[^\d]/g, ""))}
                aria-invalid={!preview || undefined}
                className="text-foreground h-[30px] w-10 bg-transparent font-mono text-[13px] tabular-nums outline-none"
              />
              <span className="text-muted-foreground/80 text-xs">min</span>
            </span>
          ) : (
            <TimeField
              id="schedule-time"
              value={at}
              invalid={!parseClock(at)}
              onChange={setAt}
              onBlur={() => {
                const parsed = parseClock(at);
                if (parsed) setAt(clockText(parsed.hour, parsed.minute));
              }}
            />
          )}

          <span className="flex-1" />
          <span role="status" className="text-muted-foreground flex min-w-0 items-center gap-[7px] text-[12.5px] whitespace-nowrap">
            <Repeat aria-hidden className="text-muted-foreground/80 size-[13px] shrink-0" />
            {preview ? (
              <span className="truncate">
                Runs <span className="text-foreground">{preview.cadence}</span> — {preview.first}
              </span>
            ) : (
              <span className="text-destructive">{mode === "every" ? "Give it a number of minutes" : "Type a time like 09:00"}</span>
            )}
          </span>
          <button
            type="submit"
            disabled={actions.busy === "compose" || !preview}
            className={cn(BUTTON.primary, "ml-3 gap-2 pr-2.5 pl-3.5 whitespace-nowrap")}
          >
            Add schedule
            <EnterHint />
          </button>
        </div>
        {actions.errors.compose ? (
          <p role="alert" className="text-destructive m-0 text-xs">
            {actions.errors.compose}
          </p>
        ) : null}
      </ComposerCard>

      <div className="grid grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-6">
        {/* ── Left: the jobs, and what a schedule is ── */}
        <div className="flex min-w-0 flex-col gap-7">
          <section aria-labelledby="jobs-h" className="flex flex-col gap-2">
            <div className="flex items-baseline gap-2 px-1">
              <Label count={items.length}>
                <span id="jobs-h">Standing jobs</span>
              </Label>
              <span className="flex-1" />
              {items.length ? (
                <span className="text-muted-foreground/80 text-xs">
                  {running ? `${running} running` : "none running"} · {paused ? `${paused} paused` : "none paused"}
                </span>
              ) : null}
            </div>
            {items.length === 0 ? (
              <Empty
                title={query ? `No standing job matches ‘${query}’` : "No standing jobs yet"}
                action={
                  query ? null : (
                    <button
                      type="button"
                      onClick={() => document.getElementById("schedule-note")?.focus()}
                      className={cn(BUTTON.secondary, BUTTON.small)}
                    >
                      Add a standing job
                    </button>
                  )
                }
              >
                {query
                  ? "The search narrows this list. Clear it to see every job."
                  : suggestions.length
                    ? "Add one above, or take one of the suggestions — he runs it on its cadence and reports in the chat it posts to."
                    : "Add one above and he runs it on its cadence, reporting in the chat it posts to."}
              </Empty>
            ) : (
              <div className="flex flex-col gap-2.5">
                {items.map((schedule) => (
                  <JobCard
                    key={schedule.id}
                    schedule={schedule}
                    now={now}
                    chat={chatOf(schedule.conversation_id)}
                    projectName={(id) => snap.projects.find((project) => project.id === id)?.name}
                    busy={actions.busy === `job-${schedule.id}`}
                    error={actions.errors[`job-${schedule.id}`]}
                    taken={
                      taken && schedule.note === taken.note && (ms(schedule.created_at) ?? 0) >= taken.since ? taken.basis : null
                    }
                    onToggle={() =>
                      void actions.run(
                        `job-${schedule.id}`,
                        () => update("schedule", schedule.id, { status: schedule.status === "active" ? "paused" : "active" }),
                        "Couldn’t change it — the server refused.",
                      )
                    }
                    onDelete={() => void remove("schedule", schedule.id, schedule.note)}
                    onUndo={() => void undo(schedule)}
                  />
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="vs-h" className="flex flex-col gap-3.5 px-1">
            <h2 id="vs-h" className="text-muted-foreground/80 m-0 text-[10.5px] font-semibold tracking-[0.1em] uppercase">
              Schedule or reminder?
            </h2>
            <div className="grid grid-cols-2 gap-7">
              <Versus icon={<Bell />} title="A reminder fires once">
                At the time you set, then it’s done. Right for “check this build in 30 minutes.”
              </Versus>
              <Versus icon={<Repeat />} title="A schedule keeps going">
                On its cadence until you pause it. Each run is a turn in the chat it posts to, so the report lands where you’ll
                read it. Right for “every weekday, tell me what’s red.”
              </Versus>
            </div>
            <p className="border-border/80 text-muted-foreground/80 m-0 border-t pt-3 text-xs leading-normal">
              Pausing keeps the job and where it posts. Deleting removes it — you’ll be asked first.
            </p>
          </section>
        </div>

        {/* ── Right: suggestions from his real reminders ── */}
        <section
          aria-labelledby="suggest-h"
          className="bg-sidebar border-border flex min-w-0 flex-col self-start overflow-hidden rounded-[14px] border"
        >
          <div className="border-border/80 flex flex-col gap-1 border-b px-4 pt-3.5 pb-3">
            <div className="flex items-baseline gap-2">
              <h2 id="suggest-h" className="m-0 text-[13px] font-semibold">
                Suggested from what you keep asking him
              </h2>
              <span className="text-muted-foreground/80 text-xs tabular-nums">{suggestions.length}</span>
            </div>
            <p className="text-muted-foreground/80 m-0 text-xs leading-normal">
              {pastCount
                ? `Patterns in your ${pastCount} past reminder${pastCount === 1 ? "" : "s"}. Add one and he won’t need the nudge.`
                : "Patterns in your past reminders show up here. There are none yet."}
            </p>
          </div>
          <div className="flex flex-col gap-2.5 p-3">
            {suggestions.length === 0 ? (
              <Empty title="Nothing to suggest">
                {pastCount
                  ? "None of your reminders repeat in a way a schedule would cover — or a schedule already does."
                  : "When you keep asking him for the same nudge, it shows up here as a job you can add in one press."}
              </Empty>
            ) : (
              suggestions.map((suggestion) => (
                <article
                  key={suggestion.key}
                  aria-label={suggestion.note}
                  className="bg-card border-border flex flex-col gap-2.5 rounded-xl border py-3 pr-3 pl-3.5"
                >
                  <div className="flex items-center gap-2">
                    <Chip>Every day · {suggestion.dailyAt}</Chip>
                    <span className="flex-1" />
                    <button
                      type="button"
                      aria-label="Dismiss this suggestion"
                      onClick={() => dismiss(suggestion.key)}
                      className="text-muted-foreground/80 hover:bg-muted/50 hover:text-foreground flex size-[26px] items-center justify-center rounded-[7px] transition-colors"
                    >
                      <X aria-hidden className="size-[13px]" />
                    </button>
                  </div>
                  <span className="text-foreground text-[13.5px] leading-[1.45] font-semibold">{suggestion.note}</span>
                  {/* The clamp sits inside the padding: on the padded box itself, the third line showed
                      through the bottom padding as a sliver of cut-off text. */}
                  <span className="bg-background rounded-lg px-2.5 py-2">
                    <span className="text-muted-foreground line-clamp-2 text-xs leading-normal italic">“{suggestion.quote}”</span>
                  </span>
                  <div className="flex items-center gap-2.5">
                    <span
                      className={cn(
                        "min-w-0 flex-1 truncate text-[11.5px]",
                        actions.errors[`suggest-${suggestion.key}`] ? "text-destructive" : "text-muted-foreground/80",
                      )}
                    >
                      {actions.errors[`suggest-${suggestion.key}`] ?? suggestion.basis}
                    </span>
                    <button
                      type="button"
                      disabled={actions.busy === `suggest-${suggestion.key}`}
                      onClick={() => void takeSuggestion(suggestion)}
                      className={cn(BUTTON.secondary, BUTTON.small, "gap-1.5 pr-3 pl-2.5")}
                    >
                      <Plus aria-hidden className="size-[13px]" />
                      Add
                    </button>
                  </div>
                </article>
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function CadenceButton({
  on,
  disabled,
  onClick,
  describedBy,
  children,
}: {
  on: boolean;
  disabled?: boolean;
  onClick: () => void;
  describedBy?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled}
      aria-describedby={describedBy}
      onClick={onClick}
      className={cn(
        "flex h-7 items-center rounded-[7px] px-3 text-[12.5px] whitespace-nowrap transition-colors",
        on ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
        disabled && "text-muted-foreground/50 hover:text-muted-foreground/50 pointer-events-none",
      )}
    >
      {children}
    </button>
  );
}

function Versus({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <KindTile>{icon}</KindTile>
      <div className="flex flex-col gap-1 pt-1">
        <span className="text-[13px] font-semibold">{title}</span>
        <p className="text-muted-foreground m-0 text-[12.5px] leading-[1.55]">{children}</p>
      </div>
    </div>
  );
}

/* ── One standing job ───────────────────────────────────────────────────── */

function JobCard({
  schedule,
  now,
  chat,
  projectName,
  busy,
  error,
  taken,
  onToggle,
  onDelete,
  onUndo,
}: {
  schedule: Schedule;
  now: Date;
  chat?: { title: string; projectId: number | null };
  projectName: (id: number) => string | undefined;
  busy: boolean;
  error?: string;
  /** Set when this job was just added from a suggestion: what it was based on. */
  taken: string | null;
  onToggle: () => void;
  onDelete: () => void;
  onUndo: () => void;
}) {
  const active = schedule.status === "active";
  const nextAt = ms(schedule.next_fire);
  const lastAt = ms(schedule.last_fired);
  const next = nextAt === null ? null : new Date(nextAt);
  const overdue = active && next !== null && next.getTime() < now.getTime() - 60_000;
  const strip = schedule.daily_at ? weekStrip(schedule.daily_at, next, now, !active) : null;
  // "9:00" in the day cells, as the board writes it; the badge keeps the stored "09:00".
  const clockShort = schedule.daily_at?.replace(/^0(?=\d:)/, "") ?? "";
  const runs = strip?.filter((day) => day.run).length ?? 0;
  const project = chat?.projectId ? projectName(chat.projectId) : undefined;
  const titleId = `job-${schedule.id}-h`;

  return (
    <ItemMenu title="Schedule" copy={schedule.note} onDelete={onDelete}>
      <article aria-labelledby={titleId} className={cn("bg-card border-input flex flex-col overflow-hidden rounded-[14px] border", busy && "opacity-70")}>
        <div className="flex items-start gap-3.5 pt-4 pr-3 pl-4">
          <span className="bg-muted/60 text-foreground flex size-9 shrink-0 items-center justify-center rounded-[10px]">
            <Repeat aria-hidden className="size-4" />
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <div className="flex items-center gap-2.5">
              <Chip>{cadenceBadge(schedule)}</Chip>
              {schedule.daily_at ? (
                <span aria-label="Runs every day of the week" className="flex gap-[3px]">
                  {["S", "M", "T", "W", "T", "F", "S"].map((letter, index) => (
                    <span
                      key={index}
                      aria-hidden
                      className={cn(
                        "flex size-[18px] items-center justify-center rounded-[5px] text-[10px] font-semibold",
                        active ? "bg-muted text-foreground" : "text-muted-foreground/50 border-border border border-dashed",
                      )}
                    >
                      {letter}
                    </span>
                  ))}
                </span>
              ) : null}
            </div>
            <h3 id={titleId} className="text-foreground m-0 text-base leading-[1.4] font-semibold">
              {schedule.note}
            </h3>
          </div>
          <div className="flex shrink-0 items-center gap-2.5 pt-0.5">
            <span className={cn("text-xs", active ? "text-roam" : "text-muted-foreground")}>{active ? "Running" : "Paused"}</span>
            <button
              type="button"
              role="switch"
              aria-checked={active}
              aria-label={active ? "Pause this schedule" : "Resume this schedule"}
              disabled={busy}
              onClick={onToggle}
              className={cn(
                "flex h-5 w-[34px] items-center rounded-full border px-0.5 transition-colors",
                active ? "border-roam/45 bg-roam/20 justify-end" : "border-input bg-muted justify-start",
              )}
            >
              <span className={cn("size-3.5 rounded-full", active ? "bg-roam" : "bg-muted-foreground/80")} />
            </button>
            <Menu>
              <MenuTrigger asChild>
                <button
                  type="button"
                  aria-label="More actions for this schedule"
                  className="text-muted-foreground hover:bg-muted/50 hover:text-foreground flex size-[30px] items-center justify-center rounded-lg transition-colors"
                >
                  <MoreHorizontal aria-hidden className="size-4" />
                </button>
              </MenuTrigger>
              <MenuContent>
                <MenuItem icon={<Copy />} onSelect={() => void copyText(schedule.note)}>
                  Copy text
                </MenuItem>
                <MenuSeparator />
                <MenuItem icon={<Trash2 />} danger onSelect={onDelete}>
                  Delete…
                </MenuItem>
              </MenuContent>
            </Menu>
          </div>
        </div>

        <dl className="bg-background border-border/80 mx-4 mt-4 ml-[66px] grid grid-cols-3 overflow-hidden rounded-[10px] border leading-[1.25]">
          <Fact label="Next run" first>
            {!active ? (
              <>
                <dd className="text-foreground/80 m-0 text-[13.5px] whitespace-nowrap">Paused</dd>
                <dd className="text-muted-foreground/80 m-0 text-[11.5px]">Resume it to run again</dd>
              </>
            ) : next ? (
              <>
                <dd className={cn("m-0 truncate text-[13.5px] whitespace-nowrap", overdue ? "text-kith" : "text-foreground")}>
                  {dayClock(next, now)}
                </dd>
                <dd className={cn("m-0 text-[11.5px]", overdue ? "text-kith" : "text-muted-foreground/80")}>
                  {overdue ? `${countdown(next, now)} — it hasn’t run` : dayOffset(next, now) <= 1 ? dayMonth(next) : countdown(next, now)}
                </dd>
              </>
            ) : (
              <dd className="text-muted-foreground m-0 text-[13.5px]">Not set</dd>
            )}
          </Fact>
          <Fact label="Last run">
            {lastAt !== null ? (
              <>
                <dd className="text-foreground/80 m-0 truncate text-[13.5px] whitespace-nowrap">{dayClock(new Date(lastAt), now)}</dd>
                <dd className="text-muted-foreground/80 m-0 text-[11.5px]">{dayMonth(new Date(lastAt))}</dd>
              </>
            ) : (
              <>
                <dd className="text-foreground/80 m-0 text-[13.5px] whitespace-nowrap">Never run yet</dd>
                <dd className="text-muted-foreground/80 m-0 text-[11.5px]">No report yet</dd>
              </>
            )}
          </Fact>
          <Fact label="Posts to">
            {schedule.conversation_id ? (
              <>
                <dd className="text-foreground m-0 truncate text-[13.5px] whitespace-nowrap">{chat?.title ?? "A chat"}</dd>
                <dd className="text-muted-foreground/80 m-0 truncate text-[11.5px] whitespace-nowrap">
                  {project ? `${project} · chat` : "chat"}
                </dd>
              </>
            ) : (
              /* A job made here has no chat until it first runs: the scheduler starts one then and
                 every later run posts to it. */
              <>
                <dd className="text-foreground/80 m-0 text-[13.5px] whitespace-nowrap">A new chat</dd>
                <dd className="text-muted-foreground/80 m-0 truncate text-[11.5px] whitespace-nowrap">started on its first run</dd>
              </>
            )}
          </Fact>
        </dl>

        {strip ? (
          <div className="mx-4 mt-3.5 ml-[66px] flex flex-col gap-2">
            <div className="flex items-baseline gap-2">
              <Label count={active ? `${runs} run${runs === 1 ? "" : "s"}` : "paused"}>Next 7 days</Label>
            </div>
            <div className="grid grid-cols-7 gap-1.5">
              {strip.map((day) => (
                <div
                  key={day.key}
                  className={cn(
                    "flex h-[58px] flex-col items-center justify-center gap-px rounded-[9px] border",
                    day.next
                      ? "bg-muted/60 text-foreground border-[color-mix(in_oklab,var(--foreground)_22%,transparent)]"
                      : day.run
                        ? "bg-background text-foreground border-border/80"
                        : "text-muted-foreground/50 border-border border-dashed",
                  )}
                >
                  <span className="text-muted-foreground/80 text-[11px]">{day.dow}</span>
                  <span className="text-[13px] font-semibold tabular-nums">{day.date}</span>
                  <span
                    className={cn(
                      "font-mono text-[10.5px] whitespace-nowrap",
                      day.next ? "text-foreground" : day.run ? "text-muted-foreground/80" : "text-muted-foreground/50",
                    )}
                  >
                    {day.next ? `next · ${clockShort}` : day.run ? clockShort : "off"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="text-destructive m-0 mt-3 ml-[66px] text-xs">
            {error}
          </p>
        ) : null}

        {taken ? (
          <div className="border-border/80 text-muted-foreground/80 mt-4 flex h-[42px] items-center gap-2 border-t pr-3 pl-[66px] text-xs">
            <Lightbulb aria-hidden className="size-[13px] shrink-0" />
            <span className="min-w-0 flex-1 truncate">Added just now from a suggestion · {taken.charAt(0).toLowerCase() + taken.slice(1)}</span>
            <button type="button" onClick={onUndo} disabled={busy} className={cn(BUTTON.ghost, BUTTON.small)}>
              Undo
            </button>
          </div>
        ) : (
          <div className="h-4" />
        )}
      </article>
    </ItemMenu>
  );
}

function Fact({ label, first, children }: { label: string; first?: boolean; children: ReactNode }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-[3px] px-3.5 py-2.5", !first && "border-border/80 border-l")}>
      <dt className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">{label}</dt>
      {children}
    </div>
  );
}
