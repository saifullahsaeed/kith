/**
 * The companion column — specifically, what it does to the thing it is a column *beside*.
 *
 * Everything else here is layout and can be read off the screen. This cannot: a host that is
 * torn down and rebuilt looks identical a moment later, and the only visible symptom is the
 * conversation underneath it reloading. So it is asserted on mounts rather than on pixels.
 */
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { FileText } from "lucide-react";

import { Companions, withCompanions } from "./companions";

afterEach(cleanup);

describe("a chat's body", () => {
  /* The bug this exists to stop, found twice in the same afternoon at two different layers.
   *
   * `Companions` returned a bare Fragment with no panels and a `div` with one; the caller in
   * `workspace.renderSurface` *separately* returned the host itself with no panels and a
   * `<Companions>` with one. Either one is enough to do the damage, because React reconciles by
   * type: change what sits at that position and the whole subtree below it unmounts — the chat's
   * runtime, its thread, its scroll and its half-written message — and comes back by refetching
   * the conversation. That is the work panel "reloading the chat" when it opens and again when
   * it closes.
   *
   * Fixing it in the component alone left the caller free to do it again, which is why the
   * composition is a function with one home rather than a branch at the call site. */
  it("sits inside a column whether or not it is carrying anything", () => {
    const empty = withCompanions({ surface: "chat" }, <p>body</p>, [], () => {});
    const full = withCompanions(
      { surface: "chat" },
      <p>body</p>,
      [{ key: "p0", title: "Work", icon: FileText, body: <p>panel</p> }],
      () => {},
    );

    expect((empty as { type: unknown }).type).toBe(Companions);
    expect((full as { type: unknown }).type).toBe(Companions);
  });

  it("is handed back untouched when it is not a chat", () => {
    const host = <p>board</p>;
    expect(withCompanions({ surface: "board" }, host, [], () => {})).toBe(host);
  });

  it("survives a panel being opened and closed beside it", () => {
    let mounts = 0;
    let setCount: (n: number) => void = () => {};

    function Body() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <div>the chat</div>;
    }

    function Harness() {
      const [count, set] = useState(0);
      setCount = set;
      return (
        <>
          {withCompanions(
            { surface: "chat" },
            <Body />,
            Array.from({ length: count }, (_, i) => ({
              key: `p${i}`,
              title: "Work",
              icon: FileText,
              body: <div>panel {i}</div>,
            })),
            () => {},
          )}
        </>
      );
    }

    render(<Harness />);
    expect(mounts).toBe(1);

    act(() => setCount(1));
    expect(screen.getByText("the chat")).toBeInTheDocument();
    expect(mounts, "opening the first panel must not rebuild the chat").toBe(1);

    act(() => setCount(0));
    expect(screen.getByText("the chat")).toBeInTheDocument();
    expect(mounts, "nor must closing the last one").toBe(1);
  });
});

describe("the companion column", () => {
  it("does not remount the host when the first panel opens, or when the last one closes", () => {
    let mounts = 0;
    let setCount: (n: number) => void = () => {};

    function Host() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <div>the chat</div>;
    }

    function Harness() {
      const [count, set] = useState(0);
      setCount = set;
      return (
        <Companions
          host={<Host />}
          onClose={() => {}}
          panels={Array.from({ length: count }, (_, i) => ({
            key: `p${i}`,
            title: "Work",
            icon: FileText,
            body: <div>panel {i}</div>,
          }))}
        />
      );
    }

    render(<Harness />);
    expect(mounts).toBe(1);

    /* The host used to be a Fragment's only child with no panels and a `div`'s child with one,
     * and React reconciles by type: opening Work on a chat that had none unmounted the chat. */
    act(() => setCount(1));
    expect(screen.getByText("the chat")).toBeInTheDocument();
    expect(mounts).toBe(1);

    act(() => setCount(0));
    expect(screen.getByText("the chat")).toBeInTheDocument();
    expect(mounts).toBe(1);
  });
});
