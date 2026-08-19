import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { remarkTexSymbols, symbolsOnly } from "@/lib/remark-tex-symbols";
import { MARKDOWN_REHYPE } from "@/lib/markdown-plugins";

function md(source: string) {
  render(<ReactMarkdown remarkPlugins={[remarkGfm, remarkTexSymbols]}>{source}</ReactMarkdown>);
}

describe("TeX symbols in prose", () => {
  it("renders the arrow the model actually writes", () => {
    // 34 of the 40 real TeX spans in his transcripts are exactly this.
    md("Coder writes code $\\to$ Critic reviews it");
    expect(screen.getByText(/Coder writes code → Critic reviews it/)).toBeTruthy();
    expect(screen.queryByText(/\\to/)).toBeNull();
  });

  it("leaves a money range completely alone", () => {
    // The reason `$` is not a math delimiter here: this is 25 of the spans in the corpus.
    md("Budget is $5,000–$8,000 for the quarter");
    expect(screen.getByText(/\$5,000–\$8,000/)).toBeTruthy();
  });

  it("does not touch structured maths without the rehype half wired in", () => {
    // remark lifts the span out; `rehype-katex` is what typesets it. With only the remark half,
    // nothing should be left showing its dollars either.
    md("This is $\\mathcal{O}(N^2)$ in the worst case");
    expect(screen.queryByText(/\$/)).toBeNull();
  });

  it("handles a run of symbols, and refuses a run containing an unknown one", () => {
    expect(symbolsOnly("\\alpha \\to \\beta")).toBe("α → β");
    expect(symbolsOnly("\\alpha \\frobnicate \\beta")).toBeNull();
  });

  it("needs a backslash, so no amount of dollars alone can trigger it", () => {
    expect(symbolsOnly("5,000–")).toBeNull();
    expect(symbolsOnly("PATH")).toBeNull();
    expect(symbolsOnly("")).toBeNull();
  });

  it("does not reach inside code, where a dollar means a dollar", () => {
    md("Run `echo $\\to$ file` please");
    expect(screen.getByText(/echo \$\\to\$ file/)).toBeTruthy();
  });
});

describe("typeset maths", () => {
  const katex = (source: string) => {
    const { container } = render(
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkTexSymbols]} rehypePlugins={MARKDOWN_REHYPE}>
        {source}
      </ReactMarkdown>,
    );
    return container;
  };

  it("typesets the line that was showing raw", () => {
    const c = katex("Governed by wavelength ($\\lambda/2$) and channel bandwidth ($c / 2B$).");
    // Both spans, though only the first carries a marker — see `blockHasMaths`.
    expect(c.querySelectorAll(".katex").length).toBe(2);
    // No delimiters survive. (`textContent` still holds the TeX source: KaTeX keeps it in a
    // visually-hidden MathML annotation, which is what makes the output copyable and readable
    // to a screen reader.)
    expect(c.textContent).not.toContain("$");
    expect(c.querySelector("annotation")?.textContent).toBe("\\lambda/2");
  });

  it("typesets units and approximations from the same table", () => {
    const c = katex("Even a 1-nanosecond clock jitter causes $\\sim 30\\text{ cm}$ of error.");
    expect(c.querySelectorAll(".katex").length).toBe(1);
    expect(c.textContent).toContain("30");
    expect(c.textContent).toContain("cm");
  });

  it("still refuses to touch money", () => {
    const c = katex("Budget is $5,000–$8,000 and headcount $200k – $250k.");
    expect(c.querySelectorAll(".katex").length).toBe(0);
    expect(c.textContent).toContain("$5,000–$8,000");
    expect(c.textContent).toContain("$200k – $250k");
  });

  it("keeps a lone symbol as a character rather than a typeset island", () => {
    const c = katex("Coder writes code $\\to$ Critic reviews it");
    expect(c.querySelectorAll(".katex").length).toBe(0);
    expect(c.textContent).toContain("→");
  });
});

describe("the two ways this broke in the built app", () => {
  const katex = (source: string) => {
    const { container } = render(
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkTexSymbols]} rehypePlugins={MARKDOWN_REHYPE}>
        {source}
      </ReactMarkdown>,
    );
    return container;
  };

  it("a lone arrow does not drag the prices beside it into the maths", () => {
    // This rendered `$5,000–$8,000` as a formula: `$\to$` carries a backslash, so it promoted
    // the whole paragraph. A symbol is punctuation, not evidence of maths.
    const c = katex("Budget is $5,000–$8,000 and headcount $200k – $250k. Code $\\to$ Critic.");
    expect(c.querySelectorAll(".katex").length).toBe(0);
    expect(c.textContent).toContain("$5,000–$8,000");
    expect(c.textContent).toContain("$200k – $250k");
    expect(c.textContent).toContain("→");
  });

  it("renders $$…$$ as display maths, not as inline with loose dollars around it", () => {
    const c = katex("$$Y_i = H_i \\cdot X_i + N_i$$");
    expect(c.querySelectorAll(".math-display, .katex-display").length).toBeGreaterThan(0);
    expect(c.textContent).not.toContain("$");
  });

  it("still promotes a marker-less span inside a genuine maths block", () => {
    const c = katex("Wavelength ($\\lambda/2$) and bandwidth ($c / 2B$).");
    expect(c.querySelectorAll(".katex").length).toBe(2);
  });
});
