/** Files the viewer shows without decoding them as text: a picture, a PDF, or a hand-off
 *  to whatever application owns the type. */
import { useEffect, useState } from "react";
import { ExternalLink, FileBox, FolderOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { rawFileUrl } from "@/lib/files";
import { IconAction } from "./icon-action";

/**
 * A file's own bytes, as a URL an `<img>` or `<embed>` can use.
 *
 * The indirection is the API token. Every `/api` request needs a header, `fetch` is wrapped
 * once to add it — and an image element does not go through `fetch`, so pointing `src`
 * straight at the endpoint yields a 401 and a broken-image icon with nothing in the console
 * to say why. So the bytes are fetched (with the header), turned into a blob URL, and that
 * is what the element gets.
 *
 * Revoked on the way out. A blob URL pins its bytes in memory until it is released, and a
 * few full-page screenshots is tens of megabytes held by a viewer that has been closed.
 */
/** The bytes of one of his files, as a URL an `<img>` can use.
 *
 *  A fetch and a blob rather than pointing `src` straight at the endpoint: every API call
 *  carries a token header, and an `<img>` cannot send one. Exported because the tool-result
 *  card for "he looked at a picture" needs exactly this and there is no second way to do it. */
export function useMedia(
  path: string,
  projectId?: number | null,
): { url: string; error: string; loading: boolean } {
  const [state, setState] = useState<{ url: string; error: string }>({ url: "", error: "" });

  useEffect(() => {
    let cancelled = false;
    let created = "";
    setState({ url: "", error: "" });

    fetch(rawFileUrl(path, projectId))
      .then(async (response) => {
        if (!response.ok) {
          // The server sends JSON on refusal — too big, no such file, not allowed — and
          // that sentence is the useful thing to show.
          const body = (await response.json().catch(() => ({}))) as { error?: string };
          throw new Error(body.error ?? `couldn't read it (${response.status})`);
        }
        return response.blob();
      })
      .then((blob) => {
        created = URL.createObjectURL(blob);
        if (cancelled) URL.revokeObjectURL(created);
        else setState({ url: created, error: "" });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ url: "", error: err instanceof Error ? err.message : String(err) });
      });

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [path, projectId]);

  return { ...state, loading: !state.url && !state.error };
}

/** A picture, at its own size up to the width of the window.
 *
 * Checkered behind, because a PNG with transparency on a dark background is otherwise
 * indistinguishable from a PNG with a dark background — and knowing which one he produced
 * is often the entire question being asked of the viewer. */
export function ImageBody({
  path,
  alt,
  projectId,
}: {
  path: string;
  alt: string;
  projectId?: number | null;
}) {
  const { url, error, loading } = useMedia(path, projectId);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);

  if (error) return <p className="p-5 text-sm text-destructive">{error}</p>;
  if (loading) return <Loading label="Loading it…" />;

  return (
    <div className="flex min-h-full flex-col items-center gap-3 p-6">
      <div className="checkerboard max-w-full overflow-hidden rounded-lg ring-1 ring-border/60">
        <img
          src={url}
          alt={alt}
          onLoad={(event) =>
            setSize({
              w: event.currentTarget.naturalWidth,
              h: event.currentTarget.naturalHeight,
            })
          }
          className="block h-auto max-w-full"
        />
      </div>
      {size ? (
        <p className="text-muted-foreground text-[11px] tabular-nums">
          {size.w.toLocaleString()} × {size.h.toLocaleString()}
        </p>
      ) : null}
    </div>
  );
}

/** A PDF, in the browser's own reader — pages, scrolling, search and print for free. */
export function PdfBody({ path, projectId }: { path: string; projectId?: number | null }) {
  const { url, error, loading } = useMedia(path, projectId);

  if (error) return <p className="p-5 text-sm text-destructive">{error}</p>;
  if (loading) return <Loading label="Loading it…" />;
  // <embed> rather than <iframe>: Chromium hands the blob to its built-in PDF viewer
  // either way, and an embed cannot navigate itself somewhere else.
  return <embed src={url} type="application/pdf" className="size-full" title={path} />;
}

/**
 * A video, played here rather than handed to another application.
 *
 * From a blob rather than pointed at `/api/workspace/raw` directly, and that is forced rather
 * than chosen: the API needs its token on every call, a `<video src>` is a browser navigation
 * and cannot carry a header, and the token has already been ruled out of query strings because
 * it would print into the request log. `useMedia` fetches it — which does carry the header — and
 * hands over a blob. It costs waiting for the whole file before the first frame, which over
 * loopback against a 40MB ceiling is not a wait anyone will notice.
 *
 * `controls` and nothing else. No autoplay, because a video opening in a preview pane and
 * starting to make noise is the behaviour everybody has learned to hate.
 */
export function VideoBody({ path, projectId }: { path: string; projectId?: number | null }) {
  const { url, error, loading } = useMedia(path, projectId);

  if (error) return <p className="text-destructive p-5 text-sm">{error}</p>;
  if (loading) return <Loading label="Loading it…" />;
  return (
    <div className="flex size-full items-center justify-center bg-black/40 p-4">
      {/* The element reports its own failure. A `.mov` carrying ProRes is a file Chromium will
          not decode, and no extension table can tell that from one carrying H.264 — so the
          fallback is a sentence and the buttons in the header, not a guess made in advance. */}
      <video
        src={url}
        controls
        className="max-h-full max-w-full rounded-lg shadow-lg"
        title={path}
      >
        <p className="text-muted-foreground p-5 text-sm">
          This one will not play here — open it in another application.
        </p>
      </video>
    </div>
  );
}

/** Sound, with the waveform the browser gives us and nothing invented on top. */
export function AudioBody({ path, projectId }: { path: string; projectId?: number | null }) {
  const { url, error, loading } = useMedia(path, projectId);

  if (error) return <p className="text-destructive p-5 text-sm">{error}</p>;
  if (loading) return <Loading label="Loading it…" />;
  return (
    <div className="flex size-full flex-col items-center justify-center gap-3 p-6">
      <audio src={url} controls className="w-full max-w-lg" title={path} />
      <span className="text-muted-foreground/60 font-mono text-[11px]">
        {path.split("/").pop()}
      </span>
    </div>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <p className="flex items-center gap-2 p-5 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      {label}
    </p>
  );
}

/**
 * What to show instead of a text body for a file this window can't render.
 *
 * The whole point of the viewer is to see the work, and for a spreadsheet the honest
 * answer is "not here — in the app you already use for spreadsheets". So this is a
 * real invitation with the application named, rather than a red line of prose next to
 * an icon nobody would think to press.
 */
export function NeedsAnApp({
  name,
  onOpenOnHost,
  because,
}: {
  name: string;
  onOpenOnHost?: (reveal: boolean) => Promise<unknown>;
  /** Why it cannot be shown, when the reason is not simply "it isn't text" — a file past the
   *  size the viewer will read, most often. Given here rather than printed in red on its own,
   *  because the answer to "too big to show" is the same two buttons as the answer to "not
   *  text", and a bare error is a dead end where a way out already exists. */
  because?: string;
}) {
  const [app, setApp] = useState<string | null>(null);
  const [busy, setBusy] = useState<"open" | "reveal" | null>(null);
  const [failed, setFailed] = useState("");

  // Asked by extension, so the button can be labelled without touching the file.
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/workspace/opens-with?path=${encodeURIComponent(name)}`)
      .then((response) => response.json())
      .then((body: { opensWith?: string | null }) => {
        if (!cancelled) setApp(body.opensWith ?? null);
      })
      .catch(() => {
        /* the generic label is a fine fallback */
      });
    return () => {
      cancelled = true;
    };
  }, [name]);

  const run = (reveal: boolean) => {
    if (!onOpenOnHost) return;
    setBusy(reveal ? "reveal" : "open");
    setFailed("");
    onOpenOnHost(reveal)
      .catch((err: unknown) => setFailed(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-12 text-center">
      <span className="mb-2 flex size-11 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        <FileBox className="size-5" />
      </span>
      <p className="text-sm font-medium">This one needs its own application</p>
      <p className="text-muted-foreground max-w-sm text-xs leading-relaxed">
        {because
          ? `${because}. ${app ? `${app} is what this machine opens it with.` : "It'll open in whatever you normally use for this kind of file."}`
          : app
            ? `It's not text, so there's nothing to show here. ${app} is what this machine opens it with.`
            : "It's not text, so there's nothing to show here. It'll open in whatever you normally use for this kind of file."}
      </p>

      {onOpenOnHost ? (
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          <Button onClick={() => run(false)} disabled={busy !== null}>
            {busy === "open" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <ExternalLink className="size-4" />
            )}
            {app ? `Open in ${app}` : "Open in another app"}
          </Button>
          <Button variant="outline" onClick={() => run(true)} disabled={busy !== null}>
            {busy === "reveal" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <FolderOpen className="size-4" />
            )}
            Show in folder
          </Button>
        </div>
      ) : null}

      {failed ? <p className="text-destructive mt-3 max-w-sm text-xs">{failed}</p> : null}
      <p className="text-muted-foreground/60 mt-3 text-[11px]">
        It opens where it lives, so anything you change there is the real file.
      </p>
    </div>
  );
}

/** Getting the file out of the sandbox and into an app you already have.
 *
 * Two actions rather than one: "open" is what you want for a spreadsheet or a PDF the
 * viewer can't render well, and "show in folder" is what you want when you're about to
 * do something else with it — or when it's a script, which the server reveals rather
 * than runs.
 */
export function HostActions({ onOpenOnHost }: { onOpenOnHost: (reveal: boolean) => Promise<unknown> }) {
  const [busy, setBusy] = useState<"open" | "reveal" | null>(null);
  const [failed, setFailed] = useState("");

  const run = (reveal: boolean) => {
    setBusy(reveal ? "reveal" : "open");
    setFailed("");
    onOpenOnHost(reveal)
      .catch((err: unknown) => setFailed(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  return (
    <>
      {failed ? (
        <span className="text-destructive max-w-56 truncate text-[11px]" title={failed}>
          {failed}
        </span>
      ) : null}
      <IconAction
        label="Open in another app"
        onClick={() => run(false)}
        icon={
          busy === "open" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <ExternalLink className="size-4" />
          )
        }
      />
      <IconAction
        label="Show in folder"
        onClick={() => run(true)}
        icon={
          busy === "reveal" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <FolderOpen className="size-4" />
          )
        }
      />
    </>
  );
}
