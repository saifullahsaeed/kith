/**
 * The menu-bar presence.
 *
 * Closing the window does not stop Kith — the agent runs in the backend process, which is
 * separate. The tray exists so that is *visible*: something in the menu bar means he is still
 * there when no window is open.
 *
 * All of which was the intent, and none of which was happening. Two commits walked past this
 * file and left it calling things that had gone:
 *
 *   `247b241 The feed gets its own routes; /autonomy goes` deleted the endpoint this read for
 *   "is he roaming" and "what is he doing", and posted to for "Let it roam" and "Take a step
 *   now". `f17a7d2 Take the autonomy controls out of the interface` then removed those controls
 *   from the app — and not from here, so the menu went on offering two verbs for a subsystem
 *   that no longer existed. Both were no-ops.
 *
 *   `715456d Nothing authenticated the API` put a token on every `/api` call and updated the
 *   renderer registration. This file kept using a bare `fetch`, so every request it made came
 *   back 401 into a silent catch. That is why the header read "Kith" and never anything else:
 *   the readout had not received a byte since auth landed, and "Open his folder" — which reads
 *   the workspace root from the same call — had quietly stopped opening anything.
 *
 * So: the dead verbs are gone rather than reimplemented, which is what `f17a7d2` was doing when
 * it missed this file. What is left is a readout, and it is worth more than the verbs were.
 *
 * **It says what is happening without being opened.** A menu you have to open to learn anything
 * from is a launcher. The count of things waiting on you sits next to the icon, so "he needs
 * me" is answerable from across the room; a quieter mark says he is mid-turn.
 *
 * **It hears rather than asks.** The main process already holds a resumable event stream, and
 * `turn`, `question`, `permission` and `schedule` are exactly what this wants to know. It used
 * to poll on click and be wrong in between.
 */

import { Menu, Tray, app, nativeImage, shell } from "electron";

import { BACKEND_ORIGIN, TRAY_GUID, TRAY_ICON, TRAY_WAITING, TRAY_WORKING } from "../config";
import { apiHeaders } from "../server/api-token";
import { onServerEvent } from "../server/events";
import { showMainWindow } from "./window";

/**
 * Module scope, not a local. A Tray assigned to a local variable gets garbage collected and the
 * icon silently vanishes from the menu bar.
 */
let tray: Tray | null = null;
let unsubscribe: (() => void) | null = null;
let refreshing: NodeJS.Timeout | null = null;
let ticking: NodeJS.Timeout | null = null;
let spinning: NodeJS.Timeout | null = null;
let spinningFor: Mood | null = null;
let frame = 0;
/** Which file the icon is currently showing, so a redraw only happens on a real change. */
let painted = "";

/**
 * One thing that cannot go further without you.
 *
 * `requestId` is the difference between a menu bar that reports and one that is worth having.
 * A permission he is blocked on can be answered from here — allow once, allow always, refuse —
 * without opening the window, which is the whole situation the menu bar exists for: you are in
 * another application and he has stopped. Everything else can only offer to take you to it.
 */
export interface Waiting {
  label: string;
  /** Set for a pending permission, which can be answered in place. */
  requestId?: string;
  /** What he wants to do, for the submenu's own heading. */
  detail?: string;
}

/** A turn he has already finished, for the "what happened while I was away" list. */
export interface Done {
  focus: string;
  at: string;
  seconds: number;
  tools: number;
}

export interface State {
  /** Conversations with a turn running. */
  working: number;
  /** What the running turn is on, when he is on a named task. */
  doing: string;
  waiting: Waiting[];
  /** Newest first. The answer to "what has he been doing", which nothing here could say. */
  recent: Done[];
  /** What he has cost this run of the server, in dollars. Not estimated — see `meter.py`. */
  costUsd: number;
  /** The next standing job to fire, if there is one. */
  nextWake: { note: string; at: string } | null;
  /** A newer Kith than this one, when there is one. */
  update: { latest: string; page: string; download: string } | null;
}

const IDLE: State = {
  working: 0,
  doing: "",
  waiting: [],
  recent: [],
  costUsd: 0,
  nextWake: null,
  update: null,
};
let state: State = IDLE;

async function read(path: string): Promise<Record<string, unknown>> {
  const response = await fetch(`${BACKEND_ORIGIN}${path}`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()) as Record<string, unknown>;
}

/**
 * What he is up to, for the menu.
 *
 * Every call carries the token now. Failure is still silent — a menu bar is not worth an error
 * dialog — but it falls back to `IDLE` rather than to a plausible-looking zero, so a backend
 * that is down reads as "not running" instead of "nothing waiting for you".
 */
async function readState(): Promise<State> {
  try {
    const [conversations, permissions, messages, usage, schedules, activity, update] =
      await Promise.all([
        read("/api/conversations?limit=40"),
        read("/api/permissions"),
        read("/api/messages"),
        read("/api/usage"),
        read("/api/schedules"),
        read("/api/activity?limit=6"),
        read("/api/update"),
      ]);

    const rows = (conversations.conversations ?? []) as Record<string, unknown>[];
    const pending = (permissions.pending ?? []) as Record<string, unknown>[];
    const unread = Number(messages.unread ?? 0);

    const waiting: Waiting[] = [];
    // Each one on its own row, with its id, because a row that says "3 waiting" can only take
    // you to the window. Named individually you can answer them from here.
    for (const one of pending) {
      waiting.push({
        label: short(String(one.what ?? "something"), 52),
        requestId: String(one.id ?? ""),
        detail: String(one.why ?? ""),
      });
    }
    // A question he asked is the same class of thing as a permission — he has stopped and cannot
    // start again — and it was invisible here. `waiting` is set on the row by the server; see
    // `services/conversations.recent`.
    for (const row of rows.filter((one) => one.waiting)) {
      waiting.push({ label: `Asked you: ${title(row)}` });
    }
    if (unread) waiting.push({ label: `${unread} unread from him` });

    const working = rows.filter((one) => one.working);
    const only = working.length === 1 ? working[0] : undefined;
    return {
      working: working.length,
      doing: only ? title(only) : "",
      waiting,
      recent: turns((activity.ticks ?? []) as Record<string, unknown>[]),
      costUsd: Number(usage.costUsd ?? 0),
      nextWake: soonest((schedules.schedules ?? []) as Record<string, unknown>[]),
      update: update.newer
        ? {
            latest: String(update.latest ?? ""),
            page: String(update.page ?? ""),
            download: String(update.download ?? ""),
          }
        : null,
    };
  } catch {
    return IDLE;
  }
}

/**
 * The last few turns he finished, newest first.
 *
 * The question this menu could not answer at all, and the main reason it was not worth opening:
 * he runs while the window is closed, and there was nowhere that said what came of it. `focus`
 * is what was asked and `seconds` is how long it took, which together are enough to tell an
 * afternoon of real work from an afternoon of one thing retried.
 *
 * A running turn is not in here — it has no outcome yet, and it is already the header.
 */
export function turns(ticks: Record<string, unknown>[]): Done[] {
  return ticks
    .filter((one) => typeof one.at === "string" && String(one.focus ?? "").trim())
    .slice(0, 5)
    .map((one) => ({
      focus: short(String(one.focus).replace(/\s+/g, " ").trim(), 44),
      at: String(one.at),
      seconds: Math.round(Number(one.seconds ?? 0)),
      tools: Array.isArray(one.tools) ? one.tools.length : 0,
    }));
}

function short(text: string, at: number): string {
  return text.length > at ? `${text.slice(0, at - 1)}…` : text;
}

/** "4m ago", "2h ago" — coarse on purpose; the exact minute of a finished turn is not a fact
 *  anyone needs from a menu bar. */
export function ago(at: string): string {
  const since = Date.now() - new Date(at).getTime();
  if (Number.isNaN(since)) return "";
  const minutes = Math.round(since / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

/** "38s", "2m" — how long he spent, in the unit that reads. */
export function took(seconds: number): string {
  if (seconds <= 0) return "";
  return seconds < 90 ? `${seconds}s` : `${Math.round(seconds / 60)}m`;
}

function title(row: Record<string, unknown>): string {
  const text = String(row.title ?? row.summary ?? "").trim();
  return text.length > 44 ? `${text.slice(0, 43)}…` : text || "a conversation";
}

/** The standing job that fires next, ignoring the ones pointed at nothing. */
export function soonest(schedules: Record<string, unknown>[]): State["nextWake"] {
  const active = schedules
    .filter((one) => one.status === "active" && typeof one.next_fire === "string")
    .map((one) => ({ note: String(one.note ?? "a standing job"), at: String(one.next_fire) }))
    .filter((one) => !Number.isNaN(new Date(one.at).getTime()))
    .sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
  return active[0] ?? null;
}

/** "in 12m", "in 3h", "due" — the same vocabulary the Work panel uses. */
export function until(at: string): string {
  const left = new Date(at).getTime() - Date.now();
  if (left <= 0) return "due";
  const minutes = Math.round(left / 60_000);
  if (minutes < 1) return "in under a minute";
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `in ${hours}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
}

/** The three states, as one word. The icon shows which; this is what the code reasons about. */
export type Mood = "waiting" | "working" | "resting";

export function moodOf(current: State): Mood {
  if (current.waiting.length) return "waiting";
  return current.working ? "working" : "resting";
}

/**
 * The text beside the icon, which is now only ever a count.
 *
 * It used to carry the working state too, as a `·`, and that did not work at all: at menu-bar
 * size a lone dot is indistinguishable from a dead pixel, and there is no reason anyone would
 * read it as "mid-turn". Shape and motion say what state he is in; the number says how many
 * things are stacked up, which is the one thing a shape cannot count.
 */
export function mark(current: State): string {
  return current.waiting.length > 1 ? ` ${current.waiting.length}` : "";
}

/**
 * Point the icon at the state.
 *
 * Resting is the mark, still. Working is the mark turning — `frame` advances on a timer while
 * he is mid-turn and stops the moment he is not, so motion in the menu bar means exactly one
 * thing. Waiting swaps to the filled disc, in colour: a template image is polite and a request
 * he is blocked on should not be.
 */
function paint(mood: Mood, frame: number): void {
  if (!tray || tray.isDestroyed()) return;
  const wanted =
    mood === "waiting"
      ? TRAY_WAITING
      : mood === "working"
        ? (TRAY_WORKING[frame % TRAY_WORKING.length] ?? TRAY_ICON)
        : TRAY_ICON;
  if (wanted === painted) return; // setImage on every tick is a redraw nobody asked for
  const icon = nativeImage.createFromPath(wanted);
  if (icon.isEmpty()) return;
  icon.setTemplateImage(mood !== "waiting");
  tray.setImage(icon);
  painted = wanted;
}

/**
 * Turn the mark while he works, and only while he works.
 *
 * Keyed on the mood *changing*, not on every render. `render` runs whenever the stream says
 * anything — several times a second mid-turn — and restarting the interval each time, from a
 * frame counter that also restarted, produced a spinner that twitched between the first two
 * frames instead of going round. The counter lives out here for the same reason.
 */
function spin(mood: Mood): void {
  if (mood === spinningFor) return;
  spinningFor = mood;
  if (spinning) clearInterval(spinning);
  spinning = null;
  if (mood !== "working") return;
  spinning = setInterval(() => {
    frame += 1;
    paint("working", frame);
  }, 110);
}

export function summary(current: State): string {
  if (current.waiting.length) {
    return current.waiting.length === 1
      ? "Waiting on you"
      : `Waiting on you — ${current.waiting.length} things`;
  }
  if (current.working) {
    if (current.doing) return `Working on ${current.doing}`;
    return current.working === 1 ? "Working" : `Working — ${current.working} conversations`;
  }
  return "Here, nothing running";
}

/** Allow it, always allow it, or refuse — the three answers, without opening the window. */
async function answer(requestId: string, verdict: "session" | "always" | "deny"): Promise<void> {
  const path =
    verdict === "deny"
      ? `/api/permissions/${requestId}/deny`
      : `/api/permissions/${requestId}/approve`;
  try {
    await fetch(`${BACKEND_ORIGIN}${path}`, {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(verdict === "deny" ? {} : { scope: verdict }),
    });
  } catch {
    // He stays blocked and the row stays in the menu, which is the honest outcome — better than
    // a menu that reports success and a turn that never moves.
  }
}

function render(onQuit: () => void): void {
  if (!tray || tray.isDestroyed()) return;
  const current = state;

  // macOS only — it is the platform with text beside a menu-bar icon. Elsewhere the icon and
  // the tooltip carry it, which is what they did before this existed.
  if (process.platform === "darwin") tray.setTitle(mark(current));
  tray.setToolTip(`Kith — ${summary(current).toLowerCase()}`);
  const mood = moodOf(current);
  paint(mood, frame);
  spin(mood);

  const money = current.costUsd > 0 ? `$${current.costUsd.toFixed(2)} this run` : "";
  const wake = current.nextWake ? `Next: ${current.nextWake.note} ${until(current.nextWake.at)}` : "";

  tray.setContextMenu(
    Menu.buildFromTemplate([
      // First, above his status. It is the one thing in this menu you would otherwise never
      // find out at all — there is no other surface in a running Kith that mentions a release —
      // and it is a different kind of thing from the rest, which is why it sits apart rather
      // than in the "waiting on you" list. He is not blocked on it; you are just behind.
      ...(current.update
        ? ([
            {
              label: `Update to ${current.update.latest}`,
              click: () =>
                void shell.openExternal(current.update!.download || current.update!.page),
            },
            { type: "separator" },
          ] as const)
        : []),
      { label: summary(current), enabled: false },

      ...(current.waiting.length
        ? ([
            { type: "separator" },
            ...current.waiting.map((one) =>
              one.requestId
                ? {
                    label: one.label,
                    // A submenu rather than a click that opens the window. Three answers, in the
                    // order you actually want them: the safe one first, the permanent one second,
                    // and refusing last so it is not the thing under the pointer.
                    submenu: [
                      { label: one.detail || "He is asking to do this", enabled: false },
                      { type: "separator" as const },
                      {
                        label: "Allow once",
                        click: () => void answer(one.requestId!, "session").then(() => refresh(onQuit)),
                      },
                      {
                        label: "Always allow this",
                        click: () => void answer(one.requestId!, "always").then(() => refresh(onQuit)),
                      },
                      { type: "separator" as const },
                      {
                        label: "Refuse",
                        click: () => void answer(one.requestId!, "deny").then(() => refresh(onQuit)),
                      },
                      { type: "separator" as const },
                      { label: "Open Kith to decide", click: () => showMainWindow() },
                    ],
                  }
                : { label: one.label, click: () => showMainWindow() },
            ),
          ] as const)
        : []),

      // What came of the time the window was shut. Without this the menu could say he was idle
      // and mean either "he has done nothing all day" or "he finished everything an hour ago".
      ...(current.recent.length
        ? ([
            { type: "separator" },
            { label: "Recently", enabled: false },
            ...current.recent.map((one) => ({
              label: `  ${one.focus}`,
              sublabel: [ago(one.at), took(one.seconds), one.tools ? `${one.tools} tools` : ""]
                .filter(Boolean)
                .join(" · "),
              click: () => showMainWindow(),
            })),
          ] as const)
        : []),

      { type: "separator" },
      { label: "Open Kith", accelerator: "Command+O", click: () => showMainWindow() },
      { label: "Open his folder", click: () => void openWorkspace() },

      // The two numbers you would otherwise open the app to find, for a thing that runs and
      // spends money while you are not looking at it. Shown only when there is one: a menu that
      // says "$0.00" and "no schedules" every time is furniture.
      ...(money || wake
        ? ([
            { type: "separator" },
            ...(money ? [{ label: money, enabled: false }] : []),
            ...(wake ? [{ label: wake, enabled: false }] : []),
          ] as const)
        : []),

      { type: "separator" },
      {
        // Says "Quit Kith" rather than "Quit": the window closing is not quitting, and this is
        // the control that actually ends the app.
        label: "Quit Kith",
        accelerator: "Command+Q",
        click: () => {
          onQuit();
          app.quit();
        },
      },
    ]),
  );
}

/**
 * Re-read, then redraw — coalesced.
 *
 * A turn in flight emits a great many events and each one would otherwise be five HTTP requests.
 * The delay is long enough to collapse a burst and short enough that the count beside the icon
 * appears while you are still looking at the screen it appeared on.
 */
function refresh(onQuit: () => void): void {
  if (refreshing) return;
  refreshing = setTimeout(() => {
    refreshing = null;
    void readState().then((next) => {
      state = next;
      render(onQuit);
      countdown(onQuit);
    });
  }, 400);
}

/** Keep "next: in 12m" honest while nothing else is happening. Stops when there is nothing due. */
function countdown(onQuit: () => void): void {
  if (ticking) clearTimeout(ticking);
  ticking = null;
  if (!state.nextWake) return;
  ticking = setTimeout(() => {
    ticking = null;
    render(onQuit);
    countdown(onQuit);
  }, 60_000);
}

//: The kinds that can change any of the four things above. Anything else — a workspace write, a
//: project rename — cannot, and waking for it would be five requests to redraw the same menu.
const WATCHED = new Set(["turn", "permission", "question", "message", "schedule"]);

async function openWorkspace(): Promise<void> {
  try {
    const status = await read("/api/permissions");
    const root = String((status.workspace as { root?: string } | undefined)?.root ?? "");
    if (root) await shell.openPath(root);
  } catch {
    /* nothing to open */
  }
}

export function createTray(onQuit: () => void): Tray | null {
  const icon = nativeImage.createFromPath(TRAY_ICON);
  if (icon.isEmpty()) {
    // Not fatal — the app is perfectly usable without a menu-bar icon, and a missing asset
    // should not stop it launching.
    console.warn(`[kith] tray icon missing or unreadable: ${TRAY_ICON}`);
    return null;
  }
  // Belt and braces: the filename already ends in Template, but marking it explicitly keeps the
  // light/dark inversion working if the file is ever renamed.
  icon.setTemplateImage(true);

  tray = process.platform === "darwin" ? new Tray(icon, TRAY_GUID) : new Tray(icon);
  render(onQuit);
  refresh(onQuit);

  unsubscribe = onServerEvent((event) => {
    const kind = (event.data as { kind?: string } | null)?.kind;
    if (event.type === "changed" && kind && !WATCHED.has(kind)) return;
    refresh(onQuit);
  });

  // Note: once a context menu is set, macOS stops delivering click events, so left-clicking
  // opens the menu rather than the window. That is deliberate — one predictable interaction
  // beats a hidden one — but it also means a `click` handler here would never run, so there is
  // no rebuilding "as it opens": whatever was last rendered is what the menu shows.
  //
  // Which the stream handles, for everything that happens. The exception is the one number that
  // changes when nothing happens at all — a scheduled wake getting closer — so a slow tick runs
  // while there is a countdown to be wrong about, and not otherwise.
  return tray;
}

export function destroyTray(): void {
  unsubscribe?.();
  unsubscribe = null;
  if (refreshing) clearTimeout(refreshing);
  refreshing = null;
  if (ticking) clearTimeout(ticking);
  ticking = null;
  if (spinning) clearInterval(spinning);
  spinning = null;
  spinningFor = null;
  frame = 0;
  painted = "";
  state = IDLE;
  tray?.destroy();
  tray = null;
}
