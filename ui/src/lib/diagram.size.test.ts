import { describe, expect, it } from "vitest";
import { naturalSize } from "@/lib/diagram";

/** What mermaid actually emits: a viewBox with the real layout, `width="100%"`, and an inline
 *  max-width. The `100%` is what turned a narrow graph into a wall of 80px text. */
const MERMAID_OUTPUT = (w: number, h: number) =>
  `<svg id="d" width="100%" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" style="max-width: ${w}px;"><g/></svg>`;

/** How the browser would lay it out given our CSS: width attr, capped by max-width:100%,
 *  height:auto keeping proportion. */
function laidOut(html: string, column: number) {
  const w = Number(/width="(\d+)"/.exec(html)?.[1] ?? 0);
  const h = Number(/height="(\d+)"/.exec(html)?.[1] ?? 0);
  const scale = Math.min(column / w, 1);
  return { width: Math.round(w * scale), height: Math.round(h * scale), scale };
}

const COLUMN = 1200;

describe("a diagram is never drawn bigger than it was laid out", () => {
  it("a straight vertical chain keeps its own size instead of filling the column", () => {
    // The case in the screenshot: narrow and tall.
    const { html } = naturalSize(MERMAID_OUTPUT(220, 600));
    expect(html).not.toContain('width="100%"');
    const box = laidOut(html, COLUMN);
    expect(box.scale).toBe(1);          // not magnified 5.5x
    expect(box.width).toBe(220);
    expect(box.height).toBe(600);
  });

  it("a genuinely wide diagram still shrinks to fit, in proportion", () => {
    const { html } = naturalSize(MERMAID_OUTPUT(2400, 400));
    const box = laidOut(html, COLUMN);
    expect(box.scale).toBe(0.5);
    expect(box.width).toBe(COLUMN);
    expect(box.height).toBe(200);       // aspect ratio held
  });

  it("a diagram exactly the column width is untouched", () => {
    const box = laidOut(naturalSize(MERMAID_OUTPUT(COLUMN, 300)).html, COLUMN);
    expect(box.scale).toBe(1);
  });

  it("markup it cannot measure is left exactly as it was", () => {
    const junk = "<svg><g/></svg>";
    expect(naturalSize(junk).html).toBe(junk);
  });
});
