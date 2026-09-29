import { afterEach, describe, expect, it, vi } from "vitest";
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
