/**
 * Navigation for a control that may be rendered with no router above it.
 *
 * `useNavigate` throws outside a router, and the thread, the composer and the session bar are
 * rendered outside one in their tests — the app mounts a router, a unit test does not. So the
 * control asks for a `go` through this, and gets a real one in the app and a harmless one in a
 * test, instead of every caller growing a guard of its own.
 */
import type { ReactNode } from "react";
import { useInRouterContext, useNavigate } from "react-router-dom";

function WithNavigate({ children }: { children: (go: (path: string) => void) => ReactNode }) {
  const navigate = useNavigate();
  return <>{children((path) => navigate(path))}</>;
}

export function Go({ children }: { children: (go: (path: string) => void) => ReactNode }) {
  return useInRouterContext() ? (
    <WithNavigate>{children}</WithNavigate>
  ) : (
    <>{children(() => {})}</>
  );
}
