import { useCallback, useEffect, useRef, useState } from "react";

import { ArrowLeft, ArrowRight, Crosshair, KeyRound, Monitor, Plus, RotateCw, X } from "lucide-react";

import { CookieRow, ViewportRow } from "@/components/shell/browser-tools";
import { Absent } from "@/components/shell/plugin-surface";
import { Button } from "@/components/ui/button";
import { activeComposer } from "@/lib/active-composer";
import { sendToChat, type Picked } from "@/lib/browser-pick";

/**
 * The Browser tab: Kith's chrome, and a hole where the shell paints the page.
 *
 * ## The hole is real
 *
 * There is no page inside this component. The page is a `WebContentsView` — actual web
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
 * ## Why the chrome is Kith's
 *
 * The address bar, the back button and the spinner are drawn here, in Kith's own trusted
 * renderer, from status the main process pushes, and no page can draw or fake them. That is the
 * rule the sealed plugin surfaces follow too — a person's click on chrome Kith drew is the
 * authorisation for what follows — and it is why typing an address here needs no permission
 * prompt: nobody is asking for anything, the person is using a browser.
 */

interface ViewStatus {
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  failure: string;
  /** The size the page is being laid out at, when the model set one and it is not the pane.
   *  Kith letterboxes the page inside the pane, so without this the pane would look broken —
   *  a smaller page floating on the background — with nothing saying it was chosen. */
  viewport: { width: number; height: number } | null;
  /** How much of that size is actually being painted. A viewport bigger than the pane is laid
   *  out in full and drawn smaller, so this is the difference between the page you are looking
   *  at and the page it is — and the chip says it, because a silently shrunken page is the bug
   *  this replaced. */
  scale: number;
  /** The tab in front — the one this chrome is about. Empty when none is open. */
  tab: string;
  /** Every tab, in strip order. */
  tabs: TabSummary[];
}

interface TabSummary {
  id: string;
  title: string;
  url: string;
  loading: boolean;
}

/** What a tab is called in the strip: its title, else its host, else what it is. */
function tabLabel(tab: TabSummary): string {
  if (tab.title && tab.title !== tab.url) return tab.title;
  try {
    const host = new URL(tab.url).host;
    if (host) return host;
  } catch {
    /* about:blank and friends */
  }
  return "New tab";
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
  viewport: null,
  scale: 1,
  tab: "",
  tabs: [],
};

/**
 * Is something drawn on top of this pane?
 *
 * Asked of the document rather than of a list of things that might cover it. A `WebContentsView`
 * is composited by the main process **over** the whole window, so anything Kith draws above the
 * pane — a dialog, a dropdown, the control panel, a drag preview, a screen not written yet —
 * appears underneath the browser instead of over it. Keeping a list of those would mean every
 * future overlay has to remember to tell the browser about itself, and the first one that forgets
 * is a bug that looks exactly like the app having come apart.
 *
 * `elementFromPoint` asks who is actually on top. Five points rather than one, because a panel
 * that covers most of the pane still hides most of the page, and a single centre sample would
 * call that uncovered.
 */
function covered(hole: HTMLElement, box: DOMRect): boolean {
  const inset = 4;
  const points: [number, number][] = [
    [box.left + box.width / 2, box.top + box.height / 2],
    [box.left + inset, box.top + inset],
    [box.right - inset, box.top + inset],
    [box.left + inset, box.bottom - inset],
    [box.right - inset, box.bottom - inset],
  ];
  for (const [x, y] of points) {
    const top = document.elementFromPoint(x, y);
    // Outside the viewport answers null — that is the pane being scrolled off, which `place`
    // already handles by its rectangle, so it does not count as buried.
    if (!top) continue;
    if (top === hole || hole.contains(top)) return false;
  }
  return true;
}

export function BrowserPane({
  conversationId,
  visible = true,
}: {
  /** Which conversation this pane belongs to — the view's `owner`, which is what gives each
   *  conversation its own browser instead of all of them sharing one page. The server names the
   *  same conversation when the model calls in, so both sides address the same view. */
  conversationId: string;
  /** False while another tab is in front. The view has to be taken off the screen, not merely
   *  left where it is — it would paint over whatever replaced it. */
  visible?: boolean;
}) {
  const owner = conversationId;
  const hole = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<ViewStatus>(EMPTY);
  const [typed, setTyped] = useState("");
  /** Which fold-out row is open, if any. One at a time: both are two-line answers to a question
   *  somebody asked on purpose, and stacking them would push the page halfway down the pane. */
  const [panel, setPanel] = useState<"" | "viewport" | "cookies">("");
  /** True while the inspector is live in the page. The call does not answer until they click
   *  something or give up, so nothing else should be offered meanwhile. */
  const [picking, setPicking] = useState(false);
  /** What to say about the last pick, when there is anything to say. Success is silent — the
   *  attachment appearing in the composer is the receipt. */
  const [picked, setPicked] = useState("");
  /** The last URL the shell reported, so typing is not overwritten mid-word by a status push
   *  and the bar still follows the page when it genuinely moves. */
  const shown = useRef("");

  const shell = (window as unknown as { kith?: Shell }).kith;
  const bridge = shell?.webView;

  /* Returns the answer as well as folding it into the status. The fold-out rows need both: the
   * status to draw themselves from, and the refusal sentence the shell sends back for a size it
   * would not take — which is not a status and has nowhere else to go. */
  const ask = useCallback(
    async (verb: string, extra: Record<string, unknown> = {}) => {
      if (!bridge) return undefined;
      const answer = await bridge.ask({ verb, owner, ...extra });
      if (answer && typeof answer.url === "string") setStatus(answer as unknown as ViewStatus);
      return answer;
    },
    [bridge, owner],
  );

  /**
   * Point at a component, and put it in the chat.
   *
   * The whole cost this removes is *identification*. Asking about one card among nine on a page
   * takes several rounds of "the third one, no, the one below it" before anything can be
   * answered — and the answer to which one was always available for one gesture. So the pick
   * carries three things at once: the crop, the selector that finds it again, and the words on
   * it. See `lib/browser-pick`.
   *
   * It goes to whichever chat has focus, not to this pane's own conversation. They are almost
   * always the same, and when they are not, the one being typed in is the one that was meant —
   * the same rule a dropped file already follows.
   */
  const startPick = useCallback(async () => {
    if (!bridge || picking) return;
    setPicking(true);
    setPicked("");
    try {
      const answer = (await bridge.ask({ verb: "pick", owner })) as Picked & {
        error?: string;
      };
      if (answer?.error) {
        setPicked(answer.error);
        return;
      }
      // Escape, a minute of nothing, or a click that landed on no element. Silent: cancelling is
      // an ordinary way to use a picker and does not deserve a line of chrome about it.
      if (!answer || answer.cancelled) return;
      const said = await sendToChat(answer, activeComposer());
      if (said) setPicked(said);
    } catch (error) {
      setPicked((error as Error).message);
    } finally {
      setPicking(false);
    }
  }, [bridge, picking, owner]);

  /* An inspector outlives the pane that started it otherwise: the overlay is drawn *in the page*,
   * and the page is still there when this component goes away. */
  useEffect(() => {
    if (!bridge) return;
    return () => {
      void bridge.ask({ verb: "unpick", owner });
    };
  }, [bridge, owner]);

  /* Status, pushed rather than polled. The main process knows when the page moved; asking it
   * on a timer would mean an address bar that is right most of the time. */
  useEffect(() => {
    if (!bridge) return;
    return bridge.onStatus((raw) => {
      // Status is broadcast to the window, so every browser pane hears every browser's — and
      // with a view per conversation there are several. Without the owner check, one
      // conversation's address bar would follow another conversation's page.
      if (raw.owner !== owner) return;
      const next = raw as unknown as ViewStatus;
      setStatus(next);
      if (next.url !== shown.current) {
        shown.current = next.url;
        setTyped(next.url);
      }
    });
  }, [bridge, owner]);

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
        if (!visible || box.width < 1 || box.height < 1 || covered(element, box)) {
          void bridge.ask({ verb: "hide", owner });
          return;
        }
        void bridge
          .ask({
            verb: "place",
            owner,
            rect: { x: box.left, y: box.top, width: box.width, height: box.height },
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

    /* Re-measure whenever anything is drawn over the app.
     *
     * **This is the one that was missing, and it showed.** The control panel opens as a
     * full-window overlay while the pane underneath stays mounted — same rectangle, no resize,
     * no scroll — so nothing re-ran and a browser went on painting over the panel, with the
     * panel's own text visibly cut off behind it. A resize observer cannot see this: the pane
     * has not moved or changed size, it has been *buried*.
     *
     * A mutation observer on the body catches the overlay appearing, whatever draws it — a
     * dialog, a menu, a route, something not written yet. `covered()` does the deciding. */
    const overlays = new MutationObserver(measure);
    overlays.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-state", "aria-hidden", "style", "class"],
    });

    return () => {
      cancelAnimationFrame(frame);
      watcher.disconnect();
      overlays.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      // Off the screen on the way out. A view left behind is a browser painted over whichever
      // tab replaced this one, with nothing on screen explaining where it came from.
      void bridge.ask({ verb: "hide", owner });
    };
  }, [bridge, owner, visible]);

  /* No shell means no browser, said plainly. This is the one surface that genuinely cannot
   * work in a browser tab pointed at the server: the whole mechanism is a native view the
   * desktop app composites, so there is nothing to fall back to and pretending otherwise would
   * leave an empty rectangle and no explanation. */
  if (!bridge) {
    return (
      <Absent
        title="Browser"
        detail="A browser tab needs the Kith desktop app — this is a native view the app draws, so there is nothing to show here."
      />
    );
  }

  const tabs = status.tabs ?? [];

  return (
    <div className="flex h-full w-full flex-col">
      {/* The strip. Always drawn, one tab or ten, so "+" is where a person looks for it — and so
          a tab he opened from a link is visible the moment it exists rather than a page that
          changed under them. Kith's chrome, like the address bar: no page can draw or fake it. */}
      <div
        role="tablist"
        aria-label="Browser tabs"
        className="border-border/60 bg-muted/40 flex flex-none items-end gap-0.5 overflow-x-auto border-b px-1.5 pt-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((tab) => {
          const front = tab.id === status.tab;
          const label = tabLabel(tab);
          return (
            <div
              key={tab.id}
              className={`group/tab relative flex h-7 min-w-[72px] max-w-[180px] flex-1 items-center rounded-t-md border border-b-0 ${
                front
                  ? "border-border/60 bg-background text-foreground"
                  : "text-muted-foreground hover:bg-accent/50 border-transparent"
              }`}
            >
              <button
                type="button"
                role="tab"
                aria-selected={front}
                title={tab.url || label}
                onClick={() => {
                  if (!front) void ask("tab-switch", { tab: tab.id });
                }}
                // A middle-click closes, as it does in every browser.
                onAuxClick={(event) => {
                  if (event.button === 1) void ask("tab-close", { tab: tab.id });
                }}
                className="flex h-full min-w-0 flex-1 items-center gap-1.5 truncate ps-2.5 pe-6 text-start text-[11px]"
              >
                {tab.loading ? (
                  <span aria-label="loading" className="border-muted-foreground/60 size-2.5 flex-none animate-spin rounded-full border border-t-transparent" />
                ) : null}
                <span className="truncate">{label}</span>
              </button>
              <button
                type="button"
                aria-label={`Close ${label}`}
                title="Close tab"
                onClick={() => void ask("tab-close", { tab: tab.id })}
                className={`hover:bg-foreground/10 absolute end-1 flex size-4 items-center justify-center rounded ${
                  front ? "opacity-70" : "opacity-0 group-hover/tab:opacity-70"
                }`}
              >
                <X className="size-3" aria-hidden />
              </button>
            </div>
          );
        })}
        <Button
          variant="ghost"
          size="icon"
          className="mb-0.5 size-6 flex-none"
          onClick={() => void ask("tab-new")}
          title="New tab"
          aria-label="New tab"
        >
          <Plus className="size-3.5" />
        </Button>
      </div>

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

        {/* The viewport chip. Shown only when a size is in force, because chrome for a default
            state is noise. Clicking it opens the row that set it — putting the page back is one
            click from there, and it is no longer the *only* thing somebody might want to do with
            a viewport now that they can choose one themselves. */}
        {status.viewport ? (
          <button
            type="button"
            onClick={() => setPanel((now) => (now === "viewport" ? "" : "viewport"))}
            title="The size this page is laid out at"
            className="border-kith/40 bg-kith-soft text-kith ml-1 flex flex-none items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]"
          >
            {status.viewport.width} × {status.viewport.height}
            {/* The page is laid out at the full size and painted smaller when the pane cannot
                hold it. Saying which is the whole point — a page silently cut down to the pane
                and reported as the size that was asked for is what this replaced. */}
            {status.scale < 0.999 ? ` · ${Math.round(status.scale * 100)}%` : ""}
          </button>
        ) : null}

        <Button
          variant="ghost"
          size="icon"
          className={`ml-1 size-7 ${picking ? "text-kith" : ""}`}
          onClick={() => void startPick()}
          disabled={picking}
          title={picking ? "Click something in the page — Esc to cancel" : "Pick a component and send it to the chat"}
          aria-label="Pick a component"
          aria-pressed={picking}
        >
          <Crosshair className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className={`size-7 ${panel === "viewport" ? "text-kith" : ""}`}
          onClick={() => setPanel((now) => (now === "viewport" ? "" : "viewport"))}
          title="Lay the page out at another size"
          aria-label="Screen size"
          aria-pressed={panel === "viewport"}
        >
          <Monitor className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className={`size-7 ${panel === "cookies" ? "text-kith" : ""}`}
          onClick={() => setPanel((now) => (now === "cookies" ? "" : "cookies"))}
          title="Copy a login over from another browser"
          aria-label="Import cookies"
          aria-pressed={panel === "cookies"}
        >
          <KeyRound className="size-3.5" />
        </Button>
      </header>

      {/* Rows, never popovers. A menu drawn over this pane would be *underneath* the page — see
          the note at the top of `browser-tools`. A row shortens the hole instead, and the resize
          observer below moves the page down to match. */}
      {panel === "viewport" ? (
        <ViewportRow ask={ask} viewport={status.viewport} scale={status.scale} />
      ) : null}
      {panel === "cookies" ? <CookieRow ask={ask} url={status.url} /> : null}

      {/* The hole. Nothing renders here — the shell paints the page over this rectangle. The
          background shows for the instant before the first `place` lands, and behind a page
          that has not painted. */}
      <div ref={hole} className="bg-muted flex min-h-0 flex-1 items-center justify-center">
        {/* With no tab open there is no view to paint here, so the hole says what to do instead of
            sitting blank. Inside the hole on purpose: `covered()` counts what is in it as the
            pane's own, so this never reads as something drawn over the browser. */}
        {tabs.length === 0 ? (
          <p className="text-muted-foreground px-6 text-center text-xs">
            Nothing is open. Type an address above, or ask him to open one.
          </p>
        ) : null}
      </div>

      {picked ? (
        <p className="border-border/60 text-muted-foreground flex-none border-t px-2.5 py-1 text-[11px]">
          {picked}
        </p>
      ) : null}

      {status.failure ? (
        <p className="border-border/60 text-muted-foreground flex-none border-t px-2.5 py-1 text-[11px]">
          {status.failure}
        </p>
      ) : null}
    </div>
  );
}
