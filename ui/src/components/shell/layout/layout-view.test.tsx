/**
 * The layout, rendered — the parts of it that only exist once there is a DOM.
 *
 * `tree.test.ts` covers what the operations do to the value; this covers what the pane does
 * with a pointer. Three things live only here and all three are ways a drag can feel broken:
 * a drop that lands on the wrong pane because a nested one let the event bubble to its parent,
 * a highlight that strobes because crossing into a child counts as leaving, and a tab strip
 * that cannot be dragged from at all.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
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
