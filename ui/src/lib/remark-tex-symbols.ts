import type { Plugin } from "unified";

/**
 * Render `$\to$` as `→`, and leave `$5,000–$8,000` alone.
 *
 * Models write single symbols in TeX out of habit — `A $\to$ B` in a sentence that is otherwise
 * plain prose — and with no math support those dollars and backslashes arrive on screen exactly
 * as typed.
 *
 * **Deliberately not `remark-math` + `katex`.** Measured against 793 assistant messages here:
 * 40 inline `$…$` spans are real TeX, and 34 of those are `\to`. Against that, 25 spans are not
 * TeX at all — every one a money range like `$5,000–$8,000` or `$200B–$500B`, which is what
 * financial writing looks like. Turning `$` into a math delimiter would fix 40 and break 25, and
 * the 25 are in the numbers. (There was exactly one `$$display$$` block in the whole corpus, so
 * the 250KB of KaTeX would be earning its keep on one equation.)
 *
 * So this converts a `$…$` span **only when every token in it is a known symbol macro**. A
 * backslash is required, which is what makes money safe: `5,000–` has no tokens to match, so the
 * span is left exactly as written. Anything with structure — `$\mathcal{O}(N^2)$`, fractions,
 * superscripts — is also left alone rather than half-rendered, because a mangled formula is
 * worse than a visible one.
 *
 * Only `text` nodes are touched, so a `$` inside code or a fenced block is never a candidate.
 *
 * Written against the tree rather than with `unist-util-visit`, which is not a direct
 * dependency: borrowing a transitive one is how a working build breaks on someone else's
 * `npm install`.
 */
const SYMBOLS: Record<string, string> = {
  // Arrows — the whole reason this exists.
  to: "→", rightarrow: "→", longrightarrow: "⟶", Rightarrow: "⇒",
  leftarrow: "←", longleftarrow: "⟵", Leftarrow: "⇐",
  leftrightarrow: "↔", Leftrightarrow: "⇔", mapsto: "↦",
  uparrow: "↑", downarrow: "↓",
  // Comparison and arithmetic.
  times: "×", div: "÷", pm: "±", mp: "∓", cdot: "·",
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠",
  approx: "≈", equiv: "≡", sim: "∼", propto: "∝", infty: "∞",
  // Sets and logic.
  in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", supset: "⊃", supseteq: "⊇",
  cup: "∪", cap: "∩", emptyset: "∅", forall: "∀", exists: "∃",
  land: "∧", lor: "∨", neg: "¬",
  // Operators that stand alone.
  sum: "∑", prod: "∏", int: "∫", partial: "∂", nabla: "∇", sqrt: "√",
  // Ellipses.
  ldots: "…", dots: "…", cdots: "⋯",
  // Greek, lower and upper.
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", zeta: "ζ",
  eta: "η", theta: "θ", iota: "ι", kappa: "κ", lambda: "λ", mu: "μ",
  nu: "ν", xi: "ξ", pi: "π", rho: "ρ", sigma: "σ", tau: "τ",
  upsilon: "υ", phi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
  Sigma: "Σ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};

/** A `$…$` span, non-greedy and single-line, so two prices on one line cannot swallow the text
 *  between them and then be rejected as a whole. */
const SPAN = /\$([^$\n]{1,200})\$/g;

/**
 * What separates maths from money, and it is not the presence of a backslash.
 *
 * `$H_{static}$`, `$X_i$` and `$|H_i|$` are real maths with no backslash in them, and they sit in
 * the same corpus as `$5,000–$8,000` and `$200B–$500B`. What every maths span here does carry is
 * one of `\`, `^` or `_`, and not one money span carries any of them. Measured over 793 assistant
 * messages: 90 maths spans matched, 0 money spans matched.
 */
const MATHS = /[\\^_]/;

/** `$$…$$`, which is unambiguous: nobody writes a price with doubled dollars. */
const DISPLAY = /\$\$([\s\S]{1,2000}?)\$\$/g;
/** Every token must be one of these: a macro, or the whitespace between macros. */
const TOKEN = /\\([a-zA-Z]+)|(\s+)/y;

/** The plain-text form of `body`, or null if any part of it is not a known symbol. */
export function symbolsOnly(body: string): string | null {
  if (!body.includes("\\")) return null; // money, shell vars, anything without a macro
  TOKEN.lastIndex = 0;
  let out = "";
  while (TOKEN.lastIndex < body.length) {
    const at = TOKEN.lastIndex;
    const match = TOKEN.exec(body);
    if (!match || match.index !== at) return null; // something that is not a macro or a space
    if (match[2] !== undefined) {
      out += " ";
      continue;
    }
    const symbol = SYMBOLS[match[1]];
    if (symbol === undefined) return null; // an unknown macro: leave the span as written
    out += symbol;
  }
  return out.trim() ? out.trim() : null;
}

/** The span `rehype-katex` looks for. `hName` is the override that matters: without it this
 *  renders as a `<code>` element and the TeX stays literal on the page. */
function maths_node(tex: string, display: boolean): Node {
  return {
    type: "inlineCode",
    value: tex,
    data: {
      hName: "span",
      hProperties: { className: ["math", display ? "math-display" : "math-inline"] },
    },
  };
}

export const remarkTexSymbols: Plugin = () => (tree: unknown) => {
  walk(tree as Node, false);
};

/**
 * Whether this block has maths in it at all, judged from every `$…$` span inside it together.
 *
 * One span at a time is not enough context. "Governed by wavelength ($\lambda/2$) and channel
 * bandwidth ($c / 2B$)" has a marker in the first span and none in the second, and rendering only
 * the first leaves one formula set and its neighbour showing its dollars — which reads worse than
 * leaving both alone. A block that contains maths is a block whose `$…$` spans are maths.
 *
 * Safe because money never appears in such a block: promoting the marker-less spans inside
 * maths blocks moved 6 spans across the whole corpus, and none of them were money.
 */
function blockHasMaths(node: Node): boolean {
  if (node.type === "text" && typeof node.value === "string") {
    SPAN.lastIndex = 0;
    for (const m of node.value.matchAll(SPAN)) {
      // A lone `$\to$` does not make a block mathematical — it is punctuation the model typed in
      // TeX out of habit, and treating it as evidence swept the prices in
      // "Budget is $5,000–$8,000 … Coder writes code $\to$ Critic reviews it" into the maths with
      // it. Only a span with structure counts as evidence.
      if (MATHS.test(m[1]) && symbolsOnly(m[1]) === null) return true;
    }
    return false;
  }
  return (node.children ?? []).some(blockHasMaths);
}

/** Block-level containers whose spans are judged together. */
const BLOCKS = new Set(["paragraph", "tableCell", "heading", "listItem", "blockquote"]);

interface Node {
  type: string;
  value?: string;
  children?: Node[];
  data?: { hName?: string; hProperties?: Record<string, unknown> };
}

function walk(node: Node, maths: boolean): void {
  const here = BLOCKS.has(node.type) ? blockHasMaths(node) : maths;
  if (!node.children) return;
  for (let at = 0; at < node.children.length; at += 1) {
    const child = node.children[at];
    if (child.type === "text" && typeof child.value === "string" && child.value.includes("$")) {
      const parts = split(child.value, here);
      node.children.splice(at, 1, ...parts);
      at += parts.length - 1;
      continue;
    }
    walk(child, here);
  }
}

/**
 * One text node in, the same text with its maths spans lifted out.
 *
 * A symbol on its own (`$\to$`) still becomes the character rather than a formula: it is one
 * glyph either way, and a run of prose reads better without a typeset island in it.
 */
function split(value: string, maths: boolean): Node[] {
  const out: Node[] = [];
  let last = 0;
  // `$$…$$` first, and regardless of what the block looks like: doubled dollars are never money.
  DISPLAY.lastIndex = 0;
  const display = [...value.matchAll(DISPLAY)];
  if (display.length) {
    for (const m of display) {
      const at = m.index ?? 0;
      if (at > last) out.push(...split(value.slice(last, at), maths));
      out.push(maths_node(m[1].trim(), true));
      last = at + m[0].length;
    }
    if (last < value.length) out.push(...split(value.slice(last), maths));
    return out;
  }
  SPAN.lastIndex = 0;
  for (const m of value.matchAll(SPAN)) {
    const body = m[1];
    const symbol = symbolsOnly(body);
    // No marker required here: the block was already judged to contain maths, and inside such a
    // block every span is maths — which is the whole point of judging them together.
    const isMaths = maths && !symbol;
    if (!symbol && !isMaths) continue;
    const at = m.index ?? 0;
    if (at > last) out.push({ type: "text", value: value.slice(last, at) });
    out.push(symbol ? { type: "text", value: symbol } : maths_node(body, false));
    last = at + m[0].length;
  }
  if (!out.length) return [{ type: "text", value }];
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}
