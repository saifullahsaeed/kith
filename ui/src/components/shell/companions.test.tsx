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

import { Companions } from "./companions";

afterEach(cleanup);

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
