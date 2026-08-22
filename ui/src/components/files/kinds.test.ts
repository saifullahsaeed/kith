/**
 * What a file is, decided from its name.
 *
 * The table is the whole of this module's behaviour, and the way it fails is silent: a kind that
 * is missing does not error, it falls through to `plain` and the file is read as text. That is how
 * a four-megabyte `.mp4` came back as "that file is too big to open here (3975240 bytes; max
 * 2000000)" — two wrong statements in one line, from one absent row.
 *
 * So the cases here are mostly about the fall-through: which extensions must *not* reach it, and
 * which must, and the two that sit deliberately on the edge.
 */
import { describe, expect, it } from "vitest";

import { classify, looksBinary, looksTooBig, skipTextRead } from "./kinds";

describe("what the browser can show itself", () => {
  it("knows a video from its name", () => {
    for (const name of ["bg.mp4", "clip.webm", "a/b/take.mov", "loop.m4v"]) {
      expect(classify(name).type, name).toBe("video");
    }
  });

  it("knows sound", () => {
    for (const name of ["voice.mp3", "note.m4a", "raw.wav", "tune.flac"]) {
      expect(classify(name).type, name).toBe("audio");
    }
  });

  it("does not read media as text", () => {
    // The bug in one assertion. `plain` here means a text read, and a text read means the
    // size limit for text applied to something that was never text.
    for (const name of ["bg.mp4", "voice.mp3", "shot.png", "paper.pdf"]) {
      expect(skipTextRead(name), name).toBe(true);
    }
  });
});

describe("the two that sit on the edge", () => {
  it("treats an SVG as a picture but still reads its source", () => {
    // It is a picture first and markup second, and the source is what you want half the time.
    expect(classify("logo.svg").type).toBe("image");
    expect(skipTextRead("logo.svg")).toBe(false);
  });

  it("treats a page as something to look at, with source available", () => {
    expect(classify("index.html").type).toBe("page");
    expect(skipTextRead("index.html")).toBe(false);
  });
});

describe("everything else", () => {
  it("still reaches code and plain", () => {
    expect(classify("main.py")).toMatchObject({ type: "code", lang: "python" });
    expect(classify("notes.md").type).toBe("markdown");
    expect(classify("whatever.xyz").type).toBe("plain");
  });

  it("is not fooled by an extension inside a folder name", () => {
    expect(classify("mp4/notes.md").type).toBe("markdown");
  });

  it("reads the last extension, not the first", () => {
    expect(classify("archive.mp4.txt").type).toBe("plain");
  });

  it("copes with no extension at all", () => {
    expect(classify("LICENSE").type).toBe("plain");
  });
});

describe("reading the server's refusals", () => {
  it("recognises not-text", () => {
    expect(looksBinary("binary file")).toBe(true);
  });

  it("recognises both ways the server says too big", () => {
    // Two call sites word it differently — the text read says "too big to open here", the media
    // one says "too big to show here" — and both must reach the panel with the buttons on it.
    expect(looksTooBig("that file is too big to open here (3975240 bytes; max 2000000)")).toBe(true);
    expect(looksTooBig("bg.mp4 is 90,000,000 bytes, too big to show here (max 40,000,000)")).toBe(true);
  });

  it("does not mistake an ordinary failure for either", () => {
    expect(looksTooBig("there's no bg.mp4")).toBe(false);
    expect(looksBinary("there's no bg.mp4")).toBe(false);
  });
});
