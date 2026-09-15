/**
 * Does each surface fill its pane, or cover the window?
 *
 * The generic version of `look-at-inbox.mjs`. `PaneBody` is a fragment, so a surface root is the
 * *direct child* of the pane body div — `[role="tabpanel"]` per layout-view.tsx:509 — which means
 * every surface can be measured the same way without knowing anything about it: navigate to the
 * route, then compare the pane body's box against its first element child's.
 *
 * The discriminator is `position` plus containment. A pane body stops where the app header starts,
 * so a surface filling its pane is contained in it and shorter than the window; the takeover-era
 * roots were `fixed inset-0`, which measures exactly the viewport and is contained by nothing.
 *
 * The ambient wash is measured separately and on purpose: `.kith-ambient` is itself
 * `position: fixed; inset: 0`, so a root can be converted and the wash inside it still escape.
 *
 *     node scripts/look-at-surfaces.mjs http://127.0.0.1:8610
 *     node scripts/look-at-surfaces.mjs http://127.0.0.1:8611
 */
import { chromium } from "playwright";

const url = process.argv[2] ?? "http://127.0.0.1:8610";
/**
 * A marker per surface, because `querySelector('[role="tabpanel"]')` would return the *first*
 * pane in document order — the 240px conversations pane — and then happily measure it three
 * times under three different names. The marker can live anywhere inside the surface, including
 * inside a `fixed` root (`position` does not change DOM ancestry), and the walk below climbs
 * from it to the pane's direct child, which is the surface root.
 */
const ROUTES = [
  ["alerts", "/messages", 'aside[aria-label="Alerts"]'],
  ["board", "/control-panel/overview", ".kith-ambient"],
  ["settings", "/settings/model", ".kith-ambient"],
  ["context", "/context", ".kith-ambient"],
];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror: " + e.message.split("\n")[0]));

const rows = [];
for (const [name, path, marker] of ROUTES) {
  await page.goto(url + path, { waitUntil: "networkidle" });
  // The async surfaces are lazy chunks behind Suspense; the settle window is what lets the
  // ScreenLoading fallback give way to the real element.
  await page.waitForTimeout(2000);

  const row = await page.evaluate((marker) => {
    const box = (el) => {
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) };
    };
    const contained = (inner, outer) => {
      if (!inner || !outer) return null;
      const p = inner.getBoundingClientRect();
      const q = outer.getBoundingClientRect();
      return (
        p.left >= q.left - 1 && p.right <= q.right + 1 && p.top >= q.top - 1 && p.bottom <= q.bottom + 1
      );
    };

    // `.kith-ambient` also exists at the app level (workspace.tsx:583), outside every pane — so
    // take the first match that is *inside* one, or the app-level wash gets measured instead of
    // the surface and `closest` silently returns null.
    const all = [...document.querySelectorAll(marker)];
    const found = all.find((el) => el.closest('[role="tabpanel"]')) ?? all[0] ?? null;
    const pane = found?.closest('[role="tabpanel"]') ?? null;
    // Climb to the pane's direct child: `PaneBody` is a fragment, so that child IS the surface
    // root, whatever it happens to be.
    let root = found;
    while (root && pane && root.parentElement !== pane) root = root.parentElement;
    const ambient = root?.querySelector(".kith-ambient") ?? null;

    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      pane: box(pane),
      root: box(root),
      rootPosition: root ? getComputedStyle(root).position : null,
      rootContainedInPane: contained(root, pane),
      rootFillsPane:
        root && pane
          ? Math.abs(root.getBoundingClientRect().width - pane.getBoundingClientRect().width) <= 1 &&
            Math.abs(root.getBoundingClientRect().height - pane.getBoundingClientRect().height) <= 1
          : null,
      ambientPosition: ambient ? getComputedStyle(ambient).position : "none",
      ambientContainedInPane: ambient ? contained(ambient, pane) : "none",
      foundMarker: !!found,
      panesOnScreen: document.querySelectorAll('[role="tabpanel"]').length,
    };
  }, marker);
  rows.push({ surface: name, path, ...row });
}

console.log(JSON.stringify({ url, rows, errors: errors.slice(0, 5) }, null, 2));
await browser.close();
