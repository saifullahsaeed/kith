/**
 * The half of the design surface that is presentation, plus the one part of it that is not.
 *
 * `scopeStyleSheet` is the exception. His stylesheet becomes real CSS in the app's own document,
 * so his rules and ours share a cascade, and scoping is the only thing keeping `.card{display:none}`
 * a statement about his design rather than about the page around it. `parse.ts` refuses the
 * selectors that would climb out; this confines the ones that stayed.
 */
import { describe, expect, it } from "vitest";

import { parseDesign } from "@/lib/design/parse";
import { attrsToProps, elementName, scopeStyleSheet, styleToCss, styleToReact } from "@/lib/design/render";

describe("styleToReact", () => {
  it("camel-cases the properties React insists on", () => {
    expect(styleToReact({ "background-color": "red", "border-top-left-radius": "4px" })).toEqual({
      backgroundColor: "red",
      borderTopLeftRadius: "4px",
    });
  });

  it("leaves a custom property alone, which is what makes a token resolve", () => {
    // `--kith-accent` is defined by the app, so a design referring to one follows a theme
    // switch without anything being told about it. Camel-casing this would break that quietly.
    expect(styleToReact({ "--kith-gap": "4px", color: "var(--kith-accent)" })).toEqual({
      "--kith-gap": "4px",
      color: "var(--kith-accent)",
    });
  });

  it("round-trips back to css for a patch", () => {
    expect(styleToCss({ display: "flex", gap: "12px" })).toBe("display: flex; gap: 12px");
  });
});

describe("scopeStyleSheet", () => {
  it("confines every rule to the design", () => {
    expect(scopeStyleSheet(".card { gap: 4px }", ".d1")).toBe(".d1 .card { gap: 4px }");
  });

  it("confines each selector in a list, not just the first", () => {
    // The mistake worth a test: prefixing the prelude as one string gives
    // `.d1 .a, .b`, and `.b` is then loose in the page.
    const scoped = scopeStyleSheet(".a, .b { gap: 4px }", ".d1");
    expect(scoped).toContain(".d1 .a");
    expect(scoped).toContain(".d1 .b");
    expect(scoped).not.toMatch(/,\s*\.b\s*{/);
  });

  it("reaches inside a media query", () => {
    const scoped = scopeStyleSheet("@media (max-width: 600px) {\n.card { gap: 4px }\n}", ".d1");
    expect(scoped).toContain("@media (max-width: 600px)");
    expect(scoped).toContain(".d1 .card");
  });

  it("scopes what the sanitiser hands it, end to end", () => {
    const { sheet } = parseDesign("<style>.card{padding:16px} @media (min-width:900px){.card{padding:24px}}</style>");
    const scoped = scopeStyleSheet(sheet, ".d1");
    expect(scoped.match(/\.d1 \.card/g)).toHaveLength(2);
  });
});

describe("svg, which the html parser lowercased on the way in", () => {
  it("puts the capitals back in an element name", () => {
    expect(elementName("clippath")).toBe("clipPath");
    expect(elementName("lineargradient")).toBe("linearGradient");
    expect(elementName("div")).toBe("div");
  });

  it("puts them back in an attribute too", () => {
    expect(attrsToProps({ viewbox: "0 0 10 10", "stroke-width": "2" })).toEqual({
      viewBox: "0 0 10 10",
      "stroke-width": "2",
    });
  });

  it("drops class, which is passed separately", () => {
    expect(attrsToProps({ class: "card", alt: "x" })).toEqual({ alt: "x" });
  });
});
