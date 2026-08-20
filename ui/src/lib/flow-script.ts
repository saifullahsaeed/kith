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

/** The step list, under `flow:`, named `loop:` rather than `steps:`. Indented, because it is a
 *  key of the flow block and not of the document. */
const LOOPS = /^[ \t]+loop[ \t]*:/m;

export function hasFlowScript(code: string): boolean {
  return KEY.test(frontmatter(code));
}

/**
 * Does this one repeat, or does it play once and stop?
 *
 * The library takes `steps:` and `loop:` as two names for the same list and runs both the same
 * way — round and round, forever, because that is all the animation loop knows how to do. Which
 * is wrong as a default: an explanation that plays a fourth time is not explaining any more, it
 * is a thing moving in the corner of the page while someone tries to read the paragraph under
 * it.
 *
 * So the two names are made to mean what they say. `steps:` plays once and holds its last frame;
 * `loop:` is him asking for the repeat, for the case where the point *is* the repetition — a
 * poll, a heartbeat, a queue that never empties. Stopping it is `stop`/`play` on the bar either
 * way.
 *
 * Read off the source rather than out of the parsed script, because the parser normalises both
 * names to `steps` and then nothing downstream can tell which one he wrote.
 */
export function loopsForever(code: string): boolean {
  const front = frontmatter(code);
  return KEY.test(front) && LOOPS.test(front);
}

/** The YAML block above the diagram, or "" when there is not one.
 *
 *  An unclosed block is the ordinary state of a fence that is still arriving, and is treated as
 *  all frontmatter: the key is already there or it is not, and waiting for the closing dashes
 *  would only mean deciding late. */
function frontmatter(code: string): string {
  const source = code.replace(/^\s+/, "");
  if (!OPENS.test(source)) return "";
  const body = source.slice(source.indexOf("\n") + 1);
  const end = body.search(CLOSES);
  return end === -1 ? body : body.slice(0, end);
}
