import { useEffect, useState } from "react";

/**
 * Which theme is on, right now, and again whenever it changes.
 *
 * The theme is a `dark` class on `<html>`, toggled in the header and remembered in
 * localStorage. That is fine for CSS, which follows the class on its own — and useless to any
 * component that has to be *told* its colours, because nothing published the value.
 *
 * The roadmap chart was the case in point. ReactFlow takes a `colorMode` prop and styles itself
 * from it; the prop was the literal string `"dark"`, so the whole graph stayed dark in light
 * mode — dark canvas, dark controls, dark nodes, in the middle of a white page.
 *
 * Watched with a MutationObserver rather than read once, so flipping the toggle re-themes the
 * chart immediately instead of on the next remount.
 */
export function useDarkMode(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));

  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => setDark(root.classList.contains("dark")));
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    // The class can change between first render and this effect attaching.
    setDark(root.classList.contains("dark"));
    return () => observer.disconnect();
  }, []);

  return dark;
}
