/**
 * The one wire out of a sealed canvas, and the thing that refuses to believe most of what comes
 * down it.
 *
 * The frame is opaque by design — see `canvas.ts` for why — which buys safety and costs the two
 * things a drawing in a conversation actually wants. It cannot say how tall it is, so its height
 * is a guess the person has to drag; and nothing they do inside it is knowable, so he can hand
 * over an instrument and never learn which way they turned it.
 *
 * `postMessage` is the only channel a sandboxed frame has, and it is not governed by any of the
 * policy the document carries: CSP's directives are about fetching, and there is no fetch here.
 * So the channel was already open the day the canvas shipped. This module is what finally listens
 * to it, and — more of the code than the listening — what decides which of it is real.
 *
 * ── Why this is the untrusted edge ──────────────────────────────────────────────────────────
 *
 * Everything arriving here was written by a model that reads the open web, running in a frame
 * that was sealed precisely because it is not trusted. So a message is *evidence of an event*,
 * never an instruction and never a value to pass along unexamined. Concretely:
 *
 * * **Identity before content.** The only trustworthy fact about a message from an opaque frame
 *   is which window sent it — `event.origin` is the string "null" for every sandboxed frame on
 *   the page, so it distinguishes nothing. `event.source === frame.contentWindow` is the check.
 * * **A closed vocabulary.** Two message types, both listed here. Anything else is dropped
 *   silently rather than logged, because a page that can make the app write to a console it
 *   shares with the person has already gained something.
 * * **Shapes, not values.** Every field is bounded — the height to a range that fits on a screen,
 *   the state to a fixed number of short keys and primitive values. A canvas cannot send prose,
 *   so it cannot smuggle a paragraph of instructions into a turn dressed as a slider reading.
 *
 * Kept out of the component because none of it is about React, and because a validator that has
 * never been run against a hostile input is a validator nobody has checked.
 */

/** The envelope version. Bumped only if the shape below changes incompatibly; an unrecognised
 *  version is dropped, which is what makes a stale cached canvas harmless rather than confusing. */
export const PROTOCOL = 1;

/** How tall a canvas may claim to be. The floor is about one line of content plus its padding;
 *  the ceiling is a few screens, past which the answer is the full-screen view rather than a
 *  taller box in the middle of a conversation. */
export const HEIGHT_RANGE = [80, 2400] as const;

/** Caps on one report. Thirty-two controls is more than any instrument he has drawn; two hundred
 *  characters is a label or a number, and nothing that could carry an argument. */
const MAX_KEYS = 32;
const MAX_TEXT = 200;
const MAX_TITLE = 80;
const KEY = /^[\w.-]{1,40}$/;

export type CanvasValue = string | number | boolean;

/**
 * The tokens the host may restyle a live canvas with.
 *
 * A closed list, because this is the one message that travels *into* the frame, and its whole job
 * is writing CSS custom properties. Anything not named here is dropped rather than set, so a
 * theme message can never reach a property that means something to the page's own layout.
 */
/**
 * The palette a canvas sees, as the custom-property names it sees them under.
 *
 * One function, used by both ends, and that is the point of it existing at all. The stylesheet
 * baked into a new document and the message that repaints a live one were written separately and
 * immediately drifted: the tokens are named `bg` and `accent-soft`, the palette calls those
 * `surface` and `accentSoft`, and three of the nine silently failed to travel. A theme switch
 * changed the text and left the ground behind — visible only as "light mode doesn't work", with
 * nothing failing anywhere. Derive both from here and that class of bug cannot come back.
 */
export function canvasTokens(theme: Record<string, string>): Record<string, string> {
  return {
    bg: theme.surface,
    line: theme.line,
    text: theme.text,
    dim: theme.dim,
    accent: theme.accent,
    "accent-soft": theme.accentSoft,
    second: theme.second,
    "second-soft": theme.secondSoft,
    muted: theme.muted,
  };
}

/** The message the host sends *into* a canvas when the app's theme changes. Built here so the
 *  two ends of the wire are described in one file. */
export function themeMessage(theme: Record<string, string>, dark: boolean) {
  return { kith: PROTOCOL, type: "theme", dark, tokens: canvasTokens(theme) };
}

export type CanvasMessage =
  | { type: "height"; px: number }
  | { type: "state"; title: string; values: Record<string, CanvasValue> };

/**
 * What, if anything, this message actually is.
 *
 * Returns null for everything it does not recognise, and callers treat null as "nothing
 * happened" — there is no error path, because the sender is not someone we are helping to get it
 * right. A page that sends nonsense simply does not report.
 */
export function readCanvasMessage(data: unknown): CanvasMessage | null {
  if (!data || typeof data !== "object") return null;
  const message = data as Record<string, unknown>;
  if (message.kith !== PROTOCOL) return null;

  if (message.type === "height") {
    const px = Number(message.px);
    if (!Number.isFinite(px)) return null;
    return { type: "height", px: Math.min(HEIGHT_RANGE[1], Math.max(HEIGHT_RANGE[0], Math.round(px))) };
  }

  if (message.type === "state") {
    const raw = message.values;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const values: Record<string, CanvasValue> = {};
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      if (Object.keys(values).length >= MAX_KEYS) break;
      if (!KEY.test(key)) continue;
      if (typeof value === "string") values[key] = value.slice(0, MAX_TEXT);
      else if (typeof value === "number" && Number.isFinite(value)) values[key] = value;
      else if (typeof value === "boolean") values[key] = value;
    }
    if (!Object.keys(values).length) return null;
    const title = typeof message.title === "string" ? message.title.slice(0, MAX_TITLE).trim() : "";
    return { type: "state", title, values };
  }

  return null;
}

/**
 * The script the sealed document carries, which is the other half of the wire.
 *
 * Injected rather than asked for, and that is the lesson of this whole feature written into a
 * design decision. The diagram renderer worked because he already wrote mermaid *and* was told
 * the fence renders; the canvas sat unused for an evening because he was told nothing, and wrote
 * files and shelled out to `open` instead. Requiring a page to call an API it has to remember
 * would repeat that. So the two things worth knowing report themselves:
 *
 * * **Height**, from a `ResizeObserver` on the body. This is what makes a canvas stop being a
 *   guessed rectangle you have to drag — and it is why `groundwork` gives the body no height at
 *   all: a document pinned to its frame reports the frame's height back, which is the question,
 *   not the answer.
 * * **Anything with an id**, on change. A slider, a checkbox, a select — the controls an
 *   instrument is actually made of, captured with no cooperation at all.
 *
 * And one thing he *is* told about, because it cannot be inferred: `kith.report({...})`, for a
 * page whose state is a variable rather than a control. His click-through of the decision loop
 * keeps its step in a closure; nothing can read that from the outside, and guessing from clicks
 * would report which pixel was pressed rather than what it meant.
 *
 * Written as a string of ES5 rather than compiled from a module because it has to survive being
 * concatenated into someone else's document, under a policy that permits inline script and
 * nothing else — no import, no bundler, no source map.
 */
export const BRIDGE = `
(function () {
  var V = ${PROTOCOL};
  function send(message) {
    try { parent.postMessage(message, "*"); } catch (e) { /* no parent to tell */ }
  }

  var lastHeight = 0;
  function reportHeight() {
    // The body, not the document element. The html element carries a min-height so its ground
    // fills the frame, which means asking it how tall the content is returns the frame's height.
    var body = document.body;
    var height = Math.ceil((body && body.scrollHeight) || document.documentElement.scrollHeight || 0);
    if (!height || Math.abs(height - lastHeight) < 2) return;
    lastHeight = height;
    send({ kith: V, type: "height", px: height });
  }

  function controls() {
    var values = {};
    var nodes = document.querySelectorAll("input[id], select[id], textarea[id]");
    for (var i = 0; i < nodes.length && i < ${MAX_KEYS}; i++) {
      var el = nodes[i];
      if (!el.id) continue;
      if (el.type === "radio" || el.type === "checkbox") {
        if (el.type === "checkbox") values[el.id] = !!el.checked;
        else if (el.checked) values[el.name || el.id] = String(el.value);
      } else {
        var n = Number(el.value);
        values[el.id] = el.value !== "" && !isNaN(n) ? n : String(el.value);
      }
    }
    return values;
  }

  var reported = {};
  function reportState() {
    var values = {};
    var found = controls();
    for (var k in found) values[k] = found[k];
    for (var j in reported) values[j] = reported[j];
    for (var one in values) { send({ kith: V, type: "state", title: document.title || "", values: values }); return; }
  }

  /** What a page says about itself when its state is not in a control. */
  window.kith = {
    report: function (patch) {
      if (!patch || typeof patch !== "object") return;
      for (var k in patch) reported[k] = patch[k];
      reportState();
    }
  };

  /* The one message that comes the other way. A theme change used to mean rebuilding the whole
     document and reloading the frame, which restarted every animation in it — you switched to
     dark and the thing you were watching began again. The palette is custom properties, so
     changing them is a repaint and nothing more. Only the names the host knows about are set;
     anything else in the message is ignored. */
  var ALLOWED = ${JSON.stringify(Object.keys(canvasTokens({})))};
  addEventListener("message", function (event) {
    var m = event.data;
    if (!m || typeof m !== "object" || m.kith !== V || m.type !== "theme") return;
    if (event.source !== parent) return;
    var root = document.documentElement;
    for (var i = 0; i < ALLOWED.length; i++) {
      var key = ALLOWED[i];
      var value = m.tokens && m.tokens[key];
      if (typeof value === "string" && value.length < 64) root.style.setProperty("--kith-" + key, value);
    }
    root.style.colorScheme = m.dark ? "dark" : "light";
  });

  addEventListener("input", reportState, true);
  addEventListener("change", reportState, true);

  /* Deferred because this script is in the head, ahead of anything he wrote, so that
     window.kith exists before his own top-level code runs. There is no body to watch yet at
     that point, and watching the document element instead is exactly the measurement mistake
     this is trying to avoid. */
  function watch() {
    if (!document.body) return;
    if (window.ResizeObserver) new ResizeObserver(reportHeight).observe(document.body);
    reportHeight();
    reportState();
  }
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", watch);
  else watch();
  addEventListener("load", function () { reportHeight(); reportState(); });
  setTimeout(function () { reportHeight(); reportState(); }, 80);
})();
`.trim();
