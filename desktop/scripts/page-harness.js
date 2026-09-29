/**
 * Runs the browser pane's page scripts in real Chromium, for `src/web/page-scripts.dom.test.ts`.
 *
 * Arranged the way `web-views.ts` arranges a pane that nobody is looking at — a `WebContentsView`
 * inside a window, hidden — because that is the state the model drives it in most of the time,
 * and the state in which an input path that works on screen can quietly do nothing.
 *
 *     electron scripts/page-harness.js <job.json> <results.json>
 *
 * The job names a CommonJS build of `page-scripts.ts` and a set of cases. Each case is a page and
 * the body of an async function, run here in the main process with:
 *
 *   run(js, gesture = true)  executeJavaScript in the page, as `web-views.ts` does
 *   insertText(text)         the main-process text path `type` uses
 *   scripts                  the page scripts themselves
 *
 * What it returns is written to the results file under the case's name, or `{ harnessError }`.
 */

const { app, BrowserWindow, WebContentsView } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const [jobFile, resultsFile] = process.argv.slice(-2);
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "kith-page-harness-"));
app.setPath("userData", scratch);

app.whenReady().then(async () => {
  if (app.dock) app.dock.hide();
  const job = JSON.parse(fs.readFileSync(jobFile, "utf8"));
  const scripts = require(job.scripts);
  const window = new BrowserWindow({ show: false, width: 1280, height: 800 });
  const view = new WebContentsView({
    webPreferences: { partition: "kith-page-harness", sandbox: true, contextIsolation: true, backgroundThrottling: false },
  });
  window.contentView.addChildView(view);
  view.setBounds({ x: 0, y: 0, width: 1280, height: 800 });
  view.setVisible(false);

  const contents = view.webContents;
  const run = (js, gesture = true) => contents.executeJavaScript(js, gesture);
  const insertText = (text) => contents.insertText(text);

  const results = {};
  for (const [name, test] of Object.entries(job.cases)) {
    try {
      await contents.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(test.html)}`);
      const body = new Function("helpers", `return (async ({ run, insertText, scripts }) => { ${test.run} })(helpers);`);
      results[name] = await body({ run, insertText, scripts });
    } catch (error) {
      results[name] = { harnessError: String((error && error.message) || error) };
    }
  }
  fs.writeFileSync(resultsFile, JSON.stringify(results));
  window.destroy();
  fs.rmSync(scratch, { recursive: true, force: true });
  app.exit(0);
});
