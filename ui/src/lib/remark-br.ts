import type { Plugin } from "unified";

/**
 * Turn a literal `<br>` into an actual line break.
 *
 * Not the model doing something odd — this is the standard markdown idiom. A GFM table cell
 * cannot contain a newline, so `<br>` is the only way to put two lines in one, and GitHub,
 * GitLab and most renderers accept it because they allow inline HTML. Kith's renderers run
 * `remark-gfm` and nothing else, so the tag arrived as text and every multi-line table cell in
 * the app read `Cursor (Anysphere)<br>Cognition (Devin)<br>Codeium`.
 *
 * **Deliberately not `rehype-raw`.** That would render *all* embedded HTML, and markdown here
 * carries tool results — web pages he fetched, file contents, shell output — which is exactly
 * the input you do not hand a raw-HTML renderer. This converts one tag with no attributes and
 * touches nothing else, so there is no new surface to reason about.
 *
 * Written against the tree rather than with `unist-util-visit`, which is not a direct
 * dependency: borrowing a transitive one is how a working build breaks on someone else's
 * `npm install`.
 */
const BR = /^<br\s*\/?>$/i;

export const remarkBr: Plugin = () => (tree: unknown) => {
  walk(tree as Node);
};

interface Node {
  type: string;
  value?: string;
  children?: Node[];
}

function walk(node: Node): void {
  if (!node.children) return;
  for (let at = 0; at < node.children.length; at += 1) {
    const child = node.children[at];
    // `html` is what remark calls a raw tag it parsed but will not render.
    if (child.type === "html" && typeof child.value === "string" && BR.test(child.value.trim())) {
      node.children[at] = { type: "break" };
      continue;
    }
    walk(child);
  }
}
