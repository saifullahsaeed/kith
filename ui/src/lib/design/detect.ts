/**
 * Whether a deliverable is a thing the design editor can open.
 *
 * This is the whole of the feature's "how does a design get created" story, and it is
 * deliberately nothing: there is no design tool, no `kind: "design"`, no migration and no enum
 * for him to pick the right member of. He writes an HTML file and attaches it to a task, both of
 * which he does already, and the editor notices.
 *
 * That choice is the lesson of `delegate_subtask`, which shipped complete and was never called,
 * against the ```html canvas, which worked the day it landed because the fence was already what
 * he wrote. A surface that requires wanting a new shape does not get used; one that recognises
 * the shape already being produced does. `looksRenderable` in `canvas.ts` makes the same bet from
 * the other end of the same output.
 *
 * Judged on the path rather than the contents, and that is a deliberate difference from
 * `looksRenderable`. The canvas has the text in hand — it is deciding about a fence already in
 * the reply. A deliverable row has a path and a title, and reading every file on a task to decide
 * which buttons to draw would be a request per row for a question whose answer is usually no. The
 * page itself reads the file and says what it found, which is where a wrong guess is cheap.
 */

/** Extensions a design lives in. Only HTML: the document format *is* the dialect. */
const DESIGN_SUFFIX = /\.html?$/i;

export function looksLikeADesign(kind: string, content: string): boolean {
  if (kind !== "file") return false;
  const path = content.trim();
  if (!path || path.length > 400) return false;
  return DESIGN_SUFFIX.test(path);
}
