import type { FC } from "react";
import { PlugZap } from "lucide-react";

import { useLayout } from "@/components/shell/layout/store";
import { pluginSurface, pluginSurfaces } from "@/lib/plugin-index";

/** `plugin__sketchpad__draw` -> `["sketchpad", "draw"]`, or `["", ""]`. */
export function splitPluginTool(name: string): [string, string] {
  if (!name.startsWith("plugin__")) return ["", ""];
  const rest = name.slice("plugin__".length);
  const at = rest.indexOf("__");
  if (at <= 0) return ["", ""];
  return [rest.slice(0, at), rest.slice(at + 2)];
}

/**
 * What a plugin command did, and one click to go and look at it.
 *
 * **The point of a plugin call is usually not its return value.** He draws seventeen shapes and
 * the useful thing is the board, not seventeen cards saying a write succeeded. So this says what
 * changed in one line and puts the surface one click away — which is the whole of the complaint
 * that a plugin's output was a blob you had to read before you knew a tab existed.
 *
 * Keyed on the `plugin__` namespace, so no plugin has to know this component is here. A plugin
 * with no surface gets the sentence and no button, which is the honest shape rather than a
 * button that opens nothing.
 */
export const PluginOutcome: FC<{ plugin: string; command: string; result: unknown }> = ({
  plugin,
  command,
  result,
}) => {
  const open = useLayout((one) => one.open);
  const held = result as { from?: string; changed?: Record<string, unknown>; queued?: { args?: Record<string, unknown> }; note?: string } | null;

  /* Its first surface, if it has one. Read from the index the app already holds rather than
   * fetched, because this renders inside a transcript that may be scrolled through long after
   * the call — and a card that fires a request per plugin call would be a request per row. */
  const surfaces = pluginSurfaces().filter((one) => one.plugin === plugin);
  const surface = surfaces[0];
  const known = surface ? pluginSurface(surface.plugin, surface.view) : undefined;

  return (
    <div className="flex items-center gap-2.5 rounded-lg bg-muted/30 p-2.5 ring-1 ring-border/60">
      <PlugZap className="text-muted-foreground/60 size-3.5 shrink-0" />
      <p className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
        {describe(command, held)}
      </p>
      {known ? (
        <button
          type="button"
          onClick={() => open({ surface: "plugin", plugin: surface.plugin, view: surface.view })}
          className="text-kith hover:text-kith/80 focus-visible:text-kith/80 shrink-0 text-xs font-medium underline-offset-2 hover:underline"
        >
          Open {known.title}
        </button>
      ) : null}
    </div>
  );
};

/** One line about what the call changed, from the shapes `commands.py` actually returns. */
function describe(
  command: string,
  held: { changed?: Record<string, unknown>; queued?: { args?: Record<string, unknown> }; note?: string } | null,
): string {
  const verb = command.replace(/_/g, " ");
  if (held?.note) return String(held.note);
  // `does.collect` — the whole call, queued for the surface to fold in.
  const queued = held?.queued?.args;
  if (queued) {
    const named = Object.entries(queued)
      .filter(([, value]) => value !== undefined && value !== "")
      .slice(0, 3)
      .map(([key, value]) => `${key} ${value}`)
      .join(", ");
    return named ? `${verb} — ${named}` : verb;
  }
  // `does.set` — named keys, set to named values.
  if (held?.changed && Object.keys(held.changed).length) {
    return `${verb} — ${Object.entries(held.changed)
      .map(([key, value]) => `${key}: ${value}`)
      .join(", ")}`;
  }
  return verb;
}


/**
 * One card per plugin a run touched, with a way into its tab.
 *
 * **Rendered beside the collapsed tool group rather than inside it**, which is the whole fix.
 * On a result body it was two clicks and a guess away — seventeen `draw` calls collapse to a
 * single "18 tools" line, so you had to expand the group and then expand a row to find a button
 * you had no reason to believe was there. It was reported as not appearing, which is the right
 * way to describe something nobody would ever find.
 *
 * One card per plugin, not per call: what he drew is the board, and seventeen cards each
 * reporting a successful write is the noise this replaces.
 */
export const PluginRunOutcome: FC<{ names: string[] }> = ({ names }) => {
  const open = useLayout((one) => one.open);

  /* Which plugins this run called, in the order they were first called, de-duplicated. Insertion
   * order rather than sorted, so the card matches the order the work happened in. */
  const touched: string[] = [];
  for (const name of names) {
    const [plugin] = splitPluginTool(name);
    if (plugin && !touched.includes(plugin)) touched.push(plugin);
  }
  if (!touched.length) return null;

  const cards = touched
    .map((plugin) => ({ plugin, surface: pluginSurfaces().find((one) => one.plugin === plugin) }))
    // A plugin with no surface has nowhere to go, and a button that opens nothing is worse than
    // no button. Its calls still read normally in the group above.
    .filter((one) => !!one.surface);
  if (!cards.length) return null;

  return (
    <div className="flex flex-wrap gap-1.5">
      {cards.map(({ plugin, surface }) => (
        <button
          key={plugin}
          type="button"
          onClick={() => open({ surface: "plugin", plugin, view: surface!.view })}
          className="border-border/70 bg-card/50 hover:border-kith/50 focus-visible:border-kith/50 flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition"
        >
          <PlugZap className="text-muted-foreground/60 size-3.5 shrink-0" />
          <span className="font-medium">{surface!.title}</span>
          <span className="text-muted-foreground/70">— open</span>
        </button>
      ))}
    </div>
  );
};
