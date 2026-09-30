import { afterEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { BrowserPane } from "./browser-pane";

/**
 * The tab strip, as the person uses it. The native view is the shell's and is not here; what is
 * here is Kith's chrome — which tab is in front, and that each control asks the shell for the
 * right thing.
 */

const status = {
  owner: "c1",
  url: "https://example.com/",
  title: "Example",
  loading: false,
  canGoBack: false,
  canGoForward: false,
  failure: "",
  viewport: null,
  scale: 1,
  tab: "t2",
  tabs: [
    { id: "t1", title: "Docs", url: "https://docs.example.com/", loading: false },
    { id: "t2", title: "Example", url: "https://example.com/", loading: true },
  ],
};

function withShell() {
  const asked: Record<string, unknown>[] = [];
  let push: (next: Record<string, unknown>) => void = () => {};
  (window as unknown as { kith: unknown }).kith = {
    webView: {
      ask: vi.fn(async (request: Record<string, unknown>) => {
        asked.push(request);
        return status;
      }),
      onStatus: (listener: (next: Record<string, unknown>) => void) => {
        push = listener;
        return () => {};
      },
    },
  };
  return { asked, push: (next: Record<string, unknown>) => act(() => push(next)) };
}

afterEach(() => {
  cleanup();
  delete (window as unknown as { kith?: unknown }).kith;
});

describe("the browser's tab strip", () => {
  it("draws every tab, the front one selected", () => {
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);
    shell.push(status);

    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((one) => one.textContent)).toEqual(["Docs", "Example"]);
    expect(tabs[1]?.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("loading")).toBeTruthy();
  });

  it("switches, closes and opens by asking the shell", () => {
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);
    shell.push(status);

    fireEvent.click(screen.getByRole("tab", { name: "Docs" }));
    fireEvent.click(screen.getByRole("button", { name: "Close Example" }));
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));

    const verbs = shell.asked.map((one) => [one.verb, one.tab ?? ""]);
    expect(verbs).toEqual(
      expect.arrayContaining([
        ["tab-switch", "t1"],
        ["tab-close", "t2"],
        ["tab-new", ""],
      ]),
    );
  });

  it("marks a private tab, and opens one on asking", () => {
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);
    shell.push({ ...status, tabs: [{ ...status.tabs[1], loading: false, private: true }] });

    expect(screen.getByLabelText("private")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "New private tab" }));
    expect(shell.asked.some((one) => one.verb === "tab-new" && one.private === true)).toBe(true);
  });

  it("ignores another conversation's browser", () => {
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);
    shell.push({ ...status, owner: "c2", tabs: [{ id: "t1", title: "Theirs", url: "x", loading: false }], tab: "t1" });

    expect(screen.queryByRole("tab", { name: "Theirs" })).toBeNull();
  });

  it("says what to do when nothing is open", () => {
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);
    shell.push({ ...status, url: "", title: "", tab: "", tabs: [] });

    expect(screen.getByText(/Nothing is open/)).toBeTruthy();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });
});

describe("something drawn over the pane", () => {
  /* The live view is composited above the whole window, so anything Kith draws over the pane has
     to take it off the screen — with `hide`, at once, and never behind a capture — while a recent
     picture of the page, kept in the hole all along, is what shows in its place. */
  const hitTest = Object.getOwnPropertyDescriptor(document, "elementFromPoint");
  afterEach(() => {
    vi.restoreAllMocks();
    if (hitTest) Object.defineProperty(document, "elementFromPoint", hitTest);
    else delete (document as unknown as { elementFromPoint?: unknown }).elementFromPoint;
    document.body.style.pointerEvents = "";
  });

  const PANE = { left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600, x: 0, y: 0 };
  const NOWHERE = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
  /** Every element measures as the pane unless named here. */
  const layout = (rects = new Map<Element, typeof PANE>()) => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { ...(rects.get(this) ?? PANE), toJSON: () => ({}) } as DOMRect;
    });
  };
  const hits = (answer: () => Element | null) =>
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: answer });
  const verbs = (asked: Record<string, unknown>[]) => asked.map((one) => one.verb);

  it("keeps the page live when hit-testing only finds the hole's own ancestors", async () => {
    layout();
    hits(() => document.documentElement);
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);

    await waitFor(() => expect(verbs(shell.asked)).toContain("place"));
    expect(verbs(shell.asked)).not.toContain("hide");
  });

  it("hides the view for a panel drawn over it", async () => {
    layout();
    const panel = document.createElement("div");
    document.body.appendChild(panel);
    hits(() => panel);
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);

    await waitFor(() => expect(verbs(shell.asked)).toContain("hide"));
    expect(verbs(shell.asked)).not.toContain("place");
    panel.remove();
  });

  it("hides the view for a menu the hit test cannot see", async () => {
    /* A portalled menu over one corner of the pane, between every point the hit test tries — and a
       modal one makes <body> click-through, so the hit test would see through it anyway. */
    const menu = document.createElement("div");
    const wrapper = document.createElement("div");
    wrapper.appendChild(menu);
    layout(new Map([[wrapper, NOWHERE], [menu, { ...NOWHERE, left: 790, right: 900, top: 0, bottom: 40, width: 110, height: 40 }]]));
    hits(() => document.documentElement);
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);
    await waitFor(() => expect(verbs(shell.asked)).toContain("place"));

    document.body.appendChild(wrapper);
    await waitFor(() => expect(verbs(shell.asked)).toContain("hide"));
    wrapper.remove();
  });

  it("hides the view while a modal menu is open anywhere", async () => {
    layout();
    hits(() => document.documentElement);
    const shell = withShell();
    render(<BrowserPane conversationId="c1" />);
    await waitFor(() => expect(verbs(shell.asked)).toContain("place"));

    document.body.style.pointerEvents = "none";
    await waitFor(() => expect(verbs(shell.asked)).toContain("hide"));
  });

  it("keeps the latest picture of the page under the view, where the page was painted", async () => {
    layout();
    hits(() => document.documentElement);
    const shell = withShell();
    const kith = (window as unknown as { kith: { webView: { ask: (r: Record<string, unknown>) => Promise<unknown> } } }).kith;
    const plain = kith.webView.ask;
    kith.webView.ask = async (request) =>
      request.verb === "frame"
        ? { seq: 3, snapshot: "data:image/jpeg;base64,AAAA", x: 10, y: 0, width: 390, height: 600 }
        : plain(request);

    const { container } = render(<BrowserPane conversationId="c1" />);
    shell.push({ ...status, frame: 3 });

    await waitFor(() => expect(container.querySelector('img[src^="data:image/jpeg"]')).not.toBeNull());
    const picture = container.querySelector("img") as HTMLImageElement;
    expect([picture.style.left, picture.style.width]).toEqual(["10px", "390px"]);

    // A tab with no picture yet clears it rather than showing the last tab's page.
    shell.push({ ...status, frame: 0 });
    await waitFor(() => expect(container.querySelector("img")).toBeNull());
  });
});
