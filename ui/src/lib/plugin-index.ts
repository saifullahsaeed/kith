/**
 * What plugin surfaces exist, read synchronously at import.
 *
 * **Why this cannot be a fetch.** `useLayout` evaluates `readStored() ?? defaultLayout()` at
 * module import, before any request can resolve. A plugin index that arrives over HTTP arrives
 * too late to give a rehydrated plugin tab its real title, icon or minimum width — so the pane
 * would paint at the placeholder's width and then resize under the person, which reads as the
 * app rearranging itself for no reason.
 *
 * Because a surface declaration is *data*, core ships it in the document it already templates.
 * The block is `<script type="application/json">`, never an executable one: `script-src` hashes
 * apply only to scripts that run, so this adds nothing to the hash list — and the hash list is
 * exactly what broke the first time a script went into that document, where the policy did not
 * know about it and the page loaded with no token and 401'd everything.
 *
 * Refreshed over HTTP afterwards, because installing a plugin must not need a reload.
 */

export interface PluginSurface {
  plugin: string;
  pluginName: string;
  view: string;
  title: string;
  icon: string;
  minWidth: number;
  minHeight: number;
  instances: "single" | "many";
  answers: "conversation" | "any";
  /** State keys holding a path to one of the plugin's own files. When one changes the host
   *  reads the bytes and pushes them in as an asset — declared, never requested, because a
   *  frame has no verb that reaches outside its own store. */
  assets?: string[];
}

let surfaces: PluginSurface[] = read();
/** Whether the index has been settled at all — see `standing` in `surfaceFor`. */
let settled = surfaces.length > 0;
const listeners = new Set<() => void>();

function read(): PluginSurface[] {
  try {
    const raw = document.getElementById("kith-plugins")?.textContent;
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed?.surfaces) ? (parsed.surfaces as PluginSurface[]) : [];
  } catch {
    /* A malformed block is no plugins, not a crash on boot. */
    return [];
  }
}

export function pluginSurfaces(): PluginSurface[] {
  return surfaces;
}

export function pluginSurface(plugin: string, view: string): PluginSurface | undefined {
  return surfaces.find((one) => one.plugin === plugin && one.view === view);
}

/** Whether we know the whole list yet. `false` is the window between import and the first fetch. */
export function indexSettled(): boolean {
  return settled;
}

export function setPluginSurfaces(next: PluginSurface[]): void {
  surfaces = next;
  settled = true;
  for (const listener of listeners) listener();
}

/** For `useSyncExternalStore` — a tab's title has to re-render when the index lands. */
export function watchPluginSurfaces(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
