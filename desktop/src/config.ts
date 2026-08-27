/**
 * Everything the shell needs to know about its surroundings, in one place.
 *
 * The backend is a separate process (Docker today, a bundled binary later), so
 * these are addresses to reach rather than things this app owns.
 */

import * as path from "node:path";

/** Where the Kith server listens. It serves BOTH the API and the built UI. */
export const BACKEND_ORIGIN = process.env.KITH_ORIGIN ?? "http://127.0.0.1:8611";

/** Cheap liveness endpoint — answers without touching the model or the database. */
export const HEALTH_URL = `${BACKEND_ORIGIN}/api/health`;

/**
 * How long to wait for the backend before giving up and showing the problem.
 *
 * This was correctness rather than politeness: the page opened its own `EventSource`s on mount,
 * and a failed handshake closes one *permanently* — no retry — so loading half a second early cost
 * the live feed for the whole session while the rest of the UI looked fine.
 *
 * That specific failure is gone. The stream is held by the main process now and retries with
 * backoff (`server/events.ts`), so an early start costs a reconnect rather than a session. The wait
 * stays because the *document* still has to come from the server, and a window that opens on a
 * connection error is a blank window — but it is politeness again now, not a load-bearing hack.
 */
export const BACKEND_WAIT_MS = 30_000;
export const BACKEND_POLL_MS = 250;

/** Window geometry. Minimums stop the layout collapsing into unusable columns. */
export const WINDOW = {
  width: 1_280,
  height: 860,
  minWidth: 940,
  minHeight: 600,
} as const;

/**
 * Stable identifier so macOS remembers this icon's menu-bar position between
 * launches instead of reshuffling it.
 */
export const TRAY_GUID = "6f1c0f9e-6a4f-4a1e-9a0a-6b5f2f7a1c31";

const RESOURCES = path.join(__dirname, "..", "resources");

/**
 * The preload script, compiled beside this file.
 *
 * One receive-only channel, so the event stream can live in the main process instead of in the
 * page — see `preload.ts` for what that buys and `server/events.ts` for why. `__dirname` is `out/`
 * at runtime, which is where tsc puts both.
 */
export const PRELOAD = path.join(__dirname, "preload.js");

/**
 * The 1x path only. macOS picks up the `@2x` sibling automatically, and the
 * `Template` suffix is what makes it invert for light/dark menu bars.
 */
export const TRAY_ICON = path.join(RESOURCES, "trayTemplate.png");

/**
 * The three states the menu bar has to tell apart at a glance: resting, working, waiting on you.
 *
 * A single character beside a static icon was the first attempt and it was not readable — a `·`
 * is indistinguishable from a speck on the screen, and nobody learns that a dot means "mid-turn".
 * Shape and motion carry it instead.
 *
 * Working is `TRAY_ICON` rotated, not a new symbol: the mark is already an open ring with a gap,
 * so turning it reads as a spinner using the drawing that is already there. Waiting is a filled
 * disc — solid against open is the strongest contrast available at sixteen pixels — and it is the
 * one icon that is *not* a template, because a request he is blocked on should pull the eye
 * rather than politely match whatever the menu bar is doing.
 */
export const TRAY_WORKING = Array.from({ length: 8 }, (_unused, frame) =>
  path.join(RESOURCES, `trayWorking${frame}Template.png`),
);

export const TRAY_WAITING = path.join(RESOURCES, "trayWaiting.png");

/**
 * The app's real icon — dock, notifications, anywhere the running app needs to say who it
 * is with a picture rather than a name. A packaged build already gets this from the .app
 * bundle itself; it is unpackaged (`npm run dev`) that has nothing, because that is
 * genuinely running as Electron.app, not Kith.app, until something says otherwise.
 */
export const APP_ICON = path.join(RESOURCES, "icon.png");

/** Schemes we are willing to hand to the operating system. */
export const EXTERNAL_SCHEMES = new Set(["http:", "https:", "mailto:"]);

/**
 * Token appended to the user agent so the page can recognise THIS shell.
 *
 * The UI reserves space for the window controls and turns its header into a drag
 * region, and it must only do that in our own window. Sniffing for "Electron"
 * looked sufficient and was not: plenty of other things are Electron browsers
 * (Claude's own preview pane, VS Code's simple browser), and the app would have
 * drawn a 78px gap for traffic lights that were never there.
 *
 * A dedicated token is exact, needs no preload, and survives reloads and
 * navigation in a way a query parameter would not.
 */
export const UA_TOKEN = "KithDesktop";
