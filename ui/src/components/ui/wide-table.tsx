/**
 * A markdown table that scrolls sideways, and shows when it does.
 *
 * `.kith-table` used to say so in CSS alone: two `background-attachment: local` gradients parked
 * over two `scroll` shadows, so a shadow showed exactly where there was more. It never showed.
 * A scroll container's background is painted *under* its content, and every row here paints its
 * own — the header's tint, the zebra stripe, the hover — so the shadow was drawn and then covered.
 * The pricing table in a real reply ended mid-word at the edge of the column with nothing to say
 * a fourth column was there.
 *
 * So the fades are elements laid over the table, shown from its measured scroll position. The
 * measuring follows both ways the answer changes: you scroll it, or it grows — a streaming reply
 * adds a row at a time.
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

export function WideTable({ children }: { children: ReactNode }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => {
      const past = el.scrollWidth - el.clientWidth;
      // `abs` because a right-to-left table scrolls into negative numbers.
      const at = Math.abs(el.scrollLeft);
      const start = past > 1 && at > 1;
      const end = past > 1 && at < past - 1;
      setEdges((was) => (was.start === start && was.end === end ? was : { start, end }));
    };
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(el);
    if (el.firstElementChild) observer?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", measure);
      observer?.disconnect();
    };
  }, []);

  return (
    <div className="kith-table-frame">
      <div ref={scroller} className="kith-table">
        {children}
      </div>
      <span aria-hidden data-edge="start" data-shown={edges.start || undefined} />
      <span aria-hidden data-edge="end" data-shown={edges.end || undefined} />
    </div>
  );
}
