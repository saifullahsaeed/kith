/** URL <-> view-state helpers for the app's routes (react-router drives the URL;
 * these just translate between a path and what's open).
 *
 * The app is a single Workspace with the Control Panel as an overlay; the URL
 * stays in step with what's open so deep links, refresh, and back/forward work:
 *   /                          → home (chat + mind)
 *   /control-panel/<tab>       → the panel open on a tab (e.g. /control-panel/memories)
 *   /tasks/<id>                → the panel open on that task's detail
 *   /settings/<tab>            → settings on a tab (model | tools | chat | advanced)
 *   /chat/<id>                 → that conversation open, which is where a question he is
 *                                waiting on has to land — the answer is in one chat and
 *                                nowhere else
 *   /messages                  → the inbox open, for a notice with nothing narrower to
 *                                point at (a reach-out, a stall, a session resting itself)
 *   /context                   → the context detail screen, itemised. Its own route rather than
 *                                a panel tab: the Control Panel is everything he *is*, which
 *                                outlives any conversation, and this is about the one on screen.
 */

// The panel's internal tab values. The URL uses these verbatim except for the
// one whose nav label ("Memories") differs from its value ("memory").
const TABS = [
  "overview",
  "lifetime",
  "memory",
  "journal",
  "projects",
  "reminders",
  "schedules",
  "sources",
  "workspace",
] as const;

export type PanelTab = (typeof TABS)[number];

const SLUG_FOR: Partial<Record<PanelTab, string>> = { memory: "memories" };
const TAB_FOR_SLUG: Record<string, PanelTab> = { memories: "memory" };

function tabSlug(tab: PanelTab): string {
  return SLUG_FOR[tab] ?? tab;
}

function slugTab(slug: string): PanelTab | null {
  if (TAB_FOR_SLUG[slug]) return TAB_FOR_SLUG[slug];
  return (TABS as readonly string[]).includes(slug) ? (slug as PanelTab) : null;
}

/** Settings' tabs. Model and Tools are separate because they are separate decisions
 *  with separate costs — per token against per search — and each saves on its own. */
const SETTINGS_TABS = ["model", "persona", "skills", "tools", "chat", "advanced"] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number];

export type Route = {
  panelOpen: boolean;
  tab: PanelTab;
  taskId: number | null;
  settingsTab: SettingsTab | null;
  inboxOpen: boolean;
  contextOpen: boolean;
  /** A conversation to open. Notifications need it: "he is waiting on an answer" is useless
   *  if it lands you in whichever chat you happened to have open. */
  conversationId: string | null;
};

/** Read the current URL into the app's view state. */
const HOME: Route = {
  panelOpen: false,
  tab: "overview",
  taskId: null,
  settingsTab: null,
  inboxOpen: false,
  contextOpen: false,
  conversationId: null,
};

export function parseLocation(pathname: string = window.location.pathname): Route {
  if (pathname.startsWith("/settings")) {
    const slug = pathname.split("/")[2] ?? "";
    const tab = (SETTINGS_TABS as readonly string[]).includes(slug)
      ? (slug as SettingsTab)
      : "model";
    return { ...HOME, settingsTab: tab };
  }
  if (pathname.startsWith("/tasks/")) {
    const id = Number.parseInt(pathname.split("/")[2] ?? "", 10);
    if (Number.isFinite(id)) return { ...HOME, panelOpen: true, tab: "projects", taskId: id };
  }
  if (pathname.startsWith("/control-panel")) {
    const tab = slugTab(pathname.split("/")[2] ?? "") ?? "overview";
    return { ...HOME, panelOpen: true, tab };
  }
  if (pathname.startsWith("/chat/")) {
    const id = pathname.split("/")[2] ?? "";
    if (id) return { ...HOME, conversationId: decodeURIComponent(id) };
  }
  if (pathname.startsWith("/messages")) {
    return { ...HOME, inboxOpen: true };
  }
  if (pathname.startsWith("/context")) {
    return { ...HOME, contextOpen: true };
  }
  return HOME;
}

export const pathForHome = () => "/";
export const pathForTab = (tab: PanelTab) => `/control-panel/${tabSlug(tab)}`;
export const pathForTask = (id: number) => `/tasks/${id}`;
export const pathForSettings = (tab: SettingsTab = "model") => `/settings/${tab}`;
export const pathForMessages = () => "/messages";
export const pathForContext = () => "/context";
export const pathForConversation = (id: string) => `/chat/${encodeURIComponent(id)}`;
