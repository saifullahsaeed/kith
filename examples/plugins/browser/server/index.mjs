/**
 * A browser Kith drives, as an MCP server.
 *
 * ## What this is
 *
 * A subprocess speaking JSON-RPC on stdin and stdout. That is the whole of the MCP contract, so
 * it is hand-rolled here rather than imported: a plugin's server has **no language constraint at
 * all**, and a sixty-line stdio loop makes that concrete in a way a dependency would hide.
 *
 * ## Why a browser belongs in a server rather than in the surface
 *
 * A surface is a sealed frame with `default-src 'none'` — no network, no cross-origin frames,
 * nothing to drive. So the browser lives out here, where a plugin's code is allowed to do real
 * work, and the surface is a *view* of it. That split is the plugin design in miniature: the
 * subprocess does, the frame shows, and neither can do the other's job.
 *
 * ## The boundary this runs inside
 *
 * Kith compiles the `reach` from `kith.plugin.json` into a `sandbox-exec` profile and starts this
 * with a constructed environment and its own working directory. Two consequences that are not
 * obvious and both cost a measurement to find:
 *
 * * **`HOME` is the plugin's own storage, not the person's.** Playwright derives its browser path
 *   from `HOME`, so it would look for Chromium somewhere that has never had one. This sets
 *   `PLAYWRIGHT_BROWSERS_PATH` from the script's *own* location instead — the plugin brings its
 *   own browsers, so nothing outside the plugin folder has to be declared or granted.
 * * **The working directory must be inside the allowed tree.** Node's `uv_cwd` returns EPERM
 *   otherwise and the process dies before any of this runs. Kith sets it; this comment exists so
 *   nobody "tidies" that away.
 *
 * ## How a screenshot reaches Kith and the surface
 *
 * Written to a file in the plugin's own storage. Kith's `read_file` routes image suffixes to
 * `read_image`, so returning the *path* means the model genuinely sees the page. The same path
 * goes into `_kith_state`, which the host writes into the plugin's store — and the surface reads
 * it from there and asks the renderer for the bytes, because a sealed frame cannot fetch.
 */

import { mkdir, readdir, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createInterface } from "node:readline";

/* Where Chromium is, decided before playwright is imported because it reads this at import.
 *
 * `HOME` is the plugin's own storage under confinement, not the person's — so playwright's
 * default would look for a browser somewhere that has never had one. Two ways out, and the
 * manifest takes the first:
 *
 * 1. **The shared cache**, declared as `reach.read` and its path supplied as a declared env
 *    value. The install screen then says, in as many words, that this plugin will be able to
 *    read your Playwright browser cache — which is the boundary doing its job rather than
 *    being worked around.
 * 2. **Its own copy**, downloaded into `browsers/` by `npm run postinstall`. Self-contained
 *    and needs no reach at all, at the cost of a few hundred megabytes copied on every install.
 */
process.env.PLAYWRIGHT_BROWSERS_PATH ||= new URL("../browsers", import.meta.url).pathname;

const { chromium } = await import("playwright");

/** Where screenshots go. `HOME` is the plugin's own storage under confinement — the one
 *  directory this process may write to. */
const SHOTS = join(process.env.HOME ?? ".", "shots");
/** How many to keep. A session's worth; the oldest goes because the newest is what is being
 *  looked at. */
const KEEP = 24;
/** The window Kith sees. Fixed rather than resizable: a screenshot whose dimensions change
 *  between calls is one he cannot compare to the last. */
const VIEWPORT = { width: 1280, height: 820 };

let browser = null;
let page = null;
/** What has happened, newest last. The surface renders this as a log so a person can see what
 *  he did without reading the transcript. */
const steps = [];

async function ensure() {
  if (page && !page.isClosed()) return page;
  if (!browser) {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (err) {
      // Actionable, because the alternative is a stack trace about a path nobody chose. This is
      // the one failure a person can actually fix, and they can only fix it if they are told how.
      throw new Error(
        `Chromium is not where this plugin was told to look (${process.env.PLAYWRIGHT_BROWSERS_PATH}). ` +
          `Run \`npx playwright install chromium\`, then set PLAYWRIGHT_BROWSERS_PATH on the ` +
          `Browser plugin in Settings to your cache — usually ~/Library/Caches/ms-playwright. ` +
          `Original: ${String(err.message ?? err).split("\n")[0]}`,
      );
    }
  }
  page = await browser.newPage({ viewport: VIEWPORT });
  // A real user agent. Plenty of sites serve a different page to headless Chromium, and a
  // plugin that quietly sees a different internet than the person does is worse than one that
  // fails.
  await page.setExtraHTTPHeaders({ "Accept-Language": "en-GB,en;q=0.9" });
  return page;
}

/** Take a shot, write it, and prune. Returns what the model and the surface both need. */
async function capture(note) {
  const here = await ensure();
  await mkdir(SHOTS, { recursive: true });
  const name = `shot-${Date.now().toString(36)}.png`;
  const path = join(SHOTS, name);
  await writeFile(path, await here.screenshot({ type: "png" }));

  const held = (await readdir(SHOTS)).filter((one) => one.endsWith(".png")).sort();
  for (const stale of held.slice(0, -KEEP)) await unlink(join(SHOTS, stale)).catch(() => {});

  steps.push({ at: Date.now(), note, url: here.url(), title: await here.title() });
  if (steps.length > 40) steps.splice(0, steps.length - 40);

  return {
    // The path, not the bytes. `read_file` routes image suffixes to `read_image`, so this is
    // what makes the model actually see the page rather than be told about it.
    screenshot: path,
    url: here.url(),
    title: await here.title(),
    viewport: VIEWPORT,
  };
}

/** What the host lifts off a result and writes into the plugin's store. */
function shared(shot) {
  return {
    shot: shot.screenshot,
    url: shot.url,
    title: shot.title,
    width: VIEWPORT.width,
    height: VIEWPORT.height,
    steps: steps.slice(-12),
  };
}

const TOOLS = [
  {
    name: "open",
    description:
      "Open a URL in the browser and return what is on screen. The reply's `screenshot` is a " +
      "file path — read_file it to actually look at the page.",
    inputSchema: {
      type: "object",
      properties: { url: { type: "string", description: "An absolute http(s) URL." } },
      required: ["url"],
    },
    async run({ url }) {
      const here = await ensure();
      const wanted = String(url ?? "").trim();
      if (!/^https?:\/\//i.test(wanted)) {
        return { error: "That is not an http(s) URL. Give a full address including the scheme." };
      }
      // `domcontentloaded` rather than `load`: a page whose analytics never finish loading is
      // still a page you can read, and waiting for `load` is how a browser tool times out on
      // sites that work perfectly well.
      await here.goto(wanted, { waitUntil: "domcontentloaded", timeout: 25_000 });
      return capture(`opened ${wanted}`);
    },
  },
  {
    name: "click",
    description:
      "Click something, by its visible text or a CSS selector. Prefer text — it survives a " +
      "redesign, and it is what a person would say.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "Visible text of the thing to click." },
        selector: { type: "string", description: "A CSS selector, when text will not do." },
      },
    },
    async run({ text, selector }) {
      const here = await ensure();
      const target = selector
        ? here.locator(String(selector)).first()
        : here.getByText(String(text ?? ""), { exact: false }).first();
      try {
        await target.click({ timeout: 8_000 });
      } catch (err) {
        // Named, and with what *is* there. "Click failed" costs him a round guessing; a list of
        // the visible buttons lets him fix it in the same breath.
        const options = await here
          .locator("a, button, [role=button], input[type=submit]")
          .evaluateAll((all) => all.map((one) => (one.innerText || one.value || "").trim()).filter(Boolean).slice(0, 20));
        return {
          error: `Could not click ${selector ?? text}: ${String(err.message ?? err).split("\n")[0]}`,
          clickable: options,
        };
      }
      await here.waitForTimeout(400);
      return capture(`clicked ${selector ?? text}`);
    },
  },
  {
    name: "type",
    description: "Type into a field, found by its label, placeholder or a CSS selector.",
    inputSchema: {
      type: "object",
      properties: {
        into: { type: "string", description: "Label, placeholder or CSS selector of the field." },
        text: { type: "string", description: "What to type." },
        enter: { type: "boolean", description: "Press Enter afterwards." },
      },
      required: ["into", "text"],
    },
    async run({ into, text, enter }) {
      const here = await ensure();
      const wanted = String(into ?? "");
      const field = wanted.match(/^[.#\[]|\s>/)
        ? here.locator(wanted).first()
        : here.getByLabel(wanted).or(here.getByPlaceholder(wanted)).first();
      try {
        await field.fill(String(text ?? ""), { timeout: 8_000 });
        if (enter) await field.press("Enter");
      } catch (err) {
        return { error: `Could not type into ${wanted}: ${String(err.message ?? err).split("\n")[0]}` };
      }
      await here.waitForTimeout(enter ? 900 : 200);
      return capture(`typed into ${wanted}`);
    },
  },
  {
    name: "read",
    description:
      "The page as text. Much cheaper than a screenshot and usually enough — reach for the " +
      "picture when layout matters or when the text does not explain what you are seeing.",
    inputSchema: {
      type: "object",
      properties: {
        selector: { type: "string", description: "Narrow to one part of the page." },
      },
    },
    async run({ selector }) {
      const here = await ensure();
      const scope = selector ? here.locator(String(selector)).first() : here.locator("body");
      const text = (await scope.innerText({ timeout: 8_000 }).catch(() => "")) || "";
      // Bounded, because a page's text is the one thing here that can be enormous and it lands
      // in the prompt whole.
      const CAP = 12_000;
      return {
        url: here.url(),
        title: await here.title(),
        text: text.length > CAP ? `${text.slice(0, CAP)}\n…[${text.length - CAP} more characters]` : text,
      };
    },
  },
  {
    name: "look",
    description: "Take a fresh screenshot without doing anything. Returns a path to read_file.",
    inputSchema: { type: "object", properties: {} },
    run: () => capture("looked"),
  },
  {
    name: "scroll",
    description: "Scroll the page. Positive is down, in viewport-heights.",
    inputSchema: {
      type: "object",
      properties: { by: { type: "number", description: "Viewport-heights, e.g. 1 or -0.5." } },
    },
    async run({ by }) {
      const here = await ensure();
      const amount = Number.isFinite(Number(by)) ? Number(by) : 1;
      await here.evaluate((n) => window.scrollBy(0, window.innerHeight * n), amount);
      await here.waitForTimeout(300);
      return capture(`scrolled ${amount}`);
    },
  },
  {
    name: "back",
    description: "Go back one page in history.",
    inputSchema: { type: "object", properties: {} },
    async run() {
      const here = await ensure();
      await here.goBack({ waitUntil: "domcontentloaded", timeout: 15_000 }).catch(() => {});
      return capture("went back");
    },
  },
];

// --------------------------------------------------------------------------- #
// The stdio loop. This is all MCP is.
// --------------------------------------------------------------------------- #

const say = (message) => process.stdout.write(`${JSON.stringify(message)}\n`);
const reply = (id, result) => say({ jsonrpc: "2.0", id, result });
const fail = (id, message) => say({ jsonrpc: "2.0", id, error: { code: -32000, message } });

const lines = createInterface({ input: process.stdin });
for await (const line of lines) {
  const raw = line.trim();
  if (!raw) continue;
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    continue; // A non-JSON line is not a request. Kith's client skips them too.
  }
  const { id, method, params } = message;

  if (method === "initialize") {
    reply(id, {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "kith-browser", version: "0.1.0" },
    });
    continue;
  }
  if (method === "notifications/initialized") continue;
  if (method === "tools/list") {
    reply(id, { tools: TOOLS.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })) });
    continue;
  }
  if (method === "tools/call") {
    const tool = TOOLS.find((one) => one.name === params?.name);
    if (!tool) {
      fail(id, `no tool called ${params?.name}`);
      continue;
    }
    try {
      const answer = await tool.run(params?.arguments ?? {});
      /* `_kith_state` is a key Kith reserves on a tool result: it takes it off before the model
       * sees it and writes the contents into this plugin's own store. That is how the surface
       * learns there is a new screenshot without this process needing a channel of its own —
       * and it is the only way, since an MCP server is only ever called, never pushing. */
      reply(id, {
        content: [{ type: "text", text: JSON.stringify(answer, null, 2) }],
        isError: !!answer.error,
        ...(answer.error ? {} : { _kith_state: shared(answer) }),
      });
    } catch (err) {
      fail(id, String(err?.message ?? err).split("\n")[0]);
    }
    continue;
  }
  if (id !== undefined) fail(id, `unsupported method ${method}`);
}

await browser?.close().catch(() => {});
