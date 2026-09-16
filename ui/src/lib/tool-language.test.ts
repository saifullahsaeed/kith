import { describe, expect, it } from "vitest";

import { withoutPictureBlocks } from "@/components/assistant-ui/tool-result";
import { summaryLine } from "@/lib/tool-language";

/**
 * A batch read's text half, once the pictures are drawn above it.
 *
 * Each image's block is the same sentence written to him — "Look at the image below and
 * describe or judge what you actually see" — so with the picture on screen the block is noise.
 * Everything else in the batch is a real file and has to survive intact.
 */
describe("the leftover text of a batch read", () => {
  const text = [
    "===== /tmp/a.png =====\nLook at the image below and describe or judge what you actually see.",
    "===== /tmp/notes.txt =====\nline one\n\nline two",
    "===== /tmp/b.png =====\nLook at the image below and describe or judge what you actually see.",
  ].join("\n\n");

  it("drops the blocks whose pictures are shown", () => {
    const left = withoutPictureBlocks(text, [{ path: "/tmp/a.png" }, { path: "/tmp/b.png" }]);
    expect(left).not.toContain("/tmp/a.png");
    expect(left).not.toContain("/tmp/b.png");
  });

  it("keeps a non-image file whole, blank lines and all", () => {
    const left = withoutPictureBlocks(text, [{ path: "/tmp/a.png" }, { path: "/tmp/b.png" }]);
    // The split is on a blank line *followed by a heading*, so a blank line inside a file's
    // own contents must not end its block.
    expect(left.trim()).toBe("===== /tmp/notes.txt =====\nline one\n\nline two");
  });

  it("leaves text alone when nothing was shown", () => {
    expect(withoutPictureBlocks(text, [])).toBe(text);
  });

  it("leaves a path it was not given", () => {
    const left = withoutPictureBlocks(text, [{ path: "/tmp/somewhere-else.png" }]);
    expect(left).toBe(text);
  });
});

/**
 * The arguments panel stays, and is the reason it stays.
 *
 * It was briefly hidden as "the row above already said it", which was wrong for exactly the
 * case it was hiding: `describeCall` names three paths and then says "+1 more", so on a batch
 * of four the panel is the only place the fourth filename exists at all. Pinned here so nobody
 * removes it again on the same reasoning.
 */
describe("the row's own subject line", () => {
  it("stops naming files after three, which is why the panel keeps the full list", async () => {
    const { describeCall } = await import("@/lib/tool-language");
    const four = ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png", "/tmp/d.png"];
    const subject = describeCall("read_file", { paths: four }).subject;

    expect(subject).toContain("+1 more");
    expect(subject).not.toContain("/tmp/d.png");
  });

  it("names them all when there are few enough", async () => {
    const { describeCall } = await import("@/lib/tool-language");
    const subject = describeCall("read_file", { paths: ["/tmp/a.png", "/tmp/b.png"] }).subject;
    expect(subject).toBe("/tmp/a.png, /tmp/b.png");
  });
});

describe("cutting a call down to one row", () => {
  it("leaves a phrase that already fits", () => {
    expect(summaryLine("ran ./domaincheck2.sh --test")).toBe("ran ./domaincheck2.sh --test");
  });

  /* The case this exists for. A shell subject is whatever a person typed, and what he types is
   * routinely a loop over four `curl`s — whole, one of these came to about a thousand
   * characters in a heading. */
  it("keeps only the first line of a multi-line command", () => {
    expect(summaryLine('ran echo "one"\nfor d in a b c; do\n  curl "$d"\ndone')).toBe(
      'ran echo "one"',
    );
  });

  it("drops a line continuation left dangling by the cut", () => {
    expect(summaryLine("ran ./domaincheck.sh \\\n  a.com b.com")).toBe("ran ./domaincheck.sh");
  });

  it("collapses runs of whitespace", () => {
    expect(summaryLine("ran   whois    -h   iana.org")).toBe("ran whois -h iana.org");
  });

  it("truncates past the limit and marks it", () => {
    const long = summaryLine("ran " + "x".repeat(200));
    expect(long.length).toBeLessThanOrEqual(73);
    expect(long.endsWith("\u2026")).toBe(true);
  });

  /* Cutting mid-path or mid-flag reads as a rendering fault rather than an elision, so the cut
   * moves back to a word boundary when one is close to the end. */
  it("prefers a word boundary near the cut", () => {
    const words = "ran whois --host iana.org --timeout 20 --verbose --retries 3 --quiet extra tail";
    const kept = summaryLine(words).replace(/\u2026$/, "");

    // What survives is a whole prefix of the original, ending where a word ends — not part-way
    // through `--retries` or a path.
    expect(words.startsWith(kept)).toBe(true);
    expect(words[kept.length]).toBe(" ");
    expect(kept.endsWith(" ")).toBe(false);
  });

  /* "" is the caller's signal to fall back to counting, rather than heading a run with a stub. */
  it("gives nothing back when there would be nothing to read", () => {
    expect(summaryLine("  \n  ")).toBe("");
    expect(summaryLine("ok")).toBe("");
  });
});
