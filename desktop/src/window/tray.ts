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

import { BACKEND_ORIGIN, TRAY_GUID, TRAY_ICON } from "../config";
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

/** One thing that cannot go further without you. */
export interface Waiting {
  label: string;
  /** Where the answer is given, when we know. */
  conversationId?: string;
}

export interface State {
  /** Conversations with a turn running. */
  working: number;
  waiting: Waiting[];
  /** What he has cost this run of the server, in dollars. Not estimated — see `meter.py`. */
  costUsd: number;
  /** The next standing job to fire, if there is one. */
  nextWake: { note: string; at: string } | null;
}

const IDLE: State = { working: 0, waiting: [], costUsd: 0, nextWake: null };
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
    const [conversations, permissions, messages, usage, schedules] = await Promise.all([
      read("/api/conversations?limit=40"),
      read("/api/permissions"),
      read("/api/messages"),
      read("/api/usage"),
      read("/api/schedules"),
    ]);

    const rows = (conversations.conversations ?? []) as Record<string, unknown>[];
    const pending = (permissions.pending ?? []) as unknown[];
    const unread = Number(messages.unread ?? 0);

    const waiting: Waiting[] = [];
    if (pending.length) {
      waiting.push({
        label: `${pending.length} waiting for permission`,
      });
    }
    // A question he asked is the same class of thing as a permission — he has stopped and cannot
    // start again — and it was invisible here. `waiting` is set on the row by the server; see
    // `services/conversations.recent`.
    for (const row of rows.filter((one) => one.waiting)) {
      waiting.push({
        label: `Asked you: ${title(row)}`,
        conversationId: String(row.id ?? ""),
      });
    }
    if (unread) {
      waiting.push({ label: `${unread} unread from him` });
    }

    return {
      working: rows.filter((one) => one.working).length,
      waiting,
      costUsd: Number(usage.costUsd ?? 0),
      nextWake: soonest((schedules.schedules ?? []) as Record<string, unknown>[]),
    };
  } catch {
    return IDLE;
  }
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

/**
 * The mark beside the icon.
 *
 * A number when something is waiting on you, because that is the one state worth interrupting
 * for and a digit in the menu bar is unmistakable. A single dot while he is working, which is
 * information without being a demand. Nothing at all when he is idle — an app that always shows
 * something in the menu bar is an app you stop seeing.
 */
export function mark(current: State): string {
  if (current.waiting.length) return ` ${current.waiting.length}`;
  return current.working ? " ·" : "";
}

export function summary(current: State): string {
  if (current.waiting.length) return "Waiting on you";
  if (current.working) {
    return current.working === 1 ? "Working" : `Working — ${current.working} conversations`;
  }
  return "Here, nothing running";
}

function render(onQuit: () => void): void {
  if (!tray || tray.isDestroyed()) return;
  const current = state;

  tray.setTitle(mark(current));
  tray.setToolTip(`Kith — ${summary(current).toLowerCase()}`);

  const money = current.costUsd > 0 ? `$${current.costUsd.toFixed(2)} so far this run` : "";
  const wake = current.nextWake ? `${current.nextWake.note} ${until(current.nextWake.at)}` : "";

  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: summary(current), enabled: false },
      ...(current.waiting.length
        ? ([
            { type: "separator" },
            ...current.waiting.map((one) => ({
              label: one.label,
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
            ...(wake ? [{ label: `Next: ${wake}`, enabled: false }] : []),
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
    });
  }, 400);
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

  // Still rebuilt as it opens. The stream keeps it true between events; this covers the one
  // thing no event announces — a scheduled wake getting closer while nothing else happens.
  tray.on("right-click", () => refresh(onQuit));
  tray.on("click", () => refresh(onQuit));

  // Note: once a context menu is set, macOS stops delivering click events, so left-clicking
  // opens the menu rather than the window. That is deliberate — one predictable interaction
  // beats a hidden one.
  return tray;
}

export function destroyTray(): void {
  unsubscribe?.();
  unsubscribe = null;
  if (refreshing) clearTimeout(refreshing);
  refreshing = null;
  state = IDLE;
  tray?.destroy();
  tray = null;
}
