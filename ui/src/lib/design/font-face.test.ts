import { describe, expect, it } from "vitest";
import { parseDesign, sanitiseStyleSheet } from "@/lib/design/parse";
import { scopeStyleSheet } from "@/lib/design/render";

const face = '@font-face{font-family:Tajawal;font-style:normal;font-weight:400;font-display:block;src:url(data:font/woff2;base64,d09GMg==) format("woff2");unicode-range:U+0600-06FF,U+0750-077F;}';

describe("embedded design fonts", () => {
  it("keeps a data font through parsing and canvas scoping", () => {
    const design = parseDesign(`<style>${face}body{font-family:Tajawal}</style><p>مرحبًا</p>`);
    const scoped = scopeStyleSheet(design.sheet, ".design");
    expect(scoped).toContain("@font-face");
    expect(scoped).toContain("font-family: Tajawal");
    expect(scoped).toContain('src: url(data:font/woff2;base64,d09GMg==) format("woff2")');
    expect(scoped).toContain("unicode-range: U+0600-06FF,U+0750-077F");
    expect(scoped).toContain(".design { font-family: Tajawal }");
    expect(design.dropped).toEqual([]);
  });

  it("preserves descriptors rather than treating them as nested rules", () => {
    expect(scopeStyleSheet(face, ".design")).toContain('src:url(data:font/woff2;base64,d09GMg==)');
  });

  it("still scopes selectors inside media queries beside a font", () => {
    const sheet = sanitiseStyleSheet(`${face}@media (min-width:600px){.card{padding:24px}}`, []);
    expect(scopeStyleSheet(sheet, ".design")).toContain(".design .card");
  });

  it("refuses remote, local, obfuscated and extra font sources", () => {
    for (const src of ['url(https://example.com/font.woff2)', 'local(Tajawal)', 'url(//example.com/font)', 'url(data:text/html;base64,YWJj)', 'url(data:font/woff2;base64,YWJj) format("woff2"),url(https://example.com/font)', 'url(data:font/woff2;base64,YWJj) /*x*/ url(https://example.com/font)', 'url(data:font/woff2;base64,YWJj)\\65vil']) {
      const dropped: string[] = [];
      expect(sanitiseStyleSheet(`@font-face{font-family:Tajawal;src:${src};}.card{padding:16px}`, dropped)).not.toContain("@font-face");
      expect(dropped.length).toBeGreaterThan(0);
    }
  });

  it("does not let font rules carry ordinary CSS declarations", () => {
    expect(sanitiseStyleSheet('@font-face{font-family:Tajawal;src:url(data:font/woff2;base64,YWJj);background:red}', [])).not.toContain("@font-face");
  });
});
