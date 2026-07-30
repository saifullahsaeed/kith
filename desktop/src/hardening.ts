/**
 * Security posture that has to be set once, for the whole app.
 *
 * Per-window concerns (navigation, external links) live in window.ts. What's here
 * is session- and app-level, and both items are cases where Electron's *absence*
 * of configuration is the dangerous state rather than a safe default.
 */

import { Menu, app, session } from "electron";

import { UA_TOKEN } from "./config";

/**
 * Permissions the renderer is allowed to have.
 *
 * Notifications is the only one the UI actually asks for — see the unread-message
 * hook in web/src/hooks/use-messages.ts. Everything else is denied.
 */
const ALLOWED_PERMISSIONS = new Set(["notifications"]);

/**
 * Deny every permission the app doesn't need.
 *
 * This is not belt-and-braces. With no handler installed, Electron **grants every
 * permission automatically** — camera, microphone, screen capture, clipboard read,
 * idle detection — with no prompt and no record. For an app whose renderer displays
 * agent-authored HTML and markdown, silently auto-granting screen capture is not a
 * posture anyone would choose deliberately.
 *
 * Both hooks are needed: the request handler covers `navigator.permissions.request`,
 * while the check handler covers the synchronous query path. Installing only one
 * leaves the other wide open.
 */
export function lockDownPermissions(): void {
  const target = session.defaultSession;
  const decide = (permission: string): boolean => {
    const allowed = ALLOWED_PERMISSIONS.has(permission);
    if (!allowed) console.warn(`[kith] denied permission request: ${permission}`);
    return allowed;
  };

  target.setPermissionRequestHandler((_contents, permission, callback) => {
    callback(decide(permission));
  });
  target.setPermissionCheckHandler((_contents, permission) => decide(permission));
}

/**
 * Install an application menu that keeps the standard edit shortcuts.
 *
 * Electron's default menu is replaced the moment you set your own, and the edit
 * role is where Cmd+C / Cmd+V / Cmd+A live. Omit it and copy-paste stops working
 * in the chat composer — which reads as the app being broken, because it is.
 */
export function installApplicationMenu(): void {
  const isMac = process.platform === "darwin";
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      ...(isMac ? [{ role: "appMenu" as const }] : []),
      { role: "fileMenu" },
      { role: "editMenu" },
      { role: "viewMenu" },
      { role: "windowMenu" },
    ]),
  );
}

/**
 * Turn on the sandbox process-wide.
 *
 * Must be called before the app is ready. It is already the per-window default;
 * doing it at app level means a window created elsewhere later cannot quietly opt
 * out. Note that turning the sandbox *off* would also break `window.EventSource`,
 * so the activity feed depends on this staying on.
 */
export function enableSandbox(): void {
  app.enableSandbox();
}

/**
 * Append our own token to the user agent.
 *
 * This is how the page tells "running inside the Kith desktop shell" apart from
 * "running in a browser that happens to be built on Electron" — a distinction that
 * matters because the UI only reserves space for window controls in the former.
 * Appending keeps the standard UA intact so nothing that sniffs Chrome breaks.
 */
export function identifyToRenderer(): void {
  app.userAgentFallback = `${app.userAgentFallback} ${UA_TOKEN}/${app.getVersion()}`;
}
