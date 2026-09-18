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
/** A bound on how many live browsers the app will hold. Each is a renderer process, so this is
 *  memory rather than bookkeeping — six because a view per conversation means several open at
 *  once, and four made working on three things at a time evict one of them. */
const MAX_VIEWS = 6;
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
  /** Which conversation's browser this is, or empty for an app-wide one. The pane checks it:
   *  status is broadcast to the window, and two conversations' panes both hear all of it. */
  owner: string;
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  /** Set when the last navigation failed, so the pane can say so rather than sitting blank. */
  failure: string;
  /** The viewport the page is being laid out at, when the model or the person has set one and
   *  it is not simply the pane. The chrome shows it, so the pane never looks broken without
   *  saying why — and the person can clear it from there in one click. */
  viewport: { width: number; height: number } | null;
  /** How much that viewport is shrunk to fit the pane it is drawn in. 1 when it fits.
   *
   *  **The viewport used to be reported without it, and that made the answer untrue.** A
   *  viewport larger than the pane was quietly cut down to the pane, and the call still said
   *  the size it had been asked for — so `resize(preset: "desktop")` in a 700-pixel pane laid
   *  the page out at 700 pixels and reported 1920×1080. The page is now laid out at the full
   *  size and *drawn* smaller, and this is the number that says by how much. */
  scale: number;
}

/** The named sizes `resize` accepts, and the reason they are named rather than invented: the
 *  model should not have to remember what a phone's width is, and a closed list is the
 *  difference between "check it at a phone size" and a call that guesses. Exported so the test
 *  can hold this table against the sizes the browser plugin's manifest declares — the two
 *  lists drifting apart is the failure mode where the manifest offers a size nobody knows. */
export const VIEWPORT_PRESETS: Record<string, { width: number; height: number }> = {
  phone: { width: 390, height: 844 },
  "phone-landscape": { width: 844, height: 390 },
  tablet: { width: 820, height: 1180 },
  "tablet-landscape": { width: 1180, height: 820 },
  laptop: { width: 1280, height: 800 },
  desktop: { width: 1920, height: 1080 },
  square: { width: 900, height: 900 },
};

/**
 * An aspect ratio, as a person writes one.
 *
 * `"16:9"`, `"9/16"`, `"4:3"`, or a bare number. The shape a page is looked at in is at least
 * as often a ratio as a pair of pixel counts — "how does this hold up at 21:9", "check it at
 * 9:16 for a phone" — and the arithmetic is the sort a model gets subtly wrong when it is
 * asked to do it in its head and then report the number it used.
 *
 * Returns `0` for anything unreadable, so the caller can say so rather than silently laying
 * the page out at a size nobody asked for.
 */
export function parseRatio(raw: string): number {
  const wanted = String(raw ?? "").trim();
  if (!wanted) return 0;
  const pair = /^(\d+(?:\.\d+)?)\s*[:/x×]\s*(\d+(?:\.\d+)?)$/i.exec(wanted);
  if (pair) {
    const across = Number(pair[1]);
    const down = Number(pair[2]);
    if (!(across > 0) || !(down > 0)) return 0;
    return across / down;
  }
  const bare = Number(wanted);
  return Number.isFinite(bare) && bare > 0 ? bare : 0;
}

/** How small or large a hand-set viewport may be. Wide enough for every real device and every
 *  pane this app is likely to have; narrow enough that a fat-fingered call cannot ask the page
 *  to lay itself out at ten pixels and read nothing back from it. */
const VIEWPORT_RANGE = { width: [200, 3840], height: [200, 2160] } as const;

/** Clamp one dimension to its range. Applied to whatever arrives — the manifest coerces, but
 *  this end also answers to the person's own chrome, and neither should trust the other. */
function clampDimension(value: number, [low, high]: readonly [number, number]): number {
  return Math.min(high, Math.max(low, Math.round(value)));
}

/** A rectangle to paint at, and how much the page inside it is shrunk to get there. */
export interface Placed extends Rect {
  /** 1 when the pane can hold the viewport; less when the page is drawn smaller than it is
   *  laid out. Never more — a page is not magnified to fill a pane it did not ask for. */
  scale: number;
}

/**
 * Where the page sits inside its pane when a viewport is set: **centred, never clipped, and
 * laid out at the size that was actually asked for.**
 *
 * The point of a viewport is that the page lays out at that exact size — media queries, JS
 * width checks, everything. A view painting past the pane's edge would cover Kith's chrome
 * beside it, which is the one thing this file must never allow, so the drawn rectangle is
 * still bounded by the pane.
 *
 * **Those two used to be in conflict and the pane won, silently.** A viewport bigger than the
 * pane was cut down to the pane, so `resize(preset: "desktop")` in a 700-pixel pane laid the
 * page out at 700 pixels — the mobile breakpoint — while the call reported 1920×1080 back.
 * Every judgement made from that was a judgement about the wrong layout, stated confidently.
 *
 * So the two are separated. The page is laid out at the full viewport (`enableDeviceEmulation`
 * carries that to Chromium) and *drawn* at a fraction of it, the way a design tool zooms out of
 * a canvas. `scale` is that fraction, it travels in the status, and the chrome shows it — so a
 * page being looked at at 58% says so instead of pretending.
 */
export function fitted(rect: Rect, viewport: { width: number; height: number } | null): Placed {
  if (!viewport) return { ...rect, scale: 1 };
  const scale = Math.min(1, rect.width / viewport.width, rect.height / viewport.height);
  // Rounded after scaling and then held inside the pane, because rounding up by a pixel on both
  // axes is how a letterboxed view ends up one pixel over Kith's chrome.
  const width = Math.min(rect.width, Math.max(1, Math.round(viewport.width * scale)));
  const height = Math.min(rect.height, Math.max(1, Math.round(viewport.height * scale)));
  return {
    x: rect.x + Math.round((rect.width - width) / 2),
    y: rect.y + Math.round((rect.height - height) / 2),
    width,
    height,
    scale,
  };
}

/**
 * Which browser is being spoken about.
 *
 * **`owner` is the third of the three, and it is the whole reason this is an object.** A view
 * used to be keyed by plugin and surface alone, so there was one browser for the entire app: a
 * page opened while working on one thing was the same page as the one opened while working on
 * another, and two errands running side by side fought over it — one navigating away under the
 * other mid-read.
 *
 * `owner` is the conversation when the surface declares `answers: "conversation"`, and empty
 * when it declares `answers: "any"`. So a plugin chooses between a tab per conversation and one
 * browser for the app, in the manifest, where somebody reviewing it can see which.
 *
 * Cookies are *not* separated by owner — the session partition stays per plugin. Two
 * conversations are two tabs of one browser profile, which is the familiar arrangement and the
 * useful one: signing in once holds for the next conversation instead of every chat starting
 * logged out. It also means a view evicted to make room loses its place on the page and not its
 * login.
 */
export interface Target {
  plugin: string;
  view: string;
  owner: string;
}

interface Held {
  plugin: string;
  view: string;
  owner: string;
  /** When this view was last spoken to. Only used to decide which to evict — see `MAX_VIEWS`. */
  touched: number;
  contents: WebContentsView;
  attached: boolean;
  failure: string;
  /** Where the renderer last put it, so a re-show does not need a fresh measurement. */
  rect: Rect | null;
  /** The viewport the page is laid out at, or null for "the whole pane". Kept on the held view
   *  rather than in the pane's renderer because it outlives the pane: a `look` with the tab
   *  shut must come back at the viewport the model asked for, not at whatever the pane was. */
  viewport: { width: number; height: number } | null;
  /** The bounds actually set last, so `place`'s hot path can skip a no-op `setBounds` even
   *  when a viewport is in play — the letterboxed rect is not the pane rect, and comparing the
   *  wrong pair would force a relayout on every scroll of the containing column. */
  bounds: Rect | null;
  /** The emulation actually in force, for the same reason `bounds` is kept: `place` runs on
   *  every scroll of the column the pane lives in, and re-asserting a device override is a
   *  relayout of the page inside it. Null means none is set. */
  emulated: { width: number; height: number; scale: number } | null;
}

const held = new Map<string, Held>();
const listeners = new Set<(status: ViewStatus) => void>();

const keyOf = (at: Target): string => `${at.plugin}/${at.view}/${at.owner}`;

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
export function place(at: Target, rect: Rect, home = ""): ViewStatus {
  const one = touch(held.get(keyOf(at))) ?? create(at, home);
  const rounded = round(rect);
  one.rect = rounded;
  if (!one.attached) {
    const window = getMainWindow();
    if (!window || window.isDestroyed()) return statusOf(one);
    window.contentView.addChildView(one.contents);
    one.attached = true;
  }
  if (rounded.width < 1 || rounded.height < 1) {
    hide(at);
    return statusOf(one);
  }
  applyBounds(one, fitted(rounded, one.viewport));
  showNow(one);
  return statusOf(one);
}

/**
 * Set the bounds a view paints at — the one place `setBounds` is called.
 *
 * One door rather than three: `place`, `resize` and `hide` all want the same two invariants —
 * skip the call when nothing moved, and remember what was set — and inlining them was how the
 * viewport work nearly grew a second bookkeeping half that agreed with the first only by luck.
 *
 * It now carries the emulation too, and for the same reason. The drawn rectangle and the size
 * the page is laid out at are two halves of one decision (`fitted`), and they were briefly set
 * in different places: a pane resize moved the bounds and left the page laid out at the size
 * the *old* pane had implied, which is the bug this whole change exists to remove, reintroduced
 * one layer down.
 */
function applyBounds(one: Held, next: Placed): void {
  if (!same(one.bounds, next)) {
    one.contents.setBounds({ x: next.x, y: next.y, width: next.width, height: next.height });
    one.bounds = { x: next.x, y: next.y, width: next.width, height: next.height };
  }
  applyEmulation(one, next.scale);
}

/**
 * Lay the page out at the viewport, whatever size it is drawn at.
 *
 * `enableDeviceEmulation` is the same mechanism DevTools' device mode uses: `viewSize` is the
 * viewport the page sees — media queries, `innerWidth`, every breakpoint — and `scale` is how
 * much of the pane the result is painted into. Without it, a viewport larger than the pane was
 * simply cut down to the pane and reported as though it had held.
 *
 * Failure degrades honestly rather than loudly. If Chromium refuses the override the bounds are
 * still the letterboxed rectangle, so the page lays out at the drawn size — exactly the old
 * behaviour — and the status still carries the scale, so nothing claims a size it did not get.
 */
function applyEmulation(one: Held, scale: number): void {
  const contents = one.contents.webContents;
  if (contents.isDestroyed()) return;
  const wanted = one.viewport
    ? { width: one.viewport.width, height: one.viewport.height, scale }
    : null;
  const now = one.emulated;
  if (!wanted && !now) return;
  if (
    wanted &&
    now &&
    now.width === wanted.width &&
    now.height === wanted.height &&
    now.scale === wanted.scale
  ) {
    return;
  }
  one.emulated = wanted;
  try {
    if (!wanted) {
      contents.disableDeviceEmulation();
      return;
    }
    contents.enableDeviceEmulation({
      // `desktop`, not `mobile`. `mobile` additionally turns on touch emulation and the mobile
      // viewport meta handling, which is a different question from "how wide is the window" and
      // is not what anybody asking for a phone-sized viewport of their own app means.
      screenPosition: "desktop",
      screenSize: { width: wanted.width, height: wanted.height },
      viewPosition: { x: 0, y: 0 },
      // 0 means "whatever this display uses", which keeps a retina capture retina.
      deviceScaleFactor: 0,
      viewSize: { width: wanted.width, height: wanted.height },
      scale: wanted.scale,
    });
  } catch {
    /* See the note above: the bounds alone are the old behaviour, which is a page laid out
     * small rather than a page laid out wrong. Nothing to say to the caller. */
  }
}

/**
 * Lay the page out at a viewport — a size, an aspect ratio, or back to the whole pane.
 *
 * This is the model's answer to "what does this look like on a phone": the view's own bounds
 * become the viewport, so media queries, JS width checks and every breakpoint see the real
 * size — emulation rather than a picture of it. It never touches Kith's window; the page is
 * letterboxed inside the pane it already lives in (`fitted`), and with no pane showing it the
 * resting bounds take the viewport instead, so a `look` with the tab shut comes back at the
 * size that was asked for.
 *
 * `width` and `height` are independent on purpose: "make it 390 wide" with the height left
 * alone is the call a model actually wants to make half the time. A preset fills both, and
 * wins when both arrive — a named size nobody has to reconstruct beats a half-remembered
 * number.
 */
export async function resize(
  at: Target,
  asked: {
    preset?: string | undefined;
    width?: number | undefined;
    height?: number | undefined;
    ratio?: string | undefined;
  },
): Promise<{ width: number; height: number } & ViewStatus> {
  const one = touch(held.get(keyOf(at))) ?? create(at, "");
  const preset = String(asked.preset ?? "");
  const ratio = parseRatio(String(asked.ratio ?? ""));
  if (asked.ratio && !ratio) {
    throw new Error(
      `${asked.ratio} is not a ratio I can read. Write one as 16:9, 9:16, 4:3 — or as a number.`,
    );
  }
  const asksForSize =
    typeof asked.width === "number" || typeof asked.height === "number" || ratio > 0;

  let viewport: { width: number; height: number } | null;
  if (preset === "reset") {
    viewport = null;
  } else if (preset || asksForSize) {
    /* The size being changed *from*, so one dimension can be given and the other left alone.
     * A preset fills both; otherwise it is whatever is in force, and failing that the resting
     * size — never the pane, which would make the same call mean different things in different
     * layouts. */
    let base = { width: DEFAULT_BOUNDS.width, height: DEFAULT_BOUNDS.height };
    if (preset) {
      const named = VIEWPORT_PRESETS[preset];
      if (!named) {
        const known = [...Object.keys(VIEWPORT_PRESETS), "reset"].join(", ");
        throw new Error(`${preset} is not a size I know. The named sizes are: ${known}.`);
      }
      base = { ...named };
    } else if (one.viewport) {
      base = { ...one.viewport };
    }

    let width = typeof asked.width === "number" ? asked.width : base.width;
    let height = typeof asked.height === "number" ? asked.height : base.height;
    /* A ratio settles the dimension that was *not* named, so "1440 wide at 16:10" and "900 tall
     * at 4:3" both say the obvious thing. Given neither, it reshapes around the width, because
     * the width is the one a layout is actually designed against. */
    if (ratio > 0) {
      if (typeof asked.height === "number" && typeof asked.width !== "number") {
        width = height * ratio;
      } else {
        height = width / ratio;
      }
    }
    viewport = {
      width: clampDimension(width, VIEWPORT_RANGE.width),
      height: clampDimension(height, VIEWPORT_RANGE.height),
    };
  } else {
    // Nothing asked for. "Resize" with no size is how a model says "put it back", and the
    // answer says so rather than leaving him to wonder whether the call did anything.
    viewport = null;
  }

  one.viewport = viewport;
  const showing = one.rect && (one.rect.width >= 1 || one.rect.height >= 1);
  const placed = showing
    ? fitted(one.rect as Rect, viewport)
    : viewport
      ? { x: 0, y: 0, ...viewport, scale: 1 }
      : { ...DEFAULT_BOUNDS, scale: 1 };
  applyBounds(one, placed);
  announce(one);
  /* The size the page is **laid out** at, and separately how much of it is on screen.
   *
   * These were one number and it was the wrong one. A viewport bigger than the pane used to be
   * cut down to the pane while this still reported what had been asked for, so a model checking
   * a desktop layout inside a narrow pane was shown the mobile one and told it was desktop.
   * The page now genuinely lays out at `width × height`; `scale` says what fraction of that is
   * being painted, which is a fact about the picture rather than about the page.
   */
  const size = viewport ?? { width: placed.width, height: placed.height };
  // `scale` rides in on the status rather than being named here — one source for it, so the
  // number the pane draws and the number the answer carries cannot drift apart.
  return { width: size.width, height: size.height, ...statusOf(one) };
}

/**
 * Take a view off the screen without destroying it.
 *
 * The distinction matters. A tab you switched away from must stop painting over the tab you
 * switched to — that is what this is for — but the page must still be *there* when you come
 * back, with its scroll position, its form contents and its login intact. Destroying and
 * recreating would be a reload, and a reload is how you lose a half-filled form.
 */
export function hide(at: Target): void {
  const one = touch(held.get(keyOf(at)));
  if (!one) return;
  // Back to the resting rectangle as well as hidden, so a screenshot still works while nobody
  // is looking — see `DEFAULT_BOUNDS`. A viewport stays set when it is one: the resting bounds
  // carry it, so a `look` answers at the size that was asked for.
  applyBounds(one, restingBounds(one));
  hideNow(one);
  one.rect = null;
}

/** The rectangle a view sits at when no pane is showing it: in the window, sized, and hidden —
 *  sized to the viewport when one is set, so the next `look` is a picture of the page at the
 *  size the model asked about rather than a size nobody chose. */
function restingBounds(one: Held): Placed {
  // Scale 1: nothing is showing it, so nothing constrains it, and a `look` at a viewport nobody
  // is watching should be the page at its own size rather than at some pane's leftovers.
  return one.viewport ? { x: 0, y: 0, ...one.viewport, scale: 1 } : { ...DEFAULT_BOUNDS, scale: 1 };
}

/** Destroy a view and everything in it. Only for a plugin being switched off or removed. */
export function forget(plugin: string, view = "", owner = ""): void {
  for (const [key, one] of [...held.entries()]) {
    if (one.plugin !== plugin) continue;
    if (view && one.view !== view) continue;
    if (owner && one.owner !== owner) continue;
    forgetOne(key);
  }
}

/**
 * Destroy a plugin's browser views **and everything its browser remembers**.
 *
 * Cookies, local storage, IndexedDB, the cache — the logged-in sessions somebody signed into
 * that plugin's tab. Kept in `persist:kith-plugin-<id>`, which lives in the *app's* own userData
 * and so is reachable from nowhere else in Kith: the Python server does the uninstalling and has
 * no way to touch an Electron session, which is why this exists as a route rather than as a line
 * in `install.py`.
 *
 * **Nothing cleared it, and that was a privacy bug rather than untidiness.** "Remove this plugin
 * and delete its data" left a browser profile holding live logins on disk indefinitely, and
 * reinstalling the plugin silently inherited them — so a plugin could come back already signed
 * in to accounts somebody thought they had removed.
 */
export async function forgetSession(plugin: string): Promise<void> {
  forget(plugin);
  hardened.delete(plugin);
  const target = session.fromPartition(partitionFor(plugin));
  // Every storage type by name rather than the default set: the default omits cache, and a cache
  // holding authenticated responses is the same problem in a different drawer.
  await target.clearStorageData({
    storages: [
      "cookies",
      "filesystem",
      "indexdb",
      "localstorage",
      "shadercache",
      "serviceworkers",
      "cachestorage",
    ],
  });
  await target.clearCache();
  await target.clearAuthCache();
}

/** Every view belonging to one conversation. For a conversation being deleted — otherwise its
 *  browser outlives it, holding a renderer process for a chat that no longer exists. */
export function forgetOwner(owner: string): void {
  for (const [key, one] of [...held.entries()]) {
    if (one.owner === owner) forgetOne(key);
  }
}

function forgetOne(key: string): void {
  const one = held.get(key);
  if (!one) return;
  detach(one);
  if (!one.contents.webContents.isDestroyed()) one.contents.webContents.close();
  held.delete(key);
}

/** Every live view, for the shell to tidy on quit. */
export function forgetAll(): void {
  for (const one of held.values()) {
    detach(one);
    if (!one.contents.webContents.isDestroyed()) one.contents.webContents.close();
  }
  held.clear();
}

export function status(at: Target): ViewStatus | null {
  const one = touch(held.get(keyOf(at)));
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
export async function navigate(at: Target, url: string, home = ""): Promise<ViewStatus> {
  const one = touch(held.get(keyOf(at))) ?? create(at, home);
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

export async function back(at: Target): Promise<ViewStatus> {
  return step(at, (contents) => {
    // `navigationHistory` is the API in this Electron; `webContents.goBack` still exists but is
    // the deprecated spelling and says nothing about whether it could.
    if (contents.navigationHistory.canGoBack()) contents.navigationHistory.goBack();
  });
}

export async function forward(at: Target): Promise<ViewStatus> {
  return step(at, (contents) => {
    if (contents.navigationHistory.canGoForward()) contents.navigationHistory.goForward();
  });
}

export async function reload(at: Target): Promise<ViewStatus> {
  return step(at, (contents) => contents.reload());
}

/** What a person would read on the page. `innerText`, for the reason `render-service` gives:
 *  it respects layout, so it skips hidden nodes and keeps the line breaks that make the result
 *  legible instead of one run-on paragraph. */
export async function read(at: Target): Promise<{ text: string } & ViewStatus> {
  const one = required(at);
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
  at: Target,
  where: {
    text?: string | undefined;
    selector?: string | undefined;
    x?: number | undefined;
    y?: number | undefined;
  },
): Promise<{ clicked: string; options: string[] } & ViewStatus> {
  const one = required(at);
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
  at: Target,
  text: string,
  into = "",
): Promise<ViewStatus> {
  const one = required(at);
  if (into) await click(at, { text: into, selector: into.startsWith("#") ? into : "" });
  // Set, then announced. Every form library listens for `input`; a value assigned without it is
  // one React overwrites on its next render, and one validation never sees.
  await one.contents.webContents.executeJavaScript(dispatchType(text), true);
  await settle(one);
  return statusOf(one);
}

/** A key that is an instruction rather than a character. */
export async function press(at: Target, key: string): Promise<ViewStatus> {
  const one = required(at);
  const named = KEYS[key];
  if (!named) throw new Error(`${key} is not a key this can press.`);
  await one.contents.webContents.executeJavaScript(dispatchKey(named), true);
  await settleAfterInput(one);
  announce(one);
  return statusOf(one);
}

export async function scroll(at: Target, by: number): Promise<ViewStatus> {
  const one = required(at);
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
export async function shot(
  at: Target,
  crop: (Rect & { label?: string }) | null = null,
): Promise<{ png: string; width: number; height: number } & ViewStatus> {
  const one = required(at);
  await settle(one);
  const contents = one.contents.webContents;

  /* A `look` exists so a turn can see a page nobody is watching, which is what makes the view's
   * resting bounds load-bearing rather than cosmetic — see `DEFAULT_BOUNDS`. */
  const box = one.contents.getBounds();
  /* **Page pixels and painted pixels are two different things now**, and a crop is given in the
   * first. The page lays out at the viewport (`applyEmulation`) and is painted at `scale` of
   * that, so an element the page says is at 1400,300 is painted at 1400×scale, 300×scale — and
   * a capture rectangle in the page's own numbers would have cut out the wrong part of it. */
  const scale = one.emulated?.scale ?? 1;
  const pageWidth = one.viewport?.width ?? box.width;
  const pageHeight = one.viewport?.height ?? box.height;

  let region: Rect | null = null;
  if (crop) {
    const left = Math.max(0, Math.min(pageWidth, crop.x));
    const top = Math.max(0, Math.min(pageHeight, crop.y));
    const right = Math.max(left, Math.min(pageWidth, crop.x + crop.width));
    const bottom = Math.max(top, Math.min(pageHeight, crop.y + crop.height));
    region = {
      x: Math.round(left * scale),
      y: Math.round(top * scale),
      width: Math.max(1, Math.round((right - left) * scale)),
      height: Math.max(1, Math.round((bottom - top) * scale)),
    };
  }

  /* `capturePage`, not `Page.captureScreenshot`.
   *
   * The debugger was the first attempt and it hangs: a screenshot command waits for a new
   * compositor frame, and a parked view produces one only when something changes. `capturePage`
   * asks the compositor for the current surface instead and answers immediately.
   */
  let png = "";
  let width = 0;
  let height = 0;
  try {
    const image = region ? await contents.capturePage(region) : await contents.capturePage();
    /* Resized to the **page's** own pixels, not the widget's.
     *
     * It used to target the widget's width, which was the same number until a viewport could be
     * drawn smaller than it is laid out. It is not the same number any more: a 1920-wide page
     * painted into a 1100-pixel pane captures at 1100×dpr, and throwing that down to 1100 spends
     * the retina pixels that were the only thing making the shrunken page legible. The target is
     * what the page thinks it is — full size where the display can pay for it, and never an
     * upscale of pixels that were never there.
     */
    const target = Math.round(region ? region.width / scale : pageWidth);
    const native = image.getSize();
    const sized = native.width > target ? image.resize({ width: target }) : image;
    const out = sized.getSize();
    width = out.width;
    height = out.height;
    png = sized.toPNG().toString("base64");
  } catch (error) {
    /* **A screenshot is the one act here that genuinely needs the tab open.**
     *
     * Everything else works on a view nobody is looking at: pages load, text reads, and input
     * dispatched inside the page lands. A capture cannot, and Chromium is blunt about it —
     * `UnknownVizError` from the compositor, because there is no on-screen surface to copy.
     *
     * So it is said, in a sentence carrying the alternative. A blank PNG written to a file is
     * far worse than a refusal: the model reads it, spends thousands of tokens on nothing, and
     * concludes the page is empty.
     */
    throw new Error(
      "A screenshot needs the Browser tab open. Call `show` to open it, then look again — or " +
        `use \`read\` for the page's text, which works either way. (${(error as Error).message})`,
    );
  }

  return { png, width, height, ...statusOf(one) };
}

/**
 * A picture of **one thing on the page**, found by what it says or by a selector.
 *
 * The cheap half of the pair below. A `look` is the whole page and costs a few thousand tokens
 * to read; most questions about a page are about one component of it — does this card wrap, is
 * this button the right colour, what does the header actually look like — and a picture of the
 * rest of the page is tokens spent on everything that was not asked about.
 *
 * Padded, deliberately, and the padding is not a rounding allowance. A component cropped exactly
 * to its own box is a component with no context: you cannot see that it is too close to the
 * thing above it, or that it is overlapping its neighbour, which is most of what anybody is
 * looking for. A margin of the page around it is what makes the picture answer the question.
 *
 * The failure path is `click`'s, for the same reason: a selector that matched nothing comes back
 * with the list of what is on the page rather than with nothing at all.
 */
export async function snap(
  at: Target,
  where: { selector?: string | undefined; text?: string | undefined; pad?: number | undefined },
): Promise<
  { png: string; width: number; height: number; selector: string; label: string; options: string[] } & ViewStatus
> {
  const one = required(at);
  await settle(one);
  const pad = clampPad(where.pad);
  const found = (await one.contents.webContents.executeJavaScript(
    boxOf(where.selector ?? "", where.text ?? ""),
    true,
  )) as
    | { x: number; y: number; width: number; height: number; selector: string; label: string }
    | { options: string[] };

  if ("options" in found) {
    return {
      png: "",
      width: 0,
      height: 0,
      selector: "",
      label: "",
      options: found.options,
      ...statusOf(one),
    };
  }

  const shrunk = await shot(at, padded(found, pad));
  return {
    png: shrunk.png,
    width: shrunk.width,
    height: shrunk.height,
    selector: found.selector,
    label: found.label,
    options: [],
    ...statusOf(one),
  };
}

/**
 * Hand the person an inspector, and take what they point at.
 *
 * ## Why this is a person's verb and not the model's
 *
 * `snap` above already crops to a selector, and a model has `read` to find selectors with. What
 * a model cannot do is know which of nine cards on the page is *the one you meant* — and the
 * round trips spent narrowing that down ("the third one", "no, the one below it") are the
 * expensive part of asking about a page at all. Pointing at it is one gesture and settles it.
 *
 * So this is on the fixed IPC channel the pane's own chrome uses, next to back and reload, and
 * it is not in `VIEW_ACTS`: the model cannot start it, and a plugin cannot either. A page being
 * picked over is a page somebody is looking at, with their hand on the mouse.
 *
 * ## What it injects, and why that is allowed
 *
 * An overlay drawn *inside the page* — an outline that follows the pointer and a label naming
 * what is under it. This is Kith's own script, the same door `read`, `click` and `type` already
 * go through; the plugin still cannot script the page, which is the property that matters. It
 * is inert: `pointer-events: none` on everything drawn, one capture-phase listener that takes
 * the click and stops it reaching the page, and it removes itself on the way out however it
 * ends.
 *
 * Resolves with `cancelled` on Escape, on a click outside the document, and on a minute passing
 * — a picker left running is an overlay on a page somebody has walked away from.
 */
export async function pick(
  at: Target,
  pad?: number,
): Promise<
  {
    cancelled: boolean;
    png: string;
    width: number;
    height: number;
    selector: string;
    label: string;
    tag: string;
  } & ViewStatus
> {
  const one = required(at);
  const padding = clampPad(pad);
  const empty = {
    cancelled: true,
    png: "",
    width: 0,
    height: 0,
    selector: "",
    label: "",
    tag: "",
  };

  let chosen: Picked | null = null;
  try {
    chosen = (await one.contents.webContents.executeJavaScript(picker(), true)) as Picked | null;
  } catch (error) {
    /* A navigation mid-pick destroys the world the promise was living in, and that is the
     * ordinary way for this to end badly rather than an error worth a sentence: the person
     * clicked a link instead of picking. */
    void error;
    return { ...empty, ...statusOf(one) };
  }
  if (!chosen) return { ...empty, ...statusOf(one) };

  const shrunk = await shot(at, padded(chosen, padding));
  return {
    cancelled: false,
    png: shrunk.png,
    width: shrunk.width,
    height: shrunk.height,
    selector: chosen.selector,
    label: chosen.label,
    tag: chosen.tag,
    ...statusOf(one),
  };
}

/** What the page answers with when somebody points at something. Named rather than inline
 *  because `pick` has to hold it across an await, and an inline shape on a `null`-initialised
 *  variable narrows to `null` before the assignment ever happens. */
interface Picked extends Rect {
  selector: string;
  label: string;
  tag: string;
}

/** Take the picker off the page from outside it — the pane unmounting, the tab being switched
 *  away from. Without this an overlay outlives the thing that started it, and the page is left
 *  with an outline following a pointer nobody is pointing. */
export async function unpick(at: Target): Promise<void> {
  const one = touch(held.get(keyOf(at)));
  if (!one || one.contents.webContents.isDestroyed()) return;
  try {
    await one.contents.webContents.executeJavaScript(
      "(() => { if (window.__kithPick) window.__kithPick.stop(null); return true; })()",
      true,
    );
  } catch {
    /* The page went away, which is the thing this was trying to achieve. */
  }
}

/** Room around a picked element, in page pixels. Bounded because a padding larger than the page
 *  is a crop of the whole page dressed up as a crop of one thing. */
const PAD_RANGE = [0, 400] as const;
const DEFAULT_PAD = 24;

function clampPad(value: number | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_PAD;
  return Math.min(PAD_RANGE[1], Math.max(PAD_RANGE[0], Math.round(value)));
}

/** An element's box with room around it. Clamped at the top and left so padding never pulls the
 *  rectangle off the page, which `capturePage` answers to with an empty image. */
function padded(box: Rect, pad: number): Rect {
  const x = Math.max(0, box.x - pad);
  const y = Math.max(0, box.y - pad);
  return {
    x,
    y,
    width: box.width + (box.x - x) + pad,
    height: box.height + (box.y - y) + pad,
  };
}

// --------------------------------------------------------------------------- //
// The plumbing
// --------------------------------------------------------------------------- //

function create(at: Target, home: string): Held {
  if (held.size >= MAX_VIEWS) {
    /* Least recently spoken to, not oldest opened.
     *
     * It was insertion order, which is exactly wrong once there is a view per conversation: the
     * browser you have had open all afternoon and are still using is the oldest one, so the
     * cheapest thing to close was the thing most in use. Whatever nobody has touched for
     * longest is the honest choice.
     *
     * Evicting loses the page's position, not its login — cookies live in the plugin's
     * partition and outlive any one view. See `Target`.
     */
    let stale: [string, Held] | null = null;
    for (const entry of held.entries()) {
      if (!stale || entry[1].touched < stale[1].touched) stale = entry;
    }
    if (stale) forgetOne(stale[0]);
  }
  harden(at.plugin);
  const contents = new WebContentsView({
    webPreferences: {
      // The plugin's own session, never the person's and never Kith's. See the module comment.
      // The plugin's, not the conversation's. Two conversations are two tabs of one profile —
      // see `Target` for why cookies deliberately do not follow the owner.
      partition: partitionFor(at.plugin),
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
  const one: Held = {
    plugin: at.plugin,
    view: at.view,
    owner: at.owner,
    touched: Date.now(),
    contents,
    attached: false,
    failure: "",
    rect: null,
    viewport: null,
    bounds: null,
    emulated: null,
  };
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
  applyBounds(one, { ...DEFAULT_BOUNDS, scale: 1 });
  hideNow(one);
  held.set(keyOf(at), one);
  watch(one);
  if (home) void navigate(at, home);
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
    void navigate(ownerOf(one), url);
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
      owner: one.owner,
      url: "",
      title: "",
      loading: false,
      canGoBack: false,
      canGoForward: false,
      failure: "this view is gone",
      viewport: one.viewport,
      scale: one.emulated?.scale ?? 1,
    };
  }
  return {
    plugin: one.plugin,
    view: one.view,
    owner: one.owner,
    url: contents.getURL(),
    title: contents.getTitle(),
    loading: contents.isLoading(),
    canGoBack: contents.navigationHistory.canGoBack(),
    canGoForward: contents.navigationHistory.canGoForward(),
    failure: one.failure,
    viewport: one.viewport,
    scale: one.emulated?.scale ?? 1,
  };
}

function required(at: Target): Held {
  const one = touch(held.get(keyOf(at)));
  if (!one || one.contents.webContents.isDestroyed()) {
    throw new Error(
      `${at.plugin}'s ${at.view} browser is not open here. Call \`show\` to open its tab, then try again.`,
    );
  }
  return one;
}

/** Mark a view as spoken to, so eviction can tell it from one nobody is using. Returns what it
 *  was given, so it can wrap a lookup rather than needing a line of its own at each one. */
function touch(one: Held | undefined): Held | undefined {
  if (one) one.touched = Date.now();
  return one;
}

/** The target a held view answers to. For the places that have the view and need its address —
 *  a popup being turned into a navigation, for one. */
function ownerOf(one: Held): Target {
  return { plugin: one.plugin, view: one.view, owner: one.owner };
}

async function step(
  at: Target,
  act: (contents: Electron.WebContents) => void,
): Promise<ViewStatus> {
  const one = required(at);
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

/**
 * The part of a page script both `snap` and `pick` need: a selector you can hand back.
 *
 * Built by walking up from the element and stopping the moment the chain is **unique in the
 * document**, rather than by climbing a fixed number of levels. A chain that is not unique is
 * worse than no selector at all: it looks specific, `document.querySelector` answers with the
 * first thing that matches it, and the next call acts on some other card on the same page.
 *
 * An `id` or a `data-testid` short-circuits it, because those are what a person would have
 * written themselves, and they survive the page being re-rendered — which `:nth-of-type` does
 * not always.
 */
const SELECTOR_FOR = `
    const selectorFor = (el) => {
      if (!el || !el.tagName) return "";
      const parts = [];
      let node = el;
      let climbed = 0;
      while (node && node.nodeType === 1 && node !== document.documentElement && climbed < 8) {
        if (node.id && /^[A-Za-z][\\w-]*$/.test(node.id) && document.querySelectorAll("#" + node.id).length === 1) {
          parts.unshift("#" + node.id);
          break;
        }
        const testid = node.getAttribute("data-testid") || node.getAttribute("data-test-id");
        if (testid) {
          parts.unshift(node.tagName.toLowerCase() + '[data-testid="' + testid.replace(/"/g, '\\\\"') + '"]');
          break;
        }
        let part = node.tagName.toLowerCase();
        const parent = node.parentElement;
        if (parent) {
          const alike = [...parent.children].filter((c) => c.tagName === node.tagName);
          if (alike.length > 1) part += ":nth-of-type(" + (alike.indexOf(node) + 1) + ")";
        }
        parts.unshift(part);
        // Stop as soon as it identifies one thing. Climbing further only makes it longer and
        // more brittle, and a shorter selector is one a person can read.
        try {
          if (document.querySelectorAll(parts.join(" > ")).length === 1) break;
        } catch (_) { break; }
        node = parent;
        climbed += 1;
      }
      return parts.join(" > ");
    };
    const labelFor = (el) =>
      ((el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("alt") || el.title || "")
        .replace(/\\s+/g, " ")
        .trim()
        .slice(0, 120));
`;

/** Where a named thing *is*, as a rectangle. `locate` answers with a point to click; a picture
 *  needs the whole box, and the two disagreeing about which element they found would be the
 *  worst kind of bug here — a crop of one component and a click on another. Same finder, same
 *  order of preference, different answer. */
function boxOf(selector: string, text: string): string {
  const wanted = JSON.stringify(text.toLowerCase());
  const css = JSON.stringify(selector);
  return `(() => {
    ${SELECTOR_FOR}
    const box = (el) => {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return null;
      return {
        x: Math.round(r.left),
        y: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
    };
    if (${css}) {
      const el = document.querySelector(${css});
      const at = el && box(el);
      if (at) return { ...at, selector: ${css}, label: labelFor(el) };
    }
    const named = [
      ...document.querySelectorAll(
        'a, button, input, select, textarea, [role=button], [role=link], [onclick], [tabindex], [data-testid], section, article, header, footer, nav, main, form, table, figure',
      ),
    ].filter((el) => box(el));
    if (${wanted}) {
      const exact = named.find((el) => labelFor(el).toLowerCase() === ${wanted});
      const loose = named.find((el) => labelFor(el).toLowerCase().includes(${wanted}));
      const el = exact || loose;
      const at = el && box(el);
      if (at) return { ...at, selector: selectorFor(el), label: labelFor(el) };
    }
    return { options: named.map(labelFor).filter(Boolean).slice(0, 25) };
  })()`;
}

/**
 * The inspector, as a script the page runs.
 *
 * Everything drawn is `pointer-events: none` and `position: fixed`, so it never changes what is
 * under the pointer and never contributes to the page's own layout — an overlay that reflows the
 * thing you are trying to measure would be the one unforgivable version of this.
 *
 * The click is taken in the **capture** phase and stopped three ways (`preventDefault`,
 * `stopPropagation`, `stopImmediatePropagation`). Picking the "Delete account" button must
 * never also press it, and a link would otherwise navigate out from under the capture that was
 * about to be taken.
 *
 * `window.__kithPick` is the handle the outside world has on it: starting a second pick stops
 * the first, and `unpick` uses it when the pane goes away. A picker with no way to be stopped
 * from outside is an outline following the pointer on a page nobody is picking from.
 */
function picker(): string {
  return `(() => {
    ${SELECTOR_FOR}
    if (window.__kithPick) window.__kithPick.stop(null);
    return new Promise((resolve) => {
      const chrome = [];
      const make = (css) => {
        const el = document.createElement("div");
        el.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;" + css;
        document.documentElement.appendChild(el);
        chrome.push(el);
        return el;
      };
      const outline = make(
        "border:2px solid #4c8dff;background:rgba(76,141,255,0.13);border-radius:3px;box-shadow:0 0 0 1px rgba(0,0,0,0.28);left:0;top:0;width:0;height:0",
      );
      const tip = make(
        "font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;background:#11161f;color:#e9eef7;padding:2px 7px;border-radius:5px;white-space:nowrap;max-width:70vw;overflow:hidden;text-overflow:ellipsis;left:0;top:0;opacity:0",
      );
      const hint = make(
        "left:50%;bottom:18px;transform:translateX(-50%);font:12px/1.6 system-ui,-apple-system,sans-serif;background:#11161f;color:#e9eef7;padding:6px 13px;border-radius:999px;opacity:.95",
      );
      hint.textContent = "Click a component to send it to the chat  ·  Esc to cancel";

      let at = null;
      const draw = (el) => {
        at = el;
        if (!el || !el.getBoundingClientRect) { outline.style.width = "0"; tip.style.opacity = "0"; return; }
        const r = el.getBoundingClientRect();
        outline.style.left = r.left + "px";
        outline.style.top = r.top + "px";
        outline.style.width = Math.max(0, r.width - 4) + "px";
        outline.style.height = Math.max(0, r.height - 4) + "px";
        tip.textContent = (selectorFor(el) || el.tagName.toLowerCase()) +
          "  " + Math.round(r.width) + "×" + Math.round(r.height);
        tip.style.opacity = "1";
        // Above the element, or below it when it is against the top of the window.
        tip.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 240)) + "px";
        tip.style.top = (r.top > 24 ? r.top - 22 : Math.min(r.bottom + 6, window.innerHeight - 24)) + "px";
      };

      let last = { x: -1, y: -1 };
      const onMove = (event) => {
        last = { x: event.clientX, y: event.clientY };
        draw(document.elementFromPoint(event.clientX, event.clientY));
      };
      const onScroll = () => {
        if (last.x >= 0) draw(document.elementFromPoint(last.x, last.y));
      };
      const onKey = (event) => {
        if (event.key === "Escape") { event.preventDefault(); stop(null); }
      };
      const onClick = (event) => {
        // Three ways, in the capture phase. Picking a button must not also press it, and a link
        // would navigate out from under the capture that is about to be taken.
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        const el = at || document.elementFromPoint(event.clientX, event.clientY);
        if (!el || !el.getBoundingClientRect) return stop(null);
        const r = el.getBoundingClientRect();
        stop({
          x: Math.round(r.left),
          y: Math.round(r.top),
          width: Math.round(r.width),
          height: Math.round(r.height),
          selector: selectorFor(el),
          label: labelFor(el),
          tag: el.tagName.toLowerCase(),
        });
      };

      let done = false;
      function stop(answer) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        window.removeEventListener("mousemove", onMove, true);
        window.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("keydown", onKey, true);
        window.removeEventListener("click", onClick, true);
        for (const el of chrome) el.remove();
        if (window.__kithPick && window.__kithPick.stop === stop) delete window.__kithPick;
        // The outline has to be off the page before the capture is taken, and removing an element
        // does not repaint before this promise's continuation runs in the main process. One frame
        // is the difference between a picture of a component and a picture of a blue rectangle.
        requestAnimationFrame(() => requestAnimationFrame(() => resolve(answer)));
      }

      // A picker nobody finishes is an overlay on a page somebody walked away from.
      const timer = setTimeout(() => stop(null), 60000);
      window.__kithPick = { stop };
      window.addEventListener("mousemove", onMove, true);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("keydown", onKey, true);
      window.addEventListener("click", onClick, true);
    });
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
