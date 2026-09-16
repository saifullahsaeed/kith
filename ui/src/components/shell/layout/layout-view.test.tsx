/**
 * The layout, rendered — the parts of it that only exist once there is a DOM.
 *
 * `tree.test.ts` covers what the operations do to the value; this covers what the pane does
 * with a pointer. Three things live only here and all three are ways a drag can feel broken:
 * a drop that lands on the wrong pane because a nested one let the event bubble to its parent,
 * a highlight that strobes because crossing into a child counts as leaving, and a tab strip
 * that cannot be dragged from at all.
 */
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { LayoutView } from "./layout-view";
import { useLayout } from "./store";
import { pane, panes, split, tabKey } from "./tree";
import { TAB_MIME } from "./drag";

const work = { surface: "work" as const };
const roadmap = { surface: "board" as const };

/** A drag payload the pane will accept, since jsdom has no real DataTransfer. */
function transfer(key: string) {
  const held: Record<string, string> = { [TAB_MIME]: key };
  return {
    types: [TAB_MIME],
    getData: (type: string) => held[type] ?? "",
    setData: (type: string, value: string) => {
      held[type] = value;
    },
    dropEffect: "",
    effectAllowed: "",
  };
}

beforeEach(() => {
  localStorage.clear();
  useLayout.setState({
    tree: split("row", [pane([work]), pane([roadmap])]),
    focused: "",
  });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const body = (ref: { surface: string }) => <div>body:{ref.surface}</div>;

describe("the pane", () => {
  it("shows a tab per surface and the active one's body", () => {
    render(<LayoutView render={body} />);

    expect(screen.getByText("Work")).toBeInTheDocument();
    expect(screen.getByText("Board")).toBeInTheDocument();
    expect(screen.getByText("body:work")).toBeInTheDocument();
  });

  it("closes a tab from its own button, and the split collapses with it", () => {
    render(<LayoutView render={body} />);

    fireEvent.click(screen.getByLabelText("Close Work"));

    expect(useLayout.getState().tree.kind).toBe("pane");
    expect(panes(useLayout.getState().tree)[0].tabs.map(tabKey)).toEqual(["board"]);
  });

  it("makes tabs draggable and carries the key on the drag", () => {
    render(<LayoutView render={body} />);
    const data = transfer("");

    fireEvent.dragStart(screen.getByText("Work").closest("[draggable]")!, {
      dataTransfer: data,
    });

    expect(data.getData(TAB_MIME)).toBe("work");
  });
});

describe("dropping", () => {
  it("docks onto the pane the pointer is over, not its parent", () => {
    const { container } = render(<LayoutView render={body} />);
    const target = panes(useLayout.getState().tree)[1];
    const element = container.querySelector(`[data-pane="${target.id}"]`)!;
    // jsdom gives every element a zero rect, which `edgeAt` reads as "center" — the drop into
    // an existing strip, which is the one worth asserting reaches the right pane.
    fireEvent.drop(element, { dataTransfer: transfer("work"), clientX: 5, clientY: 5 });

    const tree = useLayout.getState().tree;
    expect(tree.kind, "the emptied source pane collapsed the split").toBe("pane");
    expect(panes(tree)[0].tabs.map(tabKey)).toEqual(["board", "work"]);
  });

  it("ignores a drag that is not one of ours", () => {
    const { container } = render(<LayoutView render={body} />);
    const before = useLayout.getState().tree;
    const target = panes(before)[1];

    fireEvent.drop(container.querySelector(`[data-pane="${target.id}"]`)!, {
      dataTransfer: { types: ["Files"], getData: () => "" },
    });

    expect(useLayout.getState().tree).toBe(before);
  });

  it("focuses the pane you press in, so a new tab lands where you are looking", () => {
    const { container } = render(<LayoutView render={body} />);
    const second = panes(useLayout.getState().tree)[1];

    fireEvent.mouseDown(container.querySelector(`[data-pane="${second.id}"]`)!);

    expect(useLayout.getState().focused).toBe(second.id);
  });
});

describe("an empty pane", () => {
  it("says what to do rather than showing nothing", () => {
    useLayout.setState({ tree: pane([]), focused: "" });
    render(<LayoutView render={body} />);

    expect(screen.getByText(/Nothing open here/)).toBeInTheDocument();
  });
});

describe("the strip", () => {
  it("reorders tabs when a drop lands between two of them", () => {
    useLayout.setState({
      tree: pane([{ surface: "work" }, { surface: "board" }, { surface: "settings" }], 0),
      focused: "",
    });
    const { container } = render(<LayoutView render={body} />);
    measure(container);

    /* The dragged first tab, aimed into the left half of the third: past the second, before
     * the third — a position only real coordinates can name, which is why the event is built
     * by hand below. */
    fireEvent(container.querySelectorAll("[data-tab]")[0]!, dropEvent("work", 240));

    expect(panes(useLayout.getState().tree)[0].tabs.map(tabKey)).toEqual([
      "board",
      "work",
      "settings",
    ]);
  });

  it("inserts into another pane's strip where it was aimed, not at the end", () => {
    useLayout.setState({
      tree: split("row", [
        pane([{ surface: "work" }, { surface: "board" }], 0, "left"),
        pane([{ surface: "settings" }], 0, "right"),
      ]),
      focused: "",
    });
    const { container } = render(<LayoutView render={body} />);
    measure(container, "left");
    measure(container, "right");

    // The left half of the first tab of the other pane: "before work", not "after everything".
    fireEvent(
      container.querySelectorAll('[data-pane="left"] [data-tab]')[0]!,
      dropEvent("settings", 40),
    );

    const tabs = panes(useLayout.getState().tree).find((one) => one.id === "left")!.tabs.map(
      tabKey,
    );
    expect(tabs).toEqual(["settings", "work", "board"]);
  });
});

/** A drop the way a browser delivers it.
 *
 * jsdom has no DragEvent with coordinates — fireEvent.drop builds one without clientX, and a
 * drop without a pointer position reads as "past the last tab", which would make every caret
 * assertion accidentally about nothing. Built by hand, with the position that matters. */
function dropEvent(key: string, clientX: number): Event {
  const event = new Event("drop", { bubbles: true, cancelable: true });
  Object.assign(event, { clientX, clientY: 5, dataTransfer: transfer(key) });
  return event;
}

/** jsdom gives every element a zero rect, which reads as "past the last tab" — so the tests put
 *  real rectangles on the buttons, the one fact the caret arithmetic is about. */
function rect(left: number, right: number): DOMRect {
  return {
    left,
    right,
    top: 0,
    bottom: 24,
    width: right - left,
    height: 24,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect;
}

/** Give the tab buttons of one pane real rectangles, laid out left to right. */
function measure(container: HTMLElement, paneId?: string): void {
  const scope = paneId
    ? container.querySelectorAll('[data-pane="' + paneId + '"] [data-tab]')
    : container.querySelectorAll("[data-tab]");
  let at = 0;
  for (const node of scope) {
    const el = node as HTMLElement;
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue(rect(at, at + 100));
    at += 100;
  }
}

describe("the keyboard", () => {
  it("walks the strip with the arrow keys, which is the only way in without a mouse", () => {
    useLayout.setState({
      tree: pane([{ surface: "work" }, { surface: "board" }, { surface: "settings" }], 0),
      focused: "",
    });
    render(<LayoutView render={body} />);
    const strip = screen.getByRole("tablist");

    fireEvent.keyDown(strip, { key: "ArrowRight" });
    expect(panes(useLayout.getState().tree)[0].active).toBe(1);

    fireEvent.keyDown(strip, { key: "End" });
    expect(panes(useLayout.getState().tree)[0].active).toBe(2);

    // Past the end is the end, not a wrap: a strip is a row of things, not a carousel.
    fireEvent.keyDown(strip, { key: "ArrowRight" });
    expect(panes(useLayout.getState().tree)[0].active).toBe(2);

    fireEvent.keyDown(strip, { key: "Home" });
    expect(panes(useLayout.getState().tree)[0].active).toBe(0);
  });

  it("marks the chosen tab as the selected one, and only it", () => {
    useLayout.setState({ tree: pane([{ surface: "work" }, { surface: "board" }], 1), focused: "" });
    render(<LayoutView render={body} />);

    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent("Board");
  });
});

/** A layout that has been measured — the one thing the global stub deliberately never reports.
 *
 * `setup.ts` installs an observer that observes nothing, on the grounds that jsdom lays nothing
 * out and a faithful one would only ever say zero. True, and it also means the yielding rule and
 * everything downstream of it — the rail — cannot be reached at all. So this one answers with a
 * width of the test's choosing, for the tests that are about what happens at that width. */
function measuredAt(width: number): () => void {
  const real = (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    // A plain field rather than a constructor parameter property: `erasableSyntaxOnly` is on.
    report: ResizeObserverCallback;
    constructor(report: ResizeObserverCallback) {
      this.report = report;
    }
    observe(element: Element) {
      this.report(
        [
          {
            target: element,
            contentRect: { width, height: 900 },
            // The panel library reads `borderBoxSize[0]` off the same entry and throws without
            // it, so a fake that only fills in what *this* app reads breaks the group instead.
            borderBoxSize: [{ inlineSize: width, blockSize: 900 }],
            contentBoxSize: [{ inlineSize: width, blockSize: 900 }],
          } as unknown as ResizeObserverEntry,
        ],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  };
  return () => {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = real;
  };
}

describe("a railed pane", () => {
  let restore = () => {};

  beforeEach(() => {
    // 400px for a row wanting 560 + 380: the least-recently-focused child collapses to a rail.
    restore = measuredAt(400);
    useLayout.setState({
      tree: split("row", [
        pane([{ surface: "chat", conversationId: "c1" }, { surface: "work" }], 1, "left"),
        pane([{ surface: "board" }], 0, "right"),
      ]),
      focused: "right",
      /* Which child gives way is decided by the focus order, and it survives `setState` — so
       * without clearing it the pane a previous test clicked in this file stays the one that
       * keeps its width, and the rail appears on the other side of the split. */
      order: [],
    });
  });

  afterEach(() => restore());

  it("shows the tabs it holds, as icons", () => {
    render(<LayoutView render={body} />);

    expect(screen.getByLabelText("Show Chat")).toBeInTheDocument();
    expect(screen.getByLabelText("Show Work")).toBeInTheDocument();
  });

  it("opens the tab you actually clicked, not whichever one the pane had active", () => {
    render(<LayoutView render={body} />);

    fireEvent.click(screen.getByLabelText("Show Chat"));

    const state = useLayout.getState();
    expect(state.focused, "clicking a rail focuses its pane").toBe("left");
    expect(
      panes(state.tree).find((one) => one.id === "left")!.active,
      "and activates the tab under the pointer",
    ).toBe(0);
  });

  it("names the pane's own selection, so a rail says what it is holding open", () => {
    render(<LayoutView render={body} />);

    // `active` was 1 — Work — so that is the one the rail marks, not the first icon in it.
    expect(screen.getByLabelText("Show Work")).toHaveAttribute("aria-current", "true");
    expect(screen.getByLabelText("Show Chat")).not.toHaveAttribute("aria-current");
  });
});

describe("a pinned tab", () => {
  beforeEach(() => {
    useLayout.setState({
      tree: pane([{ surface: "work" }, { surface: "board" }, { surface: "context" }], 0, "only"),
      focused: "only",
      order: ["only"],
      pins: { board: { place: "p", slot: { direction: "row", index: 0, size: 100 } } },
      zoomed: null,
    });
  });

  it("sits at the front of the strip once the layout has been through a commit", () => {
    /* `setState` above is not a mutation, and invariant 7 is normalised in `commit` — so the
     * order arrives with the first real operation, not at render. Asserted here anyway because
     * the *rendered* order is what the drag layer measures its rects against. */
    useLayout.getState().activate("only", 0);
    render(<LayoutView render={body} />);

    const strip = Array.from(document.querySelectorAll("[data-tab]")).map((one) =>
      one.getAttribute("data-tab"),
    );
    expect(strip).toEqual(["board", "work", "context"]);
  });

  it("has no close button — that X is the accidental one", () => {
    render(<LayoutView render={body} />);
    expect(screen.queryByLabelText("Close Board")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Close Work")).toBeInTheDocument();
  });

  it("survives closing the others", () => {
    render(<LayoutView render={body} />);
    /* Right-click is Radix's trigger and jsdom does not give it a real pointer, so the menu is
     * driven through the store the item calls — the assertion is about which keys "the others"
     * covers, which is the part that was wrong to get wrong. */
    const state = useLayout.getState();
    const pane0 = panes(state.tree)[0];
    const others = pane0.tabs
      .filter((tab) => tab.uid !== pane0.tabs[pane0.active].uid)
      .map(tabKey)
      .filter((key) => !(key in state.pins));
    others.forEach((key) => state.close(key));

    expect(panes(useLayout.getState().tree)[0].tabs.map(tabKey)).toEqual(["board", "work"]);
  });
});

describe("a zoomed pane", () => {
  it("is the only thing on screen, and the rest of the tree is untouched", () => {
    useLayout.setState({
      tree: split("row", [
        pane([{ surface: "work" }], 0, "left"),
        pane([{ surface: "context" }], 0, "right"),
      ]),
      focused: "left",
      order: ["left", "right"],
      pins: {},
      zoomed: "right",
    });
    const { container } = render(<LayoutView render={body} />);

    expect(container.querySelector('[data-pane="right"]')).toBeInTheDocument();
    expect(container.querySelector('[data-pane="left"]')).not.toBeInTheDocument();
    expect(useLayout.getState().tree.kind, "the split is still there").toBe("split");
  });

  it("offers a way out that is not the keyboard", () => {
    useLayout.setState({
      tree: pane([{ surface: "work" }], 0, "only"),
      focused: "only",
      order: ["only"],
      pins: {},
      zoomed: "only",
    });
    render(<LayoutView render={body} />);

    fireEvent.click(screen.getByLabelText("Leave zoom"));

    expect(useLayout.getState().zoomed).toBeNull();
  });
});


describe("a tab you have opened stays open", () => {
  /* The pane used to render only the active tab, so every switch unmounted the one you left:
   * its runtime destroyed, its scroll and its half-typed message with it. Coming back was not
   * showing it again, it was building it from nothing — measured on a real forty-turn chat at
   * 4.5MB refetched and 3,000 parts rebuilt, per click.
   *
   * These assert the two halves that make it safe: built only once you look at it, and never
   * torn down after. A counter rather than a snapshot, because "was it rebuilt" is a question
   * about mounts and the DOM alone cannot answer it. */
  const mounts: string[] = [];

  function Counted({ name }: { name: string }) {
    useEffect(() => {
      mounts.push(name);
    }, [name]);
    return <div>body:{name}</div>;
  }

  const counting = (ref: { surface: string }) => <Counted name={ref.surface} />;

  beforeEach(() => {
    mounts.length = 0;
    useLayout.setState({ tree: pane([work, roadmap]), focused: "" });
  });

  it("builds only the tab you are looking at", () => {
    render(<LayoutView render={counting} />);
    // The pane holds two; a layout restored with eight must not open eight conversations to
    // make the second click fast.
    expect(mounts).toEqual(["work"]);
  });

  it("keeps the one you left mounted, hidden rather than destroyed", () => {
    render(<LayoutView render={counting} />);
    fireEvent.click(screen.getByText("Board"));

    expect(mounts).toEqual(["work", "board"]);
    // Still in the document — that is the whole change. `visibility`, not `display`, because a
    // box with no layout has no scroll offset either.
    expect(screen.getByText("body:work")).toBeInTheDocument();
    expect(screen.getByText("body:board")).toBeInTheDocument();
  });

  it("does not rebuild it when you come back", () => {
    render(<LayoutView render={counting} />);
    fireEvent.click(screen.getByText("Board"));
    fireEvent.click(screen.getByText("Work"));
    // Two mounts for two tabs, however often you switch between them.
    expect(mounts).toEqual(["work", "board"]);
  });

  it("takes the hidden one out of reach of the keyboard and a screen reader", () => {
    render(<LayoutView render={counting} />);
    fireEvent.click(screen.getByText("Board"));
    // Without this, Tab walks you through the composer of a chat you cannot see and a reader
    // reads both conversations as one.
    const held = screen.getByText("body:work").closest("[inert]");
    expect(held).not.toBeNull();
    expect(screen.getByText("body:board").closest("[inert]")).toBeNull();
  });

  it("lets go of a tab that is closed", () => {
    render(<LayoutView render={counting} />);
    fireEvent.click(screen.getByText("Board"));
    fireEvent.click(screen.getByLabelText("Close Work"));
    expect(screen.queryByText("body:work")).not.toBeInTheDocument();
  });
});
