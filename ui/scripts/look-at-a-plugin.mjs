/**
 * Look at a plugin surface, and measure it.
 *
 * **This exists because "it renders" is not something you can reason your way to.** Two bugs in
 * this subsystem were invisible to every other kind of check: a React surface whose graph laid
 * out at *zero* height — thirteen nodes present in the DOM, no space to draw them in — and a
 * board that folded its queue twice on a tab switch. Both looked fine in the store, fine over
 * HTTP, and fine in every unit test. The only way to know was to open it.
 *
 * jsdom cannot help: it has no layout engine, so every box measures zero and a height bug is
 * indistinguishable from a working one. This drives real Chromium.
 *
 *   node scripts/look-at-a-plugin.mjs flowpad
 *   node scripts/look-at-a-plugin.mjs sketchpad --state '{"shapes":[{"shape":"rect","x":40,"y":40,"label":"hi"}]}'
 *
 * It mounts against a running Kith the way the app does — an authenticated POST for a ticket,
 * then the frame's own unauthenticated GET — pushes a state in, measures every box, and writes
 * a screenshot. The mount is released afterwards, so nothing is left in the ticket table.
 *
 * It lives here rather than beside the examples because Node resolves imports from the file's
 * own location, and `playwright` is a dev dependency of this package. Run it from `ui/`.
 */

import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const [plugin, ...rest] = process.argv.slice(2);
if (!plugin) {
  console.error("usage: node look.mjs <plugin-id> [--view board] [--state '<json>'] [--out /tmp/x.png]");
  process.exit(1);
}

const flag = (name, fallback) => {
  const at = rest.indexOf(`--${name}`);
  return at >= 0 ? rest[at + 1] : fallback;
};

const view = flag("view", "board");
const out = flag("out", `/tmp/${plugin}-${view}.png`);
const state = JSON.parse(flag("state", "{}"));
const origin = flag("origin", "http://127.0.0.1:8611");

/** The token the page is handed in the document. Read from disk, the way the server writes it. */
const token = (
  await readFile(new URL("../../server/data/api.token", import.meta.url), "utf8").catch(() =>
    readFile(new URL("api.token", `file://${process.env.HOME}/.kith/`), "utf8"),
  )
).trim();

const headers = { "X-Kith-Token": token, "Content-Type": "application/json" };

/* The light palette, so a screenshot is comparable between runs. The renderer normally supplies
 * whichever half the person is in; a fixed one here means a diff is about the surface. */
const theme = {
  bg: "#faf9f7", line: "#e4e1dc", text: "#1c1a17", dim: "#6b6660",
  accent: "#b4703a", "accent-soft": "#f0e2d5", second: "#3a6b58",
  "second-soft": "#dceade", muted: "#f2f0ed",
};

const mounted = await fetch(`${origin}/api/plugins/${plugin}/surface/${view}/mount`, {
  method: "POST",
  headers,
  body: JSON.stringify({ conversation: "look", client: "look", theme }),
}).then((r) => r.json());

if (!mounted.ticket) {
  console.error("could not mount:", mounted.error ?? mounted);
  process.exit(1);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 600 } });
  await page.goto(`${origin}/api/plugins/frame/${mounted.ticket}`, { waitUntil: "load" });

  // Stand in for the host's push. The real one is driven by a query on the plugin's store.
  await page.evaluate((held) => {
    window.postMessage({ kith: 1, type: "render", rev: 1, state: held, host: {} }, "*");
  }, state);
  await page.waitForTimeout(900);

  const measured = await page.evaluate(() => {
    const box = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const { width, height } = el.getBoundingClientRect();
      return { w: Math.round(width), h: Math.round(height) };
    };
    return {
      // A zero height anywhere in this chain is the bug that started this file.
      body: box("body"),
      root: box("body > div") ?? box("body > svg"),
      // Whatever the surface's own outermost box is, if it named one.
      drawn: document.body.querySelectorAll("svg, canvas, .react-flow__node").length,
      text: document.body.innerText.split("\n").filter(Boolean).slice(0, 4),
    };
  });

  console.log(JSON.stringify({ plugin, view, ...measured }, null, 2));
  for (const [name, size] of Object.entries(measured)) {
    if (size && typeof size === "object" && "h" in size && size.h === 0) {
      console.error(`\n!! ${name} laid out at zero height — nothing in it can be seen.`);
    }
  }

  await page.screenshot({ path: out });
  console.log(`\nscreenshot: ${out}`);
} finally {
  await browser.close();
  await fetch(`${origin}/api/plugins/frame/${mounted.ticket}`, { method: "DELETE", headers });
}
