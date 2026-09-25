/**
 * What a sealed frame may do, and the palette it is handed — the interface's half of the seal.
 *
 * The only sealed frame left is a plugin's surface: an opaque document Kith did not write,
 * mounted with no same-origin, under the policy in `server/kith/domain/seal.py`. This file used
 * to be split across `canvas.ts` and `canvas-bridge.ts`, which also held the ```html canvas in a
 * reply. That canvas was retired — a reply's HTML is now either source or a design on the canvas
 * (the board) — and these three names are what survived it.
 *
 * `server/tests/test_the_seal_is_stated_once.py` reads `FRAME_SANDBOX` out of this file and
 * asserts it matches the server's, so the two languages cannot drift.
 */

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

/** The envelope version on every message across a frame boundary. Bumped only if the shape
 *  changes incompatibly; an unrecognised version is dropped, which is what makes a stale cached
 *  document harmless rather than confusing. */
export const PROTOCOL = 1;

/**
 * The palette a sealed document sees, as the custom-property names it sees them under.
 *
 * One function, used by every end, and that is the point of it existing at all. The stylesheet
 * baked into a new document and the message that repaints a live one were once written
 * separately and immediately drifted: the tokens are named `bg` and `accent-soft`, the palette
 * calls those `surface` and `accentSoft`, and three of the nine silently failed to travel. A
 * theme switch changed the text and left the ground behind — visible only as "light mode doesn't
 * work", with nothing failing anywhere. Derive every copy from here and that class of bug cannot
 * come back.
 */
export function frameTokens(theme: Record<string, string>): Record<string, string> {
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
