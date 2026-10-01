/**
 * Reminders: nudges that fire once, at a set time.
 *
 * Drawn from the 2026-09-30 board. The old screen was one card per reminder, 24 of them, all
 * fired, each with a trash can — a page that answered "how many reminders are there" when the
 * questions you open it with are "what is about to fire" and "what was all that about".
 *
 * So: a composer that speaks in times people pick ("Tonight 9 PM", not "in 357 min"), what is
 * still to fire with a countdown and the three things you do to one (snooze, edit, cancel), and
 * Past folded by day — one line per day saying what the day's reminders were about, one day open
 * at a time. On the right, what the reminders have in common, which is the case for a schedule.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import {
  AlarmClock,
  ArrowRight,
  Bell,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Link2,
  MessageSquare,
  RotateCcw,
  X,
} from "lucide-react";

import { useConfirm } from "@/components/ui/confirm";
import { DatePicker } from "@/components/ui/date-picker";
import { ItemMenu } from "@/components/ui/item-menu";
import { Layer, useLayer } from "@/hooks/use-layer";
import { useNow } from "@/hooks/use-now";
import type { BrainSnapshot, Reminder } from "@/lib/backend/brain";
import { pathForTab } from "@/lib/router";
import { cn } from "@/lib/utils";
import { BUTTON, Empty, Kbd, KindTile, Label, PageTitle } from "./chrome";
import { matches } from "./format";
import { foldByDay, repeatSummary, statusWord, suggestSchedules } from "./reminder-patterns";
import {
  QUICK_TIMES,
  agoPhrase,
  clock,
  countdown,
  dayAt,
  dayAtStart,
  firePayload,
  fromPicked,
  isoDay,
  parseClock,
  clockText,
  resolveQuickTime,
  setAhead,
  snoozeTarget,
  untilLong,
  type PickedTime,
  type QuickTime,
} from "./reminder-time";
import { ComposerCard, EnterHint, Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, STRONG_BORDER, TimeField, useActions, useChats } from "./timed-parts";
import type { Handlers } from "./types";

/** A reminder row as the server sends it — which carries the chat it was set from, though the
 *  shared `Reminder` type does not say so. */
type Row = Reminder & { conversation_id?: string | null; project_id?: number | null };

const ms = (iso: string) => {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : t;
};

const NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];

/** The first task a note names, when it is a real one: "#213" in "report on task #213". */
function namedTask(note: string, tasks: BrainSnapshot["tasks"]) {
  for (const match of note.matchAll(/#(\d+)\b/g)) {
    const task = tasks.find((candidate) => candidate.id === Number(match[1]));
    if (task) return task;
  }
  return undefined;
}

export function Reminders({
  snap,
  query,
  remove,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  // A clock, for the countdowns and the composer's preview — nothing here is fetched on it.
  const tick = useNow(15_000);
  const now = useMemo(() => new Date(tick), [tick]);
  const confirm = useConfirm();
  const navigate = useNavigate();
  const chatOf = useChats();
  const layer = useLayer(Layer.Panel);
  const actions = useActions();
  const noteBox = useRef<HTMLInputElement>(null);

  const all = snap.reminders as Row[];
  const shown = useMemo(() => all.filter((r) => matches(query, r.note, r.status, statusWord(r.status))), [all, query]);
  const upcoming = useMemo(
    () => shown.filter((r) => r.status === "pending").sort((a, b) => ms(a.fire_at) - ms(b.fire_at)),
    [shown],
  );
  const past = useMemo(() => shown.filter((r) => r.status !== "pending"), [shown]);
  const days = useMemo(() => foldByDay(past), [past]);
  const firedCount = past.filter((r) => r.status === "done").length;
  const cancelledCount = past.filter((r) => r.status === "cancelled").length;

  // The patterns read every reminder, not the search's slice of them — a search narrows the
  // list, it does not change what you keep asking him for.
  const repeats = useMemo(() => repeatSummary(all.filter((r) => r.status !== "pending")), [all]);
  const suggestable = useMemo(() => suggestSchedules(all, snap.schedules).length, [all, snap.schedules]);

  /* ── The composer ── */
  const [note, setNote] = useState("");
  const [choice, setChoice] = useState<QuickTime>("30m");
  const [picked, setPicked] = useState<PickedTime>(() => {
    const hour = new Date(Date.now() + 60 * 60_000);
    return { date: isoDay(hour), time: clockText(hour.getHours(), 0) };
  });
  // The chat a "Remind again" came from, carried so the copy can post where the original did.
  const [againFrom, setAgainFrom] = useState<string | null>(null);
  const target = resolveQuickTime(choice, now, picked);
  const passed = target !== null && target.getTime() <= now.getTime();
  const linked = namedTask(note, snap.tasks);

  const setReminder = async () => {
    const text = note.trim();
    if (!text) {
      noteBox.current?.focus();
      return;
    }
    // Resolved again at the moment of sending: "in 30 min" is from the press, not the last tick.
    const when = resolveQuickTime(choice, new Date(), picked);
    if (!when || when.getTime() <= Date.now()) return;
    const ok = await actions.run(
      "compose",
      () => create("reminder", { note: text, ...firePayload(choice, when), ...(againFrom ? { conversation_id: againFrom } : {}) }),
      "The server didn’t take it — nothing was set.",
    );
    if (ok) {
      setNote("");
      setChoice("30m");
      setAgainFrom(null);
    }
  };

  /** "Remind again" from the menu: straight away at a quick time, or into the composer to reword. */
  const remindAgain = (reminder: Row, when: QuickTime | "compose") => {
    if (when === "compose") {
      setNote(reminder.note);
      setChoice("30m");
      setAgainFrom(reminder.conversation_id ?? null);
      noteBox.current?.focus();
      noteBox.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return;
    }
    const at = resolveQuickTime(when, new Date());
    if (!at) return;
    void actions.run(
      `again-${reminder.id}`,
      () =>
        create("reminder", {
          note: reminder.note,
          ...firePayload(when, at),
          ...(reminder.conversation_id ? { conversation_id: reminder.conversation_id } : {}),
        }),
      "The server didn’t take it — nothing was set.",
    );
  };

  /* ── Upcoming: snooze, edit, cancel ── */
  const [editing, setEditing] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  const snooze = (reminder: Row, to: Date) =>
    actions.run(
      `row-${reminder.id}`,
      () => update("reminder", reminder.id, { at: to.toISOString() }),
      "Couldn’t snooze it — the server refused the change.",
    );
  const cancel = async (reminder: Row) => {
    const ok = await confirm({
      title: "Cancel this reminder?",
      description: "It won’t fire. It stays in Past, marked cancelled.",
      subject: reminder.note,
      confirmLabel: "Cancel reminder",
      destructive: true,
    });
    if (!ok) return;
    await actions.run(
      `row-${reminder.id}`,
      () => update("reminder", reminder.id, { status: "cancelled" }),
      "Couldn’t cancel it — the server refused the change.",
    );
  };

  /* Keyboard: N for a new one; ↑↓ through Upcoming, S snooze, E edit, ⌫ cancel — the keys the
     right rail lists. Off while typing, and while anything (a menu, the confirm) sits on top. */
  const keyed = useRef({ upcoming, selected, editing, snooze, cancel });
  useEffect(() => {
    keyed.current = { upcoming, selected, editing, snooze, cancel };
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
      if (!layer.frontmost()) return;
      const { upcoming: rows, selected: sel, editing: edit, snooze, cancel } = keyed.current;
      if (edit !== null) return;
      const key = event.key.toLowerCase();
      if (key === "n") {
        event.preventDefault();
        noteBox.current?.focus();
        return;
      }
      if (rows.length === 0) return;
      const index = rows.findIndex((row) => row.id === sel);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        const next = index === -1 ? 0 : Math.min(rows.length - 1, Math.max(0, index + step));
        setSelected(rows[next].id);
        return;
      }
      const row = rows[index];
      if (!row) return;
      if (key === "s") {
        event.preventDefault();
        void snooze(row, snoozeTarget(new Date(row.fire_at), new Date(), 30));
      } else if (key === "e") {
        event.preventDefault();
        setEditing(row.id);
      } else if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        void cancel(row);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [layer]);

  /* ── Past: one day open at a time — the newest, until you choose ── */
  const [openDay, setOpenDay] = useState<string | null | undefined>(undefined);
  const open = openDay === undefined ? (days[0]?.key ?? null) : openDay;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageTitle
        title="Reminders"
        count={upcoming.length ? `${upcoming.length} upcoming` : "none upcoming"}
        subtitle="Nudges that fire once, at a set time."
      >
        <span className="text-muted-foreground/80 flex items-center gap-[7px] pb-0.5 text-xs">
          <Kbd>N</Kbd>new reminder
        </span>
      </PageTitle>

      <div className="grid grid-cols-[minmax(0,1fr)_360px] gap-8">
        <div className="flex min-w-0 flex-col gap-[26px]">
          {/* ── Composer ── */}
          <ComposerCard label="Remind him" htmlFor="reminder-note" ariaLabel="New reminder" onSubmit={() => void setReminder()}>
            <div
              className={cn(
                "bg-background focus-within:border-ring flex h-[46px] items-center gap-2.5 rounded-[10px] border pr-2 pl-3.5 transition-colors",
                note ? STRONG_BORDER : "border-input",
              )}
            >
              <input
                id="reminder-note"
                ref={noteBox}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="What should he come back to — e.g. check the build and report on task #213"
                autoComplete="off"
                className="text-foreground placeholder:text-muted-foreground/80 h-full min-w-0 flex-1 bg-transparent text-sm outline-none"
              />
              {linked ? (
                <span
                  title={`The note names task #${linked.id}: ${linked.goal}`}
                  className="bg-muted/60 border-border text-foreground/80 flex h-[26px] shrink-0 items-center gap-1.5 rounded-full border px-[9px] text-[11.5px] whitespace-nowrap"
                >
                  <Link2 aria-hidden className="text-muted-foreground/80 size-3" />
                  {linked.project_id ? (
                    <>
                      <span className="max-w-40 truncate">
                        {snap.projects.find((project) => project.id === linked.project_id)?.name ?? "Task"}
                      </span>
                      <span className="text-muted-foreground/50">·</span>
                    </>
                  ) : null}
                  <span className="text-muted-foreground font-mono text-[11px]">#{linked.id}</span>
                </span>
              ) : null}
            </div>

            <div className="mt-0.5 flex items-center gap-1.5">
              <div role="group" aria-label="When it fires" className="flex gap-1.5">
                {QUICK_TIMES.map((option) => {
                  const on = option.value === choice;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setChoice(option.value)}
                      className={cn(
                        "flex h-7 items-center gap-1.5 rounded-[7px] border px-2.5 text-xs whitespace-nowrap transition-colors",
                        on
                          ? cn(STRONG_BORDER, "bg-muted text-foreground font-medium")
                          : "border-border text-muted-foreground hover:text-foreground hover:bg-muted/50",
                      )}
                    >
                      {option.value === "pick" ? <CalendarDays aria-hidden className="size-3" /> : null}
                      {option.label}
                    </button>
                  );
                })}
              </div>
              <span className="flex-1" />
              <span role="status" className="text-muted-foreground flex items-center gap-[7px] text-[12.5px] whitespace-nowrap">
                <Clock aria-hidden className="text-muted-foreground/80 size-[13px]" />
                {target === null ? (
                  <span>Pick a day and a time</span>
                ) : passed ? (
                  <span className="text-destructive">That time has already passed</span>
                ) : (
                  <span>
                    Fires <span className="text-foreground">{dayAt(target, now)}</span> — {untilLong(target, now)}
                  </span>
                )}
              </span>
              <button
                type="submit"
                disabled={actions.busy === "compose" || target === null || passed}
                className={cn(BUTTON.primary, "ml-3 gap-2 pr-2.5 pl-3.5 whitespace-nowrap")}
              >
                Set reminder
                <EnterHint />
              </button>
            </div>

            {choice === "pick" ? (
              <div className="text-muted-foreground flex items-center gap-2 text-[12.5px]">
                <span>On</span>
                <DatePicker
                  value={picked.date}
                  onChange={(date) => setPicked((was) => ({ ...was, date }))}
                  placeholder="Pick a day"
                  ariaLabel="Day it fires"
                  className="w-44"
                />
                <span>at</span>
                <TimeField
                  id="reminder-time"
                  label="Time it fires"
                  value={picked.time}
                  invalid={!parseClock(picked.time)}
                  onChange={(time) => setPicked((was) => ({ ...was, time }))}
                  onBlur={() => {
                    const parsed = parseClock(picked.time);
                    if (parsed) setPicked((was) => ({ ...was, time: clockText(parsed.hour, parsed.minute) }));
                  }}
                />
              </div>
            ) : null}

            {actions.errors.compose ? (
              <p role="alert" className="text-destructive m-0 text-xs">
                {actions.errors.compose}
              </p>
            ) : null}
          </ComposerCard>

          {/* ── Upcoming ── */}
          <section aria-labelledby="upcoming-h" className="flex flex-col gap-2">
            <div className="px-1">
              <Label count={upcoming.length}>
                <span id="upcoming-h">Upcoming</span>
              </Label>
            </div>
            {upcoming.length === 0 ? (
              <Empty
                title={query ? `Nothing upcoming matches ‘${query}’` : "Nothing is waiting to fire"}
                action={
                  query ? null : (
                    <button type="button" onClick={() => noteBox.current?.focus()} className={cn(BUTTON.secondary, BUTTON.small)}>
                      New reminder
                    </button>
                  )
                }
              >
                {query
                  ? "The search narrows this list. Clear it to see everything still to fire."
                  : "Set one above and it waits here, with a countdown, until he picks it up."}
              </Empty>
            ) : (
              upcoming.map((reminder) =>
                editing === reminder.id ? (
                  <EditRow
                    key={reminder.id}
                    reminder={reminder}
                    now={now}
                    busy={actions.busy === `row-${reminder.id}`}
                    error={actions.errors[`row-${reminder.id}`]}
                    onCancel={() => {
                      actions.clear(`row-${reminder.id}`);
                      setEditing(null);
                    }}
                    onSave={async (text, at) => {
                      const ok = await actions.run(
                        `row-${reminder.id}`,
                        () => update("reminder", reminder.id, { note: text, at: at.toISOString() }),
                        "Couldn’t save it — the server refused the change.",
                      );
                      if (ok) setEditing(null);
                    }}
                  />
                ) : (
                  <UpcomingRow
                    key={reminder.id}
                    reminder={reminder}
                    now={now}
                    selected={selected === reminder.id}
                    chat={chatOf(reminder.conversation_id)?.title}
                    task={namedTask(reminder.note, snap.tasks)}
                    busy={actions.busy === `row-${reminder.id}`}
                    error={actions.errors[`row-${reminder.id}`]}
                    onSnooze={(to) => void snooze(reminder, to)}
                    onEdit={() => {
                      setSelected(reminder.id);
                      setEditing(reminder.id);
                    }}
                    onCancel={() => void cancel(reminder)}
                    onDelete={() => void remove("reminder", reminder.id, reminder.note)}
                  />
                ),
              )
            )}
          </section>

          {/* ── Past, folded by day ── */}
          {days.length > 0 ? (
            <section aria-labelledby="past-h" className="flex flex-col gap-2">
              <div className="flex items-baseline gap-2 px-1">
                <Label count={past.length}>
                  <span id="past-h">Past</span>
                </Label>
                <span className="flex-1" />
                <span className="text-muted-foreground/80 text-xs">
                  {firedCount === past.length
                    ? `All ${past.length} fired · none cancelled`
                    : `${firedCount} fired · ${cancelledCount ? `${cancelledCount} cancelled` : "none cancelled"}`}
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                {days.map((day) => {
                  const isOpen = day.key === open;
                  const head = (
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      onClick={() => setOpenDay(isOpen ? null : day.key)}
                      className={cn(
                        "flex h-[42px] w-full items-center gap-3 rounded-[10px] px-3 text-left text-[13px] transition-colors",
                        !isOpen && "hover:bg-muted/60",
                      )}
                    >
                      {isOpen ? (
                        <ChevronDown aria-hidden className="text-foreground size-3.5 shrink-0" />
                      ) : (
                        <ChevronRight aria-hidden className="text-muted-foreground/80 size-3.5 shrink-0" />
                      )}
                      <span className="w-12 shrink-0 font-semibold tabular-nums">{day.date}</span>
                      <span className="text-muted-foreground/80 w-[30px] shrink-0 text-xs">{day.dow}</span>
                      <span className="text-foreground/80 w-[164px] shrink-0 text-[12.5px] whitespace-nowrap">{day.summary}</span>
                      <span className="text-muted-foreground/80 min-w-0 flex-1 truncate text-[12.5px]">{day.preview}</span>
                      <span className="text-muted-foreground/80 shrink-0 font-mono text-[11px] whitespace-nowrap">{day.span}</span>
                    </button>
                  );
                  if (!isOpen) return <div key={day.key}>{head}</div>;
                  return (
                    <div key={day.key} className="bg-card border-border my-1 overflow-hidden rounded-xl border">
                      {head}
                      <div className="flex flex-col gap-0.5 px-1.5 pb-1.5">
                        {day.items.map((reminder) => (
                          <PastRow
                            key={reminder.id}
                            reminder={reminder as Row}
                            busy={actions.busy === `again-${reminder.id}`}
                            error={actions.errors[`again-${reminder.id}`]}
                            onAgain={(when) => remindAgain(reminder as Row, when)}
                            onDelete={() => void remove("reminder", reminder.id, reminder.note)}
                          />
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : null}
        </div>

        {/* ── Right rail ── */}
        <aside aria-label="About your reminders" className="flex min-w-0 flex-col gap-4">
          {repeats ? (
            <section aria-labelledby="repeat-h" className="bg-card border-border flex flex-col overflow-hidden rounded-[14px] border">
              <div className="border-border/80 flex h-11 shrink-0 items-center gap-2 border-b px-4">
                <h2 id="repeat-h" className="m-0 text-[13px] font-semibold">
                  They keep repeating
                </h2>
              </div>
              <div className="flex flex-col gap-3.5 px-4 pt-3.5 pb-4">
                <div className="flex flex-col gap-1">
                  <span className="flex items-baseline gap-1.5">
                    <span className="text-[26px] leading-[1.2] font-[650] tracking-[-0.01em] tabular-nums">{repeats.repeated}</span>
                    <span className="text-muted-foreground/80 text-[13px]">of {repeats.total}</span>
                  </span>
                  <p className="text-foreground/80 m-0 text-[12.5px] leading-normal">{repeats.sentence}</p>
                </div>
                <div className="flex flex-col gap-2.5">
                  {repeats.kinds.map((kind) => (
                    <div key={kind.key} className="flex flex-col gap-[5px]">
                      <span className="text-foreground/80 flex justify-between gap-3 text-xs">
                        <span className="truncate">{kind.label}</span>
                        <span className="text-muted-foreground/80 font-mono text-[11.5px] tabular-nums">{kind.n}</span>
                      </span>
                      <span className="bg-muted flex h-[3px] overflow-hidden rounded-full">
                        <span
                          className="bg-muted-foreground/80 block h-[3px] rounded-full"
                          style={{ width: `${Math.round((kind.n / repeats.total) * 100)}%` }}
                        />
                      </span>
                    </div>
                  ))}
                </div>
                <p className="text-muted-foreground/80 m-0 text-xs leading-normal">
                  {suggestable === 0
                    ? "Each of these already has a standing job."
                    : `${NUMBER_WORDS[suggestable] ?? suggestable} of these pattern${suggestable === 1 ? " would be better as a standing job than as a nudge" : "s would be better as standing jobs than as nudges"}.`}
                </p>
                <button
                  type="button"
                  onClick={() => navigate(pathForTab("schedules"))}
                  className={cn(BUTTON.secondary, "self-start gap-2")}
                >
                  {suggestable === 0 ? "See schedules" : "See suggested schedules"}
                  <ArrowRight aria-hidden className="text-muted-foreground size-[13px]" />
                </button>
              </div>
            </section>
          ) : null}

          <section aria-labelledby="fires-h" className="bg-card border-border flex flex-col overflow-hidden rounded-[14px] border">
            <div className="border-border/80 flex h-11 shrink-0 items-center gap-2 border-b px-4">
              <h2 id="fires-h" className="m-0 text-[13px] font-semibold">
                When one fires
              </h2>
            </div>
            <ul className="m-0 flex list-none flex-col gap-3.5 px-4 pt-3.5 pb-4">
              <Explain icon={<MessageSquare />}>
                <span className="text-foreground">He picks it up</span> as a turn in the chat it was set from — even while you’re
                away.
              </Explain>
              <Explain icon={<AlarmClock />}>
                <span className="text-foreground">Snooze</span> moves it later; <span className="text-foreground">Edit</span>{" "}
                changes the words or the time.
              </Explain>
              <Explain icon={<RotateCcw />}>
                <span className="text-foreground">Remind again</span> copies a fired one to a new time. The original stays here in
                Past.
              </Explain>
            </ul>
            <div className="border-border/80 text-muted-foreground/80 flex flex-wrap gap-3.5 border-t px-4 py-2.5 text-[11.5px]">
              <span className="flex items-center gap-1.5">
                <Kbd>↑↓</Kbd>move
              </span>
              <span className="flex items-center gap-1.5">
                <Kbd>S</Kbd>snooze
              </span>
              <span className="flex items-center gap-1.5">
                <Kbd>E</Kbd>edit
              </span>
              <span className="flex items-center gap-1.5">
                <Kbd>⌫</Kbd>cancel
              </span>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function Explain({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <KindTile>{icon}</KindTile>
      <span className="text-foreground/80 pt-1 text-[12.5px] leading-normal">{children}</span>
    </li>
  );
}

/* ── One upcoming reminder ──────────────────────────────────────────────── */

function UpcomingRow({
  reminder,
  now,
  selected,
  chat,
  task,
  busy,
  error,
  onSnooze,
  onEdit,
  onCancel,
  onDelete,
}: {
  reminder: Row;
  now: Date;
  selected: boolean;
  chat?: string;
  task?: BrainSnapshot["tasks"][number];
  busy: boolean;
  error?: string;
  onSnooze: (to: Date) => void;
  onEdit: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const fireAt = new Date(reminder.fire_at);
  const late = fireAt.getTime() < now.getTime() - 60_000;
  /* Where it lands. One set from here is bound to no chat; when it fires, the scheduler starts a
     chat for it (named after the note) and he picks it up there. */
  const where = reminder.conversation_id
    ? `he’ll pick it up in ${chat ? `the “${chat}” chat` : "the chat it was set from"}${task ? `, on #${task.id} ${task.goal}` : ""}`
    : "he’ll start a chat for it when it fires";
  const tomorrowNine = (() => {
    const at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0);
    return at.getTime() > fireAt.getTime() ? at : null;
  })();
  return (
    <ItemMenu title="Reminder" copy={reminder.note} onDelete={onDelete}>
      <article
        aria-label={reminder.note}
        className={cn(
          "bg-card flex items-center gap-3.5 rounded-xl border py-3 pr-2.5 pl-3.5 transition-colors",
          // Neutral, not amber: being moved to with the keyboard is not something needing you.
          selected ? cn(STRONG_BORDER, "bg-muted/40") : "border-input",
          busy && "opacity-70",
        )}
      >
        <span className="bg-muted/60 text-foreground flex size-9 shrink-0 items-center justify-center rounded-[10px]">
          <Bell aria-hidden className="size-4" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
          <span className="text-foreground truncate text-sm leading-[1.25]">{reminder.note}</span>
          <span className="text-muted-foreground/80 flex gap-1.5 overflow-hidden text-xs leading-[1.25] whitespace-nowrap">
            <span>{dayAtStart(fireAt, now)}</span>
            <span className="text-muted-foreground/50">·</span>
            <span>set {agoPhrase(new Date(reminder.created_at), now)}</span>
            <span className="text-muted-foreground/50">·</span>
            <span className="truncate">{where}</span>
          </span>
          {error ? (
            <span role="alert" className="text-destructive text-xs">
              {error}
            </span>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-px pr-1.5 pl-2">
          <span className={cn("text-[15px] leading-[1.25] font-semibold whitespace-nowrap tabular-nums", late && "text-kith")}>
            {countdown(fireAt, now)}
          </span>
          <span className="text-muted-foreground/80 font-mono text-[11px]">{clock(fireAt)}</span>
        </div>
        <span aria-hidden className="bg-border h-7 w-px shrink-0" />
        <div className="flex shrink-0 gap-0.5">
          <Menu>
            <MenuTrigger asChild>
              <button
                type="button"
                disabled={busy}
                className="text-foreground/80 hover:bg-muted/50 hover:text-foreground flex h-[30px] items-center gap-[5px] rounded-lg pr-2 pl-2.5 text-[13px] transition-colors"
              >
                Snooze
                <ChevronDown aria-hidden className="text-muted-foreground/80 size-3" />
              </button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem hint="S" onSelect={() => onSnooze(snoozeTarget(fireAt, new Date(), 30))}>
                30 minutes later
              </MenuItem>
              <MenuItem onSelect={() => onSnooze(snoozeTarget(fireAt, new Date(), 120))}>2 hours later</MenuItem>
              {tomorrowNine ? <MenuItem onSelect={() => onSnooze(tomorrowNine)}>Tomorrow at 9:00 AM</MenuItem> : null}
            </MenuContent>
          </Menu>
          <button
            type="button"
            onClick={onEdit}
            disabled={busy}
            className="text-foreground/80 hover:bg-muted/50 hover:text-foreground h-[30px] rounded-lg px-2.5 text-[13px] transition-colors"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="text-destructive hover:bg-destructive/10 h-[30px] rounded-lg px-2.5 text-[13px] transition-colors"
          >
            Cancel
          </button>
        </div>
      </article>
    </ItemMenu>
  );
}

/* ── Editing one: the words and the time, in place ──────────────────────── */

function EditRow({
  reminder,
  now,
  busy,
  error,
  onSave,
  onCancel,
}: {
  reminder: Row;
  now: Date;
  busy: boolean;
  error?: string;
  onSave: (note: string, at: Date) => void;
  onCancel: () => void;
}) {
  const fireAt = new Date(reminder.fire_at);
  const [text, setText] = useState(reminder.note);
  const [day, setDay] = useState(isoDay(fireAt));
  const [time, setTime] = useState(clockText(fireAt.getHours(), fireAt.getMinutes()));
  const at = fromPicked({ date: day, time });
  const valid = text.trim() !== "" && at !== null && at.getTime() > now.getTime();
  const save = () => {
    if (valid && at) onSave(text.trim(), at);
  };
  return (
    <form
      aria-label="Edit reminder"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
      onKeyDown={(event) => {
        // Escape abandons the edit, and only the edit — the panel would otherwise close. Not while
        // the day picker's calendar is open, though: that Escape is the calendar's, and React sees
        // it here before the picker's own document listener does.
        const calendarOpen = event.currentTarget.querySelector('[aria-label="Previous month"]');
        if (event.key === "Escape" && !event.defaultPrevented && !calendarOpen) {
          event.preventDefault();
          event.stopPropagation();
          onCancel();
        }
      }}
      className={cn("bg-card flex flex-col gap-2 rounded-xl border py-3 pr-2.5 pl-3.5", STRONG_BORDER)}
    >
      <div className="flex items-center gap-3">
        <span className="bg-muted/60 text-foreground flex size-9 shrink-0 items-center justify-center rounded-[10px]">
          <Bell aria-hidden className="size-4" />
        </span>
        <label htmlFor={`edit-${reminder.id}`} className="sr-only">
          What he should come back to
        </label>
        <input
          id={`edit-${reminder.id}`}
          autoFocus
          value={text}
          onChange={(event) => setText(event.target.value)}
          className="bg-background border-input focus-visible:border-ring text-foreground h-9 min-w-0 flex-1 rounded-lg border px-3 text-sm outline-none"
        />
        <DatePicker value={day} onChange={setDay} ariaLabel="Day it fires" placeholder="Pick a day" className="w-40" />
        <TimeField id={`edit-time-${reminder.id}`} label="Time it fires" value={time} invalid={!parseClock(time)} onChange={setTime} />
        <button type="submit" disabled={!valid || busy} className={cn(BUTTON.primary, BUTTON.small)}>
          Save
        </button>
        <button type="button" onClick={onCancel} className={cn(BUTTON.ghost, BUTTON.small)}>
          Cancel
        </button>
      </div>
      <span className={cn("pl-12 text-xs", error ? "text-destructive" : "text-muted-foreground/80")} role={error ? "alert" : undefined}>
        {error ??
          (at === null
            ? "Pick a day and a time."
            : at.getTime() <= now.getTime()
              ? "That time has already passed."
              : `Fires ${dayAt(at, now)} — ${untilLong(at, now)}.`)}
      </span>
    </form>
  );
}

/* ── One fired (or cancelled) reminder ──────────────────────────────────── */

function PastRow({
  reminder,
  busy,
  error,
  onAgain,
  onDelete,
}: {
  reminder: Row;
  busy: boolean;
  error?: string;
  onAgain: (when: QuickTime | "compose") => void;
  onDelete: () => void;
}) {
  const fireAt = new Date(reminder.fire_at);
  const fired = reminder.status === "done";
  return (
    <ItemMenu title="Reminder" copy={reminder.note} onDelete={onDelete}>
      <div className="hover:bg-muted/60 flex min-h-[52px] items-center gap-3 rounded-[9px] py-1.5 pr-1.5 pl-1 transition-colors">
        <span className="text-foreground/80 w-[62px] shrink-0 text-right font-mono text-[11.5px] whitespace-nowrap">
          {clock(fireAt)}
        </span>
        <span className="text-muted-foreground flex w-[62px] shrink-0 items-center gap-[5px] text-[11.5px]">
          {fired ? (
            <Check aria-hidden strokeWidth={2.4} className="text-roam size-3 opacity-80" />
          ) : (
            <X aria-hidden strokeWidth={2.4} className="text-muted-foreground/80 size-3" />
          )}
          {fired ? "Fired" : statusWord(reminder.status).replace(/^./, (c) => c.toUpperCase())}
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-foreground truncate text-[13px]">{reminder.note}</span>
          <span className={cn("text-[11.5px] whitespace-nowrap", error ? "text-destructive" : "text-muted-foreground/80")}>
            {error ?? setAhead(new Date(reminder.created_at), fireAt)}
          </span>
        </span>
        <Menu>
          <MenuTrigger asChild>
            <button
              type="button"
              disabled={busy}
              className="text-foreground/80 hover:bg-muted/50 hover:text-foreground flex h-7 shrink-0 items-center gap-1.5 rounded-[7px] px-2.5 text-[12.5px] whitespace-nowrap transition-colors"
            >
              <RotateCcw aria-hidden className="text-muted-foreground/80 size-[13px]" />
              Remind again
            </button>
          </MenuTrigger>
          <MenuContent>
            {QUICK_TIMES.filter((option) => option.value !== "pick").map((option) => (
              <MenuItem key={option.value} onSelect={() => onAgain(option.value)}>
                {option.label}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem onSelect={() => onAgain("compose")}>Change the words or time first…</MenuItem>
          </MenuContent>
        </Menu>
      </div>
    </ItemMenu>
  );
}
