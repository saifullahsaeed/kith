/**
 * Clearing up after a render that went wrong.
 *
 * `mermaid.render` builds its diagram in a scratch element parented to `<body>`, and on failure
 * it draws its own error graphic *into* that element and rethrows — leaving it behind. The
 * graphic is a cartoon bomb reading "Syntax error in text", and because it hangs off the body
 * rather than off the conversation it appears at the bottom of the window, under the composer,
 * attached to nothing. Reported as "what the fuck is this", which is the correct reaction.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { sweepOrphans } from "@/lib/mermaid-config";

function leftBehind(id: string) {
  const orphan = document.createElement("div");
  orphan.id = id;
  orphan.innerHTML = '<svg><g class="error-icon"></g></svg>';
  document.body.append(orphan);
  return orphan;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("what mermaid left in the body", () => {
  it("goes, whichever renderer left it", () => {
    // `d` plus the id the renderer passed: `kith-diagram-N` for the still one, `ma-…` for the
    // animator's own.
    leftBehind("dkith-diagram-7");
    leftBehind("dma-1787258162617-0");
    sweepOrphans();
    expect(document.body.children).toHaveLength(0);
  });

  it("goes even when there are several", () => {
    leftBehind("dma-1");
    leftBehind("dma-2");
    leftBehind("dma-3");
    sweepOrphans();
    expect(document.body.querySelectorAll(".error-icon")).toHaveLength(0);
  });

  it("takes nothing else with it", () => {
    const app = document.createElement("div");
    app.id = "root";
    const portal = document.createElement("div");
    portal.id = "dialog";
    document.body.append(app, portal);
    leftBehind("dma-1");
    sweepOrphans();
    expect([...document.body.children].map((el) => el.id)).toEqual(["root", "dialog"]);
  });

  it("leaves a nested element of the same name alone", () => {
    // Only a child of the body is mermaid's leftover. Anything of that name deeper in the tree
    // belongs to whatever put it there.
    const app = document.createElement("div");
    app.id = "root";
    app.innerHTML = '<div id="dma-9"></div>';
    document.body.append(app);
    sweepOrphans();
    expect(document.getElementById("dma-9")).not.toBeNull();
  });

  it("is fine when there is nothing to do", () => {
    expect(() => sweepOrphans()).not.toThrow();
  });
});
