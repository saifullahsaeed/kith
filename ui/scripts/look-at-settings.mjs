/**
 * Open a settings pane in real Chromium, read it, and screenshot it.
 *
 * The sibling of `look-at-a-plugin.mjs`, and it exists for the same reason: a pane that
 * typechecks, builds and renders nothing is indistinguishable from a working one until somebody
 * looks. Reorganising settings moves panes between groups, deletes a mount, and changes a route
 * list that three files have to agree about — and every one of those failures is silent. This is
 * the check.
 *
 *   node scripts/look-at-settings.mjs permissions
 *   node scripts/look-at-settings.mjs plugins /tmp/plugins.png
 *
 * It loads the *built* UI from the running Kith server, so it sees what the desktop shell sees
 * rather than a dev-server variant. Run `npm run build` first, or you are looking at the last
 * build. Run it from `ui/` — playwright is a dev dependency of this package and Node resolves
 * imports from the file's own location.
 *
 * The page is normally handed its API token by the document it is served in; a plain browser
 * gets one too, but the request that fetches the document does not, so every request is stamped
 * on the way out.
 *
 * **The server only serves the UI when `KITH_UI_DIST` is set**, which the desktop shell does and
 * a bare `python app.py` does not. Without it every settings URL is an apiflask 404 and this
 * reports the pane as `{"message":"Not Found"}` — which looks like a routing bug in the app and
 * is nothing of the kind. Start the server the way the shell does, or set the variable.
 */

import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const [pane = "model", ...rest] = process.argv.slice(2);
const flag = (name, fallback) => {
  const at = rest.indexOf(`--${name}`);
  return at >= 0 ? rest[at + 1] : fallback;
};

const out = rest[0] && !rest[0].startsWith("--") ? rest[0] : flag("out", `/tmp/settings-${pane}.png`);
const origin = flag("origin", "http://127.0.0.1:8611");
const dataDir = flag("data", new URL("../../server/data/", import.meta.url).pathname);
const width = Number(flag("width", 1180));
const height = Number(flag("height", 860));

const token = (
  await readFile(new URL("api.token", `file://${dataDir}`), "utf8").catch(() =>
    readFile(new URL("api.token", `file://${process.env.HOME}/.kith/`), "utf8"),
  )
).trim();

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width, height } });

  /* Anything the page logs that means it is broken. A React error boundary catches a throw and
   * draws a small apology, which screenshots as a plausible-looking pane — so the console is
   * the only place some failures are visible at all. */
  const complaints = [];
  page.on("pageerror", (error) => complaints.push(`error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") complaints.push(`console: ${message.text().slice(0, 200)}`);
  });

  await page.route("**/*", (route) =>
    route.continue({ headers: { ...route.request().headers(), "X-Kith-Token": token } }),
  );
  await page.goto(`${origin}/settings/${pane}`, { waitUntil: "load" });
  // Long enough for the setup fetch and the panes that read their own data on mount.
  await page.waitForTimeout(3500);

  const seen = await page.evaluate(() => {
    const nav = document.querySelector("nav");
    return {
      // The sidebar as text, which is how a grouping change is checked.
      sidebar: nav ? nav.innerText.split("\n").filter(Boolean) : [],
      /* Whatever the pane itself is showing.
       *
       * Reached through the nav's own row rather than by a selector on the page: the settings
       * screen is a `fixed inset-0` overlay with no `<main>`, so falling back to `document.body`
       * read the entire workspace *behind* it — thirty lines of the person's conversation list,
       * every time, with the pane nowhere in the output. */
      pane: (nav?.parentElement?.lastElementChild ?? document.body).innerText
        .split("\n")
        .filter(Boolean)
        .slice(0, 30),
      // A pane laid out at zero height is the bug `look-at-a-plugin` was written for.
      height: Math.round((document.querySelector("nav")?.getBoundingClientRect().height ?? 0)),
    };
  });

  console.log("SIDEBAR:");
  console.log(seen.sidebar.map((line) => `  ${line}`).join("\n"));
  console.log("\nPANE:");
  console.log(seen.pane.map((line) => `  ${line}`).join("\n"));
  if (complaints.length) console.error(`\n!! ${complaints.length} complaint(s):\n${complaints.join("\n")}`);
  if (!seen.height) console.error("\n!! the sidebar laid out at zero height");

  await page.screenshot({ path: out, fullPage: true });
  console.log(`\nshot: ${out}`);
} finally {
  await browser.close();
}
