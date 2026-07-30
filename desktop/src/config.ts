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
 * This wait is not politeness, it is correctness. If the window loads before the
 * server is listening, the SPA's EventSource gets a connection error, and a failed
 * EventSource handshake closes it *permanently* — no retry. The activity feed would
 * then stay dead for the whole session while the rest of the UI looked fine.
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
 * The 1x path only. macOS picks up the `@2x` sibling automatically, and the
 * `Template` suffix is what makes it invert for light/dark menu bars.
 */
export const TRAY_ICON = path.join(RESOURCES, "trayTemplate.png");

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
