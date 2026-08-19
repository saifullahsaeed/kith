/**
 * The rules that decide whether an ```html fence is a drawing or a listing, and what the frame
 * it goes into is allowed to do.
 *
 * Worth running rather than reading, for two different reasons. `looksRenderable` is a
 * judgement call about someone else's output — get it wrong in one direction and a snippet he
 * was showing you turns into a blank rectangle, wrong in the other and the whole feature never
 * fires. And `sealedDocument` is the security boundary: it is the only thing standing between
 * "he read a web page" and "a web page ran code next to your backend", so what it emits should
 * be asserted, not assumed.
 */
import { describe, expect, it } from "vitest";

import { FRAME_SANDBOX, isComplete, looksRenderable, sealedDocument } from "@/lib/canvas";
import { paletteFor } from "@/lib/kith-palette";

const light = paletteFor(false);

describe("looksRenderable — a drawing, or a listing?", () => {
  it("takes a whole document", () => {
    expect(looksRenderable("<!doctype html><html><body><p>hi</p></body></html>")).toBe(true);
    expect(looksRenderable("<html><body>hi</body></html>")).toBe(true);
  });

  it("takes a fragment that carries its own style or behaviour", () => {
    expect(looksRenderable("<style>div{color:red}</style><div>hi</div>")).toBe(true);
    expect(looksRenderable("<canvas id=c></canvas><script>draw()</script>")).toBe(true);
  });

  it("takes a self-contained drawing", () => {
    expect(looksRenderable('<svg viewBox="0 0 10 10"><circle r="4"/></svg>')).toBe(true);
  });

  it("leaves plain markup as a listing", () => {
    // The case that matters: he is *showing* you HTML, not drawing with it. Rendering this
    // gives you the words "hi" and takes away the thing you asked to see.
    expect(looksRenderable('<div class="card"><span>hi</span></div>')).toBe(false);
    expect(looksRenderable("<p>one</p>\n<p>two</p>")).toBe(false);
  });

  it("leaves anything that is not markup alone", () => {
    expect(looksRenderable("")).toBe(false);
    expect(looksRenderable("   ")).toBe(false);
    expect(looksRenderable("just some text")).toBe(false);
  });
});

describe("isComplete — has he finished writing it?", () => {
  it("is false while a tag is still open", () => {
    expect(isComplete("<html><body><p>hal")).toBe(false);
    expect(isComplete("<style>body{colo")).toBe(false);
    expect(isComplete("<canvas></canvas><script>const x = 1;")).toBe(false);
  });

  it("is true once every block it opened is closed", () => {
    expect(isComplete("<html><body><p>hi</p></body></html>")).toBe(true);
    expect(isComplete("<style>p{color:red}</style><p>hi</p>")).toBe(true);
  });

  it("is true for a fragment that never opened one", () => {
    // Nothing here can be told apart from a finished fragment, so the settle timer is what
    // decides — see `html-canvas.tsx`.
    expect(isComplete("<svg><circle/></svg>")).toBe(true);
  });
});

describe("sealedDocument — what the frame is allowed to do", () => {
  const doc = sealedDocument("<div>hi</div><script>go()</script>", light);

  it("denies everything by default", () => {
    expect(doc).toContain("http-equiv=\"Content-Security-Policy\"");
    expect(doc).toContain("default-src 'none'");
  });

  it("never lets the canvas reach the network", () => {
    // `default-src 'none'` already covers connect/frame/form; these are the ones a permissive
    // edit would most plausibly add back by hand, so they are asserted by name.
    expect(doc).not.toMatch(/connect-src (?!'none')/);
    expect(doc).not.toContain("https:");
    expect(doc).not.toContain("http:");
    expect(doc).not.toContain("'unsafe-eval'");
  });

  it("states the policy before anything it governs", () => {
    // A CSP meta placed after the first script does not apply to that script.
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("go()"));
  });

  it("keeps what he wrote", () => {
    expect(doc).toContain("go()");
    expect(doc).toContain("<div>hi</div>");
  });

  it("hands over the app's colours, since the cascade cannot reach inside", () => {
    expect(doc).toContain(light.text);
    expect(doc).toContain("--kith-accent");
  });

  it("lets his styling win over ours", () => {
    const styled = sealedDocument("<style>body{background:#ff0000}</style><p>hi</p>", light);
    expect(styled.indexOf("--kith-accent")).toBeLessThan(styled.indexOf("#ff0000"));
  });

  it("does not nest a second document inside the first", () => {
    const whole = sealedDocument("<!doctype html><html><body><p>hi</p></body></html>", light);
    expect(whole.match(/<html/g)).toHaveLength(1);
    expect(whole.match(/<body/g)).toHaveLength(1);
    expect(whole).toContain("<p>hi</p>");
  });

  it("gives a bare fragment a document to live in", () => {
    const bare = sealedDocument("<p>hi</p>", light);
    expect(bare.startsWith("<!doctype html>")).toBe(true);
    expect(bare).toContain("<p>hi</p>");
  });
});

describe("the seal leaves room for the canvas to answer back", () => {
  // Option B — a canvas whose controls report their values into the next turn — is an addition
  // to this frame, not a replacement for it. These assert that nothing here forecloses it: a
  // sandboxed frame may always `postMessage` to its parent, and CSP fetch directives do not
  // govern that. If a future edit makes either of these fail, B has just become a rewrite.
  it("does not sandbox away the one channel B needs", () => {
    expect(FRAME_SANDBOX.split(" ")).toEqual(["allow-scripts"]);
    expect(FRAME_SANDBOX).not.toContain("allow-same-origin");
  });

  it("keeps the policy about fetching, not about messaging", () => {
    const doc = sealedDocument("<p>hi</p>", light);
    expect(doc).not.toContain("sandbox ");
  });
});
