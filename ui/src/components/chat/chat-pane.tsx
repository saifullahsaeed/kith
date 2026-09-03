import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AssistantRuntimeProvider,
  useAuiState,
  useComposerRuntime,
  useLocalRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { Thread, latestUsage } from "@/components/assistant-ui/thread";
import { CheckpointsProvider } from "@/components/assistant-ui/checkpoints-context";
import { SessionBar } from "@/components/chat/session-bar";
import { ThreadSkeleton } from "@/components/chat/thread-skeleton";
import { ErrorBoundary } from "@/components/shell/error-boundary";
import { useLayout } from "@/components/shell/layout/store";
import { tabKey } from "@/components/shell/layout/tree";
import { toThreadMessages } from "@/components/chat/to-thread-messages";
import { useServerEvent } from "@/hooks/use-live";
import { AnyFileAttachmentAdapter } from "@/lib/attachments";
import { clearActiveComposer, setActiveComposer } from "@/lib/active-composer";
import { useFocusedChat } from "@/lib/focused-chat";
import { keys } from "@/lib/query-keys";
import {
  createBackendAdapter,
  fetchConversation,
  resumeTurn,
  setConversationProject,
  type StoredTurn,
} from "@/lib/backend";

/**
 * One conversation, with its own runtime, its own window into its transcript, and its own
 * scroll.
 *
 * **A pane's conversation never changes.** That single constraint is what makes several chats
 * open at once tractable, and it deleted more code than it added. When one runtime was shared
 * across every conversation, switching had to cancel the run in flight, wait for it to actually
 * stop — not merely to be asked to — and only then replace the messages, because `cancelRun`
 * returns while the run is still unwinding and its `finally` writes a cancelled status against
 * a message id `reset` has already cleared. That race is what "I can't switch while one is
 * running, and then it switches on its own a minute later" was. It cannot happen here: a
 * runtime that only ever holds one conversation has nothing to swap. The composer clearing goes
 * with it, for the same reason — each pane has its own.
 *
 * What a pane still owns:
 *
 * * a page of the transcript, and the `before` cursor for the page above it;
 * * rejoining whatever turn is already running in its conversation, which is what makes closing
 *   the window cost the live view rather than the work;
 * * its scroll, anchored across the remount that "load earlier" performs.
 *
 * `conversationId` is `""` for a chat that has not been spoken to yet. It gets its real id from
 * the stream, and re-keys its own tab when it does — see `adopted`.
 */
export function ChatPane({
  conversationId,
  /** A project chosen before the conversation exists — "New chat here" from the sidebar. */
  initialProject = null,
  /** Whether this is the chat in front of you, which decides where a dropped file lands. */
  active = false,
}: {
  conversationId: string;
  initialProject?: number | null;
  active?: boolean;
}) {
  const cache = useQueryClient();
  const rename = useLayout((state) => state.rename);

  /* The conversation this pane is showing, which starts as the one it was opened with and
   * changes exactly once: when a draft chat is named by its first turn. Held in a ref as well,
   * because the adapter is built once and reads it later. */
  const [id, setId] = useState(conversationId);
  const idRef = useRef(id);
  idRef.current = id;
  const [projectId, setProjectId] = useState<number | null>(initialProject);
  const pendingProject = useRef<number | null>(initialProject);

  const [resumed, setResumed] = useState<ThreadMessageLike[]>([]);
  const [timeline, setTimeline] = useState<StoredTurn[]>([]);
  const [windowStart, setWindowStart] = useState(0);
  const [loading, setLoading] = useState(!!conversationId);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [nearTop, setNearTop] = useState(false);
  const anchor = useRef<{ index: number; shift: number; offset: number } | null>(null);
  /** This pane's own DOM, so a viewport query cannot find another pane's thread. */
  const root = useRef<HTMLDivElement | null>(null);

  const adapter = useMemo(
    () =>
      createBackendAdapter({
        get: () => idRef.current,
        set: (given) => setId((was) => was || given),
        project: () => pendingProject.current,
      }),
    [],
  );
  const attachments = useMemo(() => new AnyFileAttachmentAdapter(), []);
  const runtime = useLocalRuntime(adapter, {
    initialMessages: resumed,
    adapters: { attachments },
  });

  /* A draft chat that has just been named takes its tab with it.
   *
   * Closing the draft tab and opening a real one would work and would be wrong twice: the tab
   * moves to the end of the strip, and focus goes with it — during the first reply, which is
   * exactly when you are watching it. `rename` keeps the tab where it is. */
  useEffect(() => {
    if (!id || id === conversationId) return;
    rename(tabKey({ surface: "chat", conversationId: conversationId }), {
      surface: "chat",
      conversationId: id,
    });
  }, [conversationId, id, rename]);

  /* The page this pane opens on.
   *
   * Through the cache, so a conversation you were just in comes back instantly and two panes
   * showing the same one do not fetch it twice. A `turn` event invalidates the key, so one
   * whose turn finished while you were elsewhere is refetched rather than restored without its
   * reply. */
  useEffect(() => {
    if (!conversationId) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    void cache
      .fetchQuery({
        queryKey: keys.conversation(conversationId),
        queryFn: () => fetchConversation(conversationId),
      })
      .then((detail) => {
        if (!live) return;
        setProjectId(detail.projectId ?? null);
        setTimeline(detail.timeline);
        setWindowStart(detail.windowStart ?? 0);
        setResumed(toThreadMessages(detail.timeline));
        setLoading(false);
      })
      .catch(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [cache, conversationId]);

  /* Put the loaded page into the thread.
   *
   * No cancel-and-wait dance: this runtime only ever holds this conversation, so there is
   * never a foreign run to unwind first. It fires on open and on each "load earlier". */
  useEffect(() => {
    if (resumed.length) runtime.thread.reset(resumed);
  }, [runtime, resumed]);

  /* Rejoin the turn already running here.
   *
   * The screen is a window onto a turn, not the thing running it: the turn lives on the server
   * and carries on whether or not anyone is watching. Guarded on `isRunning`, because this pane
   * is already streaming when it started the turn itself — resuming then would put a second
   * reader on the same events and render every token twice. */
  const rejoin = useCallback(() => {
    if (!id) return;
    const wanted = id;
    void resumeTurn(wanted).then((attached) => {
      if (!attached) return;
      // `discard()`, never a bare return: the generator has not started, so letting it go
      // leaves the response body open and a server thread writing into it.
      if (idRef.current !== wanted) return attached.discard();
      const state = runtime.thread.getState();
      if (state.isRunning) return attached.discard();
      runtime.thread.resumeRun({
        parentId: state.messages.at(-1)?.id ?? null,
        stream: () => attached.stream,
      });
    });
  }, [id, runtime]);

  useEffect(rejoin, [rejoin]);
  // Not a query: there is nothing to refetch, there is a stream to attach to.
  useServerEvent("turn", rejoin, id);

  /* Confirm the project against the conversation once it exists. It rides *with* the first turn
   * through the adapter — writing it afterwards was a turn too late, and that turn is the one
   * that says what the work is — so this is a confirmation, and the path for changing it
   * part-way through. */
  useEffect(() => {
    if (!id || pendingProject.current === null) return;
    const wanted = pendingProject.current;
    pendingProject.current = null;
    void setConversationProject(id, wanted).catch(() => {});
  }, [id]);

  /** Fetch the page above the one on screen and put it on the front. */
  const loadEarlier = useCallback(async () => {
    if (loadingEarlier || windowStart <= 0 || !id) return;
    setLoadingEarlier(true);
    const older = await fetchConversation(id, { turns: WINDOW, before: windowStart }).catch(
      () => null,
    );
    setLoadingEarlier(false);
    if (!older || !older.timeline.length) return;

    const widened = [...older.timeline, ...timeline];
    const grown = toThreadMessages(widened);
    // Remember which message you were reading before the thread is torn down and rebuilt longer.
    const viewport = root.current?.querySelector<HTMLElement>('[data-slot="aui_thread-viewport"]');
    anchor.current = null;
    if (viewport) {
      const top = viewport.getBoundingClientRect().top;
      const messages = [...viewport.querySelectorAll<HTMLElement>("[data-message-id]")];
      // The first message still on screen — the one you are reading, not the one above it.
      const index = messages.findIndex((one) => one.getBoundingClientRect().bottom > top);
      if (index >= 0) {
        anchor.current = {
          index,
          shift: grown.length - messages.length,
          offset: messages[index].getBoundingClientRect().top - top,
        };
      }
    }
    setTimeline(widened);
    setWindowStart(older.windowStart ?? 0);
    setResumed(grown);
  }, [id, loadingEarlier, timeline, windowStart]);

  /* Whether you are near the top of what is loaded, and putting you back where you were after
   * "load earlier" rebuilt the thread.
   *
   * Scoped to this pane's own subtree. It used to be `document.querySelector`, which was correct
   * while there was one thread on screen and finds somebody else's the moment there are two.
   *
   * "Near the top" is a screenful rather than a pixel count: `content-visibility` placeholders
   * measure their real height as you arrive, so `scrollTop = 0` settles at 531 once they have,
   * and under any tight constant the offer of older messages is hidden at the exact moment you
   * scrolled up to look for it.
   *
   * The restore pins a *message* to the place it already occupied, on a frame loop, because it
   * is competing with the library's scroll-to-bottom and with that settling. Pinning an element
   * rather than an offset is what makes it exact: everything above it can change height and the
   * answer is still recomputed from where the element actually is. */
  useEffect(() => {
    const viewport = root.current?.querySelector<HTMLElement>('[data-slot="aui_thread-viewport"]');
    if (!viewport) return;

    const look = () => setNearTop(viewport.scrollTop < viewport.clientHeight);
    viewport.addEventListener("scroll", look, { passive: true });

    const held = anchor.current;
    anchor.current = null;
    let frame = 0;
    let timer = 0;
    let done = false;
    const stop = () => {
      if (done) return;
      done = true;
      cancelAnimationFrame(frame);
      look();
    };

    if (!held) {
      // A freshly opened conversation sits at the bottom; only read the position once the
      // library's own scroll has run, or the pill flashes on open.
      timer = window.setTimeout(look, SETTLE_MS);
    } else {
      const until = performance.now() + RESTORE_MS;
      const pin = () => {
        if (done) return;
        const messages = viewport.querySelectorAll<HTMLElement>("[data-message-id]");
        const mine = messages[held.index + held.shift];
        if (mine) {
          const drift =
            mine.getBoundingClientRect().top - viewport.getBoundingClientRect().top - held.offset;
          if (Math.abs(drift) > 1) viewport.scrollTop += drift;
        }
        if (performance.now() < until) frame = requestAnimationFrame(pin);
        else stop();
      };
      frame = requestAnimationFrame(pin);
      // Any input from you ends it: a loop that keeps re-seizing the scroll is worse than the
      // jump it was fixing.
      for (const event of ["wheel", "touchstart", "keydown"] as const) {
        window.addEventListener(event, stop, { passive: true, once: true });
      }
    }

    return () => {
      viewport.removeEventListener("scroll", look);
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      done = true;
      for (const event of ["wheel", "touchstart", "keydown"] as const) {
        window.removeEventListener(event, stop);
      }
    };
  }, [id, resumed]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ClaimTheDrop active={active} />
      <PublishToTheWorkPanel active={active} />
      <div ref={root} className="relative flex h-full min-h-0 flex-col">
        <SessionBar
          conversationId={id}
          projectId={projectId}
          onProject={(next) => {
            setProjectId(next);
            if (!idRef.current) pendingProject.current = next;
            else void setConversationProject(idRef.current, next).catch(() => {});
          }}
        />
        {/* The thread gets its own box with a definite height rather than sitting straight in
            the column. Without one the thread root's `h-full` resolves against the whole column
            — session bar included — and the composer's bottom edge sits past the window with the
            send button clipped off it. A flex column, not a block: `Thread` is `h-full`, so in
            block layout it takes the whole box *and* the "load earlier" pill takes its own
            height on top. Anything added beside `Thread` here goes in the flow, not on top. */}
        <div className="relative flex min-h-0 flex-1 flex-col">
          <ErrorBoundary where="The conversation">
            <CheckpointsProvider
              conversationId={id}
              /* Turns above this page. Checkpoints are tagged with an absolute turn index and
                 matched against the rendered message index, so without it "restore to here" on
                 the first visible message targets whatever happened hundreds of turns earlier —
                 a destructive action aiming at the wrong commit. */
              turnOffset={windowStart}
            >
              {windowStart > 0 && nearTop ? (
                /* Floating, not stacked: in the flow this was a band across the top of the
                   conversation that claimed its own height. `pointer-events-none` on the strip
                   so only the pill is clickable. */
                <div className="pointer-events-none absolute inset-x-0 top-3 z-10 flex justify-center">
                  <button
                    type="button"
                    onClick={() => void loadEarlier()}
                    disabled={loadingEarlier}
                    className="border-border/60 bg-card text-muted-foreground hover:text-foreground hover:border-border pointer-events-auto rounded-full border px-3 py-1 text-[11px] shadow-sm transition-colors disabled:opacity-60"
                  >
                    {loadingEarlier
                      ? "Loading earlier…"
                      : `Load ${Math.min(WINDOW, windowStart)} earlier · ${windowStart} above`}
                  </button>
                </div>
              ) : null}
              <div className="relative min-h-0 flex-1">
                {loading ? <ThreadSkeleton /> : <Thread conversationId={id} />}
              </div>
            </CheckpointsProvider>
          </ErrorBoundary>
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}

/** Turns fetched at a time. Comfortably more than a screenful, far below the point where
 *  mounting and markdown-parsing them is what you are waiting for. */
const WINDOW = 40;
/** How long to hold the restored position against the thread settling after a rebuild. Long,
 *  because the settling is: on an 80-turn conversation `scrollHeight` went 25,070 → 48,106 and
 *  was still climbing at 900ms. Any input from you ends it early. */
const RESTORE_MS = 2500;
/** How long to wait before reading the scroll position on a freshly opened thread. */
const SETTLE_MS = 600;


/** Publishes this pane's composer while it is the focused chat, so a file dropped anywhere in
 *  the window knows which conversation it was meant for. Renders nothing; it exists to be
 *  *inside* the runtime provider, which is the only place the hook resolves. */
function ClaimTheDrop({ active }: { active: boolean }) {
  const composer = useComposerRuntime();
  useEffect(() => {
    if (!active) return;
    setActiveComposer(composer);
    return () => clearActiveComposer(composer);
  }, [active, composer]);
  return null;
}

/** Publishes the two facts the Work panel's context meter needs, while this is the focused
 *  chat. Inside the provider for the same reason `ClaimTheDrop` is: that is where the state
 *  lives, and the panel that renders against it is outside every pane. */
function PublishToTheWorkPanel({ active }: { active: boolean }) {
  const messages = useAuiState((state) => state.thread.messages);
  const running = useAuiState((state) => state.thread.isRunning);
  const publish = useFocusedChat((state) => state.publish);
  const clear = useFocusedChat((state) => state.clear);
  const usage = latestUsage(messages);
  useEffect(() => {
    if (!active) return;
    publish({ usage, running });
    return clear;
  }, [active, clear, publish, running, usage]);
  return null;
}
