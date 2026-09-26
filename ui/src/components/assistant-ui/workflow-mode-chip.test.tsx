/**
 * Workflow mode, per chat: off means only `/workflow` messages fan out, on means he decides. The
 * chip has to read the chat's real setting, say which it is, and write the flip to the server.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkflowModeChip } from "@/components/assistant-ui/composer-bar";
import { ConversationProvider } from "@/lib/conversation";
import { fireEvent, render, screen, waitFor } from "@/test/render";

afterEach(() => {
  vi.restoreAllMocks();
});

function serve(on: boolean) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push({ url: String(input), init });
    const body = init?.method === "POST" ? JSON.parse(String(init.body)) : { on };
    return new Response(JSON.stringify(body));
  });
  return calls;
}

describe("the workflow mode chip", () => {
  it("is absent before the chat exists — there is nothing to store it on", () => {
    serve(false);
    const { container } = render(
      <ConversationProvider id="">
        <WorkflowModeChip />
      </ConversationProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("reads the chat's setting and flips it on the server", async () => {
    const calls = serve(false);
    render(
      <ConversationProvider id="c1">
        <WorkflowModeChip />
      </ConversationProvider>,
    );
    const chip = await screen.findByRole("button", { name: /workflows/i });
    expect(chip.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(chip);
    await waitFor(() => expect(screen.getByRole("button", { name: /workflows on/i })).toBeTruthy());
    const post = calls.find((one) => one.init?.method === "POST");
    expect(post?.url).toBe("/api/chat/c1/workflow-mode");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ on: true });
  });
});
