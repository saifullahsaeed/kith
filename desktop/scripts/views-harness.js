/**
 * Runs the real `web-views.ts` — tabs, placement, the window-open handler — in Electron, for
 * `src/web/web-views.dom.test.ts`.
 *
 *     electron scripts/views-harness.js <job.json> <results.json>
 *
 * The job names a folder holding a CommonJS build of `src/` (the test transpiles it) and a set of
 * cases. The one thing swapped out is `window/window`, the app's main window: here it is a hidden
 * window of the harness's own, because the real one loads Kith's interface from the server.
 *
 * Pages come from a small HTTP server on a loopback port Kith does not use — the browser refuses
 * anything that is not http(s), and refuses Kith's own ports, which is the point of it.
 *
 * Each case is the body of an async function run here with:
 *
 *   views        the built `web/web-views` module
 *   page(path)   the address of one of the fixture pages below
 *   wait(ms)
 */

const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const http = require("node:http");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");

const [jobFile, resultsFile] = process.argv.slice(-2);
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "kith-views-harness-"));
app.setPath("userData", scratch);

const PAGES = {
  "/": `<!doctype html><title>One</title><body>
    <a href="/two" target="_blank">Two</a>
    <a href="/three">Three</a>
    </body>`,
  "/two": "<!doctype html><title>Two</title><body>the second page</body>",
  "/three": "<!doctype html><title>Three</title><body>the third page</body>",
};

app.whenReady().then(async () => {
  if (app.dock) app.dock.hide();
  const job = JSON.parse(fs.readFileSync(jobFile, "utf8"));

  const server = http.createServer((request, response) => {
    const body = PAGES[(request.url || "/").split("?")[0]];
    response.writeHead(body ? 200 : 404, { "Content-Type": "text/html; charset=utf-8" });
    response.end(body || "not here");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;

  const window = new BrowserWindow({ show: false, width: 1280, height: 800 });
  // Real paths on both sides: a module's resolved filename is the realpath, and macOS's temp
  // folder is a symlink (`/var` → `/private/var`), so a plain join never matches it.
  const stub = fs.realpathSync(path.join(job.dir, "window", "window.js"));
  const resolve = Module._resolveFilename;
  Module._resolveFilename = function (request, parent, ...rest) {
    const found = resolve.call(this, request, parent, ...rest);
    return found === stub ? path.join(scratch, "window-stub.js") : found;
  };
  fs.writeFileSync(path.join(scratch, "window-stub.js"), "exports.getMainWindow = () => global.__harnessWindow;");
  global.__harnessWindow = window;

  const views = require(path.join(job.dir, "web", "web-views.js"));
  const helpers = {
    views,
    page: (where) => `${origin}${where}`,
    wait: (ms) => new Promise((done) => setTimeout(done, ms)),
  };

  const results = {};
  for (const [name, body] of Object.entries(job.cases)) {
    try {
      const run = new Function("helpers", `return (async ({ views, page, wait }) => { ${body} })(helpers);`);
      results[name] = await run(helpers);
    } catch (error) {
      results[name] = { harnessError: String((error && error.message) || error) };
    }
    views.forgetAll();
  }
  fs.writeFileSync(resultsFile, JSON.stringify(results));
  server.close();
  window.destroy();
  fs.rmSync(scratch, { recursive: true, force: true });
  app.exit(0);
});
