import { beforeEach, describe, expect, it, vi } from "vitest";

import { PluginSurface } from "@/components/shell/plugin-surface";
import { setPluginSurfaces } from "@/lib/plugin-index";
import { render, testQueryClient, waitFor } from "@/test/render";

/**
 * A plugin tab, and the one thing it got wrong.
 *
 * **The bug this file exists for.** The render push was wired to the iframe's `onLoad` and to
 * nothing else, so the frame saw the plugin's store exactly once — as it was at mount. He then
 * drew seventeen shapes, every one of them landed in the store, and the tab went on showing its
 * own empty state because nothing ever told it. `changes.publish("plugin_state")` and
 * `STALE_ON.plugin_state` were both already wired correctly; the component simply never
 * subscribed, which is the one arrangement where every piece of plumbing is right and none of it
 * does anything.
 *
 * So what is asserted here is the *wiring*, not the shapes: that a push happens when the frame
 * says it is ready, that another happens when the store changes underneath, and that nothing is
 * pushed into a frame that has not said its own script is running — because a message delivered
 * before `window.kith.render(...)` is registered is silently dropped, which is exactly the
 * failure mode nobody can see.
 */

/** Every `postMessage` the host sent into the frame. */
function pushes(): { type: string; state?: Record<string, unknown>; rev?: number }[] {
  const frame = document.querySelector("iframe");
  const spy = frame?.contentWindow?.postMessage as unknown as ReturnType<typeof vi.fn>;
  return (spy?.mock?.calls ?? []).map((call) => call[0]);
}

/** Pretend the frame's own script has started and said so. */
function frameSaysReady(protocol = 1) {
  const frame = document.querySelector("iframe");
  window.dispatchEvent(
    new MessageEvent("message", {
      data: { kith: 1, type: "ready", protocol, handles: [] },
      source: frame?.contentWindow,
    }),
  );
}

function answer(body: unknown, ok = true) {
  return Promise.resolve({ ok, status: ok ? 200 : 500, json: () => Promise.resolve(body) });
}

let store: Record<string, unknown>;
let revisions: Record<string, number>;
let mounts: number;

beforeEach(() => {
  store = {};
  revisions = {};
  setPluginSurfaces([
    {
      plugin: "sketchpad",
      pluginName: "Sketchpad",
      view: "board",
      title: "Sketchpad",
      icon: "palette",
      minWidth: 360,
      minHeight: 240,
      instances: "single",
      answers: "conversation",
    },
  ]);

  mounts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (String(url).includes("/surface/board/mount")) {
        // A fresh ticket every time, the way the server issues them: a ticket is single-use.
        mounts += 1;
        return answer({
          ticket: `t-${mounts}`,
          url: `/api/plugins/frame/t-${mounts}`,
          protocol: 1,
          assets: ["shot"],
        });
      }
      if (String(url).includes("/state")) return answer({ values: store, revisions, slot: {} });
      if (String(url).includes("/file?path=")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
        });
      }
      return answer({});
    }),
  );

  // jsdom gives an iframe a real `contentWindow`; its `postMessage` is what the host talks
  // through, so spying on the prototype catches every frame this test mounts.
  vi.spyOn(window.HTMLIFrameElement.prototype, "contentWindow", "get").mockImplementation(
    function (this: HTMLIFrameElement) {
      const held = this as unknown as { _fake?: Window };
      if (!held._fake) held._fake = { postMessage: vi.fn() } as unknown as Window;
      return held._fake;
    },
  );
});

describe("what the host pushes into a plugin's frame", () => {
  it("says nothing until the frame says its script is running", async () => {
    render(<PluginSurface plugin="sketchpad" view="board" conversationId="c-1" />);
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());

    // `onLoad` is not the signal. The document has loaded by then and nothing guarantees the
    // page has called `window.kith.render(...)`, so a push racing it is dropped in silence.
    expect(pushes().filter((one) => one.type === "render")).toHaveLength(0);
  });

  it("pushes the store once the frame is ready", async () => {
    store = { title: "Intake flow" };
    revisions = { title: 1 };
    render(<PluginSurface plugin="sketchpad" view="board" conversationId="c-1" />);
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());

    frameSaysReady();

    await waitFor(() => {
      const sent = pushes().filter((one) => one.type === "render");
      expect(sent).toHaveLength(1);
      expect(sent[0].state).toEqual({ title: "Intake flow" });
    });
  });

  it("pushes again when the store changes underneath it", async () => {
    const client = testQueryClient();
    render(<PluginSurface plugin="sketchpad" view="board" conversationId="c-1" />, { client });
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());
    frameSaysReady();
    await waitFor(() => expect(pushes().filter((one) => one.type === "render")).toHaveLength(1));

    // What a `plugin_state` change does: `STALE_ON` invalidates `["plugins", "state"]`, which is
    // a prefix of this key. Seventeen draws landing in the store arrive exactly this way.
    store = { pending: [{ command: "draw", seq: 1 }] };
    revisions = { pending: 1 };
    await client.invalidateQueries({ queryKey: ["plugins", "state"] });

    await waitFor(() => {
      const sent = pushes().filter((one) => one.type === "render");
      expect(sent.length).toBeGreaterThan(1);
      expect(sent[sent.length - 1].state).toEqual(store);
    });
  });

  it("numbers a push by the store's revisions rather than by the clock", async () => {
    store = { a: 1 };
    revisions = { a: 3, b: 4 };
    render(<PluginSurface plugin="sketchpad" view="board" conversationId="c-1" />);
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());
    frameSaysReady();

    // So two pushes carrying identical state carry the same `rev`, which is what lets a surface
    // tell "something changed" from "the host repainted me". A clock would make every repaint
    // look like a change.
    await waitFor(() => {
      const sent = pushes().filter((one) => one.type === "render");
      expect(sent[0].rev).toBe(7);
    });
  });

  it("says so plainly when a plugin was built for a different Kith", async () => {
    const { getByText } = render(
      <PluginSurface plugin="sketchpad" view="board" conversationId="c-1" />,
    );
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());

    frameSaysReady(99);

    // A dropped message and an inert tab is the worst way for a third party to find out their
    // plugin cannot talk to this build.
    await waitFor(() => expect(getByText(/different version of Kith/)).toBeTruthy());
  });

  it("renders a placeholder rather than a frame for a plugin that is not installed", async () => {
    const { getByText } = render(
      <PluginSurface plugin="gone" view="board" conversationId="c-1" />,
    );

    // One tab, not the layout. An unknown surface used to fail the stored-layout check at the
    // root and discard every split and size in the window.
    await waitFor(() => expect(getByText(/is not installed/)).toBeTruthy());
    expect(document.querySelector("iframe")).toBeNull();
  });
});

describe("what a new frame is told", () => {
  /**
   * **The dedupe map outlived the page it was describing.**
   *
   * Declared assets are pushed in as bytes and keyed on their path, so a store write that did
   * not change a file does not re-read it. That map is a ref on this component; the page is not.
   * A `key` bump replaces the iframe and a fresh ticket replaces the document inside it, and
   * across either one every asset still looked delivered — so the reload offered as the one
   * recovery from an unresponsive surface produced a frame with no images in it.
   */
  it("pushes a declared asset into a frame that replaced the one it was sent to", async () => {
    store = { shot: "/plugins/.storage/sketchpad/board.png" };
    revisions = { shot: 1 };
    setPluginSurfaces([
      {
        plugin: "sketchpad",
        pluginName: "Sketchpad",
        view: "board",
        title: "Sketchpad",
        icon: "palette",
        minWidth: 360,
        minHeight: 240,
        instances: "single",
        answers: "conversation",
        assets: ["shot"],
      },
    ]);

    const { rerender } = render(
      <PluginSurface plugin="sketchpad" view="board" conversationId="c-1" />,
    );
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());
    frameSaysReady();
    await waitFor(() => expect(pushes().filter((one) => one.type === "asset")).toHaveLength(1));

    // A new conversation mounts a new ticket, so the frame navigates to a different document.
    rerender(<PluginSurface plugin="sketchpad" view="board" conversationId="c-2" />);
    // The *document*, not the request: `mounts` counts calls, and the frame does not point at
    // the new ticket until the answer lands.
    await waitFor(() =>
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe("/api/plugins/frame/t-2"),
    );
    frameSaysReady();

    await waitFor(() => {
      expect(pushes().filter((one) => one.type === "asset").length).toBeGreaterThan(1);
    });
  });
});
