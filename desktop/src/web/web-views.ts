/**
 * A real browser, in one of Kith's own panes.
 *
 * ## Why this exists at all
 *
 * A plugin surface is a *sealed* document: `sandbox="allow-scripts"` over a CSP of
 * `default-src 'none'`, with no network of any kind. That seal is what makes it safe to run a
 * stranger's code inside Kith's window, and it is not negotiable. It also means nothing drawn
 * in a plugin's tab can ever load a web page — so the browser plugin ran headless Chromium in
 * its own subprocess and posted PNGs into the tab. Which works, and is a picture of a browser.
 * You cannot scroll it, type into it, or log in with it.
 *
 * So the browser is not drawn by the plugin. It is provided by the shell, as a different *kind*
 * of surface, and the plugin declares that it wants one. `render-service.ts` already made the
 * argument: this app is a browser, so it should not be shipping a second one. The same is true
 * of a page you can use.
 *
 * ## What a WebContentsView is, and what it costs
 *
 * Electron 43 has no `BrowserView` worth using and `webviewTag` is off everywhere on purpose,
 * so this is a `WebContentsView`: real web contents composited by the main process *over* the
 * window rather than living in the page's DOM. Two consequences follow from that and both shape
 * this file:
 *
 * 1. **It does not participate in layout.** Nothing in CSS can position it. The renderer
 *    measures where the pane is and tells us, and we set the bounds to match — see `place`.
 * 2. **It paints on top of everything.** A menu, a dialog, a drag preview drawn by the page
 *    would appear *under* it. So the renderer also tells us to get out of the way, and
 *    `hide` is as load-bearing as `place`.
 *
 * ## Sessions, and the reason no permission prompt appears anywhere in here
 *
 * Each plugin's views share one partition of that plugin's own: `persist:kith-plugin-<id>`.
 * Never the person's everyday browser and never Kith's own session. That boundary is the whole
 * answer to "should the model need permission to navigate this?" — it starts logged out, and
 * anything logged into it is something the person logged in themselves, in this view, on
 * purpose. It holds no identity they did not put there, which makes navigating it an ordinary
 * act rather than a privileged one. A view onto their real Chrome profile would be a completely
 * different proposition and is not what this is.
 *
 * What *is* blocked is Kith's own origin: a page that could reach the backend would be a page
 * reaching Kith's API from inside Kith. Other loopback addresses are deliberately allowed —
 * the first thing anyone does with this is open the app they are building on `localhost`.
 */

import { WebContentsView, session } from "electron";

import { BACKEND_ORIGIN } from "../config";

import { getMainWindow } from "../window/window";

/** How long a navigation may take before we stop waiting and report what we have. */
const LOAD_TIMEOUT_MS = 30_000;
/** After `did-stop-loading`, a moment for late scripts to paint before a read or a shot. */
const SETTLE_MS = 450;
/** How long a click is given to *start* navigating before its answer is composed. Generous,
 *  because input dispatched inside the page reaches it a beat later than a widget event would. */
const NAVIGATION_GRACE_MS = 1_200;
/** The cap `render-service` uses for extracted text, for the same reason: tool output has to be
 *  a predictable size, and a model reading a page does not need the eight-thousand-and-first
 *  character to know what the page says. */
const MAX_TEXT_CHARS = 8_000;
/** A bound on how many live browsers the app will hold. Each is a renderer process. */
const MAX_VIEWS = 4;
/**
 * The size a view has before its pane has ever told it one.
 *
 * **Not zero, and that matters more than it looks.** A view is created by whichever comes first
 * — the pane opening, or the model calling `open` — and at zero size Chromium lays the page out
 * in a zero-pixel viewport. Everything then has an empty bounding rect, so `click` finds nothing
 * clickable and reports an empty list of options, and `capturePage` returns an empty image. Both
 * of those look exactly like a broken page rather than a browser with no window yet.
 *
 * So a view starts at a normal desktop size and hidden. Reading and clicking work whether or not
 * anybody is watching, and `place` moves it to the pane when the tab opens.
 */
const DEFAULT_BOUNDS = { x: 0, y: 0, width: 1280, height: 800 };
/**
 * Where a view sits when no pane is showing it: in the window, sized, and hidden.
 *
 * Each half of that was arrived at by getting it wrong first.
 *
 * **In the window**, because a view that is not a child of anything has no compositor: a capture
 * returns an empty image and there is nothing to lay a page out in, so `click` found no element
 * with a non-zero rectangle and reported that nothing on the page was clickable.
 *
 * **Sized**, because at zero size the page lays out in a zero-pixel viewport, with the same
 * result.
 *
 * **Hidden rather than parked off the edge.** Parking it at a negative x looked like the better
 * trick — clipped away but still composited — and `capturePage` returns an empty image for it,
 * because there is no on-screen surface to composite. Hidden but in-window still has one.
 */
/**
 * The keys a page may be sent, and what each one actually is.
 *
 * A closed table rather than a passthrough, because CDP wants the key's real identity — a
 * `key`, a `code` and a Windows virtual key code — and a page that checks `event.key` against a
 * name Electron guessed is a form that will not submit. These are the keys that *mean*
 * something; text goes through `insertText`.
 */
const KEYS: Record<string, { key: string; code: string; windowsVirtualKeyCode: number; text?: string }> = {
  Enter: { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" },
  Tab: { key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 },
  Escape: { key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 },
  Backspace: { key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 },
  ArrowDown: { key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 },
  ArrowUp: { key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 },
};

/** What the renderer measured, in device-independent pixels relative to the window's content. */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ViewStatus {
  plugin: string;
  view: string;
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  /** Set when the last navigation failed, so the pane can say so rather than sitting blank. */
  failure: string;
}

interface Held {
  plugin: string;
  view: string;
  contents: WebContentsView;
  attached: boolean;
  failure: string;
  /** Where the renderer last put it, so a re-show does not need a fresh measurement. */
  rect: Rect | null;
}

const held = new Map<string, Held>();
const listeners = new Set<(status: ViewStatus) => void>();

const keyOf = (plugin: string, view: string): string => `${plugin}/${view}`;

/** Subscribe to status changes. The shell forwards these to the renderer, which draws the
 *  chrome — the address bar, the back button's enabled state, the spinner. */
export function onViewStatus(listener: (status: ViewStatus) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Put a plugin's view at a rectangle, creating it if this is the first time.
 *
 * Called on mount, on every pane resize and on every scroll of whatever contains the pane. It
 * is therefore the hot path and does as little as possible: an existing view whose bounds have
 * not moved is left entirely alone, because `setBounds` on a live web view forces a relayout of
 * the page inside it and doing that sixty times a second while somebody drags a splitter is
 * visible as jank in the page, not in the frame.
 */
export function place(plugin: string, view: string, rect: Rect, home = ""): ViewStatus {
  const one = held.get(keyOf(plugin, view)) ?? create(plugin, view, home);
  const rounded = round(rect);
  if (!one.attached) {
    const window = getMainWindow();
    if (!window || window.isDestroyed()) return statusOf(one);
    window.contentView.addChildView(one.contents);
    one.attached = true;
  }
  if (rounded.width < 1 || rounded.height < 1) {
    hide(plugin, view);
    return statusOf(one);
  }
  if (!same(one.rect, rounded)) {
    one.contents.setBounds(rounded);
    one.rect = rounded;
  }
  showNow(one);
  return statusOf(one);
}

/**
 * Take a view off the screen without destroying it.
 *
 * The distinction matters. A tab you switched away from must stop painting over the tab you
 * switched to — that is what this is for — but the page must still be *there* when you come
 * back, with its scroll position, its form contents and its login intact. Destroying and
 * recreating would be a reload, and a reload is how you lose a half-filled form.
 */
export function hide(plugin: string, view: string): void {
  const one = held.get(keyOf(plugin, view));
  if (!one) return;
  // Back to the default rectangle as well as hidden, so a screenshot still works while nobody
  // is looking — see `DEFAULT_BOUNDS`.
  one.contents.setBounds(DEFAULT_BOUNDS);
  hideNow(one);
  one.rect = null;
}

/** Destroy a view and everything in it. Only for a plugin being switched off or removed. */
export function forget(plugin: string, view = ""): void {
  for (const [key, one] of [...held.entries()]) {
    if (one.plugin !== plugin || (view && one.view !== view)) continue;
    detach(one);
    if (!one.contents.webContents.isDestroyed()) one.contents.webContents.close();
    held.delete(key);
  }
}

/** Every live view, for the shell to tidy on quit. */
export function forgetAll(): void {
  for (const one of held.values()) {
    detach(one);
    if (!one.contents.webContents.isDestroyed()) one.contents.webContents.close();
  }
  held.clear();
}

export function status(plugin: string, view: string): ViewStatus | null {
  const one = held.get(keyOf(plugin, view));
  return one ? statusOf(one) : null;
}

// --------------------------------------------------------------------------- //
// Driving one
// --------------------------------------------------------------------------- //

/**
 * Go somewhere, and wait until it has arrived.
 *
 * Waiting is the point. The person's own click on the address bar wants the chrome to show a
 * spinner and then a title; the *model's* call wants an answer it can act on, and "I have begun
 * navigating" is not one. So both go through here and both get a settled page.
 *
 * A failed navigation resolves rather than throwing. `ERR_NAME_NOT_RESOLVED` is a fact about
 * the page, not an error in the caller — the model should be told the host does not exist, in a
 * sentence, and carry on.
 */
export async function navigate(
  plugin: string,
  view: string,
  url: string,
  home = "",
): Promise<ViewStatus> {
  const one = held.get(keyOf(plugin, view)) ?? create(plugin, view, home);
  const target = normalise(url);
  if (!target) return { ...statusOf(one), failure: `${url} is not an address I can open.` };
  one.failure = "";
  announce(one);
  try {
    await load(one, target);
  } catch (error) {
    one.failure = (error as Error).message;
  }
  await settle(one);
  announce(one);
  return statusOf(one);
}

export async function back(plugin: string, view: string): Promise<ViewStatus> {
  return step(plugin, view, (contents) => {
    // `navigationHistory` is the API in this Electron; `webContents.goBack` still exists but is
    // the deprecated spelling and says nothing about whether it could.
    if (contents.navigationHistory.canGoBack()) contents.navigationHistory.goBack();
  });
}

export async function forward(plugin: string, view: string): Promise<ViewStatus> {
  return step(plugin, view, (contents) => {
    if (contents.navigationHistory.canGoForward()) contents.navigationHistory.goForward();
  });
}

export async function reload(plugin: string, view: string): Promise<ViewStatus> {
  return step(plugin, view, (contents) => contents.reload());
}

/** What a person would read on the page. `innerText`, for the reason `render-service` gives:
 *  it respects layout, so it skips hidden nodes and keeps the line breaks that make the result
 *  legible instead of one run-on paragraph. */
export async function read(plugin: string, view: string): Promise<{ text: string } & ViewStatus> {
  const one = required(plugin, view);
  await settle(one);
  const text = (await one.contents.webContents.executeJavaScript(
    "document.body ? document.body.innerText : ''",
    true,
  )) as string;
  const trimmed =
    text.length > MAX_TEXT_CHARS
      ? `${text.slice(0, MAX_TEXT_CHARS)}\n… [truncated, ${text.length} chars total]`
      : text;
  return { text: trimmed, ...statusOf(one) };
}

/**
 * Click something, by what it says.
 *
 * Two halves, and the second is the one that matters. First the page is asked where the thing
 * is — by text, by selector, or taken as given — and then the click is delivered as **real
 * input** through `sendInputEvent` rather than by calling `element.click()` in the page.
 *
 * The difference is trust. A synthetic `.click()` is `isTrusted: false`, and a great many sites
 * ignore those: anything gated on a user gesture, most consent walls, and every framework that
 * checks. `sendInputEvent` goes in at the same place the person's mouse does, so the page cannot
 * tell the model's click from theirs — which is the entire premise of one page with two drivers.
 */
export async function click(
  plugin: string,
  view: string,
  where: {
    text?: string | undefined;
    selector?: string | undefined;
    x?: number | undefined;
    y?: number | undefined;
  },
): Promise<{ clicked: string; options: string[] } & ViewStatus> {
  const one = required(plugin, view);
  let point: { x: number; y: number } | null = null;
  let described = "";
  let options: string[] = [];

  if (typeof where.x === "number" && typeof where.y === "number") {
    point = { x: where.x, y: where.y };
    described = `${where.x},${where.y}`;
  } else {
    const found = (await one.contents.webContents.executeJavaScript(
      locate(where.selector ?? "", where.text ?? ""),
      true,
    )) as { x: number; y: number; label: string } | { options: string[] };
    if ("options" in found) {
      options = found.options;
      return { clicked: "", options, ...statusOf(one) };
    }
    point = { x: found.x, y: found.y };
    described = found.label;
  }

  // `buttons` as well as `button`: the first says which button this event is about and the
  // second is the bitmask of what is currently held. Chromium routes on both, and a press with
  // an empty mask is one a good deal of page code declines to treat as a click.
  /* Dispatched inside the page rather than through the widget.
   *
   * `Input.dispatchMouseEvent` and `sendInputEvent` both go to the render widget, and a widget
   * that is hidden accepts them and drops them — no error, no effect. The click reported the
   * element it had found and the page never moved, which is the single most confusing way for
   * this to fail. And hidden is the normal state: a browser has to be driveable in a turn where
   * nobody has the tab open.
   *
   * The cost is honest and worth naming: these events carry `isTrusted: false`. Links, buttons
   * and forms do not care — React and every other framework listen for bubbling events, which
   * these are — but something gated specifically on a user gesture may refuse. That is the
   * trade for a browser that works whether or not anybody is watching it.
   */
  await one.contents.webContents.executeJavaScript(dispatchClick(point.x, point.y), true);
  await settleAfterInput(one);
  announce(one);
  return { clicked: described, options, ...statusOf(one) };
}

/**
 * Type into whatever is focused, or into a field named first.
 *
 * `insertText` rather than a stream of key events: it is what a paste does, so it survives
 * fields that reformat as you go (a card number, a date) and it does not depend on the layout
 * of anybody's keyboard. Keys that *mean* something — Enter, Tab — go through `press`, because
 * those are not text.
 */
export async function type(
  plugin: string,
  view: string,
  text: string,
  into = "",
): Promise<ViewStatus> {
  const one = required(plugin, view);
  if (into) await click(plugin, view, { text: into, selector: into.startsWith("#") ? into : "" });
  // Set, then announced. Every form library listens for `input`; a value assigned without it is
  // one React overwrites on its next render, and one validation never sees.
  await one.contents.webContents.executeJavaScript(dispatchType(text), true);
  await settle(one);
  return statusOf(one);
}

/** A key that is an instruction rather than a character. */
export async function press(plugin: string, view: string, key: string): Promise<ViewStatus> {
  const one = required(plugin, view);
  const named = KEYS[key];
  if (!named) throw new Error(`${key} is not a key this can press.`);
  await one.contents.webContents.executeJavaScript(dispatchKey(named), true);
  await settleAfterInput(one);
  announce(one);
  return statusOf(one);
}

export async function scroll(plugin: string, view: string, by: number): Promise<ViewStatus> {
  const one = required(plugin, view);
  await one.contents.webContents.executeJavaScript(
    `window.scrollBy({ top: ${Number(by) || 0}, behavior: "instant" }); 0`,
    true,
  );
  await settle(one);
  return statusOf(one);
}

/**
 * A PNG of the page as it stands.
 *
 * Still worth having with a live view in the pane, for the case the view cannot cover: a turn
 * running when no window is open, or a model that wants to *look* at something the person is
 * not currently watching. Returned as base64 because the caller is the Python server over
 * loopback JSON, and it writes the bytes to a file the model can read.
 */
export async function shot(plugin: string, view: string): Promise<{ png: string } & ViewStatus> {
  const one = required(plugin, view);
  await settle(one);
  const contents = one.contents.webContents;

  /* A `look` exists so a turn can see a page nobody is watching, which is what makes the view's
   * resting bounds load-bearing rather than cosmetic — see `DEFAULT_BOUNDS`. */
  const box = one.contents.getBounds();
  /* `capturePage`, not `Page.captureScreenshot`.
   *
   * The debugger was the first attempt and it hangs: a screenshot command waits for a new
   * compositor frame, and a parked view produces one only when something changes. `capturePage`
   * asks the compositor for the current surface instead and answers immediately.
   *
   * Resized down to the page's own pixels afterwards. A capture comes back at the display's
   * device ratio, so a 1280x800 page arrived 2560x1600 — four times the pixels, for an image
   * whose entire purpose is to be read by a model at a few thousand tokens.
   */
  let png = "";
  try {
    const image = await one.contents.webContents.capturePage();
    // Resized to the page's own pixels. A capture comes back at the display's device ratio, so a
    // 1280x800 page arrived 2560x1600 — four times the pixels, for an image whose entire purpose
    // is to be read by a model at a few thousand tokens.
    const sized = image.getSize().width > box.width ? image.resize({ width: box.width }) : image;
    png = sized.toPNG().toString("base64");
  } catch (error) {
    /* **A screenshot is the one act here that genuinely needs the tab open.**
     *
     * Everything else works on a view nobody is looking at: pages load, text reads, and input
     * dispatched inside the page lands. A capture cannot, and Chromium is blunt about it —
     * `UnknownVizError` from the compositor, because there is no on-screen surface to copy.
     * Both alternatives were tried and measured: `Page.captureScreenshot` over the debugger
     * waits for a frame a hidden view never produces and times out, and parking the view off
     * the window's edge leaves nothing to composite either.
     *
     * So it is said, in a sentence carrying the alternative. A blank PNG written to a file is
     * far worse than a refusal: the model reads it, spends thousands of tokens on nothing, and
     * concludes the page is empty.
     */
    throw new Error(
      "A screenshot needs the Browser tab open — ask them to open it, or use `read` for the " +
        `page's text, which works either way. (${(error as Error).message})`,
    );
  }

  return { png, ...statusOf(one) };
}

// --------------------------------------------------------------------------- //
// The plumbing
// --------------------------------------------------------------------------- //

function create(plugin: string, view: string, home: string): Held {
  if (held.size >= MAX_VIEWS) {
    // Oldest first. A bound that refuses the new view would leave a person staring at a pane
    // that will not fill, with nothing saying why.
    const [oldest] = held.keys();
    if (oldest) {
      const going = held.get(oldest);
      if (going) forget(going.plugin, going.view);
    }
  }
  harden(plugin);
  const contents = new WebContentsView({
    webPreferences: {
      // The plugin's own session, never the person's and never Kith's. See the module comment.
      partition: partitionFor(plugin),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: false,
      webviewTag: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      // A view on a tab the person is not looking at is hidden, and Chromium throttles hidden
      // contents. That is normally right and is wrong here: a page mid-login or mid-upload has
      // to keep going while they read something else.
      backgroundThrottling: false,
      spellcheck: true,
    },
  });
  const one: Held = { plugin, view, contents, attached: false, failure: "", rect: null };
  /* In the window from the start, sized, and hidden.
   *
   * **A view that is not a child of anything has no compositor**, and without one Chromium has
   * nowhere to raster: `Page.captureScreenshot` returns empty data and dispatched input reaches
   * no widget. Both fail silently, which is how this took three attempts to find — a click that
   * located its target and did nothing, and a screenshot that wrote a blank file.
   *
   * So attach immediately rather than on the first `place`. It is invisible until a pane asks
   * for it, and by then the page has already loaded and can be read and clicked. Which is the
   * behaviour a `look` needs anyway: a turn with no window open still gets a real picture.
   */
  const window = getMainWindow();
  if (window && !window.isDestroyed()) {
    window.contentView.addChildView(contents);
    one.attached = true;
  }
  contents.setBounds(DEFAULT_BOUNDS);
  hideNow(one);
  held.set(keyOf(plugin, view), one);
  watch(one);
  if (home) void navigate(plugin, view, home);
  return one;
}

/**
 * Everything a page in here may not do, set once per plugin partition.
 *
 * The permission handlers are the interesting ones. A page in a pane the person is looking at
 * could plausibly ask for a camera, and the answer is still no: the plugin declared a browser,
 * not a device. Saying no in the handler rather than by prompting means there is no dialog for
 * a page to badger somebody into accepting.
 */
function harden(plugin: string): void {
  const target = session.fromPartition(partitionFor(plugin));
  if (hardened.has(plugin)) return;
  hardened.add(plugin);

  const backend = originOf(BACKEND_ORIGIN);
  target.webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    // **Kith's own origin only.** A page that can reach the backend is a page reaching Kith's
    // API from inside Kith, which is the one address that must be unreachable from here.
    //
    // Other loopback addresses are allowed on purpose, and this is a deliberate departure from
    // `render-service`'s blanket refusal. That one renders a URL a *model* chose, where the
    // whole risk is being talked into fetching something local; this is a browser a *person*
    // is driving, and the first thing anybody does with it is open the app they are building
    // on `localhost:5173`. Refusing that would be refusing the use case.
    callback({ cancel: backend !== "" && originOf(details.url) === backend });
  });

  target.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  target.setPermissionCheckHandler(() => false);
  // Downloads go nowhere. Nothing in Kith serves files out of a plugin's storage, and a save
  // dialog opening over the app from a page in a tab is not something anybody asked for.
  target.on("will-download", (event) => event.preventDefault());
}

const hardened = new Set<string>();

const partitionFor = (plugin: string): string => `persist:kith-plugin-${plugin}`;

function originOf(raw: string): string {
  try {
    return new URL(raw).origin.toLowerCase();
  } catch {
    return "";
  }
}

/** Report a status change whenever the page does something the chrome draws. */
function watch(one: Held): void {
  const contents = one.contents.webContents;
  // Written out rather than looped: to TypeScript each event name is a separate overload, so a
  // loop over them is a call whose argument is a union no single overload accepts.
  const changed = () => announce(one);
  contents.on("did-start-loading", changed);
  contents.on("did-stop-loading", changed);
  contents.on("did-navigate", changed);
  contents.on("did-navigate-in-page", changed);
  contents.on("page-title-updated", changed);
  contents.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
    // A subframe failing is the web being the web. Only the main frame is worth saying.
    if (!isMainFrame) return;
    one.failure = `${description || `error ${code}`}${url ? ` (${url})` : ""}`;
    announce(one);
  });
  contents.on("did-navigate", () => {
    one.failure = "";
  });
  // Every new window becomes a navigation in this one. There is a single pane and no tab strip,
  // so a popup would otherwise be a window with no chrome that the person cannot get back from.
  contents.setWindowOpenHandler(({ url }) => {
    void navigate(one.plugin, one.view, url);
    return { action: "deny" };
  });
}

function announce(one: Held): void {
  const snapshot = statusOf(one);
  for (const listener of listeners) listener(snapshot);
}

function statusOf(one: Held): ViewStatus {
  const contents = one.contents.webContents;
  if (contents.isDestroyed()) {
    return {
      plugin: one.plugin,
      view: one.view,
      url: "",
      title: "",
      loading: false,
      canGoBack: false,
      canGoForward: false,
      failure: "this view is gone",
    };
  }
  return {
    plugin: one.plugin,
    view: one.view,
    url: contents.getURL(),
    title: contents.getTitle(),
    loading: contents.isLoading(),
    canGoBack: contents.navigationHistory.canGoBack(),
    canGoForward: contents.navigationHistory.canGoForward(),
    failure: one.failure,
  };
}

function required(plugin: string, view: string): Held {
  const one = held.get(keyOf(plugin, view));
  if (!one || one.contents.webContents.isDestroyed()) {
    throw new Error(
      `${plugin}'s ${view} browser is not open. Its tab has to be open for this — ask them to open it.`,
    );
  }
  return one;
}

async function step(
  plugin: string,
  view: string,
  act: (contents: Electron.WebContents) => void,
): Promise<ViewStatus> {
  const one = required(plugin, view);
  act(one.contents.webContents);
  await settle(one);
  announce(one);
  return statusOf(one);
}

/** Off the screen, still alive. `setVisible` is guarded because it has not always existed on
 *  `View`, and there is no bounds-based fallback: a zero rectangle would take the page's layout
 *  with it, which is the thing `DEFAULT_BOUNDS` exists to prevent. */
function hideNow(one: Held): void {
  const view = one.contents as unknown as { setVisible?: (value: boolean) => void };
  if (typeof view.setVisible === "function") view.setVisible(false);
}

/** Back on the screen, for a pane that is showing it. */
function showNow(one: Held): void {
  const view = one.contents as unknown as { setVisible?: (value: boolean) => void };
  if (typeof view.setVisible === "function") view.setVisible(true);
}

function detach(one: Held): void {
  const window = getMainWindow();
  if (one.attached && window && !window.isDestroyed()) {
    window.contentView.removeChildView(one.contents);
  }
  one.attached = false;
}

/**
 * A click, delivered where the page will see it.
 *
 * `elementFromPoint` rather than the element the finder located, because what is *on top* at
 * that coordinate is what a person's click would hit — an overlay, a consent banner, a label
 * wrapping the input. Clicking the thing underneath would be a click no person could have made.
 *
 * The full sequence, in order, with `bubbles` throughout: pointer events first for anything
 * listening to those, then mouse, then `click`. A bare `click` event is enough for a link and
 * not enough for a good deal else.
 */
function dispatchClick(x: number, y: number): string {
  return `(() => {
    const el = document.elementFromPoint(${x}, ${y});
    if (!el) return false;
    const shared = { bubbles: true, cancelable: true, clientX: ${x}, clientY: ${y}, button: 0, view: window };
    for (const type of ["pointerdown", "mousedown"]) {
      el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type, shared));
    }
    // Focus before the release, the way a real click does — a field clicked and not focused is a
    // field the next \`type\` will miss.
    if (typeof el.focus === "function") el.focus();
    for (const type of ["pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type, shared));
    }
    return true;
  })()`;
}

/** Text into whatever is focused. Assigned and then announced, because a value set without an
 *  `input` event is one React overwrites and one validation never sees. */
function dispatchType(text: string): string {
  const value = JSON.stringify(text);
  return `(() => {
    const el = document.activeElement;
    if (!el || !("value" in el)) return false;
    const setter = Object.getOwnPropertyDescriptor(
      el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
      "value",
    );
    // Through the prototype's setter, which is what React's own value tracking hooks into. A
    // plain \`el.value =\` is invisible to it and gets reverted on the next render.
    if (setter && setter.set) setter.set.call(el, ${value});
    else el.value = ${value};
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`;
}

/** A key that means something. Enter on a field inside a form submits it, which a dispatched
 *  keydown alone does not do — so that case is handled explicitly. */
function dispatchKey(named: { key: string; code: string; windowsVirtualKeyCode: number }): string {
  const key = JSON.stringify(named.key);
  const code = JSON.stringify(named.code);
  return `(() => {
    const el = document.activeElement || document.body;
    const shape = { key: ${key}, code: ${code}, keyCode: ${named.windowsVirtualKeyCode}, which: ${named.windowsVirtualKeyCode}, bubbles: true, cancelable: true };
    const down = new KeyboardEvent("keydown", shape);
    const wanted = el.dispatchEvent(down);
    el.dispatchEvent(new KeyboardEvent("keyup", shape));
    // Only if nothing called preventDefault: a page that handles Enter itself has already done
    // whatever it meant to, and submitting on top of that would send the form twice.
    if (wanted && ${key} === "Enter" && el.form && typeof el.form.requestSubmit === "function") {
      el.form.requestSubmit();
    }
    return true;
  })()`;
}

/**
 * Settle after something that *might* navigate.
 *
 * A click on a link returns before Chromium has begun loading anything, so `settle` sees a page
 * that is not loading, waits its moment and reports the old URL — and the answer says the click
 * landed on a page that has not moved, which reads as the click having missed. This gives a
 * navigation a short window to start, and then waits for it properly.
 */
async function settleAfterInput(one: Held): Promise<void> {
  const contents = one.contents.webContents;
  const started = await new Promise<boolean>((resolve) => {
    if (contents.isDestroyed()) return resolve(false);
    const timer = setTimeout(() => {
      contents.off("did-start-navigation", began);
      resolve(false);
    }, NAVIGATION_GRACE_MS);
    const began = () => {
      clearTimeout(timer);
      contents.off("did-start-navigation", began);
      resolve(true);
    };
    contents.on("did-start-navigation", began);
  });
  if (started) await settle(one);
  else await delay(SETTLE_MS);
}

/** Wait for the page to stop loading, then a moment more. Bounded, because a page that streams
 *  for ever — a chat, a live feed — never stops loading and must not hold a call open. */
function settle(one: Held): Promise<void> {
  const contents = one.contents.webContents;
  if (contents.isDestroyed() || !contents.isLoading()) return delay(SETTLE_MS);
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      contents.off("did-stop-loading", done);
      void delay(SETTLE_MS).then(resolve);
    };
    const timer = setTimeout(() => {
      contents.off("did-stop-loading", done);
      resolve();
    }, LOAD_TIMEOUT_MS);
    contents.on("did-stop-loading", done);
  });
}

function load(one: Held, url: string): Promise<void> {
  const contents = one.contents.webContents;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${url} did not load in 30s`)), LOAD_TIMEOUT_MS);
    contents
      .loadURL(url)
      .then(() => resolve())
      .catch((error: Error) => reject(error))
      .finally(() => clearTimeout(timer));
  });
}

/** What a person types, turned into something loadable. A bare host means `https`, because
 *  guessing beats refusing them, and anything that is not http(s) is refused outright. */
function normalise(raw: string): string {
  const wanted = String(raw ?? "").trim();
  if (!wanted) return "";
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(wanted) ? wanted : `https://${wanted}`;
  try {
    const parsed = new URL(withScheme);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : "";
  } catch {
    return "";
  }
}

/** The script that finds what to click, and says what else was on offer if it cannot.
 *
 * The failure path is the useful half: a model that cannot find "Sign in" gets the list of what
 * *is* clickable and can pick, rather than taking a screenshot to find out and spending a few
 * thousand tokens to read it. */
function locate(selector: string, text: string): string {
  const wanted = JSON.stringify(text.toLowerCase());
  const css = JSON.stringify(selector);
  return `(() => {
    const box = (el) => {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return null;
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
    };
    const label = (el) =>
      (el.innerText || el.value || el.getAttribute("aria-label") || el.name || el.placeholder || "")
        .trim()
        .slice(0, 60);
    if (${css}) {
      const el = document.querySelector(${css});
      const at = el && box(el);
      if (at) return { ...at, label: label(el) || ${css} };
    }
    const clickable = [
      ...document.querySelectorAll(
        'a, button, input, select, textarea, [role=button], [role=link], [onclick], [tabindex]',
      ),
    ].filter((el) => box(el));
    if (${wanted}) {
      const exact = clickable.find((el) => label(el).toLowerCase() === ${wanted});
      const loose = clickable.find((el) => label(el).toLowerCase().includes(${wanted}));
      const el = exact || loose;
      const at = el && box(el);
      if (at) return { ...at, label: label(el) };
    }
    return { options: clickable.map(label).filter(Boolean).slice(0, 25) };
  })()`;
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

const round = (rect: Rect): Rect => ({
  x: Math.round(rect.x),
  y: Math.round(rect.y),
  width: Math.max(0, Math.round(rect.width)),
  height: Math.max(0, Math.round(rect.height)),
});

const same = (a: Rect | null, b: Rect): boolean =>
  !!a && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
