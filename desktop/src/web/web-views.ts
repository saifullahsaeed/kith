/**
 * A real browser, in one of Kith's own panes.
 *
 * ## Why the shell draws it
 *
 * A plugin surface is a *sealed* document: `sandbox="allow-scripts"` over a CSP of
 * `default-src 'none'`, with no network of any kind. That seal is what makes it safe to run a
 * stranger's code inside Kith's window, and it is not negotiable — and it means nothing drawn in
 * a plugin's tab can ever load a web page. So the browser is not a plugin surface. It is Kith's
 * own tab, drawn by the shell. `render-service.ts` already made the argument: this app is a
 * browser, so it should not be shipping a second one. The same is true of a page you can use.
 *
 * It began as a plugin — a manifest and a skill wrapped around exactly this file — and is built
 * in now. A plugin that could not supply the one thing it was for was only ever a switch, and
 * the switch cost every request a thousand tokens of command schemas.
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
 * Every view shares one partition, `BROWSER_PARTITION`. Never the person's everyday browser and
 * never Kith's own session. That boundary is the whole answer to "should the model need
 * permission to navigate this?" — it starts logged out, and anything logged into it is something
 * the person logged in themselves, in this view, on purpose (or copied across with the key button,
 * one site at a time). It holds no identity they did not put there, which makes navigating it an
 * ordinary act rather than a privileged one. A view onto their real Chrome profile would be a
 * completely different proposition and is not what this is.
 *
 * What *is* blocked is Kith's own server: a page that could reach the backend would be a page
 * reaching Kith's API from inside Kith. Other loopback addresses are deliberately allowed —
 * the first thing anyone does with this is open the app they are building on `localhost`.
 */

import { BrowserWindow, WebContentsView, session } from "electron";

import { BROWSER_PARTITION, KITH_PORTS } from "../config";

import { getMainWindow } from "../window/window";

import {
  KEYS,
  boxOf,
  chooseOption,
  dispatchClick,
  focusField,
  locate,
  picker,
  pressKey,
  scrollPage,
  typed,
  type Focused,
  type Located,
} from "./page-scripts";

/**
 * How long any one act may take, end to end, before it answers with what it has.
 *
 * **Under the server's own patience, and by a margin.** The Python side waits forty seconds for
 * an answer, and an act used to be able to take sixty: a thirty-second load, then up to thirty
 * more waiting for a page that never stops loading to stop. The server gave up first — and on
 * giving up it forgot the desktop app entirely, so one slow page switched off every browser
 * call, every notification and every rendered read until the app was restarted. The server no
 * longer forgets on a slow answer; this makes sure there is not a slow answer to forget on.
 */
const ACT_BUDGET_MS = 30_000;
/** How long a navigation may take before we stop waiting and report what we have. */
const LOAD_TIMEOUT_MS = 25_000;
/** The longest `settle` waits for a page to stop loading. A page that streams for ever — a
 *  chat, a live feed — never does, and is read as it stands once this runs out. */
const SETTLE_WAIT_MS = 12_000;
/** After `did-stop-loading`, a moment for late scripts to paint before a read or a shot. */
const SETTLE_MS = 450;
/** How long a click is given to *start* navigating before its answer is composed. Generous,
 *  because input dispatched inside the page reaches it a beat later than a widget event would. */
const NAVIGATION_GRACE_MS = 1_200;
/** The cap `render-service` uses for extracted text, for the same reason: tool output has to be
 *  a predictable size, and a model reading a page does not need the eight-thousand-and-first
 *  character to know what the page says. */
const MAX_TEXT_CHARS = 8_000;
/** A bound on how many live pages the app will hold, across every conversation's tabs. Each is a
 *  renderer process, so this is memory rather than bookkeeping — twelve because a browser per
 *  conversation with a few tabs each is the ordinary shape, and six made two busy conversations
 *  evict each other. */
const MAX_VIEWS = 12;
/** How many tabs one conversation's browser holds. A cap rather than a limit anybody should meet:
 *  past this a link that would open a tab opens in the one it was clicked in. */
const MAX_TABS = 10;
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
/** What the renderer measured, in device-independent pixels relative to the window's content. */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ViewStatus {
  /** Which conversation's browser this is. The pane checks it: status is broadcast to the
   *  window, and two conversations' panes both hear all of it. */
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
  /** Which tab is in front — the one every act without a `tab` is about. Empty with none open. */
  tab: string;
  /** Every tab, in the order the strip shows them. */
  tabs: TabSummary[];
  /** How much that viewport is shrunk to fit the pane it is drawn in. 1 when it fits.
   *
   *  **The viewport used to be reported without it, and that made the answer untrue.** A
   *  viewport larger than the pane was quietly cut down to the pane, and the call still said
   *  the size it had been asked for — so `resize(preset: "desktop")` in a 700-pixel pane laid
   *  the page out at 700 pixels and reported 1920×1080. The page is now laid out at the full
   *  size and *drawn* smaller, and this is the number that says by how much. */
  scale: number;
}

/** One tab, as the strip draws it and as the model is told about it. */
export interface TabSummary {
  id: string;
  title: string;
  url: string;
  loading: boolean;
}

/** The named sizes `resize` accepts, and the reason they are named rather than invented: the
 *  model should not have to remember what a phone's width is, and a closed list is the
 *  difference between "check it at a phone size" and a call that guesses. Exported so the test
 *  can hold this table against the sizes the `browser` tool offers — the two lists drifting
 *  apart is the failure mode where the tool offers a size nobody knows. */
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
 * Which browser is being spoken about: the conversation's.
 *
 * **One per conversation, and that is the whole reason it has a key.** There used to be one
 * browser for the entire app, so a page opened while working on one thing was the same page as
 * the one opened while working on another, and two errands running side by side fought over it —
 * one navigating away under the other mid-read. An empty owner is a turn that belongs to no
 * conversation, and gets a browser of its own.
 *
 * Cookies are *not* separated by owner — there is one partition. Two conversations are two tabs
 * of one browser profile, which is the familiar arrangement and the useful one: signing in once
 * holds for the next conversation instead of every chat starting logged out. It also means a view
 * evicted to make room loses its place on the page and not its login.
 */
export interface Target {
  owner: string;
}

/** One tab: a page, and everything its view needs to paint it where the pane is. */
interface Held {
  /** `t1`, `t2`… — what the strip keys on and what the model names a tab by. */
  id: string;
  browser: Browser;
  owner: string;
  /** When this tab was last spoken to. Only used to decide what to evict — see `MAX_VIEWS`. */
  touched: number;
  contents: WebContentsView;
  attached: boolean;
  failure: string;
  /** The viewport the page is laid out at, or null for "the whole pane". Per tab, because a
   *  phone-width check of one page is not a reason to reshape the others — and kept here rather
   *  than in the pane's renderer because it outlives the pane: a `look` with the tab shut must
   *  come back at the viewport the model asked for, not at whatever the pane was. */
  viewport: { width: number; height: number } | null;
  /** The bounds actually set last, so `place`'s hot path can skip a no-op `setBounds` even
   *  when a viewport is in play — the letterboxed rect is not the pane rect, and comparing the
   *  wrong pair would force a relayout on every scroll of the containing column. */
  bounds: Rect | null;
  /** The emulation actually in force, for the same reason `bounds` is kept: `place` runs on
   *  every scroll of the column the pane lives in, and re-asserting a device override is a
   *  relayout of the page inside it. Null means none is set. */
  emulated: { width: number; height: number; scale: number } | null;
  /** Sign-in popups this page opened and that are still open — see `watch`. Closed with it. */
  popups: Set<BrowserWindow>;
  /** When a navigation last turned out to be a file download, which this browser refuses. Lets
   *  `navigate` tell "that address is a download" from a navigation something else cut short. */
  downloaded: number;
}

/**
 * One conversation's browser: its tabs, which one is in front, and where its pane is.
 *
 * **Tabs, because one page was not enough to work in.** Comparing two things meant navigating
 * away from the first; a link that opened a new tab replaced the page it was clicked on; and the
 * person could not keep their own page open while he looked something up. So a browser holds
 * several, and exactly one is in front: the one the pane paints, the one the address bar shows,
 * and the one every act without a `tab` is about. The person and the model share that — a tab
 * the person switches to is the tab his next `read` reads, which is the same rule as the page.
 */
interface Browser {
  owner: string;
  tabs: Held[];
  active: Held | null;
  /** Where the renderer last put the pane, or null when no pane is showing it. */
  rect: Rect | null;
  /** How many tabs this browser has ever made, for naming the next. Never reused, so a closed
   *  tab's id cannot come back meaning a different page. */
  made: number;
  touched: number;
  /** Told when a tab opens. A click waits on this as well as on a navigation, because a link
   *  that opens a new tab moves nothing in the tab it was clicked in. */
  opened: Set<(tab: Held) => void>;
  /** The last tab opened, and when — for the click whose tab opened before it started listening,
   *  which is the usual order: the page asks for the window before the click's own answer is
   *  back in this process. */
  lastOpened: { tab: Held; at: number } | null;
}

const browsers = new Map<string, Browser>();
const listeners = new Set<(status: ViewStatus) => void>();

/** The conversation's browser, made empty if it has none yet. */
function browserFor(at: Target): Browser {
  const found = browsers.get(at.owner);
  if (found) {
    found.touched = Date.now();
    return found;
  }
  const made: Browser = {
    owner: at.owner,
    tabs: [],
    active: null,
    rect: null,
    made: 0,
    touched: Date.now(),
    opened: new Set(),
    lastOpened: null,
  };
  browsers.set(at.owner, made);
  return made;
}

/** The tab in front, opening a blank one when there is none — for the acts that make sense on
 *  an empty browser, which are placing it and sizing it. */
function frontOf(at: Target): Held {
  const browser = browserFor(at);
  return touch(browser.active) ?? (openTab(browser, { activate: true }) as Held);
}

/** Subscribe to status changes. The shell forwards these to the renderer, which draws the
 *  chrome — the address bar, the back button's enabled state, the spinner. */
export function onViewStatus(listener: (status: ViewStatus) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Put a conversation's view at a rectangle, creating it if this is the first time.
 *
 * Called on mount, on every pane resize and on every scroll of whatever contains the pane. It
 * is therefore the hot path and does as little as possible: an existing view whose bounds have
 * not moved is left entirely alone, because `setBounds` on a live web view forces a relayout of
 * the page inside it and doing that sixty times a second while somebody drags a splitter is
 * visible as jank in the page, not in the frame.
 */
export function place(at: Target, rect: Rect): ViewStatus {
  const browser = browserFor(at);
  const rounded = round(rect);
  browser.rect = rounded;
  /* Nothing is opened just because a pane is showing. It used to open a blank tab here, and a
   * pane re-measures on every resize and every overlay — so closing the last tab was followed at
   * once by a new blank one nobody had asked for, and the model was told there were no tabs a
   * moment before there was one again. An empty browser is a pane with an address bar. */
  const one = touch(browser.active);
  if (!one) return statusOf(browser);
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
  for (const other of browser.tabs) if (other !== one) rest(other);
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
  const one = frontOf(at);
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
  const pane = one.browser.active === one ? one.browser.rect : null;
  const placed = pane
    ? fitted(pane, viewport)
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
  const browser = browsers.get(at.owner);
  if (!browser) return;
  for (const one of browser.tabs) rest(one);
  browser.rect = null;
}

/** One tab off the screen and back to its resting rectangle, so a screenshot still works while
 *  nobody is looking — see `DEFAULT_BOUNDS`. A viewport stays set when it is one: the resting
 *  bounds carry it, so a `look` answers at the size that was asked for. */
function rest(one: Held): void {
  applyBounds(one, restingBounds(one));
  hideNow(one);
}

/** The rectangle a view sits at when no pane is showing it: in the window, sized, and hidden —
 *  sized to the viewport when one is set, so the next `look` is a picture of the page at the
 *  size the model asked about rather than a size nobody chose. */
function restingBounds(one: Held): Placed {
  // Scale 1: nothing is showing it, so nothing constrains it, and a `look` at a viewport nobody
  // is watching should be the page at its own size rather than at some pane's leftovers.
  return one.viewport ? { x: 0, y: 0, ...one.viewport, scale: 1 } : { ...DEFAULT_BOUNDS, scale: 1 };
}

/** Every tab belonging to one conversation. For a conversation being deleted — otherwise its
 *  browser outlives it, holding renderer processes for a chat that no longer exists. */
export function forgetOwner(owner: string): void {
  const browser = browsers.get(owner);
  if (!browser) return;
  for (const one of browser.tabs) close(one);
  browsers.delete(owner);
}

/** Every live view, for the shell to tidy on quit. */
export function forgetAll(): void {
  for (const browser of browsers.values()) for (const one of browser.tabs) close(one);
  browsers.clear();
}

/** Off the window and shut, with any sign-in popup it opened — a popup outliving the page that
 *  was waiting for it is a window nothing will ever answer. */
function close(one: Held): void {
  detach(one);
  for (const popup of one.popups) if (!popup.isDestroyed()) popup.close();
  one.popups.clear();
  if (!one.contents.webContents.isDestroyed()) one.contents.webContents.close();
}

export function status(at: Target): ViewStatus | null {
  const browser = browsers.get(at.owner);
  return browser ? statusOf(browser) : null;
}

// --------------------------------------------------------------------------- //
// Tabs
// --------------------------------------------------------------------------- //

/**
 * A new tab, in front unless it was asked for behind — which is what a middle-click and
 * `target=_blank` with a modifier mean, and how Chromium tells us (`background-tab`).
 *
 * Returns null at `MAX_TABS`: the caller decides what that means, because it means different
 * things — a link opens in the tab it was clicked in, a `+` says the browser is full.
 */
function openTab(browser: Browser, how: { url?: string; activate: boolean }): Held | null {
  if (browser.tabs.length >= MAX_TABS) return null;
  const one = makeTab(browser);
  // After the tab it was opened from, the way a browser does, so a run of links opened from one
  // page stays beside it rather than at the far end of the strip.
  const from = browser.active ? browser.tabs.indexOf(browser.active) : -1;
  browser.tabs.splice(from >= 0 ? from + 1 : browser.tabs.length, 0, one);
  if (how.url) void loadInto(one, how.url);
  if (how.activate || !browser.active) bringForward(browser, one);
  else rest(one);
  browser.lastOpened = { tab: one, at: Date.now() };
  for (const told of [...browser.opened]) told(one);
  announce(browser);
  return one;
}

/** Put a tab in front: it paints in the pane, the others rest behind it. */
function bringForward(browser: Browser, one: Held): void {
  const was = browser.active;
  browser.active = one;
  touch(one);
  // A picker left running on the tab being switched away from is an overlay nobody can see.
  if (was && was !== one) void stopPicking(was);
  for (const other of browser.tabs) if (other !== one) rest(other);
  const pane = browser.rect;
  if (pane && pane.width >= 1 && pane.height >= 1) {
    applyBounds(one, fitted(pane, one.viewport));
    showNow(one);
  } else {
    rest(one);
  }
}

/** Open a tab — blank, or at an address. The `+` in the strip, and `open` with `new_tab`. */
export async function newTab(at: Target, url = ""): Promise<ViewStatus> {
  if (url) return navigate(at, url, { newTab: true });
  const browser = browserFor(at);
  if (!openTab(browser, { activate: true })) return { ...statusOf(browser), failure: tabsFull(browser) };
  return statusOf(browser);
}

/** Bring a tab to the front by its id. */
export function switchTab(at: Target, id: string): ViewStatus {
  const browser = browserFor(at);
  const one = browser.tabs.find((tab) => tab.id === id);
  if (!one) throw new Error(`There is no tab ${id || "with no name"}. ${tabList(browser)}`);
  bringForward(browser, one);
  announce(browser);
  return statusOf(browser);
}

/**
 * Close a tab — the one named, or the one in front.
 *
 * The tab to its right comes forward, or failing that the one to its left, which is what every
 * browser does and so what a person expects to be looking at next. Closing the last one leaves
 * the browser empty rather than closing the pane: the pane is Kith's, not the page's.
 */
export function closeTab(at: Target, id = ""): ViewStatus {
  const browser = browserFor(at);
  const one = id ? browser.tabs.find((tab) => tab.id === id) : browser.active;
  if (!one) {
    throw new Error(id ? `There is no tab ${id}. ${tabList(browser)}` : "No tab is open, so nothing was closed.");
  }
  const at_ = browser.tabs.indexOf(one);
  browser.tabs.splice(at_, 1);
  close(one);
  if (browser.active === one) {
    const next = browser.tabs[at_] ?? browser.tabs[at_ - 1] ?? null;
    browser.active = null;
    if (next) bringForward(browser, next);
  }
  announce(browser);
  return statusOf(browser);
}

/** The tabs, as a sentence — for a refusal that should say what *is* there. */
function tabList(browser: Browser): string {
  if (!browser.tabs.length) return "No tabs are open.";
  return `The tabs are: ${browser.tabs.map((tab) => `${tab.id} (${titleOf(tab) || "untitled"})`).join(", ")}.`;
}

function tabsFull(browser: Browser): string {
  return `${browser.tabs.length} tabs are open, which is as many as one conversation's browser holds. Close one first.`;
}

function titleOf(one: Held): string {
  const contents = one.contents.webContents;
  return contents.isDestroyed() ? "" : contents.getTitle() || contents.getURL();
}

// --------------------------------------------------------------------------- //
// Driving one
// --------------------------------------------------------------------------- //

/**
 * Go somewhere, and wait until it has arrived.
 *
 * Waiting is the point. The person's own click on the address bar wants the chrome to show a
 * spinner and then a title; the *model's* call wants an answer it can act on, and "I have begun
 * navigating" is not one. So both go through here and both get a settled page — inside
 * `ACT_BUDGET_MS`, however slow the page is.
 *
 * A failed navigation resolves rather than throwing. `ERR_NAME_NOT_RESOLVED` is a fact about
 * the page, not an error in the caller — the model should be told the host does not exist, in a
 * sentence, and carry on.
 *
 * `ERR_ABORTED` is not a failure at all, usually: it is what a navigation reports when a
 * redirect or a newer navigation replaced it, and the page that replaced it is the answer. The
 * one exception is an address that turned out to be a file download, which this browser refuses
 * — that is said, because "the page did not change" would otherwise be all anybody learned.
 */
export async function navigate(
  at: Target,
  url: string,
  how: { newTab?: boolean } = {},
): Promise<ViewStatus> {
  const browser = browserFor(at);
  const target = normalise(url);
  if (!target) return { ...statusOf(browser), failure: `${url} is not an address I can open.` };
  let one = touch(browser.active);
  if (!one || how.newTab) {
    const opened = openTab(browser, { activate: true });
    if (!opened) return { ...statusOf(browser), failure: tabsFull(browser) };
    one = opened;
  }
  const started = Date.now();
  await loadInto(one, target);
  await settle(one, ACT_BUDGET_MS - (Date.now() - started));
  announce(one);
  return statusOf(one);
}

/** Load an address into one tab and record how it went. Resolves when the load has answered —
 *  arrived, failed, or run out of time — and says nothing about settling, which is the caller's. */
async function loadInto(one: Held, target: string): Promise<void> {
  const started = Date.now();
  one.failure = "";
  announce(one);
  try {
    await load(one, target);
  } catch (error) {
    if (!aborted(error)) one.failure = (error as Error).message;
    else if (one.downloaded >= started) one.failure = downloadRefused(target);
  }
  announce(one);
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
 *  legible instead of one run-on paragraph.
 *
 *  Not a user gesture. Everything that *acts* on the page is run as one, because that is what a
 *  person's hand would be; reading is not, and a gesture handed to a page for a read is a few
 *  seconds in which its own scripts may open popups and write to the clipboard. */
export async function read(at: Target): Promise<{ text: string } & ViewStatus> {
  const one = required(at);
  await settle(one);
  const text = (await one.contents.webContents.executeJavaScript(
    "document.body ? document.body.innerText : ''",
    false,
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
 * Two halves. First the page is asked where the thing is — by text, by selector, or taken as
 * given — and scrolled to it, the way a person scrolls before they click; then the click is
 * delivered at that point and the answer says **what it actually hit**.
 *
 * Both halves used to be able to fail without saying so. Nothing scrolled, so a button below the
 * fold was located at a point outside the viewport, the click went to nothing, and the answer
 * said it had been clicked. And the answer named the element that was *found*, not the one on
 * top of it — so a click a cookie banner took was reported as a click on "Submit".
 *
 * Dispatched inside the page rather than through the widget. `Input.dispatchMouseEvent` and
 * `sendInputEvent` both go to the render widget, and a widget that is hidden accepts them and
 * drops them — no error, no effect. And hidden is the normal state: a browser has to be
 * driveable in a turn where nobody has the tab open. The cost is honest and worth naming: these
 * events carry `isTrusted: false`. Links, buttons and forms do not care — React and every other
 * framework listen for bubbling events, which these are — but something gated specifically on a
 * user gesture may refuse.
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
  const contents = one.contents.webContents;
  let point: { x: number; y: number };
  let described: string;
  let covered = "";

  if (typeof where.x === "number" && typeof where.y === "number") {
    point = { x: where.x, y: where.y };
    described = `${where.x},${where.y}`;
  } else {
    const found = (await contents.executeJavaScript(
      locate(where.selector ?? "", where.text ?? ""),
      false,
    )) as Located;
    if ("options" in found) return { clicked: "", options: found.options, ...statusOf(one) };
    if ("invalid" in found) throw new Error(`${found.invalid} is not a CSS selector I can use.`);
    if ("unreachable" in found) {
      throw new Error(
        `"${found.unreachable}" is on the page but could not be brought into view to click — it may be inside a panel that is closed or clipped.`,
      );
    }
    point = { x: found.x, y: found.y };
    described = found.label;
    covered = found.covered;
  }

  const since = Date.now();
  const hit = (await contents.executeJavaScript(dispatchClick(point.x, point.y), true)) as string | null;
  if (hit === null) {
    throw new Error(
      `Nothing on the page is at ${point.x},${point.y}, so nothing was clicked. \`read\` the page, or click by what it says.`,
    );
  }
  const opened = await settleAfterInput(one, since);
  announce(one);
  const clicked = covered
    ? `${described} — but "${covered}" was on top of it and took the click`
    : opened
      ? `${described} — it opened in a new tab, ${opened.id}${opened.browser.active === opened ? ", now in front" : ", behind this one"}`
      : described;
  return { clicked, options: [], ...statusOf(one) };
}

/**
 * Type into the focused field, or into one named by its label, placeholder or a selector.
 *
 * Three steps, and each one can say no. `focusField` finds the field and selects what is in it
 * when one was named, so the text *replaces* it; then the text goes in through the main process's
 * `insertText`, the IME path — it fires `beforeinput` the way a keyboard does, so rich editors
 * that ignore a value being set (ProseMirror, Lexical, Slate) see it, and it survives fields that
 * reformat as you go; then `typed` checks it arrived, and tries the older ways if it did not.
 *
 * It used to do none of the checking. A field named by its `<label>` was not found, the miss was
 * discarded, and the text went into whatever happened to be focused — or into nothing, for a
 * rich-text editor — and every one of those answered as a success.
 */
export async function type(
  at: Target,
  text: string,
  into = "",
): Promise<{ into: string } & ViewStatus> {
  const one = required(at);
  const contents = one.contents.webContents;
  const field = (await contents.executeJavaScript(focusField(into), true)) as Focused;

  if ("fields" in field) {
    const list = field.fields.length ? `The fields on the page are: ${field.fields.join(", ")}.` : "The page has no fields to type into.";
    throw new Error(`Nothing on the page is a field called "${into}", so nothing was typed. ${list}`);
  }
  if ("problem" in field) {
    throw new Error(
      field.problem === "frame"
        ? "What has focus is inside an embedded frame, which typing cannot reach. Name the field with `into` if it is on the page itself."
        : `${field.focused ? `What has focus is ${field.focused}, which` : "Nothing has focus, and the page"} is not a field, so nothing was typed. Name one with \`into\`, or click it first.`,
    );
  }

  if (field.kind === "select") {
    const chosen = (await contents.executeJavaScript(chooseOption(text), true)) as {
      chose: string;
      choices: string[];
    };
    if (!chosen.chose) {
      throw new Error(`${field.name} is a list, and none of its choices is "${text}". It offers: ${chosen.choices.join(", ")}.`);
    }
  } else {
    await contents.insertText(text);
    const result = (await contents.executeJavaScript(typed(text, field.before, !!into), true)) as {
      landed: boolean;
    };
    if (!result.landed) {
      throw new Error(`${field.name} did not take the text — the page may be rejecting input there. \`look\` at it to see why.`);
    }
  }
  await settle(one);
  announce(one);
  return { into: field.name, ...statusOf(one) };
}

/** A key that is an instruction rather than a character. Answers with `did`, what the key
 *  actually did — see `pressKey` for why that has to be asked rather than assumed. */
export async function press(at: Target, key: string): Promise<{ did: string } & ViewStatus> {
  const one = required(at);
  const named = KEYS[key];
  if (!named) throw new Error(`${key} is not a key this can press.`);
  const since = Date.now();
  const answer = (await one.contents.webContents.executeJavaScript(pressKey(named), true)) as {
    did: string;
    on: string;
  };
  await settleAfterInput(one, since);
  announce(one);
  const on = answer.on ? ` on ${answer.on}` : "";
  const did = answer.did
    ? `${answer.did}`
    : `${key} reached the page${on}, and nothing further happens for it by default there`;
  return { did, ...statusOf(one) };
}

/** Scroll the page, or the panel inside it that actually scrolls. Answers with how far it really
 *  moved, so the bottom of a page is a `0` rather than a scroll that claims to have happened. */
export async function scroll(at: Target, by: number): Promise<{ moved: number } & ViewStatus> {
  const one = required(at);
  const answer = (await one.contents.webContents.executeJavaScript(scrollPage(by), true)) as {
    moved: number;
  };
  await settle(one);
  return { moved: answer.moved, ...statusOf(one) };
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
  return capture(one, crop);
}

/** The capture itself, for callers that have already settled the page. `snap` settled, then
 *  found its element, and settling a second time inside the same call was another twelve
 *  seconds on the clock of an act that has to answer inside `ACT_BUDGET_MS`. */
async function capture(
  one: Held,
  crop: (Rect & { label?: string }) | null,
): Promise<{ png: string; width: number; height: number } & ViewStatus> {
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
    const x = Math.round(left * scale);
    const y = Math.round(top * scale);
    // Held inside the widget after rounding. `capturePage` answers a region that reaches past
    // its own bounds with an empty image, and a blank PNG written to a file is the failure this
    // whole path is written to avoid — the model reads it and concludes the page is empty.
    region = {
      x: Math.min(x, Math.max(0, box.width - 1)),
      y: Math.min(y, Math.max(0, box.height - 1)),
      width: Math.max(1, Math.min(box.width - x, Math.round((right - left) * scale))),
      height: Math.max(1, Math.min(box.height - y, Math.round((bottom - top) * scale))),
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
  const contents = one.contents.webContents;
  const found = (await contents.executeJavaScript(boxOf(where.selector ?? "", where.text ?? ""), false)) as
    | { x: number; y: number; width: number; height: number; selector: string; label: string }
    | { options: string[] }
    | { invalid: string };

  if ("invalid" in found) throw new Error(`${found.invalid} is not a CSS selector I can use.`);
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

  /* A frame first. Finding the element may have scrolled it into view, and the compositor's
   * current surface is the one from *before* the scroll until the page paints again — so a crop
   * taken at once is the right rectangle of the wrong picture. Bounded, because a view nobody is
   * looking at may never paint at all, and that case is `capture`'s to report. */
  await contents.executeJavaScript(NEXT_FRAME, false);
  const shrunk = await capture(one, padded(found, pad));
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
 * it is not an act the `browser` tool has: the model cannot start it. A page being picked over is a
 * page somebody is looking at, with their hand on the mouse.
 *
 * ## What it injects, and why that is allowed
 *
 * An overlay drawn *inside the page* — an outline that follows the pointer and a label naming
 * what is under it. This is Kith's own script, the same door `read`, `click` and `type` already
 * go through — there is still no act that runs arbitrary script in the page. It
 * is inert: `pointer-events: none` on everything drawn, capture-phase listeners that take every
 * part of the click and stop it reaching the page, and it removes itself on the way out however
 * it ends.
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

  const shrunk = await capture(one, padded(chosen, padding));
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
  const browser = browsers.get(at.owner);
  if (browser) await Promise.all(browser.tabs.map(stopPicking));
}

async function stopPicking(one: Held): Promise<void> {
  if (one.contents.webContents.isDestroyed()) return;
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

function makeTab(browser: Browser): Held {
  makeRoom(browser);
  harden();
  const contents = new WebContentsView({
    webPreferences: {
      // The browser's own session, never the person's and never Kith's. See the module comment.
      // One for every conversation: two conversations are two tabs of one profile — see
      // `Target` for why cookies deliberately do not follow the owner.
      partition: BROWSER_PARTITION,
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
  browser.made += 1;
  const one: Held = {
    id: `t${browser.made}`,
    browser,
    owner: browser.owner,
    touched: Date.now(),
    contents,
    attached: false,
    failure: "",
    viewport: null,
    bounds: null,
    emulated: null,
    popups: new Set(),
    downloaded: 0,
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
  watch(one);
  return one;
}

/**
 * Close something to make room for one more live page, when the app is holding `MAX_VIEWS`.
 *
 * Least recently spoken to, not oldest opened: insertion order made the browser you had had open
 * all afternoon the cheapest thing to close. And never what is on screen while there is any other
 * choice — a pane somebody is reading does not get touched, because nothing is said to it while
 * they scroll with their own hand, so by age alone the page in front of them was the stalest
 * thing here.
 *
 * So, in order: a whole browser nobody is showing, then a tab behind the front one. Evicting
 * loses the page's place, not its login — cookies live in the partition and outlive any view.
 */
function makeRoom(making: Browser): void {
  const live = [...browsers.values()].reduce((sum, one) => sum + one.tabs.length, 0);
  if (live < MAX_VIEWS) return;
  let quiet: Browser | null = null;
  for (const one of browsers.values()) {
    if (one === making || one.rect !== null || !one.tabs.length) continue;
    if (!quiet || one.touched < quiet.touched) quiet = one;
  }
  if (quiet) {
    forgetOwner(quiet.owner);
    return;
  }
  let stale: Held | null = null;
  for (const one of browsers.values()) {
    for (const tab of one.tabs) {
      if (tab === one.active) continue;
      if (!stale || tab.touched < stale.touched) stale = tab;
    }
  }
  if (stale) closeTab({ owner: stale.owner }, stale.id);
}

/**
 * Everything a page in here may not do, set once on the partition.
 *
 * The permission handlers are the interesting ones. A page in a pane the person is looking at
 * could plausibly ask for a camera, and the answer is still no: this is a browser for reading and
 * using pages, not a device. Saying no in the handler rather than by prompting means there is no
 * dialog for a page to badger somebody into accepting.
 */
function harden(): void {
  if (hardened) return;
  hardened = true;
  const target = session.fromPartition(BROWSER_PARTITION);

  target.webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    // **Kith's own server, under any name.** A page that can reach the backend is a page reaching
    // Kith's API from inside Kith, which is the one address that must be unreachable from here —
    // see `reachesKith` for why that has to be judged by port and not by origin.
    //
    // Other loopback addresses are allowed on purpose, and this is a deliberate departure from
    // `render-service`'s blanket refusal. That one renders a URL a *model* chose, where the
    // whole risk is being talked into fetching something local; this is a browser a *person*
    // is driving, and the first thing anybody does with it is open the app they are building
    // on `localhost:5173`. Refusing that would be refusing the use case.
    callback({ cancel: reachesKith(details.url, KITH_PORTS) });
  });

  target.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  target.setPermissionCheckHandler(() => false);
  // Downloads go nowhere. Nothing in Kith serves files a page saved, and a save dialog opening
  // over the app from a page in a tab is not something anybody asked for.
  //
  // Said, though, rather than swallowed. A link that turns out to be a file used to do nothing
  // at all — to the person clicking it and to the model opening it — which reads as a broken
  // link rather than as a browser that declines to save files.
  target.on("will-download", (event, item, source) => {
    event.preventDefault();
    const one = [...browsers.values()]
      .flatMap((browser) => browser.tabs)
      .find((view) => view.contents.webContents === source);
    if (!one) return;
    one.downloaded = Date.now();
    one.failure = downloadRefused(item.getFilename() || item.getURL());
    announce(one);
  });
}

/**
 * Would this address reach Kith's own server?
 *
 * **By port, on every loopback name — not by origin.** It compared origins, and origins are
 * spellings: the block was `http://127.0.0.1:8611`, and `http://localhost:8611` is a different
 * origin for the same server. That one serves Kith's whole interface with the API token written
 * into the page, so opening it loaded Kith, signed in, inside a pane the model drives — settings,
 * permission mode and all, a click away from a page that talked him into it.
 *
 * So any loopback host counts: `localhost` and its subdomains, all of `127.0.0.0/8`, `0.0.0.0`,
 * `::1` and the IPv4-mapped forms. The URL parser has already turned `2130706433` and `0x7f.1`
 * into `127.0.0.1` by the time this sees them. A *name* that resolves to loopback cannot be
 * judged here — nothing is resolved yet — and is the server's to refuse: it answers only to the
 * hosts it expects (`kith/api/auth.py`).
 */
export function reachesKith(raw: string, ports: ReadonlySet<string>): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:" && url.protocol !== "ws:" && url.protocol !== "wss:") {
    return false;
  }
  const secure = url.protocol === "https:" || url.protocol === "wss:";
  const port = url.port || (secure ? "443" : "80");
  return ports.has(port) && isLoopback(url.hostname);
}

function isLoopback(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  if (host === "0.0.0.0" || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host)) return true;
  // IPv6 arrives bracketed: `[::1]`, `[::]`, and 127/8 mapped into IPv6 as `[::ffff:7fxx:xxxx]`.
  return host === "[::1]" || host === "[::]" || /^\[::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}\]$/.test(host);
}

/** What the person and the model are told when an address is a file. */
function downloadRefused(name: string): string {
  return `${name} is a file download, and this browser does not save files.`;
}

/** A navigation something else cut short — a redirect, a newer navigation, a download. */
function aborted(error: unknown): boolean {
  const failed = error as { errno?: number; code?: string; message?: string };
  return failed?.errno === -3 || failed?.code === "ERR_ABORTED" || /ERR_ABORTED/.test(String(failed?.message ?? ""));
}

let hardened = false;

/** At most this many sign-in popups per page. Enough for a login that opens a second window for
 *  two-factor; few enough that a page cannot bury the app in windows. */
const MAX_POPUPS = 2;

/** Report a status change whenever the page does something the chrome draws. */
function watch(one: Held): void {
  const contents = one.contents.webContents;
  // Written out rather than looped: to TypeScript each event name is a separate overload, so a
  // loop over them is a call whose argument is a union no single overload accepts.
  const changed = () => announce(one);
  // Cleared before it is announced, so the status that says "arrived" does not still carry the
  // failure of the page before it.
  contents.on("did-navigate", () => {
    one.failure = "";
  });
  contents.on("did-start-loading", changed);
  contents.on("did-stop-loading", changed);
  contents.on("did-navigate", changed);
  contents.on("did-navigate-in-page", changed);
  contents.on("page-title-updated", changed);
  contents.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
    // A subframe failing is the web being the web. Only the main frame is worth saying.
    if (!isMainFrame) return;
    // -3 is ERR_ABORTED: a redirect, a navigation that replaced this one, or a download. None of
    // them is the page failing, and saying so put a failure under a page that loaded fine.
    if (code === -3) return;
    one.failure = `${description || `error ${code}`}${url ? ` (${url})` : ""}`;
    announce(one);
  });
  /* A link that would open a new tab opens a new tab — in front for an ordinary click, behind for
   * a middle-click (`background-tab`). It used to become a navigation in this one, because there
   * was one page and no strip; that replaced the page the link was on, which is the page anybody
   * opening a link in a new tab meant to keep. Past `MAX_TABS` it still does, in this tab.
   *
   * **A scripted popup is the exception, because it is almost always a sign-in.** "Continue with
   * Google" opens a small window and waits for it to post the result back to `window.opener`.
   * Turned into a navigation, the page that was waiting is gone and the login can never finish —
   * so every sign-in built that way was impossible here. Chromium tells the two apart for us:
   * `new-window` is `window.open` with features, a link with `target=_blank` is `foreground-tab`.
   *
   * The popup is a real window over Kith, in the same partition (so what it signs into holds for
   * the page), under the same `harden` as everything else in it. The model cannot drive it; it
   * is the person's, like the login it exists for.
   */
  contents.setWindowOpenHandler(({ url, disposition }) => {
    if (disposition === "new-window" && one.popups.size < MAX_POPUPS && normalise(url)) {
      const parent = getMainWindow();
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          ...(parent && !parent.isDestroyed() ? { parent } : {}),
          width: 520,
          height: 700,
          autoHideMenuBar: true,
          webPreferences: {
            sandbox: true,
            contextIsolation: true,
            nodeIntegration: false,
            nodeIntegrationInSubFrames: false,
            webviewTag: false,
            webSecurity: true,
            allowRunningInsecureContent: false,
          },
        },
      };
    }
    const address = normalise(url);
    if (address && !openTab(one.browser, { url: address, activate: disposition !== "background-tab" })) {
      void loadInto(one, address);
    }
    return { action: "deny" };
  });
  /* The keys a person expects a browser to answer, taken before the page sees them — the page
   * has focus, so Kith's own window never hears these. New tab, close tab, next and previous. */
  contents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    const key = input.key.toLowerCase();
    const at = ownerOf(one);
    if (input.meta && !input.shift && key === "t") {
      event.preventDefault();
      void newTab(at);
    } else if (input.meta && !input.shift && key === "w") {
      event.preventDefault();
      closeTab(at, one.id);
    } else if (input.control && key === "tab") {
      event.preventDefault();
      const tabs = one.browser.tabs;
      const next = tabs[(tabs.indexOf(one) + (input.shift ? tabs.length - 1 : 1)) % tabs.length];
      if (next && next !== one) switchTab(at, next.id);
    }
  });
  contents.on("did-create-window", (popup) => {
    one.popups.add(popup);
    popup.on("closed", () => one.popups.delete(popup));
    // A popup opening more popups is not a sign-in any more.
    popup.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  });
}

/** Tell the chrome. Always the conversation's whole browser — its front tab and its strip — because
 *  a background tab's title changing is a change to the strip even though the page in front did
 *  not move. */
function announce(from: Held | Browser): void {
  const snapshot = statusOf(from);
  for (const listener of listeners) listener(snapshot);
}

/** The browser as the chrome and the model see it: the tab in front, and the list of them. */
function statusOf(from: Held | Browser): ViewStatus {
  const browser = "tabs" in from ? from : from.browser;
  const one = browser.active;
  const tabs = browser.tabs.map(
    (tab): TabSummary => {
      const contents = tab.contents.webContents;
      return contents.isDestroyed()
        ? { id: tab.id, title: "", url: "", loading: false }
        : { id: tab.id, title: contents.getTitle(), url: contents.getURL(), loading: contents.isLoading() };
    },
  );
  const blank = {
    owner: browser.owner,
    url: "",
    title: "",
    loading: false,
    canGoBack: false,
    canGoForward: false,
    failure: "",
    viewport: null,
    scale: 1,
    tab: one?.id ?? "",
    tabs,
  };
  if (!one) return blank;
  const contents = one.contents.webContents;
  if (contents.isDestroyed()) return { ...blank, failure: "this view is gone", viewport: one.viewport };
  return {
    ...blank,
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

/** The tab in front, for an act that needs a page. */
function required(at: Target): Held {
  const one = touch(browsers.get(at.owner)?.active ?? undefined);
  if (!one || one.contents.webContents.isDestroyed()) {
    throw new Error(
      "Nothing is open in this conversation's browser yet. `open` a page first.",
    );
  }
  one.browser.touched = Date.now();
  return one;
}

/** Mark a view as spoken to, so eviction can tell it from one nobody is using. Returns what it
 *  was given, so it can wrap a lookup rather than needing a line of its own at each one. */
function touch(one: Held | null | undefined): Held | undefined {
  if (one) one.touched = Date.now();
  return one ?? undefined;
}

/** The target a held view answers to. For the places that have the view and need its address —
 *  a popup being turned into a navigation, for one. */
function ownerOf(one: Held): Target {
  return { owner: one.owner };
}

/** Back, forward, reload — something that starts a navigation, so it waits for one to start the
 *  way a click does. `settle` straight after `goBack` saw a page that had not begun loading yet,
 *  waited its moment, and answered with the URL being left. */
async function step(
  at: Target,
  act: (contents: Electron.WebContents) => void,
): Promise<ViewStatus> {
  const one = required(at);
  act(one.contents.webContents);
  await settleAfterInput(one);
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
 * Settle after something that *might* navigate.
 *
 * A click on a link returns before Chromium has begun loading anything, so `settle` sees a page
 * that is not loading, waits its moment and reports the old URL — and the answer says the click
 * landed on a page that has not moved, which reads as the click having missed. This gives a
 * navigation a short window to start, and then waits for it properly.
 */
async function settleAfterInput(one: Held, since = Date.now()): Promise<Held | null> {
  const contents = one.contents.webContents;
  /* A navigation in this tab, or a new tab — a `target=_blank` link moves nothing where it was
   * clicked, so waiting on this tab alone read "opened in a new tab" as "nothing happened". A tab
   * that opened before this started listening counts: that is the ordinary order. */
  const early = one.browser.lastOpened;
  const started = await new Promise<"here" | Held | null>((resolve) => {
    if (early && early.at >= since) return resolve(early.tab);
    if (contents.isDestroyed()) return resolve(null);
    const done = (answer: "here" | Held | null) => {
      clearTimeout(timer);
      contents.off("did-start-navigation", began);
      one.browser.opened.delete(opened);
      resolve(answer);
    };
    const timer = setTimeout(() => done(null), NAVIGATION_GRACE_MS);
    const began = () => done("here");
    const opened = (tab: Held) => done(tab);
    contents.on("did-start-navigation", began);
    one.browser.opened.add(opened);
  });
  if (started === "here") await settle(one);
  else if (started) {
    // Its load was begun before the tab was announced, so it is either loading now or done.
    await delay(100);
    await settle(started);
  } else await delay(SETTLE_MS);
  return started && started !== "here" ? started : null;
}

/** Wait for the page to stop loading, then a moment more. Bounded, because a page that streams
 *  for ever — a chat, a live feed — never stops loading and must not hold a call open. `most`
 *  is what is left of the act's budget, for a caller that has already spent some of it. */
function settle(one: Held, most = SETTLE_WAIT_MS): Promise<void> {
  const contents = one.contents.webContents;
  const wait = Math.max(0, Math.min(SETTLE_WAIT_MS, most - SETTLE_MS));
  if (contents.isDestroyed() || !contents.isLoading() || wait === 0) return delay(SETTLE_MS);
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      contents.off("did-stop-loading", done);
      void delay(SETTLE_MS).then(resolve);
    };
    const timer = setTimeout(() => {
      contents.off("did-stop-loading", done);
      resolve();
    }, wait);
    contents.on("did-stop-loading", done);
  });
}

function load(one: Held, url: string): Promise<void> {
  const contents = one.contents.webContents;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`${url} did not finish loading in ${LOAD_TIMEOUT_MS / 1000}s — what follows is the page as it stands`)),
      LOAD_TIMEOUT_MS,
    );
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

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** Two frames, or 150ms, whichever comes first — a hidden page may paint no frames at all. */
const NEXT_FRAME =
  "new Promise((resolve) => { requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))); setTimeout(() => resolve(false), 150); })";

const round = (rect: Rect): Rect => ({
  x: Math.round(rect.x),
  y: Math.round(rect.y),
  width: Math.max(0, Math.round(rect.width)),
  height: Math.max(0, Math.round(rect.height)),
});

const same = (a: Rect | null, b: Rect): boolean =>
  !!a && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
