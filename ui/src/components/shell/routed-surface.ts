import { useEffect, useRef } from "react";

/**
 * Keeping the URL and the layout agreeing about a surface that has a route.
 *
 * The Context breakdown is reachable as `/context`, and an effect in `Workspace` opens its tab
 * when the route names it. That worked in one direction only. `closeTab` clears the URL on the
 * way out — its own comment explains why, and names the "button looks broken" symptom — but
 * `closeTab` is not the only way a tab dies. `PaneMenu`'s "Close this pane" calls the layout
 * store's `closePane` directly, taking every tab in the pane with it, and `layout/` is
 * deliberately ignorant of what a surface is, so it has nothing to tell the URL with.
 *
 * The result was a dead button. `/context` stayed in the address bar with no Context tab
 * anywhere, so `contextOpen` stayed true, and every later click on the expand control navigated
 * to the route it was already on. Same pathname, same derived boolean, no dependency change, no
 * effect, nothing on screen — until you happened to navigate somewhere else and unstick it.
 * Reported as "sometimes it shows, sometimes it just does not work".
 *
 * **Fixing it inside `closePane` was the wrong layer.** It would mean teaching the layout tree
 * which surfaces have URLs, which is the one thing `layout/` says it must not learn — and it
 * would fix this path and not the next one. The invariant is stated here instead, where the two
 * halves are already both in view: *a route that names a surface opens it, and a surface that
 * leaves while its route still names it clears the route.* Any future way of removing a tab is
 * covered without knowing about it.
 *
 * The decision is pure and tested on its own, because the wiring is what was wrong and the
 * wiring had no test — nothing in this suite mounts `Workspace`.
 */

/** What the disagreement calls for. */
export type RouteAction = "show" | "clear" | "nothing";

export function nextAction({
  open,
  present,
  wasPresent,
}: {
  /** Does the URL name this surface right now. */
  open: boolean;
  /** Is its tab in the layout tree right now. */
  present: boolean;
  /** Was its tab in the tree the last time this was asked. */
  wasPresent: boolean;
}): RouteAction {
  if (!open || present) return "nothing";
  // The route names it and it is not here. Which of the two cases depends entirely on whether
  // it was here a moment ago — and that is the whole of the distinction that keeps this from
  // fighting the person. Never here means the route is asking for it. Here until just now means
  // they closed it, and reopening would make "Close this pane" a button that does not close the
  // pane.
  return wasPresent ? "clear" : "show";
}

/**
 * The hook around it. `show` and `clear` are supplied by the caller because this module has no
 * business knowing that one is `openSurface` and the other is `navigate(pathForHome())`.
 */
export function useRoutedSurface({
  open,
  present,
  show,
  clear,
}: {
  open: boolean;
  present: boolean;
  show: () => void;
  clear: () => void;
}): void {
  const wasPresent = useRef(present);

  useEffect(() => {
    const decision = nextAction({ open, present, wasPresent: wasPresent.current });
    // Recorded before acting, so the action's own re-render sees the state it produced rather
    // than the one it was reacting to.
    wasPresent.current = present;
    if (decision === "show") show();
    else if (decision === "clear") clear();
  }, [open, present, show, clear]);
}
