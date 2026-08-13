/**
 * The composer's document and its markdown have to describe the same thing.
 *
 * Everything downstream reads the string, not the document: the wire format, the slash menu, the
 * ledger, and him. So a parser and serialiser that disagree do not fail loudly — text goes in,
 * comes out subtly different, and the difference lands in a message that was already sent. Which
 * is why most of what follows is a round trip rather than a one-way assertion.
 */

import { describe, expect, it } from "vitest";

import { composerSchema, fromMarkdown, markdownOffset, toMarkdown } from "./markdown";

/** Markdown → document → markdown. The whole contract in one function. */
const round = (text: string) => toMarkdown(fromMarkdown(text));

describe("what survives a round trip", () => {
  it("keeps plain prose exactly as it was", () => {
    expect(round("just a sentence")).toBe("just a sentence");
  });

  it("keeps headings, at the level they were written", () => {
    expect(round("## Plan")).toBe("## Plan");
  });

  it("keeps bullet lists", () => {
    expect(round("- one\n- two")).toBe("- one\n- two");
  });

  it("keeps numbered lists, and where they start", () => {
    expect(round("1. first\n2. second")).toBe("1. first\n2. second");
  });

  it("keeps emphasis, inline code and links", () => {
    expect(round("**bold** and *thin* and `code` and [a link](http://x.test)")).toBe(
      "**bold** and *thin* and `code` and [a link](http://x.test)",
    );
  });

  it("keeps a fenced block with its language", () => {
    expect(round("```py\ndef x():\n    pass\n```")).toBe("```py\ndef x():\n    pass\n```");
  });

  it("keeps a blockquote", () => {
    expect(round("> said before")).toBe("> said before");
  });

  it("keeps a table", () => {
    const table = "| a | b |\n| --- | --- |\n| 1 | 2 |";
    expect(round(table)).toBe(table);
  });
});

describe("tables, which is where this gets interesting", () => {
  it("parses a pasted table into real rows and cells", () => {
    const doc = fromMarkdown("| a | b |\n| --- | --- |\n| 1 | 2 |");
    const table = doc.firstChild!;

    expect(table.type.name).toBe("table");
    expect(table.childCount).toBe(2);
    expect(table.firstChild!.firstChild!.type.name).toBe("tableHeader");
    expect(table.lastChild!.firstChild!.type.name).toBe("tableCell");
  });

  it("wraps a cell's text in a paragraph, because a cell holds blocks", () => {
    /* The one place the two shapes genuinely disagree — markdown hands a cell's contents over as
       an inline run, and a Tiptap cell holds blocks. Getting this wrong does not throw; it
       silently drops every cell's content. */
    const cell = fromMarkdown("| a |\n| --- |\n| 1 |").firstChild!.firstChild!.firstChild!;

    expect(cell.firstChild!.type.name).toBe("paragraph");
    expect(cell.textContent).toBe("a");
  });

  it("escapes a pipe inside a cell rather than letting it end the column", () => {
    const doc = fromMarkdown("| a |\n| --- |\n| x \\| y |");
    expect(toMarkdown(doc)).toContain("x \\| y");
    // And it still reads back as one cell rather than two.
    expect(fromMarkdown(toMarkdown(doc)).lastChild!.lastChild!.childCount).toBe(1);
  });

  it("gives a ragged table a full row rather than emitting something unreadable", () => {
    const doc = fromMarkdown("| a | b |\n| --- | --- |\n| 1 |");
    expect(toMarkdown(doc)).toBe("| a | b |\n| --- | --- |\n| 1 |  |");
  });
});

describe("the caret offset the slash menu runs on", () => {
  /* The menu is driven by (text, cursorPosition) where the cursor indexes the string. A document
     has no such index, so it is computed by serialising everything left of the caret. If this is
     wrong the menu filters on the wrong characters, which looks like the menu being broken. */

  it("counts nothing before the first position", () => {
    const doc = fromMarkdown("hello");
    expect(markdownOffset(doc, 0)).toBe(0);
  });

  it("counts the characters typed so far", () => {
    const doc = fromMarkdown("/fold");
    // Position 1 is the start of the paragraph's text; 6 is the end of "/fold".
    expect(markdownOffset(doc, 6)).toBe(5);
    expect(markdownOffset(doc, 3)).toBe(2);
  });

  it("agrees with the length of the whole document at the end", () => {
    const doc = fromMarkdown("## Plan\n\n- one\n- two");
    expect(markdownOffset(doc, doc.content.size)).toBe(toMarkdown(doc).length);
  });

  it("clamps a position past the end instead of throwing", () => {
    /* The caret is reported by the editor and the document is read here; a transaction in between
       can already have shortened it. ProseMirror throws on an out-of-range cut, which would take
       the composer down over a position that was merely stale. */
    const doc = fromMarkdown("hello");
    expect(markdownOffset(doc, 9_999)).toBe(5);
    expect(markdownOffset(doc, -3)).toBe(0);
  });

  it("counts a heading's own marker, because the text he reads contains it", () => {
    const doc = fromMarkdown("## Hi");
    // "## Hi" — three characters of syntax precede the caret sitting after "H".
    expect(markdownOffset(doc, 2)).toBe(4);
  });
});

describe("input that is not really markdown", () => {
  it("returns an empty document for empty text", () => {
    expect(toMarkdown(fromMarkdown(""))).toBe("");
    expect(toMarkdown(fromMarkdown("   "))).toBe("");
  });

  it("keeps a lone asterisk as an asterisk", () => {
    /* Prose containing a stray `*` is prose. Turning it into emphasis and back would change what
       was written, which is the failure mode that makes a rich composer untrustworthy. */
    expect(round("2 * 3 = 6")).toBe("2 \\* 3 = 6");
    expect(fromMarkdown("2 * 3 = 6").textContent).toBe("2 * 3 = 6");
  });

  it("keeps a single newline as a line, not a space", () => {
    /* Markdown says a soft break is a space. A composer says someone pressed Enter and is now
       looking at two lines, and should still be looking at two lines. */
    const doc = fromMarkdown("one\ntwo");
    expect(doc.firstChild!.childCount).toBe(3); // text, break, text
    expect(doc.firstChild!.child(1).type.name).toBe("hardBreak");
  });

  it("never loses text to a malformed table", () => {
    const mangled = "| a | b\n| -- |\nnot a table at all";
    expect(fromMarkdown(mangled).textContent).toContain("not a table at all");
  });
});

describe("the schema", () => {
  it("has no node markdown cannot express", () => {
    /* Underline is the specific one: it renders, it feels like it works, and it serialises to
       nothing — so the emphasis you applied is missing from the message you sent. */
    expect(composerSchema.marks.underline).toBeUndefined();
    expect(Object.keys(composerSchema.marks).sort()).toEqual(["bold", "code", "italic", "link", "strike"]);
  });

  it("stops headings at three", () => {
    expect(round("#### too deep")).toBe("### too deep");
  });
});
