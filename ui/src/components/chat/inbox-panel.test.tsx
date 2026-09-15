/**
 * The Alerts panel is a pane, not a window.
 *
 * It was written as a route-level overlay, and when the tiling shell landed it was wired into the
 * layout correctly — it is a real `SurfaceId`, and `workspace.tsx` renders it through the pane
 * `render` callback like any other surface — but its own markup was carried across untouched. So
 * the pane asked for an element to fill it and got one carrying
 * `fixed top-0 right-0 h-dvh w-[26rem] z-20 shadow-xl`, which describes the *viewport*: opening
 * Alerts as a tab welded a 416px panel to the right edge of the window, at `z-20` over the other
 * panes, ignoring the pane it was in and unable to yield when the window ran out of room — the one
 * thing a surface's `minWidth` exists to prevent.
 *
 * jsdom lays nothing out; every element measures 0x0, and a test asserting geometry here would be
 * asserting jsdom's numbers rather than the layout's. So this pins the class contract that
 * *produces* the geometry — which is what regressed, and what the next edit to that file can
 * silently undo. The visual claim belongs to the browser, and to a `look.mjs` pass against a
 * running server.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ConfirmProvider } from "@/components/ui/confirm";
import { InboxPanel } from "./inbox-panel";
import type { useMessages } from "@/hooks/use-messages";

type Messages = ReturnType<typeof useMessages>;

/** The six fields the panel reads. The rest of the hook's interface is a no-op here because the
 *  panel never reaches for it — `ClearMenu` is the only consumer of `clear`, and nothing in this
 *  file presses it. */
function inbox(): Messages {
  return {
    messages: [],
    unread: 0,
    counts: {},
    markAllRead: async () => {},
    send: async () => {},
    refresh: () => {},
    dismiss: async () => {},
    clear: async () => {},
    enableNotifications: () => {},
  };
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <ConfirmProvider>
        <InboxPanel inbox={inbox()} onClose={() => {}} />
      </ConfirmProvider>
    </MemoryRouter>,
  );
}

describe("the Alerts panel in a pane", () => {
  it("fills its pane instead of positioning itself against the window", () => {
    renderPanel();
    const panel = screen.getByLabelText("Alerts");

    // The regression. `fixed` is the whole bug: it takes the element out of the pane's box and
    // pins it to the viewport, which is a claim a pane's content is never entitled to make.
    expect(panel).not.toHaveClass("fixed");
    // `h-dvh` and a hard `w-[26rem]` are the same claim in each axis — the first says "as tall as
    // the window", the second "416px regardless of the pane I was given".
    expect(panel).not.toHaveClass("h-dvh");
    expect(panel).not.toHaveClass("w-[26rem]");

    // And the positive half, which is the shape `history-panel` and `work-panel` already use:
    // `h-full` resolves against the pane body, whose height is definite because it is a `flex-1`
    // child of the pane's column.
    expect(panel).toHaveClass("h-full");
    expect(panel).toHaveClass("w-full");
    expect(panel).toHaveClass("min-h-0");
  });
});
