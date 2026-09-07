import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import "./board.css";

/**
 * What the browser is looking at, and a way for you to reach into it.
 *
 * ## Why this is a view rather than the browser itself
 *
 * A surface is a sealed frame — `default-src 'none'`, no network, no cross-origin frames. There
 * is nothing here that could load a page, let alone drive one. So Chromium lives in the plugin's
 * MCP server, where a plugin's code is allowed to do real work, and this shows what it sees.
 *
 * ## How the picture gets in
 *
 * The server writes a PNG into the plugin's own storage and puts its path in `_kith_state`,
 * which the host writes into the store. This reads the path from `render`, asks the host for the
 * *bytes* — a frame cannot fetch — and the bridge hands them back as a `blob:` URL, which the
 * seal permits for images. Three hops, and every one of them exists because the frame is opaque.
 *
 * ## What you can do that he cannot
 *
 * Click. He drives through the server; you click on the picture, which records a coordinate in
 * the store and shows it as pending. His skill tells him to check for that before carrying on,
 * so "you are stuck at a login, I clicked the button, carry on" works without either of you
 * describing the page to the other.
 *
 * He can also *ask* what you are looking at — `where` is a `surface` command, so it is routed
 * into this frame and awaited. That is the only thing here the store could not have done.
 */

interface Step {
  at: number;
  note: string;
  url: string;
  title: string;
}

declare global {
  interface Window {
    kith: {
      plugin: string;
      view: string;
      render(handler: (state: Record<string, unknown>) => void): void;
      on(name: string, handler: (args: Record<string, unknown>) => unknown): void;
      asset(key: string): string;
      onasset(handler: (key: string, url: string) => void): void;
      state: {
        set(values: Record<string, unknown>): void;
        drop(keys: string[]): void;
      };
      file(name: string, mime: string, bytes: ArrayBuffer): void;
      /** Set off one of this plugin's own commands. Bounded by the manifest — see `board.tsx`. */
      run(name: string, args?: Record<string, unknown>): void;
    };
  }
}

function Board() {
  const [shot, setShot] = useState("");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [size, setSize] = useState({ width: 1280, height: 820 });
  const [steps, setSteps] = useState<Step[]>([]);
  const [waiting, setWaiting] = useState<{ x: number; y: number } | null>(null);
  /** What is in the address bar. Separate from `url` so typing is not overwritten mid-word by
   *  a push, and re-synced only when the page actually changes underneath. */
  const [typed, setTyped] = useState("");
  /** Something is in flight. Cleared by the next push, because a push *is* the answer — the
   *  server screenshots after every step, so a new `shot` means the step finished. */
  const [busy, setBusy] = useState(false);
  const image = useRef<HTMLImageElement | null>(null);

  /** Set off one of this plugin's own commands.
   *
   * `kith.run` is the frame's one verb that reaches past its own store, and it is bounded by
   * the manifest: the host refuses any name not declared `present: {in: "surface"}`, and a
   * command with `host` delivery cannot be declared that way at all. So this can drive the
   * browser and can never fold a conversation or rearrange the window. */
  const run = useCallback((name: string, args: Record<string, unknown> = {}) => {
    setBusy(true);
    window.kith.run(name, args);
    // A floor, so a command that fails silently on the server does not leave the bar spinning
    // for ever. The push normally beats this comfortably.
    window.setTimeout(() => setBusy(false), 30_000);
  }, []);

  useEffect(() => {
    /* Bytes arriving for a key. Turned into a `blob:` URL by the bridge before it gets here, so
     * this never handles an ArrayBuffer — it just gets something it can put in a `src`. */
    window.kith.onasset((key, blobUrl) => {
      if (key === "shot") setShot(blobUrl);
    });

    window.kith.render((state) => {
      if (typeof state.url === "string") {
        setUrl(state.url);
        // Only when the page has genuinely moved. Overwriting on every push would eat what
        // somebody is halfway through typing, and the store is pushed on every write.
        setTyped((held) => (state.url !== url ? String(state.url) : held));
      }
      // A push carries a fresh screenshot, which means the step it was waiting on is done.
      setBusy(false);
      if (typeof state.title === "string") setTitle(state.title);
      if (Array.isArray(state.steps)) setSteps(state.steps as Step[]);
      if (typeof state.width === "number" && typeof state.height === "number") {
        setSize({ width: state.width, height: state.height });
      }
      const pending = state.click as { x: number; y: number } | undefined;
      setWaiting(pending && typeof pending.x === "number" ? pending : null);

      /* The picture itself is not in here — `shot` is a path. The manifest declares it as an
       * asset key, so the host reads the bytes and pushes them to `onasset` above. This frame
       * asks for nothing, which is the rule that keeps every privileged effect behind chrome
       * Kith drew. */
    });

    /* `where` is the two-way half: he asks this frame what is on screen and waits for the
     * answer. It is the one thing the store could not have told him, because the answer includes
     * what *you* are looking at — the scroll position, and whether a click is pending — which
     * only exists in this window. */
    window.kith.on("where", () => ({
      url,
      title,
      showing: shot ? "a screenshot" : "nothing yet",
      pendingClick: waiting ? `${waiting.x},${waiting.y}` : "",
      steps: steps.length,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* The handler above closes over the first render's values, so it is re-registered whenever any
   * of them change. `on` replaces by name, so this is a swap rather than a second listener. */
  useEffect(() => {
    window.kith.on("where", () => ({
      url,
      title,
      showing: shot ? "a screenshot" : "nothing yet",
      pendingClick: waiting ? `${waiting.x},${waiting.y}` : "",
      steps: steps.length,
    }));
  }, [url, title, shot, waiting, steps.length]);

  /** A click on the picture, in the browser's own coordinates rather than this pane's. */
  const onClick = useCallback(
    (event: React.MouseEvent<HTMLImageElement>) => {
      const box = image.current?.getBoundingClientRect();
      if (!box) return;
      // Scaled back to the viewport the server screenshotted at, so the coordinate means
      // something to the browser rather than to whatever width this pane happens to be.
      const x = Math.round(((event.clientX - box.left) / box.width) * size.width);
      const y = Math.round(((event.clientY - box.top) / box.height) * size.height);
      setWaiting({ x, y });
      window.kith.state.set({ click: { x, y, at: Date.now() } });
    },
    [size.height, size.width],
  );

  return (
    <div className="browser-shell">
      <header className="browser-chrome">
        <div className="browser-nav">
          <button
            type="button"
            onClick={() => run("go_back")}
            disabled={busy || !url}
            title="Back"
            aria-label="Back"
          >
            <svg className="kith-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m15 18-6-6 6-6" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => run("refresh")}
            disabled={busy || !url}
            title="Reload"
            aria-label="Reload"
          >
            <svg className="kith-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
              <path d="M21 3v5h-5" />
              <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
              <path d="M8 16H3v5" />
            </svg>
          </button>
        </div>

        {/* The address bar. Typing here goes through `navigate`, which the manifest declares as
            surface-invocable and which the plugin's own server performs — so a page you open by
            hand and one he opens are the same act, and the tab updates the same way. */}
        <form
          className="browser-address"
          onSubmit={(event) => {
            event.preventDefault();
            const wanted = typed.trim();
            if (!wanted) return;
            // A bare host is what a person types. Guessing `https` beats refusing them.
            run("navigate", { url: /^[a-z]+:\/\//i.test(wanted) ? wanted : `https://${wanted}` });
          }}
        >
          <input
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onFocus={(event) => event.currentTarget.select()}
            placeholder="Type an address, or ask him to open one"
            spellCheck={false}
            aria-label="Address"
          />
          {busy ? <span className="browser-busy" aria-label="Loading" /> : null}
        </form>

        {waiting ? (
          <button
            type="button"
            className="browser-pending"
            onClick={() => {
              setWaiting(null);
              window.kith.state.drop(["click"]);
            }}
            title="Cancel the click you queued"
          >
            click at {waiting.x},{waiting.y} — waiting for him
          </button>
        ) : null}
      </header>

      {shot ? (
        <div className="browser-page">
          <img
            ref={image}
            src={shot}
            alt={title || "the page"}
            onClick={onClick}
            title="Click to queue a click for him — he checks before his next step"
          />
        </div>
      ) : (
        <p className="browser-empty">
          Nothing open yet. Type an address above, or ask him to open one — he can read the
          page, click things and type into it, and you will see every step here.
        </p>
      )}

      {/* What he did, as one line.
       *
       * It was an open list, and after seven steps it had taken a third of the pane and pushed
       * the page — the thing the tab exists to show — up out of view. A log is reference: worth
       * having, worth almost no room until asked for. So the latest step stays visible, because
       * that one *is* status, and the rest is a disclosure.
       *
       * `<details>` rather than state, so it opens on a keyboard and reads correctly to a
       * screen reader without any of that being written here. Closed on every mount on
       * purpose: it is not worth a slot in the store he can see. */}
      {steps.length ? (
        <details className="browser-log">
          <summary>
            <span className="browser-step-note">{steps[steps.length - 1].note}</span>
            <span className="browser-step-title">{steps[steps.length - 1].title}</span>
            <span className="browser-log-count">
              {steps.length} step{steps.length === 1 ? "" : "s"}
            </span>
          </summary>
          <ol className="browser-steps">
            {steps
              .slice()
              .reverse()
              .map((step) => (
                <li key={step.at}>
                  <span className="browser-step-note">{step.note}</span>
                  <span className="browser-step-title">{step.title}</span>
                </li>
              ))}
          </ol>
        </details>
      ) : null}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Board />);
