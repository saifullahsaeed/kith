/**
 * **A surface that is a pane fills that pane, and positions against nothing.**
 *
 * The shell was a set of full-window takeover screens and became a tiling layout, and four of them
 * were wired into the new layout without their own markup being converted: each still carried
 * `fixed inset-0 z-30` (or, for the panel, `fixed top-0 right-0 h-dvh`), which describes the
 * *viewport*. Opened as a tab, each one escaped its pane and covered the entire window — header,
 * tab strips and every other pane — while its own header went on reserving 78px of dead space for
 * macOS traffic lights that are not over a pane.
 *
 * **Two of the four were reverted the same day, and this file records why so nobody re-does it.**
 * Board and Settings fit a full window and do not fit a pane: at the widths their own `minWidth`
 * permits, their content columns come up 30px and 214px short, and because `overflow-y-auto`
 * computes `overflow-x` to `auto`, each shortfall becomes a horizontal scrollbar rather than a
 * squeeze. Their widths cannot be tuned out — the layout is proportional, so the pane sometimes
 * gets *narrower* as the window grows, and a `minWidth` large enough to fit exceeds the window
 * beside the chat's 560. Making them fit is responsive work on their own layouts, and until that
 * exists they are takeovers by design (see the comment on each root). Context and Alerts did
 * convert, and they are what this file guards.
 *
 * Why class names rather than geometry: jsdom lays nothing out, so every element here measures 0x0
 * and a geometric assertion would be asserting jsdom's numbers, not the layout's. The class string
 * is the thing that regressed and the thing a future edit can silently undo. The geometry is
 * verified in a real engine by `scripts/look-at-surfaces.mjs`, which measures each pane body
 * against its surface root in Chromium and is what caught the `fixed` roots at 0,0 1280x860.
 */
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";

import { render } from "@/test/render";
import { ConfirmProvider } from "@/components/ui/confirm";
import { ContextDetailScreen } from "@/components/chat/context-detail";
import { ScreenLoading } from "@/components/shell/workspace";

/** Mount the way the app mounts them. `@/test/render` supplies the query cache; a surface needs
 *  the router for its own `useNavigate`, and the panel's clear menu needs the confirm provider. */
function mount(ui: ReactElement) {
  return render(
    <MemoryRouter>
      <ConfirmProvider>{ui}</ConfirmProvider>
    </MemoryRouter>,
  );
}

/** The surface root is the first element the component returns. */
function rootOf(ui: ReactElement): Element {
  const root = mount(ui).container.firstElementChild;
  if (!root) throw new Error("the surface rendered nothing");
  return root;
}

/** The contract, asserted in one place so a fifth surface is one call to check. */
function expectFillsPane(root: Element) {
  // Positioning against the viewport is the bug in its entirety: it lifts the element out of the
  // pane's box and pins it to the window. A pane's content is never entitled to make that claim.
  expect(root).not.toHaveClass("fixed");
  // The same claim in each axis — "as tall as the window", "as wide as the window".
  expect(root).not.toHaveClass("h-dvh");
  expect(root).not.toHaveClass("h-screen");
  expect(root).not.toHaveClass("w-screen");

  // The positive half: fill the box, and carry `min-h-0` so the flex-1 scroll chain inside works.
  expect(root).toHaveClass("h-full", "w-full", "min-h-0");
}

/** A wash is a child of the surface, so a `fixed` one escapes the pane even when the root does not.
 *  `.kith-ambient` is `position: fixed` by default in `index.css`, so every call site now states
 *  `absolute inset-0` explicitly — see the rule's own comment. */
function expectWashIsPaneScoped(root: Element) {
  const wash = root.querySelector(".kith-ambient");
  if (!wash) return;
  expect(wash).not.toHaveClass("fixed");
  expect(wash).toHaveClass("absolute", "inset-0");
}

describe("a surface fills its pane", () => {
  it("Context", () => {
    const root = rootOf(<ContextDetailScreen conversationId="conv-1" onClose={vi.fn()} />);
    expectFillsPane(root);
    expectWashIsPaneScoped(root);
  });
  /* Board and Settings are absent deliberately — they are takeovers until their layouts are made
     responsive, and their roots assert the opposite of this contract. See the file comment. */
});

describe("the lazy-chunk fallback", () => {
  it("blanks its own pane rather than the window", () => {
    const root = rootOf(<ScreenLoading />);
    // The one component behind every surface's `<Suspense>`: left `fixed`, a single pane fetching
    // its chunk dims the app header, every tab strip and all three panes.
    expect(root).not.toHaveClass("fixed");
    expect(root).toHaveClass("absolute", "inset-0");
  });
});
