/** Make a page exactly as tall as the panel's scrolling area, so its own columns scroll inside it.
 *
 * Journal and Lifetime are drawn to the window: the rails and the strip stay put and only the
 * reading column or the timeline scrolls. The panel's `<main>` is a plain scroller, though, and
 * what sits above it varies — a "these numbers are stale" banner appears and takes its height —
 * so `100vh` minus the header is wrong whenever anything else is on screen. This measures
 * `<main>` instead and keeps measuring as it resizes.
 *
 * The page renders with `-mb-7` to cancel the tab wrapper's bottom padding; the wrapper's top
 * padding is read rather than assumed, so a change there does not quietly overflow the page. */
import { useLayoutEffect, useRef, useState } from "react";

export function useFitPanel<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);

  useLayoutEffect(() => {
    const node = ref.current;
    const main = node?.closest("main");
    if (!node || !main) return;
    const measure = () => {
      const above = node.parentElement ? Number.parseFloat(getComputedStyle(node.parentElement).paddingTop) || 0 : 0;
      setHeight(Math.max(360, main.clientHeight - above));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(main);
    return () => observer.disconnect();
  }, []);

  return [ref, height] as const;
}
