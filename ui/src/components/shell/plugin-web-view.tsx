import { useCallback, useEffect, useRef, useState } from "react";

import { ArrowLeft, ArrowRight, RotateCw } from "lucide-react";

import { Absent } from "@/components/shell/plugin-surface";
import { Button } from "@/components/ui/button";
import { pluginSurface } from "@/lib/plugin-index";

/**
 * A browser pane: Kith's chrome, and a hole where the shell paints the page.
 *
 * ## The hole is real
 *
 * There is no page inside this component. A `web` surface is a `WebContentsView` — actual web
 * contents composited by the Electron main process *over* the window — so nothing in this file
 * renders the site, and nothing in CSS can position it either. What this does is measure where
 * the pane is and tell the shell, sixty times a second while a splitter is dragged if need be,
 * and the shell keeps the view's bounds matched to that rectangle. The `<div>` below is a
 * spacer whose only job is to be measured.
 *
 * That arrangement has one consequence worth knowing before reading the rest: **the view paints
 * on top of everything.** A dropdown, a dialog, a drag preview drawn by Kith over this pane
 * would be underneath it. So `hide` is called on unmount, when the tab is not the visible one,
 * and whenever the measured rectangle collapses — and every one of those is a bug if it is
 * missed, in the form of a browser floating over a settings dialog.
 *
 * ## Why the chrome is Kith's and not the plugin's
 *
 * The address bar, the back button and the spinner are drawn here, in Kith's own trusted
 * renderer, from status the main process pushes. A plugin does not draw them and cannot fake
 * them. That is the same rule the sealed surfaces follow — a person's click on chrome Kith drew
 * is the authorisation for what follows — and it is why typing an address here needs no
 * permission prompt: the plugin is not asking for anything, the person is using a browser.
 */

interface ViewStatus {
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  failure: string;
}

/** What the preload exposes in the desktop shell, and nothing outside it. */
interface Shell {
  webView?: {
    ask(request: Record<string, unknown>): Promise<Record<string, unknown>>;
    onStatus(listener: (status: Record<string, unknown>) => void): () => void;
  };
}

const EMPTY: ViewStatus = {
  url: "",
  title: "",
  loading: false,
  canGoBack: false,
  canGoForward: false,
  failure: "",
};

export function PluginWebView({
  plugin,
  view,
  visible = true,
}: {
  plugin: string;
  view: string;
  /** False while another tab is in front. The view has to be taken off the screen, not merely
   *  left where it is — it would paint over whatever replaced it. */
  visible?: boolean;
}) {
  const declared = pluginSurface(plugin, view);
  const hole = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<ViewStatus>(EMPTY);
  const [typed, setTyped] = useState("");
  /** The last URL the shell reported, so typing is not overwritten mid-word by a status push
   *  and the bar still follows the page when it genuinely moves. */
  const shown = useRef("");

  const shell = (window as unknown as { kith?: Shell }).kith;
  const bridge = shell?.webView;

  const ask = useCallback(
    async (verb: string, extra: Record<string, unknown> = {}) => {
      if (!bridge) return;
      const answer = await bridge.ask({ verb, plugin, view, ...extra });
      if (answer && typeof answer.url === "string") setStatus(answer as unknown as ViewStatus);
    },
    [bridge, plugin, view],
  );

  /* Status, pushed rather than polled. The main process knows when the page moved; asking it
   * on a timer would mean an address bar that is right most of the time. */
  useEffect(() => {
    if (!bridge) return;
    return bridge.onStatus((raw) => {
      if (raw.plugin !== plugin || raw.view !== view) return;
      const next = raw as unknown as ViewStatus;
      setStatus(next);
      if (next.url !== shown.current) {
        shown.current = next.url;
        setTyped(next.url);
      }
    });
  }, [bridge, plugin, view]);

  /* Keep the view over the hole.
   *
   * A `ResizeObserver` on the spacer catches the pane being resized, and a scroll listener in
   * the capture phase catches the pane being *moved* — which a resize observer does not see at
   * all, because the element's size has not changed. Both are needed: without the second, a
   * browser stays put while the column it lives in scrolls away underneath it, which looks
   * exactly like the app has come apart.
   */
  useEffect(() => {
    const element = hole.current;
    if (!element || !bridge) return;

    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = element.getBoundingClientRect();
        if (!visible || box.width < 1 || box.height < 1) {
          void bridge.ask({ verb: "hide", plugin, view });
          return;
        }
        void bridge
          .ask({
            verb: "place",
            plugin,
            view,
            rect: { x: box.left, y: box.top, width: box.width, height: box.height },
            home: declared?.home ?? "",
          })
          .then((answer) => {
            if (answer && typeof answer.url === "string") {
              setStatus(answer as unknown as ViewStatus);
              if (answer.url !== shown.current) {
                shown.current = String(answer.url);
                setTyped(String(answer.url));
              }
            }
          });
      });
    };

    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(element);
    window.addEventListener("resize", measure);
    // Capture, because the scroll happens on an ancestor and does not bubble.
    window.addEventListener("scroll", measure, true);

    return () => {
      cancelAnimationFrame(frame);
      watcher.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      // Off the screen on the way out. A view left behind is a browser painted over whichever
      // tab replaced this one, with nothing on screen explaining where it came from.
      void bridge.ask({ verb: "hide", plugin, view });
    };
  }, [bridge, plugin, view, visible, declared?.home]);

  if (!declared) return <Absent title={view || "Browser"} detail="This plugin is not installed." />;

  /* No shell means no browser, said plainly. This is the one surface kind that genuinely cannot
   * work in a browser tab pointed at the server: the whole mechanism is a native view the
   * desktop app composites, so there is nothing to fall back to and pretending otherwise would
   * leave an empty rectangle and no explanation. */
  if (!bridge) {
    return (
      <Absent
        title={declared.title}
        detail="A browser tab needs the Kith desktop app — this is a native view the app draws, so there is nothing to show here."
      />
    );
  }

  return (
    <div className="flex h-full w-full flex-col">
      <header className="border-border/60 flex flex-none items-center gap-0.5 border-b px-2 py-1.5">
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => void ask("back")}
          disabled={!status.canGoBack}
          title="Back"
          aria-label="Back"
        >
          <ArrowLeft className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => void ask("forward")}
          disabled={!status.canGoForward}
          title="Forward"
          aria-label="Forward"
        >
          <ArrowRight className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => void ask("reload")}
          title={status.loading ? "Loading" : "Reload"}
          aria-label="Reload"
        >
          <RotateCw className={`size-3.5 ${status.loading ? "animate-spin" : ""}`} />
        </Button>

        <form
          className="ml-1 flex min-w-0 flex-1 items-center"
          onSubmit={(event) => {
            event.preventDefault();
            const wanted = typed.trim();
            if (wanted) void ask("navigate", { url: wanted });
          }}
        >
          <input
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onFocus={(event) => event.currentTarget.select()}
            spellCheck={false}
            placeholder="Type an address, or ask him to open one"
            aria-label="Address"
            className="bg-muted focus:bg-background focus:ring-ring/40 min-w-0 flex-1 rounded-full px-2.5 py-1 text-xs outline-none focus:ring-1"
          />
        </form>
      </header>

      {/* The hole. Nothing renders here — the shell paints the page over this rectangle. The
          background shows for the instant before the first `place` lands, and behind a page
          that has not painted. */}
      <div ref={hole} className="bg-muted min-h-0 flex-1" />

      {status.failure ? (
        <p className="border-border/60 text-muted-foreground flex-none border-t px-2.5 py-1 text-[11px]">
          {status.failure}
        </p>
      ) : null}
    </div>
  );
}
