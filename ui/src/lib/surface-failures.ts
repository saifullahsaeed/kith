/**
 * Make a failure that nothing caught say so, instead of vanishing.
 *
 * A rejected promise started with `void something()` — and there are forty-six of those —
 * produces no error, no message and no sign at all. The button appears to do nothing. That
 * is the hardest class of bug to report, because the person using it has nothing to report:
 * they clicked, and there was silence.
 *
 * Chasing every call site would be the wrong fix — it is forty-six changes today and one
 * more every time someone adds a button. One listener covers all of them, including the ones
 * not written yet, and turns "it did nothing" into a line on the console with a stack.
 *
 * Deliberately console-only rather than a toast. A rejection is often benign — an aborted
 * fetch when a panel closes, a request cancelled by a reload — and a modal for each would
 * train everyone to dismiss them without reading. What matters is that the record exists when
 * someone goes looking.
 */
export function surfaceFailures(): void {
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    const detail = reason instanceof Error ? (reason.stack ?? reason.message) : String(reason);
    console.error("[kith] a promise failed and nothing was watching:", detail);
  });

  window.addEventListener("error", (event) => {
    // Resource errors (a broken <img>, a script that 404s) arrive here too and carry no
    // Error object. Worth logging, not worth dressing up as a crash.
    if (!event.error) {
      console.warn("[kith] a resource failed to load:", event.filename || event.message);
      return;
    }
    console.error("[kith] uncaught:", event.error.stack ?? event.error.message);
  });
}
