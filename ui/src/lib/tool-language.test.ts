import { describe, expect, it } from "vitest";

import { withoutPictureBlocks } from "@/components/assistant-ui/tool-result";

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
