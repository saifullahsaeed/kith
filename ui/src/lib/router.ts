/** URL <-> view-state helpers for the app's routes (react-router drives the URL;
 * these just translate between a path and what's open).
 *
 * The app is a single Workspace with the Control Panel as an overlay; the URL
 * stays in step with what's open so deep links, refresh, and back/forward work:
 *   /                          → home (chat + mind)
 *   /control-panel/<tab>       → the panel open on a tab (e.g. /control-panel/memories)
 *   /tasks/<id>                → the panel open on that task's detail
 */

// The panel's internal tab values. The URL uses these verbatim except for the
// one whose nav label ("Memories") differs from its value ("memory").
const TABS = [
  "overview", "lifetime", "memory", "notes", "journal", "projects",
  "curiosities", "reminders", "schedules", "messages", "people",
  "sources", "workspace", "tools",
] as const;

export type PanelTab = (typeof TABS)[number];

const SLUG_FOR: Partial<Record<PanelTab, string>> = { memory: "memories" };
const TAB_FOR_SLUG: Record<string, PanelTab> = { memories: "memory" };

export function tabSlug(tab: PanelTab): string {
  return SLUG_FOR[tab] ?? tab;
}

function slugTab(slug: string): PanelTab | null {
  if (TAB_FOR_SLUG[slug]) return TAB_FOR_SLUG[slug];
  return (TABS as readonly string[]).includes(slug) ? (slug as PanelTab) : null;
}

export type Route = { panelOpen: boolean; tab: PanelTab; taskId: number | null };

/** Read the current URL into the app's view state. */
export function parseLocation(pathname: string = window.location.pathname): Route {
  if (pathname.startsWith("/tasks/")) {
    const id = Number.parseInt(pathname.split("/")[2] ?? "", 10);
    if (Number.isFinite(id)) return { panelOpen: true, tab: "projects", taskId: id };
  }
  if (pathname.startsWith("/control-panel")) {
    const tab = slugTab(pathname.split("/")[2] ?? "") ?? "overview";
    return { panelOpen: true, tab, taskId: null };
  }
  return { panelOpen: false, tab: "overview", taskId: null };
}

export const pathForHome = () => "/";
export const pathForTab = (tab: PanelTab) => `/control-panel/${tabSlug(tab)}`;
export const pathForTask = (id: number) => `/tasks/${id}`;
