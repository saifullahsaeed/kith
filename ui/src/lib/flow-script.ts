/**
 * Whether a ```mermaid fence asked to move.
 *
 * `mermaid-animator` reads its choreography from a `flow:` key in mermaid's own YAML
 * frontmatter, which is the whole reason it is worth having: it is not a second language. The
 * fence he writes is a mermaid diagram either way, and mermaid ignores the key — measured, not
 * assumed — so the same source draws as an ordinary still diagram anywhere the animator is not.
 *
 * That is what this predicate is for. It picks which of the two renderers a fence goes to, and
 * it has to be cheap enough to run on every render of every code block, so it is a regex over
 * the head of the string rather than a YAML parse. Being wrong in the lenient direction costs
 * nothing: a fence that claims a flow script and has none renders still, because the animator
 * validates the script itself and the component falls back when it will not compile.
 */

/** Mermaid requires the frontmatter to open on the very first line, so anything else is a
 *  diagram that merely happens to contain three dashes. */
const OPENS = /^---[ \t]*\r?\n/;

/** The closing fence, and the end of anywhere `flow:` could be a top-level key. */
const CLOSES = /^---[ \t]*$/m;

/** Column zero inside the frontmatter. `flow:` nested under something else is not the key the
 *  animator reads, and `flow:` in the diagram body is a node called flow. */
const KEY = /^flow[ \t]*:/m;

export function hasFlowScript(code: string): boolean {
  const source = code.replace(/^\s+/, "");
  if (!OPENS.test(source)) return false;
  const body = source.slice(source.indexOf("\n") + 1);
  const end = body.search(CLOSES);
  // An unclosed block is the ordinary state of a fence that is still arriving. The key is
  // already there or it is not; waiting for the closing dashes would only mean deciding late.
  return KEY.test(end === -1 ? body : body.slice(0, end));
}
