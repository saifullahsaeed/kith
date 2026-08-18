import { describe, expect, it } from "vitest";
import { escapeHtml, grammarFor, highlight } from "@/lib/highlight";

const JSON_BLOCK = `[
  { "id": "jira-tracker", "name": "Jira & Confluence Integration" }
]`;

describe("highlighting code", () => {
  it("colours a fenced block whose language is named", () => {
    const html = highlight(JSON_BLOCK, "json");
    expect(html).toContain("hljs-");           // it actually marked something up
    expect(html).toContain("jira-tracker");    // and kept the content
  });

  it("falls back to detecting the language when the fence did not name one", () => {
    expect(highlight("SELECT id FROM users WHERE id = 1;")).toContain("hljs-");
  });

  it("an unknown language does not throw it away", () => {
    expect(highlight("hello world", "not-a-real-language")).toContain("hello world");
  });

  it("escapes markup in the source rather than rendering it", () => {
    // The whole reason this returns a string into dangerouslySetInnerHTML.
    const html = highlight("<img src=x onerror=alert(1)>", "json");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("leaves something enormous unlit rather than stalling the message", () => {
    const huge = "x".repeat(300_001);
    expect(highlight(huge, "json")).toBe(escapeHtml(huge));
  });

  it("keeps the ampersand in the sample that is on screen", () => {
    expect(highlight(JSON_BLOCK, "json")).toContain("&amp;");
  });
});

describe("choosing a grammar for a block that is still being typed", () => {
  it("takes the languages he actually writes", () => {
    // Measured from 224 transcripts; `text` is the commonest label by far and must resolve,
    // otherwise 71% of blocks would fall into auto-detection.
    for (const l of ["text", "python", "bash", "json", "ts", "tsx", "yaml", "sql", "diff"]) {
      expect(grammarFor(l), l).toBe(l);
    }
  });

  it("refuses an unlabelled or unknown fence rather than guessing", () => {
    // Guessing re-runs per token and changes its mind as the block grows — flickering colours.
    expect(grammarFor(undefined)).toBeNull();
    expect(grammarFor("")).toBeNull();
    expect(grammarFor("mermaid")).toBeNull();
  });

  it("renders `text` as escaped plain, not as some guessed language", () => {
    const html = highlight('{"a": 1}', "text");
    expect(html).not.toContain("hljs-");
    expect(html).toContain("&quot;a&quot;");
  });
});
