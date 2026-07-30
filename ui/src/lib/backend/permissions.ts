/** Client for the permission API — the mode, and answering what he asks for. */

export type PermissionMode = "ask" | "auto" | "bypass";

export interface PermissionRequest {
  id: string;
  kind: "read" | "write" | "delete" | "command";
  /** The path or the command itself. */
  what: string;
  why: string;
  at: number;
}

export interface WorkspaceStatus {
  root: string;
  exists: boolean;
  mode: string;
  bytes: number;
  entries: number;
}

export interface PermissionState {
  mode: PermissionMode;
  modes: PermissionMode[];
  pending: PermissionRequest[];
  grants: string[];
  sessionGrants: string[];
  workspace: WorkspaceStatus;
}

/** What each mode actually means, in one line, for the menu. */
export const MODE_LABELS: Record<PermissionMode, { label: string; hint: string }> = {
  ask: {
    label: "Ask",
    hint: "Free inside his folder. Anything outside it, or destructive, asks you first.",
  },
  auto: {
    label: "Auto",
    hint: "No prompts for ordinary work outside his folder. Dangerous things still ask.",
  },
  bypass: {
    label: "Bypass",
    hint: "No prompts at all. Nothing is checked — this is what the container used to make safe.",
  },
};

export async function fetchPermissions(): Promise<PermissionState> {
  const response = await fetch("/api/permissions");
  if (!response.ok) throw new Error(`/api/permissions returned ${response.status}`);
  return (await response.json()) as PermissionState;
}

export async function setPermissionMode(mode: PermissionMode): Promise<PermissionState> {
  const response = await fetch("/api/permissions/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!response.ok) throw new Error(`could not set mode (${response.status})`);
  return (await response.json()) as PermissionState;
}

export async function answerPermission(
  id: string,
  allow: boolean,
  scope: "session" | "always" = "session",
): Promise<void> {
  const response = await fetch(`/api/permissions/${id}/${allow ? "approve" : "deny"}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  if (!response.ok) throw new Error(`could not answer (${response.status})`);
}

/**
 * Ask the desktop shell to post a native notification.
 *
 * Returns whether one actually appeared. The browser's own Notification API cannot do this
 * from here: it reports permission "granted", throws nothing, and macOS drops it, because
 * a notification from a page has no app for macOS to attribute. Only the shell's main
 * process does — so this goes to the server, which asks the shell.
 *
 * `false` means Kith is running as a bare server rather than in the app. That is a fact
 * about the setup and worth saying out loud, because "sent one" with nothing on screen is
 * the most confusing possible outcome.
 */
export async function sendTestNotification(): Promise<boolean> {
  const response = await fetch("/api/system/notify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "Kith", body: "This is how he'll reach you." }),
  });
  if (!response.ok) return false;
  const body = (await response.json()) as { shown?: boolean };
  return Boolean(body.shown);
}

/** Open one of macOS's settings panes, by name. Returns whether it opened. */
export async function openSettingsPane(
  pane: "fullDisk" | "notifications" | "files",
): Promise<boolean> {
  const response = await fetch("/api/system/settings-pane", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pane }),
  });
  if (!response.ok) return false;
  const body = (await response.json()) as { opened?: boolean };
  return Boolean(body.opened);
}
