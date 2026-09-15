import { useEffect, useRef } from "react";

import { useQuery } from "@tanstack/react-query";

import { fetchPluginCalls, replyToPluginCall } from "@/lib/backend";
import { keys } from "@/lib/query-keys";
import type { TabRef } from "@/components/shell/layout/tree";

/**
 * Things a plugin asks *Kith* to do, performed by the app.
 *
 * ## Why this is a renderer concern
 *
 * Everything in here is something only the window knows: which tabs are open, which one is in
 * front, where the layout put them. There is no endpoint for "put this tab on screen" because
 * the layout tree lives here and is this component's to change — so a plugin command that wants
 * it has to reach the renderer, which is what `host` delivery is.
 *
 * ## The dead end it exists to fix
 *
 * The browser plugin's `look` refuses when its tab is shut, and told him to ask the person to
 * open it. He had no way to open it himself. So the single most common thing he needs from a
 * surface — *be on screen* — was the one thing he could not do, and every errand involving a
 * screenshot became a conversation. `open_surface` closes that.
 *
 * ## How it reaches here
 *
 * The same parking queue a `surface` call uses, split by who answers. That split is not
 * cosmetic: `pending` **claims** what it hands out so two windows do not race, so a mounted
 * frame polling the same route would take a host effect it has no way to perform and the call
 * would sit until its deadline with the app never seeing it.
 *
 * Everything else comes free with the queue — the twenty-second deadline, the per-window claim,
 * the wake when a turn is stopped, and the refusal before parking when nobody is watching.
 */
export function useHostEffects(
  conversationId: string,
  openSurface: (ref: TabRef) => void,
  closeTab?: (ref: TabRef) => void,
): void {
  /** Per renderer, so two windows on one backend do not both perform the same effect. */
  const client = useRef(`host-${Math.random().toString(36).slice(2, 10)}`);
  /** Answered already, so a refetch that still lists a call does not perform it twice. */
  const done = useRef<Set<string>>(new Set());

  /* Snapshot-then-subscribe, the join `use-activity` uses: fetched on mount **and** on every
   * `plugin_call` change, so an effect asked for while this window was reloading is still
   * waiting rather than lost with a push that had already happened. */
  const { data: waiting } = useQuery({
    queryKey: keys.pluginHostCalls(conversationId),
    queryFn: () => fetchPluginCalls(conversationId, client.current, "host"),
  });

  useEffect(() => {
    if (!waiting?.length) return;
    for (const call of waiting) {
      if (done.current.has(call.id)) continue;
      done.current.add(call.id);

      const view = String(call.args?.view ?? call.view ?? "");
      const ref = { surface: "plugin", plugin: call.plugin, view, instance: "" } as TabRef;

      if (call.command && view) {
        // Focus-or-open. `openTab` brings an existing tab forward rather than stacking a second
        // one, so him asking twice is not two panes.
        if (call.args?.close === true && closeTab) closeTab(ref);
        else openSurface(ref);
        void replyToPluginCall(call.id, { view, opened: true });
      } else {
        // A call naming no surface is a manifest fault that got past install. Answered rather
        // than left to time out, because a turn parked for twenty seconds on a typo is the
        // worst version of this.
        void replyToPluginCall(call.id, { error: "that command names no surface to open" }, false);
      }
    }
    // A bound on the set, so a long session does not grow it without limit. The ids are only
    // needed for as long as a refetch might still list them.
    if (done.current.size > 200) done.current = new Set([...done.current].slice(-50));
  }, [waiting, openSurface, closeTab]);
}
