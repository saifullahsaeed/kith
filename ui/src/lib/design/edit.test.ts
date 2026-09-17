/**
 * Edits written back into the file he wrote, rather than into a copy of it.
 *
 * The whole argument for HTML as the document format rests on this file. A design is worth
 * editing by hand because the diff he reads afterwards says `padding: 16px → 24px` and he can
 * infer what you meant — and that is only true if an edit touches the declaration it changed and
 * nothing else. Reprint the document and the same drag becomes a thousand-line diff with the one
 * real change buried in it, at which point the format might as well have been JSON.
 *
 * So: assertions about what *stayed the same* outnumber the ones about what changed.
 */
import { describe, expect, it } from "vitest";

import { applyStyle, materialiseIds } from "@/lib/design/edit";
import { parseDesign } from "@/lib/design/parse";

/** Every id the parser sees, in the order it mints them. */
function ids(html: string): string[] {
  const out: string[] = [];
  const walk = (node: { id: string; children: { id: string }[] }) => {
    out.push(node.id);
    for (const child of node.children) walk(child as never);
  };
  for (const child of parseDesign(html).root.children) walk(child as never);
  return out;
}

describe("materialiseIds — writing the handles into the file", () => {
  it("leaves a document he already named byte-identical", () => {
    const html = '<div data-k="hero">\n  <p data-k="hero.title">Ship it</p>\n</div>';
    expect(materialiseIds(html)).toBe(html);
  });

  it("names a node he did not, using the id the parser gave it", () => {
    const html = "<div><p>Ship it</p></div>";
    const after = materialiseIds(html);
    for (const id of ids(html)) expect(after).toContain(`data-k="${id}"`);
  });

  it("puts the id on the node the parser meant, not the one next to it", () => {
    // The failure this is really guarding: minting used to run post-order, so the deepest node
    // was k1 and the outermost frame had the highest number, while a source scan numbers them
    // the other way round. Every patch past the first nesting would have landed one node off.
    const after = materialiseIds("<div><span>a</span><em>b</em></div>");
    const order = ids("<div><span>a</span><em>b</em></div>");
    expect(after.indexOf(`data-k="${order[0]}"`)).toBeLessThan(after.indexOf(`data-k="${order[1]}"`));
    expect(after).toMatch(new RegExp(`<div[^>]*data-k="${order[0]}"`));
    expect(after).toMatch(new RegExp(`<span[^>]*data-k="${order[1]}"`));
  });

  it("changes nothing but the attribute it adds", () => {
    const html = '<div class="card"  style="gap:4px" >\n  <p>hi</p>\n</div>';
    const after = materialiseIds(html);
    expect(after).toContain('class="card"  style="gap:4px"');
    expect(after).toContain("\n  <p");
  });

  it("does not name a tag the parser unwraps", () => {
    const after = materialiseIds("<form><div>hi</div></form>");
    expect(after).toMatch(/<form>/);
    expect(after).not.toMatch(/<form[^>]*data-k/);
  });

  it("does not reach inside a dropped subtree", () => {
    const after = materialiseIds('<div><script>var a = "<p>x</p>";</script></div>');
    expect(after).toContain('var a = "<p>x</p>";');
    expect(after).not.toMatch(/<p[^>]*data-k/);
  });

  it("is idempotent, so opening a design twice does not renumber it", () => {
    const once = materialiseIds("<div><p>hi</p><span>there</span></div>");
    expect(materialiseIds(once)).toBe(once);
  });
});

describe("applyStyle — the smallest change that expresses the edit", () => {
  it("adds a style attribute to a node that had none", () => {
    expect(applyStyle('<div data-k="a">hi</div>', "a", { padding: "16px" })).toBe(
      '<div data-k="a" style="padding: 16px">hi</div>',
    );
  });

  it("changes one declaration and leaves its neighbours alone", () => {
    const after = applyStyle('<div data-k="a" style="display:flex; gap:12px; padding:16px"></div>', "a", {
      gap: "16px",
    });
    expect(after).toContain("display: flex");
    expect(after).toContain("gap: 16px");
    expect(after).toContain("padding: 16px");
    expect(after).not.toContain("gap: 12px");
  });

  it("removes a declaration when the value is null", () => {
    const after = applyStyle('<div data-k="a" style="gap:12px; padding:16px"></div>', "a", { gap: null });
    expect(after).not.toContain("gap");
    expect(after).toContain("padding: 16px");
  });

  it("drops the attribute entirely when the last declaration goes", () => {
    expect(applyStyle('<div data-k="a" style="gap:12px">hi</div>', "a", { gap: null })).toBe(
      '<div data-k="a">hi</div>',
    );
  });

  it("touches nothing else in the document", () => {
    const html = '<section>\n  <div data-k="a" style="gap:4px">\n    <p data-k="b">hi</p>\n  </div>\n</section>';
    const after = applyStyle(html, "a", { gap: "8px" });
    expect(after).toContain('<p data-k="b">hi</p>');
    expect(after).toContain("<section>\n  <div");
    expect(after.split("\n")).toHaveLength(html.split("\n").length);
  });

  it("changes the node it was asked for when two share a tag", () => {
    const html = '<div data-k="a" style="gap:4px"></div><div data-k="b" style="gap:4px"></div>';
    const after = applyStyle(html, "b", { gap: "8px" });
    expect(after).toBe('<div data-k="a" style="gap:4px"></div><div data-k="b" style="gap: 8px"></div>');
  });

  it("refuses to write a property the parser would drop", () => {
    // Otherwise the file on disk holds something the editor will never show again — and worse,
    // something a different renderer might honour.
    const after = applyStyle('<div data-k="a"></div>', "a", { behavior: "url(x.htc)" });
    expect(after).not.toContain("behavior");
  });

  it("refuses to write a value the parser would drop", () => {
    const after = applyStyle('<div data-k="a"></div>', "a", { "background-image": "url(https://evil.example/p.png)" });
    expect(after).not.toContain("url(");
  });

  it("leaves the document alone when the id is not in it", () => {
    const html = '<div data-k="a"></div>';
    expect(applyStyle(html, "nope", { gap: "8px" })).toBe(html);
  });

  it("survives a round trip through the parser", () => {
    const after = applyStyle('<div data-k="a" style="display:flex"></div>', "a", { gap: "16px" });
    const node = parseDesign(after).root.children[0];
    expect(node.style).toEqual({ display: "flex", gap: "16px" });
  });
});
