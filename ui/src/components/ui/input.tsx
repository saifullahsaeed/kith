/**
 * The one text-input look.
 *
 * It was three copies of the same class string — settings' chat tab, settings' advanced tab,
 * and the onboarding connect step — two byte-identical and the third the same plus its own
 * additions. Nothing was wrong with any of them; the problem is the arithmetic, since changing
 * how an input looks meant finding all three and a miss shows up as one field in one tab that
 * focuses differently from every other field in the app.
 *
 * A class string rather than a component, deliberately: these are `<input>`, `<textarea>` and
 * `<select>` across the three call sites, and wrapping all of that to share a border is how a
 * primitive ends up with a `kind` prop and a switch inside it.
 */
export const inputClass =
  "w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40";

/** The same field where it holds a number you are meant to compare down a column: right
 *  aligned, monospaced, and visibly dead when the knob it edits is not editable. */
export const numericInputClass =
  "w-full rounded-md border bg-transparent px-3 py-1.5 text-right font-mono text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50";
