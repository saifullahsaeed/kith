/**
 * What happens when you paste.
 *
 * The rules are ordered, and every one of them exists because the rule after it would have done
 * something wrong with that input. So these tests are as much about the boundaries between rules
 * as about any rule on its own — the interesting failures are a paste that takes the wrong branch,
 * not one that takes no branch at all.
 */

import { describe, expect, it, vi } from "vitest";

import { fromMarkdown } from "./markdown";
import {
  PASTE_CHAR_LIMIT,
  PASTE_LINE_LIMIT,
  handlePaste,
  isOversized,
  isPastedFile,
  looksLikeMarkdown,
  pastedFile,
} from "./paste";

describe("what counts as too big to type beside", () => {
  it("takes a normal message in its stride", () => {
    expect(isOversized("a couple of sentences about the meter")).toBe(false);
  });

  it("catches one enormous paragraph", () => {
    expect(isOversized("x".repeat(PASTE_CHAR_LIMIT + 1))).toBe(true);
  });

  it("catches many short lines, which fill the box just as completely", () => {
    /* A hundred one-word lines is nowhere near the character limit and completely fills the
       composer. Measuring only characters would let a stack trace through. */
    const lines = Array.from({ length: PASTE_LINE_LIMIT + 1 }, (_, n) => `line ${n}`).join("\n");
    expect(lines.length).toBeLessThan(PASTE_CHAR_LIMIT);
    expect(isOversized(lines)).toBe(true);
  });
});

describe("text that means its markdown, and text that merely contains some", () => {
  it.each([
    ["a heading", "## Plan"],
    ["a bullet", "- one\n- two"],
    ["a numbered list", "1. first"],
    ["a quote", "> said before"],
    ["a fence", "```py\nx = 1\n```"],
    ["a table", "| a | b |\n| --- | --- |"],
    ["an indented bullet", "   - nested"],
  ])("reads %s as markdown", (_what, text) => {
    expect(looksLikeMarkdown(text)).toBe(true);
  });

  it.each([
    ["arithmetic", "2 * 3 = 6"],
    ["an identifier", "some_snake_case_name and another_one"],
    ["ordinary prose", "we should probably fix the meter first"],
    ["a mid-sentence dash", "the meter - which was wrong - is fixed"],
  ])("leaves %s alone", (_what, text) => {
    /* This is the direction that matters. Detecting a list too eagerly rewrites what someone
       pasted, and they do not find out until after it is sent. */
    expect(looksLikeMarkdown(text)).toBe(false);
  });
});

describe("the pasted file", () => {
  it("is named for what it is, and recognised again by that name", () => {
    const file = pastedFile("some text", 1);
    expect(file.name).toBe("pasted-1.txt");
    expect(file.type).toBe("text/plain");
    expect(isPastedFile(file.name)).toBe(true);
  });

  it("does not mistake a file someone actually chose for a paste", () => {
    expect(isPastedFile("notes.txt")).toBe(false);
    expect(isPastedFile("pasted-notes.txt")).toBe(false);
  });
});

// --------------------------------------------------------------------------- //

/** A clipboard, as the handler sees one. */
function clipboard({ text = "", html = "", files = [] as File[] }) {
  const event = {
    preventDefault: vi.fn(),
    clipboardData: {
      files,
      getData: (kind: string) => (kind === "text/html" ? html : text),
    },
  };
  return event as unknown as ClipboardEvent & { preventDefault: ReturnType<typeof vi.fn> };
}

/** A view, as the handler uses one: a schema, a selection to replace, and somewhere for the
 *  dispatched transaction to land. */
function view() {
  const doc = fromMarkdown("");
  const dispatched: unknown[] = [];
  return {
    dispatched,
    state: {
      schema: doc.type.schema,
      tr: {
        replaceSelectionWith(node: unknown) {
          dispatched.push({ kind: "node", node });
          return this;
        },
        replaceSelection(slice: unknown) {
          dispatched.push({ kind: "slice", slice });
          return this;
        },
      },
    },
    dispatch(tr: unknown) {
      void tr;
    },
  };
}

function context() {
  const attached: File[] = [];
  let n = 0;
  return { attached, attach: (file: File) => attached.push(file), ordinal: () => ++n };
}

describe("the rules, in order", () => {
  it("takes files off the clipboard first", () => {
    /* A screenshot is the other thing people try after dragging, and Cmd-V otherwise does nothing
       at all for an image — no error, no attachment, which reads as the app ignoring you. */
    const ctx = context();
    const image = new File(["binary"], "shot.png", { type: "image/png" });
    const event = clipboard({ text: "ignored", files: [image] });

    expect(handlePaste(view() as never, event, ctx)).toBe(true);
    expect(ctx.attached).toEqual([image]);
    expect(event.preventDefault).toHaveBeenCalled();
  });

  it("lifts a large paste out of the message", () => {
    const ctx = context();
    const event = clipboard({ text: "x".repeat(PASTE_CHAR_LIMIT + 1) });

    expect(handlePaste(view() as never, event, ctx)).toBe(true);
    expect(ctx.attached[0].name).toBe("pasted-1.txt");
  });

  it("names each paste after the one before it", () => {
    const ctx = context();
    const big = clipboard({ text: "x".repeat(PASTE_CHAR_LIMIT + 1) });
    handlePaste(view() as never, big, ctx);
    handlePaste(view() as never, clipboard({ text: "y".repeat(PASTE_CHAR_LIMIT + 1) }), ctx);

    expect(ctx.attached.map((f) => f.name)).toEqual(["pasted-1.txt", "pasted-2.txt"]);
  });

  it("prefers the card over markdown parsing when the paste is both", () => {
    /* Order is the design. A thirty-thousand-character document full of headings is still too
       large to type beside, and parsing it into the composer would be the wrong answer twice. */
    const ctx = context();
    const event = clipboard({ text: "## Plan\n" + "x".repeat(PASTE_CHAR_LIMIT) });

    expect(handlePaste(view() as never, event, ctx)).toBe(true);
    expect(ctx.attached).toHaveLength(1);
  });

  it("keeps code that was code where it came from", () => {
    const target = view();
    const event = clipboard({
      text: "def x():\n    return _y_",
      html: "<pre><code>def x():\n    return _y_</code></pre>",
    });

    expect(handlePaste(target as never, event, context())).toBe(true);
    const [dispatch] = target.dispatched as { kind: string; node: { type: { name: string } } }[];
    expect(dispatch.kind).toBe("node");
    expect(dispatch.node.type.name).toBe("codeBlock");
  });

  it("parses a small table into the document", () => {
    const target = view();
    const event = clipboard({ text: "| a | b |\n| --- | --- |\n| 1 | 2 |" });

    expect(handlePaste(target as never, event, context())).toBe(true);
    expect((target.dispatched[0] as { kind: string }).kind).toBe("slice");
  });

  it("hands ordinary prose back to ProseMirror", () => {
    /* Returning false is a real answer, not a fallthrough: ProseMirror inserts plain text better
       than this would, and every paste it can handle is one this cannot get wrong. */
    const target = view();
    const event = clipboard({ text: "we should fix the meter first" });

    expect(handlePaste(target as never, event, context())).toBe(false);
    expect(target.dispatched).toHaveLength(0);
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("does nothing with an empty clipboard", () => {
    expect(handlePaste(view() as never, clipboard({}), context())).toBe(false);
  });
});
