/**
 * The text-level expansion, against the fixture `check_design.py` is held to as well.
 *
 * Two expansions exist because two renderers do — the editor in the app and the checker in a
 * bare Chromium — and the bug this surface has produced over and over is those two drawing
 * different screens from one file. So the expected outputs live in one JSON file and both
 * languages assert against it; `server/tests/test_a_screen_uses_parts_by_reference.py` is the
 * other half.
 */
import { describe, expect, it } from "vitest";

import { expandText, fillHoles, openAndClose, type Parts } from "@/lib/design/compose";
import fixture from "@/lib/design/expand.fixture.json";

const parts: Parts = Object.fromEntries(Object.entries(fixture.parts).map(([name, html]) => [name, { html }]));

describe("expanding <k-use> into markup", () => {
  for (const one of fixture.cases) {
    it(one.why, () => {
      expect(expandText(one.input, parts)).toBe(one.output);
    });
  }
});

describe("the pieces", () => {
  it("a hole is a lookup, never an expression", () => {
    expect(fillHoles("{{ 1 + 1 }} {{a}}", { a: "x" })).toBe("{{ 1 + 1 }} x");
  });

  it("matches props case-blind, because the HTML parser lowercased the attribute", () => {
    expect(fillHoles("{{isActive}}", { isactive: "yes" })).toBe("yes");
  });

  it("only rewrites self-closing on the two tags it owns", () => {
    expect(openAndClose('<k-use name="a"/><div/>')).toBe('<k-use name="a"></k-use><div/>');
  });
});
