import { describe, expect, it } from "vitest";

import {
  KEYS,
  boxOf,
  chooseOption,
  dispatchClick,
  focusField,
  locate,
  picker,
  pressKey,
  scrollPage,
  typed,
} from "./page-scripts";

/**
 * The scripts that run **inside the page**, held to the one standard a compiler cannot hold them
 * to: that they parse.
 *
 * Everything else in this file is TypeScript and gets checked. These are strings, assembled from
 * template literals with other template literals interpolated into them, and a stray backtick or
 * an unbalanced brace in one is invisible until the moment a person clicks the crosshair — at
 * which point Chromium throws a syntax error into a promise nobody is watching and the feature
 * simply does nothing. That is the failure mode this exists to make loud, and it is exactly the
 * kind of mistake the assembly here invites.
 *
 * `new Function` parses without running: there is no DOM out here and none is wanted. Whether the
 * script does the right thing to a page is `page-scripts.dom.test.ts`'s question, asked of real
 * Chromium.
 */
const SCRIPTS: [string, string][] = [
  ["picker", picker()],
  ["boxOf by selector", boxOf(".card", "")],
  ["boxOf by text", boxOf("", "Sign in")],
  ["locate", locate("", "Sign in")],
  ["dispatchClick", dispatchClick(12, 34)],
  ["focusField by name", focusField("Work email")],
  ["focusField, the focused one", focusField("")],
  ["typed, replacing", typed("hello", "", true)],
  ["typed, inserting", typed("hello", "was", false)],
  ["chooseOption", chooseOption("Canada")],
  ["scrollPage", scrollPage(600)],
  ...Object.entries(KEYS).map(([name, key]): [string, string] => [`pressKey ${name}`, pressKey(key)]),
];

describe("the scripts injected into the page", () => {
  for (const [name, script] of SCRIPTS) {
    it(`${name} parses`, () => {
      expect(() => new Function(script)).not.toThrow();
    });
  }
});

describe("what a page's own text cannot do to them", () => {
  it("survives a quote, a backtick and a brace in the text being looked for", () => {
    /* The text comes from a model's call and the selector can come from a person's clipboard,
     * and both are pasted into a script the page then runs. `JSON.stringify` is what keeps that
     * from being an injection into Kith's own injected script — asserted rather than assumed,
     * because the failure is silent and arbitrary. */
    const nasty = '"); alert(1); //`${x}';
    expect(() => new Function(boxOf("", nasty))).not.toThrow();
    expect(() => new Function(boxOf(nasty, ""))).not.toThrow();
    expect(() => new Function(typed(nasty, nasty, true))).not.toThrow();
    expect(() => new Function(typed(nasty, nasty, false))).not.toThrow();
    expect(() => new Function(focusField(nasty))).not.toThrow();
    expect(() => new Function(chooseOption(nasty))).not.toThrow();
    expect(() => new Function(locate(nasty, nasty))).not.toThrow();
  });
});
