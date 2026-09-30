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
 *   window       the stand-in main window, hidden until a case shows it
 *   nativeImage  Electron's, for looking inside a picture a case was given
 */

const { app, BrowserWindow, nativeImage } = require("electron");
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
  // Something wrong out of sight: a console error, an uncaught exception, a 500 and a 404.
  "/broken": `<!doctype html><title>Broken</title><body>looks fine
    <script>
      console.error("the cart did not load");
      setTimeout(() => { null.total; }, 10);
      fetch("/api/orders", { method: "POST" });
      fetch("/missing.json");
    </script></body>`,
  // An answer that arrives after the page has settled.
  "/slow": `<!doctype html><title>Slow</title><body><p id="out">working…</p>
    <script>setTimeout(() => { document.getElementById("out").textContent = "Order placed"; }, 2500);</script></body>`,
  // A cookie, and a page that says which cookies it can see.
  "/login": `<!doctype html><title>Login</title><body><script>document.cookie = "session=abc; path=/";</script>signed in</body>`,
  "/whoami": `<!doctype html><title>Who</title><body><p id="c"></p><script>document.getElementById("c").textContent = document.cookie || "nobody";</script></body>`,
  // A form, to record a flow from.
  "/form": `<!doctype html><title>Form</title><body>
    <label for="email">Work email</label><input id="email">
    <button onclick="document.getElementById('done').hidden = false">Send</button>
    <p id="done" hidden>Thanks, we got it</p></body>`,
  // A page drawn in one colour, to tell a real picture from a blank one.
  "/red": '<!doctype html><title>Red</title><body style="margin:0;background:#ff0000;height:100vh"></body>',
};
const STATUS = { "/api/orders": 500 };

app.whenReady().then(async () => {
  if (app.dock) app.dock.hide();
  const job = JSON.parse(fs.readFileSync(jobFile, "utf8"));

  const server = http.createServer((request, response) => {
    const where = (request.url || "/").split("?")[0];
    const body = PAGES[where];
    response.writeHead(STATUS[where] ?? (body ? 200 : 404), { "Content-Type": "text/html; charset=utf-8" });
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
    window,
    nativeImage,
  };

  const results = {};
  for (const [name, body] of Object.entries(job.cases)) {
    try {
      const run = new Function("helpers", `return (async ({ views, page, wait, window, nativeImage }) => { ${body} })(helpers);`);
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
