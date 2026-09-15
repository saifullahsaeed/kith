/**
 * Does the Alerts panel occupy its pane, or the window?
 *
 * The one thing the vitest suite cannot answer. jsdom lays nothing out — every element measures
 * 0x0 — so `inbox-panel.test.tsx` can only pin the class contract. This measures the geometry the
 * bug was actually about, in a real engine, against a real server.
 *
 *     node scripts/look-at-inbox.mjs http://127.0.0.1:8611   # built dist
 *     node scripts/look-at-inbox.mjs http://127.0.0.1:8610   # vite dev, live source
 *
 * The discriminator is height. A pane stops where the app header starts, so a panel filling its
 * pane is measurably shorter than the window; the old `fixed … h-dvh` was exactly the window's
 * height, and sat at x=0..1280 rather than inside the pane's box.
 */
import { chromium } from "playwright";

const url = process.argv[2] ?? "http://127.0.0.1:8611";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror: " + e.message.split("\n")[0]));

await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

const bell = page.getByRole("button", { name: /alerts|unread/i }).first();
const bellCount = await bell.count();
if (bellCount) {
  await bell.click();
  await page.waitForTimeout(1200);
}

const geo = await page.evaluate(() => {
  const box = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return {
      x: Math.round(b.x),
      y: Math.round(b.y),
      w: Math.round(b.width),
      h: Math.round(b.height),
      right: Math.round(b.right),
      bottom: Math.round(b.bottom),
    };
  };
  const panel = document.querySelector('aside[aria-label="Alerts"]');
  const pane = panel?.closest('[role="tabpanel"]');
  let contained = null;
  if (panel && pane) {
    const p = panel.getBoundingClientRect();
    const q = pane.getBoundingClientRect();
    contained =
      p.left >= q.left - 1 && p.right <= q.right + 1 && p.top >= q.top - 1 && p.bottom <= q.bottom + 1;
  }
  return {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    panel: box(panel),
    paneBody: box(pane),
    position: panel ? getComputedStyle(panel).position : null,
    containedInPane: contained,
    panesOnScreen: document.querySelectorAll('[role="tabpanel"]').length,
  };
});

console.log(
  JSON.stringify({ url, bellCount, panelFound: geo.panel !== null, ...geo, errors: errors.slice(0, 3) }, null, 2),
);
await browser.close();
