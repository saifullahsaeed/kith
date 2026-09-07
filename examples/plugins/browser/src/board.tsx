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
  const image = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    /* Bytes arriving for a key. Turned into a `blob:` URL by the bridge before it gets here, so
     * this never handles an ArrayBuffer — it just gets something it can put in a `src`. */
    window.kith.onasset((key, blobUrl) => {
      if (key === "shot") setShot(blobUrl);
    });

    window.kith.render((state) => {
      if (typeof state.url === "string") setUrl(state.url);
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
        <span className="browser-title">{title || "Browser"}</span>
        <span className="browser-url" title={url}>
          {url || "nothing open"}
        </span>
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
          Nothing open yet. Ask him to open a page — he can read it, click things and type into
          it, and you will see each step here.
        </p>
      )}

      {steps.length ? (
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
      ) : null}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Board />);
