import { useCallback, useEffect, useRef, useState } from "react";
import { PlugZap, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FRAME_SANDBOX } from "@/lib/canvas";
import { canvasTokens } from "@/lib/canvas-bridge";
import { paletteFor } from "@/lib/kith-palette";
import { readPluginMessage } from "@/lib/plugin-bridge";
import { indexSettled, pluginSurface } from "@/lib/plugin-index";
import { useDarkMode } from "@/lib/theme";

/**
 * One plugin surface, in a sealed frame.
 *
 * **Nothing about the seal changes here.** Same `sandbox` attribute a canvas gets, same policy,
 * same opaque origin — and the document arrives with every asset already inlined, so there is
 * nothing for it to fetch and no reason to widen anything. `lib/canvas.test.ts` is the test that
 * fails if someone loosens the seal for convenience, and it passes untouched.
 *
 * The frame has **no verb that reaches outside the plugin's own store**. It can say it is ready,
 * say how tall it is, answer a question core asked, and write its own state. It cannot ask core
 * to navigate, fold, open a tab, or fetch anything. That is what keeps every privileged effect
 * behind chrome Kith drew, and it is why a person clicking such a button can be treated as the
 * authorisation for it.
 *
 * ## Why the mount is a two-step ticket
 *
 * A frame's `src` cannot carry `X-Kith-Token`, and adding a guessable path to the open-GET list
 * would drop the unguessable-id property that exemption actually rests on. So: an authenticated
 * POST returns 24 random bytes, and the frame loads that.
 */
export function PluginSurface({
  plugin,
  view,
  instance,
  conversationId,
}: {
  plugin: string;
  view: string;
  instance?: string;
  conversationId: string;
}) {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const ticket = useRef<string>("");
  const [url, setUrl] = useState("");
  const [failure, setFailure] = useState("");
  /** Bumped to remount the frame — the one recovery this offers, and only on request. */
  const [attempt, setAttempt] = useState(0);
  // `installApiToken` patches `window.fetch` for `/api/` paths, so a plain fetch is
  // authenticated and a second helper would be a second place to forget the header.
  const dark = useDarkMode();
  const tokens = canvasTokens(paletteFor(dark));
  const declared = pluginSurface(plugin, view);

  /* The client id is per-renderer, and it is why two windows on one backend are two mounts.
   *
   * Without it a second window's mount would evict the first's ticket for the same surface, and
   * a person would click a button in one window and watch a command act in the other. */
  const client = useRef<string>("");
  if (!client.current) client.current = Math.random().toString(36).slice(2, 10);

  const mount = useCallback(async () => {
    setFailure("");
    try {
      const answer = await fetch(`/api/plugins/${plugin}/surface/${view}/mount`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instance: instance ?? "",
          client: client.current,
          conversation: conversationId,
          theme: tokens,
        }),
      });
      const body = await answer.json();
      if (!answer.ok) throw new Error(body?.error || `could not mount (${answer.status})`);
      ticket.current = body.ticket;
      setUrl(body.url);
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : String(err));
    }
  }, [conversationId, instance, plugin, tokens, view]);

  useEffect(() => {
    if (!declared) return;
    void mount();
    return () => {
      // Released rather than left to expire. The ticket table *is* the liveness table, so a
      // stale entry is what a call would park on and then time out against.
      const held = ticket.current;
      ticket.current = "";
      if (held) void fetch(`/api/plugins/frame/${held}`, { method: "DELETE" }).catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plugin, view, instance, conversationId, attempt, !!declared]);

  /* Everything the frame says, and the order the checks happen in.
   *
   * `event.source` first, before the type switch. `event.origin` is the string "null" for every
   * sandboxed frame on the page, so it distinguishes nothing — which window sent it is the only
   * trustworthy fact about a message from an opaque frame. The canvas bridge checks type first;
   * harmless with one inbound message, not harmless with three. */
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const here = frame.current;
      if (!here || event.source !== here.contentWindow) return;
      const message = readPluginMessage(event.data);
      if (!message) return;
      if (message.type === "state.set") {
        void fetch(`/api/plugins/frame/${ticket.current}/state`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values: message.values, expect: message.expect }),
        }).catch(() => {});
      } else if (message.type === "state.drop") {
        void fetch(`/api/plugins/frame/${ticket.current}/drop`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keys: message.keys }),
        }).catch(() => {});
      }
      /* `ready` and `size` are facts the frame states about itself and need no reply. `size` is
       * ignored for a pane surface, which is sized by the layout — a plugin does not get to
       * resize the person's window arrangement. */
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  /** Push the plugin's state in, so the surface renders from one source rather than two. */
  const push = useCallback(async () => {
    const here = frame.current?.contentWindow;
    if (!here) return;
    try {
      const answer = await fetch(
        `/api/plugins/${plugin}/state?conversation=${encodeURIComponent(conversationId)}`,
      );
      const body = await answer.json();
      here.postMessage(
        { kith: 1, type: "render", rev: Date.now(), state: body?.values ?? {}, host: {} },
        "*",
      );
    } catch {
      /* A surface with no state renders its empty case, which is a state it has to have anyway. */
    }
  }, [conversationId, plugin]);

  /* Repainted rather than remounted when the theme changes — the frame keeps its scroll position
   * and anything half-typed, which a remount would throw away. */
  useEffect(() => {
    frame.current?.contentWindow?.postMessage(
      { kith: 1, type: "theme", dark, tokens },
      "*",
    );
  }, [dark, tokens]);

  if (!declared) {
    /* Three answers, not two. `loading` is the window between the layout rehydrating from local
     * storage at module import and the first `GET /api/plugins`; a tab that claims a plugin is
     * missing for 200ms and then works is worse than one that says nothing. */
    if (!indexSettled()) return <div className="h-full w-full" aria-busy="true" />;
    return (
      <Absent
        title={view}
        detail={`${plugin} is not installed, so this tab has nothing to show.`}
      />
    );
  }

  if (failure) return <Absent title={declared.title} detail={failure} onRetry={() => setAttempt((n) => n + 1)} />;

  return (
    <div className="relative h-full w-full">
      {url ? (
        <iframe
          key={`${plugin}/${view}/${attempt}`}
          ref={frame}
          src={url}
          title={declared.title}
          onLoad={() => void push()}
          // The seal, unchanged and unwidened. `allow-scripts` and nothing else: with
          // `allow-same-origin` beside it the frame would share this app's origin and could
          // reach the backend with the session it already trusts, which is the entire thing
          // being prevented.
          sandbox={FRAME_SANDBOX}
          className="h-full w-full border-0 bg-transparent"
        />
      ) : null}
    </div>
  );
}

/** The pane body for a plugin that is not there, or a mount that failed.
 *
 * A placeholder rather than a discarded layout. Uninstalling a plugin used to reset every split
 * and every size in the window, because an unknown surface failed the stored-layout check at the
 * root; it costs one tab now, and the tab says which plugin it was waiting for. */
function Absent({
  title,
  detail,
  onRetry,
}: {
  title: string;
  detail: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2.5 px-6 text-center">
      <PlugZap className="text-muted-foreground/40 size-5" />
      <p className="text-sm font-medium">{title}</p>
      <p className="text-muted-foreground max-w-xs text-[12px]">{detail}</p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-1 gap-1.5">
          <RefreshCw className="size-3.5" />
          Try again
        </Button>
      ) : null}
    </div>
  );
}
