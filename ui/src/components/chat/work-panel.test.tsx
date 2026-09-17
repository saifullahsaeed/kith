/**
 * The Work panel's chrome — specifically, that it has none of its own.
 *
 * It carried a header: an icon, "N rounds this session", and a collapse button. Every part of that
 * was already on screen. The panel only ever renders inside a companion column or a tab, and both
 * draw a bar directly above it with the surface's name and its close control; the round count is
 * the same number the counters row at the foot already gives as "N steps". So the panel opened with
 * a second title, a third close button, and a duplicate of its own footer — in the one column where
 * five sections compete for height.
 */
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { render, screen } from "@/test/render";
import { WorkPanel } from "./work-panel";
import type { ActivityItem } from "@/lib/backend/activity";

const quiet = { status: null, activity: [] as ActivityItem[], refresh: vi.fn() };

/** Mounted the way the shell mounts it — a section inside reaches for `useNavigate`. */
const panel = () =>
  render(
    <MemoryRouter>
      <WorkPanel activity={quiet} conversationId="c-1" />
    </MemoryRouter>,
  );

describe("the work panel", () => {
  it("draws no header of its own — the pane it sits in already has one", () => {
    panel();

    expect(screen.queryByLabelText("Collapse the work panel")).toBeNull();
    expect(screen.queryByText("nothing yet")).toBeNull();
  });

  it("says nothing at all when there is nothing to say", () => {
    /* No counters row either: it renders only once there is a step or a token to report. An empty
     * bar is furniture, which is the same judgement that removed the header. */
    panel();

    expect(screen.queryByText(/step/)).toBeNull();
    expect(screen.queryByText(/rounds this session/)).toBeNull();
  });
});
