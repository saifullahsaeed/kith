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

import { BACKEND_ORIGIN } from "./config";

import { getMainWindow, showMainWindow } from "./window";

import { BrowserWindow, Notification, session, shell } from "electron";

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

export function renderEndpoint(): RenderEndpoint | null {
  return endpoint ? { url: endpoint, token } : null;
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
  const ROUTES = ["/render", "/notify", "/open-pane"];
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

  if (route === "/notify") return notify(request, reply);
  if (route === "/open-pane") return openPane(request, reply);

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

function readBody(request: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => {
      body += chunk;
      // A render request is a URL. Anything large is a mistake or an attack.
      if (body.length > 8_192) reject(new Error("body too large"));
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

function isLocalAddress(rawUrl: string): boolean {
  let host: string;
  try {
    host = new URL(rawUrl).hostname.toLowerCase();
  } catch {
    return true; // unparseable: refuse rather than guess
  }
  if (host === "localhost" || host.endsWith(".localhost") || host === "::1" || host === "[::1]") {
    return true;
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
  const notification = new Notification({ title, body });
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
