/**
 * Render web pages using the Chromium that Electron already ships.
 *
 * Kith needs a real browser for JS-heavy sites — the CMA circulars page serves a
 * challenge page to plain HTTP clients, so `fetch_url` gets nothing useful. That
 * used to mean Playwright inside his Docker sandbox: a second Chromium, ~150 MB,
 * its own install step, and its own update treadmill. This app *is* a browser, so
 * it does the rendering and hands back the text.
 *
 * Exposed as a tiny loopback HTTP service because the caller is the Python server,
 * a separate process. Three things make that safe rather than an open door:
 *
 *   1. It binds to 127.0.0.1 only.
 *   2. Every request must carry a token minted fresh at launch. Loopback is not a
 *      trust boundary on a shared machine — any local process can reach the port.
 *   3. Pages render in their own session partition and are blocked from reaching
 *      loopback or private addresses, so a hostile page cannot turn this into a
 *      proxy for Kith's own API or anything else on the network.
 */

import * as crypto from "node:crypto";
import * as http from "node:http";
import { URL } from "node:url";

import { cliStatus, installCli, removeCli } from "../cli/install";
import { APP_ICON, BACKEND_ORIGIN } from "../config";

import { getMainWindow, showMainWindow } from "../window/window";

import * as views from "../web/web-views";

import { BrowserWindow, Notification, dialog, session, shell } from "electron";

/** Matches the sandbox implementation this replaces (playwright's 30s goto). */
const LOAD_TIMEOUT_MS = 30_000;
/** After load, let late scripts paint. Approximates playwright's networkidle. */
const SETTLE_MS = 1_200;
/** Hard ceiling per request, so one bad page cannot pin a renderer forever. */
const TOTAL_TIMEOUT_MS = 45_000;
/** Same cap the sandbox applies, so tool output stays a predictable size. */
const MAX_TEXT_CHARS = 8_000;

/** Its own partition: browsing the open web must not touch the app's storage. */
const RENDER_PARTITION = "persist:kith-render";

const token = crypto.randomBytes(24).toString("hex");
let endpoint: string | null = null;

export interface RenderEndpoint {
  url: string;
  token: string;
}

/** Start the service. Returns where it listens, for handing to the backend. */
export async function startRenderService(): Promise<RenderEndpoint> {
  hardenRenderSession();

  const server = http.createServer((request, response) => {
    void handle(request, response);
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    // Port 0 = let the OS pick. Nothing should be guessing this address.
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  if (typeof address === "string" || address === null) {
    throw new Error("render service did not bind to a TCP port");
  }
  endpoint = `http://127.0.0.1:${address.port}`;
  console.log(`[kith] render service on ${endpoint}`);
  return { url: endpoint, token };
}

async function handle(request: http.IncomingMessage, response: http.ServerResponse): Promise<void> {
  const reply = (status: number, body: unknown): void => {
    const payload = JSON.stringify(body);
    response.writeHead(status, {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(payload),
    });
    response.end(payload);
  };

  const route = (request.url ?? "").split("?")[0] ?? "";
  const ROUTES = [
    "/render",
    "/notify",
    "/open-pane",
    "/pick-folder",
    "/browse",
    "/cli-status",
    "/install-cli",
  ];
  if (request.method !== "POST" || !ROUTES.includes(route)) {
    return reply(404, { error: "not found" });
  }
  // Constant-time compare: a token check that leaks timing is not a token check.
  const presented = String(request.headers["x-kith-token"] ?? "");
  const expected = Buffer.from(token);
  const actual = Buffer.from(presented);
  if (actual.length !== expected.length || !crypto.timingSafeEqual(actual, expected)) {
    return reply(401, { error: "bad or missing token" });
  }

  if (route === "/browse") return browse(request, reply);
  if (route === "/notify") return notify(request, reply);
  if (route === "/open-pane") return openPane(request, reply);
  if (route === "/pick-folder") return pickFolder(request, reply);
  if (route === "/cli-status") return reply(200, cliStatus());
  if (route === "/install-cli") return installOrRemoveCli(request, reply);

  let target: string;
  try {
    const body = await readBody(request);
    const parsed = JSON.parse(body) as { url?: unknown };
    if (typeof parsed.url !== "string") throw new Error("url must be a string");
    target = parsed.url.trim();
    const scheme = new URL(target).protocol;
    if (scheme !== "http:" && scheme !== "https:") throw new Error(`refusing scheme ${scheme}`);
  } catch (error) {
    return reply(400, { error: `bad request: ${(error as Error).message}` });
  }

  try {
    const text = await renderToText(target);
    reply(200, { text });
  } catch (error) {
    reply(502, { error: (error as Error).message });
  }
}

/**
 * Drive the browser tab, for the model.
 *
 * The person's own clicks on the chrome go over IPC — see `web/web-view-channel.ts` — because
 * the renderer is right there. The model's calls arrive here instead: they start in a Python
 * tool call, and this loopback service with its per-launch token is the channel that already
 * exists between the two processes. One browser, two drivers, two paths in because the drivers
 * are in different processes.
 *
 * This checks the shape of the request and not the right to make it: the `browser` tool is one
 * of Kith's own, and the token is what says the caller is the Python server.
 */
async function browse(
  request: http.IncomingMessage,
  reply: (status: number, body: unknown) => void,
): Promise<void> {
  let asked: { act?: string; owner?: string; [key: string]: unknown };
  try {
    asked = JSON.parse(await readBody(request, 64_000)) as typeof asked;
  } catch (error) {
    return reply(400, { error: `bad request: ${(error as Error).message}` });
  }

  const act = String(asked.act ?? "");
  // Which conversation's browser. The Python side names the conversation the turn belongs to,
  // so this only carries it.
  const at = { owner: String(asked.owner ?? "") };

  /* Every answer carries what went wrong out of sight since the last one — console errors,
   * failed requests — so he learns about a 500 behind a page that looks fine on the call that
   * caused it, without having to think to ask. Each one is said once. */
  const answer = (body: unknown) => {
    if (!body || typeof body !== "object") return reply(200, body);
    const fresh = views.problems(at);
    return reply(200, fresh.length ? { ...body, errors: fresh } : body);
  };

  try {
    switch (act) {
      case "open":
        return answer(
          await views.navigate(at, String(asked.url ?? ""), {
            newTab: asked.new_tab === true,
            private: asked.private === true,
          }),
        );
      case "wait":
        return answer(
          await views.waitFor(at, {
            text: typeof asked.text === "string" ? asked.text : undefined,
            selector: typeof asked.selector === "string" ? asked.selector : undefined,
            url: typeof asked.url === "string" ? asked.url : undefined,
            gone: asked.gone === true,
          }),
        );
      case "errors":
        return reply(200, { ...(views.status(at) ?? {}), errors: views.problems(at, true) });
      case "steps":
        return reply(200, { ...(views.status(at) ?? {}), ...views.steps(at) });
      case "tabs": {
        const now = views.status(at);
        return reply(200, now ?? { tab: "", tabs: [] });
      }
      case "switch":
        return answer(views.switchTab(at, String(asked.tab ?? "")));
      case "close":
        return answer(views.closeTab(at, String(asked.tab ?? "")));
      case "read":
        return answer(await views.read(at));
      case "click":
        return answer(
          await views.click(at, {
            text: typeof asked.text === "string" ? asked.text : undefined,
            selector: typeof asked.selector === "string" ? asked.selector : undefined,
            x: typeof asked.x === "number" ? asked.x : undefined,
            y: typeof asked.y === "number" ? asked.y : undefined,
          }),
        );
      case "type":
        return answer(await views.type(at, String(asked.text ?? ""), String(asked.into ?? "")));
      case "press":
        return answer(await views.press(at, String(asked.key ?? "Enter")));
      case "scroll":
        return answer(await views.scroll(at, Number(asked.by ?? 600)));
      case "back":
        return answer(await views.back(at));
      case "forward":
        return answer(await views.forward(at));
      case "reload":
        return answer(await views.reload(at));
      case "resize":
        return answer(
          await views.resize(at, {
            preset: typeof asked.preset === "string" ? asked.preset : undefined,
            width: typeof asked.width === "number" ? asked.width : undefined,
            height: typeof asked.height === "number" ? asked.height : undefined,
            ratio: typeof asked.ratio === "string" ? asked.ratio : undefined,
          }),
        );
      /* One act, two pictures. `look` with a `text` or a `selector` crops to that component;
       * `look` with neither is the whole page.
       *
       * They were briefly two commands and the manifest would not install: nine model commands
       * is the limit and that made ten, and the prompt-character budget refused it as well. The
       * limit turned out to be right about the design. "Look at the page" and "look at this one
       * thing" are one verb with an argument, not a choice to put in front of him every round —
       * and folding them cost nothing, because the manifest now spends its characters on what a
       * command *is* and leaves when-to-use-it to the skill, which is not charged for.
       *
       * `pick` — the person pointing at a component with the mouse — is deliberately not here at
       * all: see the note at the top of `web/web-view-channel.ts`.
       */
      case "look": {
        const named =
          (typeof asked.selector === "string" && asked.selector) ||
          (typeof asked.text === "string" && asked.text);
        if (!named) return answer(await views.shot(at));
        return answer(
          await views.snap(at, {
            selector: typeof asked.selector === "string" ? asked.selector : undefined,
            text: typeof asked.text === "string" ? asked.text : undefined,
            pad: typeof asked.pad === "number" ? asked.pad : undefined,
          }),
        );
      }
      case "status": {
        const now = views.status(at);
        return now ? reply(200, now) : reply(409, { error: "that browser pane is not open" });
      }
      default:
        return reply(400, { error: `no such act ${act}` });
    }
  } catch (error) {
    // 409 rather than 500: "its tab is not open" is a state the caller can act on, and the
    // model is told to ask the person to open it rather than to retry.
    reply(409, { error: (error as Error).message });
  }
}

function readBody(request: http.IncomingMessage, limit = 8_192): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => {
      body += chunk;
      // A render request is a URL. Anything large is a mistake or an attack. `/browse` raises
      // the limit, because typing a page's worth of text into a field is a legitimate call.
      if (body.length > limit) reject(new Error("body too large"));
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

/**
 * Load a URL in an offscreen window and return what a person would read.
 *
 * `document.body.innerText` deliberately, not `textContent`: innerText respects
 * layout, so it skips hidden nodes and preserves the line breaks that make the
 * result legible to a model. It is the same thing playwright's `inner_text` gives.
 */
async function renderToText(target: string): Promise<string> {
  const window = new BrowserWindow({
    show: false,
    webPreferences: {
      partition: RENDER_PARTITION,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      // Never throttled: an offscreen window is "hidden" by definition, and a
      // throttled one would stall mid-render and time out.
      backgroundThrottling: false,
    },
  });

  const stopwatch = setTimeout(() => {
    if (!window.isDestroyed()) window.destroy();
  }, TOTAL_TIMEOUT_MS);

  try {
    await load(window, target);
    await delay(SETTLE_MS);
    if (window.isDestroyed()) throw new Error("render window closed before extraction");
    const text = (await window.webContents.executeJavaScript(
      "document.body ? document.body.innerText : ''",
    )) as string;
    return text.length > MAX_TEXT_CHARS
      ? `${text.slice(0, MAX_TEXT_CHARS)}\n… [truncated, ${text.length} chars total]`
      : text;
  } finally {
    clearTimeout(stopwatch);
    if (!window.isDestroyed()) window.destroy();
  }
}

function load(window: BrowserWindow, target: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timed out loading ${target}`)), LOAD_TIMEOUT_MS);
    const settle = (error?: Error): void => {
      clearTimeout(timer);
      error ? reject(error) : resolve();
    };

    window.webContents.once("did-finish-load", () => settle());
    window.webContents.once("did-fail-load", (_event, code, description, failedUrl, isMainFrame) => {
      // Sub-resource failures are normal on real pages; only the main document
      // failing means we have nothing to read. -3 is ABORTED, which a redirect
      // or a client-side navigation raises legitimately.
      if (isMainFrame && code !== -3) settle(new Error(`${description} (${code}) loading ${failedUrl}`));
    });

    window.loadURL(target, { userAgent: "Mozilla/5.0 (Kith)" }).catch(settle);
  });
}

/**
 * Stop rendered pages reaching anything local.
 *
 * Without this, asking Kith to "read this page" would let that page's scripts talk
 * to Kith's own API on 127.0.0.1 — his memory, his tasks, his config — from inside
 * a trusted-looking local origin. It could also probe the user's LAN. The pages
 * being rendered are chosen by an agent following links it found on the internet,
 * so treating them as hostile is the only sane default.
 */
function hardenRenderSession(): void {
  const target = session.fromPartition(RENDER_PARTITION);

  target.webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    callback({ cancel: isLocalAddress(details.url) });
  });

  // Rendered pages get no permissions whatsoever — no geolocation, no camera,
  // no notifications. They are being read, not used.
  target.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  target.setPermissionCheckHandler(() => false);
}

export function isLocalAddress(rawUrl: string): boolean {
  let host: string;
  try {
    host = new URL(rawUrl).hostname.toLowerCase().replace(/\.$/, "");
  } catch {
    return true; // unparseable: refuse rather than guess
  }
  if (host === "localhost" || host.endsWith(".localhost") || host === "::1" || host === "[::1]") {
    return true;
  }
  // IPv6 arrives bracketed. `[::]` is every interface, fc00::/7 and fe80::/10 are the private and
  // link-local ranges, and `[::ffff:7f00:1]` is 127.0.0.1 written as IPv6 — which the IPv4 check
  // below never saw, so it reached Kith's own server. Mapped addresses are unwrapped and checked
  // as the IPv4 they are.
  if (host.startsWith("[")) {
    const v6 = host.slice(1, -1);
    if (v6 === "::") return true;
    const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(v6);
    if (!mapped) return /^(fc|fd|fe[89ab])/.test(v6);
    const high = parseInt(mapped[1] as string, 16);
    const low = parseInt(mapped[2] as string, 16);
    host = `${high >> 8}.${high & 255}.${low >> 8}.${low & 255}`;
  }
  // IPv4 private and loopback ranges.
  const octets = host.split(".");
  if (octets.length === 4 && octets.every((part) => /^\d{1,3}$/.test(part))) {
    const [a, b] = octets.map(Number) as [number, number, number, number];
    if (a === 127 || a === 0 || a === 10) return true;
    if (a === 169 && b === 254) return true; // link-local, incl. cloud metadata
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
  }
  return false;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Post a native notification, from the main process.
 *
 * It has to be the main process. The renderer's HTML5 Notification API looks like it
 * works — `Notification.permission` reads "granted" and nothing throws — and then macOS
 * silently drops it, because a web notification from a page has no app identity to
 * attribute to. Electron's own Notification does, which is what makes it appear in
 * Notification Center and what makes the first one act as the permission prompt.
 *
 * `isSupported()` is checked so a machine that cannot do this says so instead of the call
 * succeeding into nothing — "sent one" with nothing on screen is worse than an error.
 */
/**
 * Install or remove the `kith` command, on request from the settings page.
 *
 * Both directions through one route because both are the same privileged act with the same
 * dialog, and splitting them would mean two places that know how to escape a path into
 * AppleScript. `{remove: true}` is the only body it reads.
 *
 * It never fails the request over a cancelled password prompt. Dismissing that dialog is an
 * answer, and the page needs to tell the two apart: nothing happened because you said no,
 * versus nothing happened and here is why.
 */
async function installOrRemoveCli(
  request: http.IncomingMessage,
  reply: (status: number, body: unknown) => void,
): Promise<void> {
  let remove = false;
  try {
    const parsed = JSON.parse((await readBody(request)) || "{}") as { remove?: unknown };
    remove = parsed.remove === true;
  } catch (error) {
    return reply(400, { error: `bad request: ${(error as Error).message}` });
  }
  const result = remove ? await removeCli() : await installCli();
  reply(200, result);
}

async function notify(
  request: http.IncomingMessage,
  reply: (status: number, body: unknown) => void,
): Promise<void> {
  let title = "Kith";
  let body = "";
  let link = "";
  try {
    const parsed = JSON.parse(await readBody(request)) as {
      title?: unknown;
      body?: unknown;
      link?: unknown;
    };
    if (typeof parsed.title === "string" && parsed.title.trim()) title = parsed.title.trim();
    if (typeof parsed.body === "string") body = parsed.body;
    // In-app paths only. A notification is server-supplied and its body can contain anything
    // he wrote, so this is the one field that decides where a click goes — "/tasks/42" yes,
    // anything with a scheme or a host no.
    // A single leading slash, and nothing that could be read as a host. "//evil" passes a
    // naive "starts with /" check and `new URL("//evil", origin)` resolves it as
    // protocol-relative — a different site entirely, reached from a notification body.
    if (typeof parsed.link === "string" && /^\/(?!\/)[\w\-/]*$/.test(parsed.link)) {
      link = parsed.link;
    }
  } catch (error) {
    return reply(400, { error: `bad request: ${(error as Error).message}` });
  }
  if (!Notification.isSupported()) {
    return reply(503, { error: "this machine can't show notifications" });
  }
  const notification = new Notification({ title, body, icon: APP_ICON });
  notification.on("click", () => {
    showMainWindow();
    if (link) void openInApp(link);
  });
  notification.show();
  reply(200, { shown: true });
}

/**
 * Open one of macOS's own settings panes.
 *
 * From here rather than from the page, for two reasons. The renderer's window.open is
 * routed through the navigation hardening, which allows http, https and mailto only — so
 * `x-apple.systempreferences:` was silently dropped and the button did nothing. And the
 * pane is chosen from a fixed list by name: the alternative was widening the scheme
 * allowlist, and deliverables carry agent-authored URLs, so "any settings pane he names"
 * is not a capability worth handing over for the sake of one button.
 */
async function openPane(
  request: http.IncomingMessage,
  reply: (status: number, body: unknown) => void,
): Promise<void> {
  const PANES: Record<string, string> = {
    fullDisk: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    notifications: "x-apple.systempreferences:com.apple.preference.notifications",
    files: "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders",
  };
  let name = "";
  try {
    const parsed = JSON.parse(await readBody(request)) as { pane?: unknown };
    name = String(parsed.pane ?? "");
  } catch (error) {
    return reply(400, { error: `bad request: ${(error as Error).message}` });
  }
  const target = PANES[name];
  if (!target) return reply(400, { error: `unknown pane: ${name}` });
  await shell.openExternal(target);
  reply(200, { opened: name });
}

/**
 * Ask for a folder, with the real system dialog.
 *
 * There is no way to do this from the page. A file input gives you a `File` and deliberately
 * never a path, and typing one into a text box is how you end up with a typo pointed at a
 * folder that does not exist. The only component that can put up the platform's own folder
 * chooser is the main process, so the choice is made here and only the resulting path
 * crosses back.
 *
 * `createDirectory` is on because "put him in a new folder called Kith" is the common case
 * and macOS's chooser can do that itself.
 */
async function pickFolder(
  request: http.IncomingMessage,
  reply: (status: number, body: unknown) => void,
): Promise<void> {
  let title = "Choose a folder";
  let start = "";
  try {
    const parsed = JSON.parse(await readBody(request)) as { title?: unknown; start?: unknown };
    if (typeof parsed.title === "string" && parsed.title.trim()) title = parsed.title.trim();
    if (typeof parsed.start === "string") start = parsed.start;
  } catch {
    // Both are cosmetic; a malformed body still deserves a dialog.
  }
  const parent = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
  const options: Electron.OpenDialogOptions = {
    title,
    buttonLabel: "Choose",
    properties: ["openDirectory", "createDirectory"],
    ...(start ? { defaultPath: start } : {}),
  };
  const result = parent
    ? await dialog.showOpenDialog(parent, options)
    : await dialog.showOpenDialog(options);
  // Cancelling is a normal outcome, not a failure — say so plainly rather than with an error
  // the caller has to pattern-match on.
  reply(200, { path: result.canceled ? "" : (result.filePaths[0] ?? "") });
}

/**
 * Go to a path inside the running app.
 *
 * Asks the page to route there first, and only reloads if it cannot. The difference matters:
 * a reload throws away whatever was on screen, and a notification arriving mid-reply should
 * not cost you the reply. The app sets `window.__kithRouter` once its router is mounted and
 * listens for this event; if that flag is missing — the window is still loading, or showing
 * onboarding — a real navigation is the honest fallback.
 *
 * Deliberately not a preload bridge. The shell has no preload by design, and one existing
 * only so a notification can change the URL would be a large hole for a small feature.
 */
async function openInApp(path: string): Promise<void> {
  const window = getMainWindow();
  if (!window || window.isDestroyed()) return;
  try {
    const routed = (await window.webContents.executeJavaScript(
      `(() => {
         if (!window.__kithRouter) return false;
         window.dispatchEvent(new CustomEvent("kith:navigate", { detail: ${JSON.stringify(path)} }));
         return true;
       })()`,
    )) as boolean;
    if (routed) return;
  } catch {
    /* fall through to a real navigation */
  }
  await window.loadURL(new URL(path, BACKEND_ORIGIN).toString());
}
