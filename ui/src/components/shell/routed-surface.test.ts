import { describe, expect, it } from "vitest";

import { nextAction } from "./routed-surface";

/**
 * A surface the URL can name, and the layout that has to agree with it.
 *
 * The bug this exists for: the Context breakdown opens because `/context` in the URL flips
 * `route.contextOpen` from false to true and an effect watching that boolean opens the tab.
 * Closing the tab by its own X goes through a wrapper that navigates home, so the boolean
 * falls back to false and the next click works.
 *
 * `closePane` — the pane's own context menu, "Close this pane" — reaches into the layout store
 * directly and takes every tab in the pane with it. Nothing tells the URL. So `/context` is
 * still in the address bar with no Context tab anywhere, `contextOpen` is stuck true, and
 * clicking the expand button navigates to the route it is already on: the boolean does not
 * change, the effect does not re-run, and the button does nothing at all. Permanently, until
 * you happen to navigate somewhere else.
 *
 * Reported as "sometimes it shows, sometimes it just does not work", which is exactly what a
 * dead button looks like when whether it is dead depends on how you closed it last time.
 *
 * The rule, stated once and pure so it can be tested without a router or a DOM: a route that
 * names a surface opens it; a surface that *leaves* while its route still names it clears the
 * route, so the next request is a real transition again.
 */
describe("what to do when the URL and the layout disagree", () => {
  it("opens the surface the route asks for", () => {
    expect(nextAction({ open: true, present: false, wasPresent: false })).toBe("show");
  });

  it("does nothing once it is open", () => {
    expect(nextAction({ open: true, present: true, wasPresent: false })).toBe("nothing");
    expect(nextAction({ open: true, present: true, wasPresent: true })).toBe("nothing");
  });

  it("clears the route when the surface is taken away under it", () => {
    // closePane. The tab was there a moment ago and is not now, and nothing told the URL.
    expect(nextAction({ open: true, present: false, wasPresent: true })).toBe("clear");
  });

  it("does not reopen what was deliberately closed", () => {
    // The distinction that keeps this from fighting the person: a surface that was never here
    // is one the route is asking for, and one that just left is one they closed. Reopening the
    // second would make "close this pane" a button that does not close the pane.
    expect(nextAction({ open: true, present: false, wasPresent: true })).not.toBe("show");
  });

  it("stays out of it when the route does not name the surface", () => {
    expect(nextAction({ open: false, present: false, wasPresent: true })).toBe("nothing");
    expect(nextAction({ open: false, present: true, wasPresent: true })).toBe("nothing");
    expect(nextAction({ open: false, present: false, wasPresent: false })).toBe("nothing");
  });

  it("closing by the tab's own X is already handled and must not double-fire", () => {
    // That path closes the tab and navigates home in the same batch, so by the time this is
    // asked the route is already false. Clearing again would be a second navigation.
    expect(nextAction({ open: false, present: false, wasPresent: true })).toBe("nothing");
  });
});
