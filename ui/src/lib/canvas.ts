/**
 * An ```html fence, turned into something you can look at — and the box it is allowed to move
 * around in.
 *
 * The reason this is plain HTML rather than a `show_canvas` tool with a schema of its own is the
 * same reason the diagram renderer is mermaid: it is what he already writes. Asked to show how
 * something works, he emits a self-contained page — markup, a `<style>`, a bit of animation —
 * without being told to, and has done since long before this app existed. A tool with a bespoke
 * shape has to be wanted before it gets used, and the evidence in this codebase is that wanting
 * is not something a persona line reliably installs. So: no new syntax, no new tool. The fence
 * he was already going to write stops being a wall of source.
 *
 * Everything here is separated from the component for the reason `diagram.ts` was: none of it is
 * about React, and two of the three are decisions that want asserting rather than reading.
 * `looksRenderable` is a judgement about someone else's output, and `sealedDocument` is a
 * security boundary.
 *
 * ── The boundary ────────────────────────────────────────────────────────────────────────────
 *
 * He reads the open web, and he writes this HTML. Those two facts together mean the page in the
 * frame is not trusted content: a hostile page he read can influence what he writes, and the
 * renderer it would run in sits next to a backend that already trusts this origin. So the frame
 * gets a null origin (`allow-scripts` *without* `allow-same-origin`) and a policy that denies
 * every way of fetching anything. Animation is arithmetic and needs neither, so nothing worth
 * having is lost — but a canvas that pulls its own live data is off the table by construction,
 * and that is the trade, stated once, here.
 *
 * ── What comes next ─────────────────────────────────────────────────────────────────────────
 *
 * The canvas that answers back — controls whose values ride into the next turn — is an addition
 * to this frame rather than a replacement for it, and the shape here is chosen to keep it that
 * way. A sandboxed frame may always `postMessage` to its parent, and CSP's fetch directives have
 * no opinion about that, so the channel it needs is already open and simply not listened to. The
 * work it adds is a versioned envelope and a validator on this side; none of it is work this
 * file has to be undone for. `canvas.test.ts` asserts that, so a later edit that quietly closes
 * the door fails a test instead of being discovered a month later.
 */
import { BRIDGE, canvasTokens } from "@/lib/canvas-bridge";
import { PALETTE, type Palette } from "@/lib/kith-palette";

/**
 * What the frame may do, as the `sandbox` attribute.
 *
 * Exactly one capability. Deliberately *not* `allow-same-origin` — with it, the page inside
 * shares this app's origin and can reach the backend with the session it already trusts, which
 * is the entire thing being prevented. The omissions matter too: no `allow-popups`, so it cannot
 * open a window; no `allow-top-navigation`, so it cannot replace the app with something else;
 * no `allow-forms`, so there is nothing to submit; no `allow-modals`, so `alert` does nothing
 * rather than freezing the thread it was called from.
 */
export const FRAME_SANDBOX = "allow-scripts";

/**
 * The policy the document carries, as defence in depth behind the sandbox.
 *
 * `default-src 'none'` is the whole of it — connect, frame, worker, form and object all inherit
 * that denial, so a canvas cannot fetch, cannot open a socket, cannot embed anything. The
 * exceptions are the three things a drawing genuinely needs and none of which leave the page:
 * inline style, inline script, and data-URI images, fonts and media.
 *
 * `'unsafe-eval'` is not among them. Nothing he writes for this needs it — there are no
 * libraries to load, since there is no network to load them from — and leaving it out means the
 * one construct that turns arbitrary text into running code stays unavailable.
 */
const POLICY = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline'",
  "img-src data: blob:",
  "font-src data:",
  "media-src data: blob:",
].join("; ");

/**
 * Is this fence a drawing, or is it a listing?
 *
 * The question mermaid never had to ask. A ```mermaid block is always a picture; a ```html block
 * is a picture *sometimes*, and the rest of the time it is him showing you markup — a component
 * he is about to write, a snippet from a page he read. Rendering that second kind replaces the
 * thing you asked to see with the word "hi" in a box, which is a worse failure than never
 * rendering at all.
 *
 * The discriminator is behaviour. A whole document is a drawing by definition. Short of that, a
 * fragment counts only if it brought its own style, script or vector with it — because that is
 * what he attaches when he means *look at this*, and what he leaves off when he means *here is
 * the markup*. Bare markup stays a listing.
 *
 * It is a heuristic and it will be wrong occasionally in both directions, which is why the
 * component keeps a source toggle permanently within reach rather than treating this as final.
 */
export function looksRenderable(code: string): boolean {
  const source = code.trim().toLowerCase();
  if (!source) return false;
  if (/<!doctype\s+html/.test(source) || /<html[\s>]/.test(source)) return true;

  const behaves = /<(style|script|svg|canvas)[\s>]/.test(source);
  if (!behaves) return false;
  return /<(body|div|main|section|article|figure|header|footer|nav|aside|canvas|svg|table|ul|ol|p|h[1-6]|span|button|input|form|img|pre|a)[\s>]/.test(
    source,
  );
}

/** The blocks whose halves have to match before a document is worth mounting. */
const PAIRED = ["html", "body", "script", "style"] as const;

/**
 * Has he finished writing it?
 *
 * A fence arrives a token at a time, and the diagram renderer's answer to that — keep the last
 * drawing that worked, swap when the next one parses — is right here too but has a sharper edge.
 * A half-written mermaid diagram costs a failed parse. A half-written *document* mounts: the
 * browser will happily render `<script>const x =` by silently discarding the unfinished script,
 * so a canvas built mid-stream is not an error, it is a page that looks finished and does
 * nothing. Worse, every token that follows tears the frame down and builds it again, restarting
 * the animation on each one.
 *
 * So the frame waits for the blocks to close. Counting rather than parsing, because a partial
 * document is exactly the input a parser is entitled to correct on the way in — `DOMParser` will
 * close the tags for you and report a document that looks complete.
 *
 * A fragment that opened none of these is called complete, because nothing about its text can
 * distinguish "finished" from "still arriving"; there, the settle timer in the component is what
 * decides.
 */
export function isComplete(code: string): boolean {
  return PAIRED.every((tag) => {
    const opened = code.match(new RegExp(`<${tag}[\\s>]`, "gi"))?.length ?? 0;
    const closed = code.match(new RegExp(`</${tag}\\s*>`, "gi"))?.length ?? 0;
    return closed >= opened;
  });
}

/**
 * The page he wrote, wrapped in the policy and given this app's colours.
 *
 * Parsed rather than pattern-matched, for the reason `naturalSize` gives in `diagram.ts`: the
 * input is a whole document sometimes and a fragment the rest of the time, and the branch that
 * decides which — then splices a `<meta>` into the right place in each — is a regex walking
 * through markup, which is how you eventually eat something you did not mean to. `DOMParser`
 * normalises both shapes into the same document, and scripts parsed this way never run, so this
 * costs nothing to do to untrusted input.
 *
 * The policy goes first because a CSP `<meta>` governs only what follows it. The stylesheet goes
 * second because it is a floor, not a rule: it exists so a canvas that sets no colours at all
 * still looks like it belongs in the message rather than like a white rectangle punched through
 * the page, and anything he writes himself comes later in the document and wins.
 */
export function sealedDocument(code: string, theme: Palette): string {
  const parsed = new DOMParser().parseFromString(code, "text/html");
  const head = parsed.head ?? parsed.documentElement.insertBefore(
    parsed.createElement("head"),
    parsed.documentElement.firstChild,
  );

  const style = parsed.createElement("style");
  style.textContent = groundwork(theme);
  head.prepend(style);

  const policy = parsed.createElement("meta");
  policy.setAttribute("http-equiv", "Content-Security-Policy");
  policy.setAttribute("content", POLICY);
  head.prepend(policy);

  // The wire out. In the head, and ahead of anything he wrote, because it installs `window.kith`
  // — a page that calls `kith.report(...)` from its own top-level script would find nothing there
  // if this ran afterwards, and that failure would look like the feature being broken rather than
  // mis-ordered. It reads the document lazily, so being parsed before there is a body costs it
  // nothing. See `canvas-bridge.ts`.
  const bridge = parsed.createElement("script");
  bridge.textContent = BRIDGE;
  head.append(bridge);

  return `<!doctype html>${parsed.documentElement.outerHTML}`;
}

/**
 * The colours and the reset the frame starts from.
 *
 * A separate document cannot inherit `index.css`, so every token it might want has to arrive as
 * text. Exposed as `--kith-*` custom properties as well as being applied, because the useful
 * case is a canvas that quietly matches the app without having been told what the app looks
 * like — and the occasional one that wants the accent on purpose can ask for it by name.
 */
function groundwork(theme: Palette): string {
  // By value rather than by identity: a caller that spread or cloned the palette on the way in
  // would otherwise get light form controls on a dark canvas, which is a bug you only notice
  // once there is a `<select>` in a drawing.
  const dark = theme.background === PALETTE.dark.background;
  const tokens = Object.entries(canvasTokens(theme))
    .map(([name, value]) => `  --kith-${name}: ${value};`)
    .join("\n");
  return `
:root {
  color-scheme: ${dark ? "dark" : "light"};
${tokens}
}
* { box-sizing: border-box; }
html { min-height: 100%; background: var(--kith-bg); }
html, body { margin: 0; padding: 0; }
body {
  background: transparent;
  color: var(--kith-text);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  overflow: auto;
}
a { color: var(--kith-accent); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
`.trim();
}
