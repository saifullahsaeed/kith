/**
 * `render`, with the things every screen in this app is mounted inside.
 *
 * Server state lives in one cache now, so a component that reads any of it needs a
 * `QueryClientProvider` above it — and a test that renders one without gets "No QueryClient set"
 * rather than a failed assertion, which says nothing about the component. Re-exporting a wrapped
 * `render` means a test file says what it is testing and nothing about the plumbing.
 *
 * A fresh client per render, and that part is load-bearing: a cache shared between tests would
 * hand the second one the first one's answer, so a test could pass because of what another test
 * fetched. `retry: false` for the same reason — a deliberately failing fetch should fail once and
 * be done, not be tried again after a delay the test is not waiting for.
 */

import type { ReactElement, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render as base, type RenderOptions } from "@testing-library/react";

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, refetchOnWindowFocus: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
}

export function render(
  ui: ReactElement,
  options: Omit<RenderOptions, "wrapper"> & { client?: QueryClient } = {},
) {
  const { client = testQueryClient(), ...rest } = options;
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...base(ui, { wrapper: Wrapper, ...rest }) };
}

// Everything else comes straight from the library, so a test imports from one place.
export { screen, waitFor, within, act, fireEvent } from "@testing-library/react";
