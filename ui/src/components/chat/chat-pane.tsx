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
import { alreadyGoing, draftFor, useDrafts } from "@/lib/drafts";
import { rescueHeld } from "@/lib/queued-send";
import { useFocusedChat } from "@/lib/focused-chat";
import { keys } from "@/lib/query-keys";
import {
  createBackendAdapter,
  fetchConversation,
  resumeTurn,
  setConversationProject,
  type StoredTurn,
} from "@/lib/backend";

/** A live turn this pane has taken, as `resumeTurn` hands it over. */
type Attached = NonNullable<Awaited<ReturnType<typeof resumeTurn>>>;

/**
 * The stored page, minus the turn a stream is about to deliver.
 *
 * The transcript and the live stream carry the same turn while it runs, because the recorder
 * writes the turn's parts to the transcript as they happen rather than at the end. That overlap
 * is not a mistake to be removed — writing as it goes is what makes a turn survive a crash — so
 * the two ends stamp the same id on it instead, and the client drops whichever copy it is not
 * rendering. This is that drop, and it is the whole of it.
 *
 * A turn with no id is one recorded before ids existed, and is never claimed by anything. That is
 * the safe direction: it renders as history, which is exactly what a turn nobody is streaming is.
 */
function withoutTurn(timeline: StoredTurn[], turnId: string | undefined): StoredTurn[] {
  if (!turnId) return timeline;
  return timeline.filter((one) => one.turn !== turnId);
}

/** The turn that wrote the newest assistant entry on this page, if it says. */
function lastTurn(timeline: StoredTurn[]): string {
  for (let index = timeline.length - 1; index >= 0; index--) {
    const one = timeline[index];
    if (one.role === "assistant") return one.turn ?? "";
  }
  return "";
}

/**
 * Hold a claim on a turn for as long as its stream is running, and let go when it stops.
 *
 * The claim is what `load` reads to know the screen is not its to rebuild, and what the `turn`
 * listener reads to know an announcement is about its own work. Released from a `finally` so that
 * an abandoned stream — an abort, a thrown chunk, a pane closed mid-answer — lets go on exactly
 * the same terms as one that ran to the end. A claim that outlived its stream would go on hiding
 * the stored copy of that turn for as long as the pane lived, which is the original bug with the
 * sign flipped.
 */
async function* hold<T>(
  turnId: string,
  stream: AsyncGenerator<T>,
  claim: (turnId: string) => void,
): AsyncGenerator<T> {
  claim(turnId);
  try {
    yield* stream;
  } finally {
    claim("");
  }
}

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
 * What a pane's composer does *not* own any more is the text in it. A pane is unmounted the
 * moment its tab stops being the active one in its pane, and that took every half-written message
 * with it; the words live in `lib/drafts`, keyed on the tab, and this pane borrows them — see
 * `KeepTheDraft`.
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
  /** This tab's permanent id, which is where its unsent text is kept.
   *
   * The pane is not the owner of anything that should outlive it, and this is how it hands the
   * half-written message to something that does — see `lib/drafts`. Absent for a layout stored
   * before `uid` existed, and then the pane holds nothing rather than falling back to a key every
   * such tab would share. */
  uid,
}: {
  conversationId: string;
  initialProject?: number | null;
  active?: boolean;
  uid?: string;
}) {
  const cache = useQueryClient();
  const rename = useLayout((state) => state.rename);

  /* The conversation this pane is showing, which starts as the one it was opened with and
   * changes exactly once: when a draft chat is named by its first turn. Held in a ref as well,
   * because the adapter is built once and reads it later. */
  const [id, setId] = useState(conversationId);
  const idRef = useRef(id);
  idRef.current = id;
  /* Whatever this tab was holding when it was last unmounted, read once.
   *
   * Read here rather than in `KeepTheDraft` because the only part of a draft this outer component
   * needs is its *project*, and that has to be in place before anything can be sent. The words go
   * back into the composer from inside the provider, where the composer is. */
  const [heldOnMount] = useState(() => draftFor(uid ?? ""));
  /* The project this pane opens against, which only ever means anything for a chat that has no
   * conversation yet.
   *
   * Held first, `initialProject` second: `initialProject` comes from a ref the sidebar sets and
   * only `openConversation` clears, so returning to a drafted tab still sees whichever project
   * "New chat here" was last clicked on — while the draft records what *this* tab was actually
   * set to, including a change made in its own session bar. The stale ref used to win and quietly
   * revert it.
   *
   * And `null` outright once there is a conversation, because `pendingProject` below is a *bind*:
   * a conversation's project is already on the server, and re-sending it on every remount is the
   * write `workspace` withholds `initialProject` from named tabs specifically to prevent. */
  const openedIn = conversationId ? null : (heldOnMount?.projectId ?? initialProject);
  const [projectId, setProjectId] = useState<number | null>(openedIn);
  /* The project the first turn will carry.
   *
   * Seeded from the draft and not only from `initialProject`, which is not tidiness: the most
   * ordinary way to hit the draft bug at all is "New chat here" on a project, type, click another
   * chat, come back. Restoring the words without this would come back with the words and no
   * project, and send the one turn that says what the work is against nothing. */
  const pendingProject = useRef<number | null>(openedIn);

  const [resumed, setResumed] = useState<ThreadMessageLike[]>([]);
  /** A live turn waiting to be attached, once the page it belongs on top of is in the thread.
   *  Held as state rather than attached on the spot so that the two happen in a fixed order —
   *  see the pair of effects below `load`. */
  const [pending, setPending] = useState<Attached | null>(null);

  /** The turn whose stream this pane is rendering, or `""`.
   *
   *  The one fact that used to be missing. A pane is handed the same turn twice while it runs —
   *  once by the transcript, which the recorder writes as it goes, and once by the stream — and
   *  with no name in common between the two copies the only way to tell them apart was to guess
   *  from which arrived first. This is that name, and everything that used to be a timing guard
   *  now asks it instead. */
  const owned = useRef("");
  /** The last turn this pane read all the way to its end.
   *
   *  What `isRunning` cannot say after the fact: by the time a `turn` event announces an ending
   *  the run is over either way, and nothing but the pane knows whether it ever saw it. Kept as an
   *  id rather than a flag because a flag can only be spent once — an announcement arriving in the
   *  wrong order left it set, and the next turn was then skipped instead of attached to. An id is
   *  compared rather than consumed, so a stale one is simply an id that no longer matches. */
  const lastRead = useRef("");
  /** Take or release the claim. One function so the two ways in — a turn this pane started, and
   *  one it attached to — cannot drift into meaning different things. */
  const claim = useCallback((turnId: string) => {
    // Letting go is also the moment this pane can say it read that turn to the end.
    if (!turnId) lastRead.current = owned.current;
    owned.current = turnId;
  }, []);
  /* No `timeline` state.
   *
   * It held the fetched turns so that "load earlier" could rebuild the thread from them — which
   * is exactly what erased the session, because nothing ever appended a live turn to it. The
   * thread's own messages are the tail now, so the only thing worth keeping between pages is
   * where the loaded window starts. */
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
        // A turn this pane starts is owned on exactly the same terms as one it attaches to:
        // while its stream runs, the stored copy of it is not this pane's to render.
        onTurn: claim,
      }),
    [claim],
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

  /** Set while this pane is mounted. Work that lands after it is gone must let go of what it is
   *  holding rather than write into a runtime nobody is looking at. */
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  /** How many times this pane has gone to fetch itself. Only the newest attempt may write. */
  const loads = useRef(0);

  /* Put this pane back in step with the server: the stored page, and then the turn running on
   * top of it, in that order and as one operation.
   *
   * **This replaces three functions, and they were never three jobs.** The mount fetch, `catchUp`
   * and `rejoin` each did part of this, fired off in parallel from separate effects, and were
   * reconciled by asking `isRunning` at the moment each of them landed. That is a check-then-act
   * across an await over state that means something different at either end of it, and both
   * interleavings were bugs somebody had reported:
   *
   * * **page first, stream second** — the transcript already holds the half-written reply,
   *   because the recorder writes `said` and `reasoning` rows *while the turn runs*, so attaching
   *   rendered the same turn a second time underneath the first. That is the answer that appears
   *   twice.
   * * **stream first, page second** — the reset is skipped because a run is now in flight, and
   *   nothing ever retried it, so the conversation opened showing a reply with nothing above it.
   *   That is the chat that loads with all of its history missing.
   *
   * Neither was fixable with a better guard, because the guard was never the broken part: at
   * `reset` time nothing is running yet, and at `resumeRun` time `isRunning` reads false because
   * the reset has just produced a *completed* message. What was missing is that the two copies of
   * one turn had no name in common. Now they do — `X-Kith-Turn` on the stream, `turn` on the
   * stored turn — so the live turn is taken out of the page before the page is rendered, and the
   * two channels stop competing to describe the same thing.
   *
   * `fresh` bypasses the cache, and only a `turn` event asks for it: such an event races the
   * invalidation it arrives with, since `useLiveUpdates` coalesces on a 50ms timer, so the page
   * from *before* the turn is very often still fresh at this moment and a cached read would
   * carefully restore the conversation exactly as it already was. A mount wants the opposite — the
   * page it had a moment ago, on screen without a round trip.
   */
  const load = useCallback(
    async (fresh = false) => {
      const wanted = idRef.current;
      if (!wanted) {
        setLoading(false);
        return;
      }
      /* Which load this is. Two can be out at once — a `turn` event landing while the mount's
       * own is still in flight, or React's development double-mount — and two that both finish
       * would hand this pane two pages and two streams, of which it can only render one. The
       * later one wins, and the earlier discards what it was carrying rather than leaking a held
       * response. A counter rather than a promise: what matters is *which* is newest, which a
       * single in-flight flag cannot say. */
      const mine = ++loads.current;
      /* A stream running here already owns the screen, and describes the turn more accurately
       * than the transcript does — the transcript is behind by whatever has not been flushed.
       * There is nothing to catch up to, and rebuilding the thread under a live run is what
       * loses the reply. */
      if (owned.current) return;
      setLoading(true);
      const [detail, attached] = await Promise.all([
        cache
          .fetchQuery({
            queryKey: keys.conversation(wanted),
            queryFn: () => fetchConversation(wanted),
            ...(fresh ? { staleTime: 0 } : {}),
          })
          .catch(() => null),
        resumeTurn(wanted).catch(() => null),
      ]);

      /* `discard()` on every path that declines the stream, never a bare return. `readTurn` is a
       * generator whose body has not run, so its `finally` is not armed and letting it go collects
       * nothing: the response body stays open, with a `live_turns` watcher blocked writing into it
       * on the other end, holding one of the six sockets this origin gets. A tab is closed at
       * exactly the moment a turn is running, so this is not a rare path. */
      const gone = mine !== loads.current || !alive.current || idRef.current !== wanted;
      if (gone || !detail || owned.current) {
        attached?.discard();
        if (!gone) setLoading(false);
        return;
      }

      /* Nothing is live, and the newest turn on disk is the one this pane just finished reading:
       * the thread already holds it, in the form it was streamed. Rebuilding to show what is
       * already shown costs a `reset` — new message ids, a re-render of every part, and the scroll
       * settling again — which reads as a flash at the end of every single answer.
       *
       * Decided by comparing ids rather than by a flag that a turn event consumes, so an
       * announcement that arrives in an unexpected order costs nothing: the next turn has a
       * different id, does not match, and is loaded and attached to normally. */
      if (!attached && lastTurn(detail.timeline) && lastTurn(detail.timeline) === lastRead.current) {
        setLoading(false);
        return;
      }

      setProjectId(detail.projectId ?? null);
      setWindowStart(detail.windowStart ?? 0);
      setResumed(toThreadMessages(withoutTurn(detail.timeline, attached?.turnId)));
      setPending(attached);
      setLoading(false);
    },
    [cache],
  );

  /* Put the page into the thread.
   *
   * `reset` is destructive — it clears the repository and re-imports, minting new message ids —
   * so it must not land on a run in flight: the streaming reply's captured `parentId` stops
   * existing, the next chunk throws `Parent message not found`, and the reply simply disappears
   * while the server keeps generating into nothing. It does not even look stuck, because
   * `isRunning` is derived from the last message's status and after a reset that is a completed
   * stored turn.
   *
   * What changed is that this guard is no longer load-bearing for *correctness*. `load` declines
   * to fetch at all while this pane owns a stream, so the only way to reach here mid-run is for a
   * run to have started between that check and this commit — which means the person has just sent
   * something, and the history above it is already on screen and already right. Skipping used to
   * mean the history was lost for good; now it means there is nothing to put back. */
  useEffect(() => {
    if (!resumed.length) return;
    if (runtime.thread.getState().isRunning) return;
    runtime.thread.reset(resumed);
  }, [runtime, resumed]);

  /* And the live turn on top of it.
   *
   * A second effect rather than a few more lines at the end of `load`, and declared *after* the
   * reset above, because that is what orders the two: `load` sets both pieces of state in one
   * batch, so both effects run in the same commit, and React runs effects in the order they are
   * declared. The page is in the thread before a stream is ever attached to it — every time,
   * rather than whenever the two round trips happen to land. */
  useEffect(() => {
    if (!pending) return;
    setPending(null);
    if (!alive.current) return pending.discard();
    const state = runtime.thread.getState();
    if (state.isRunning) return pending.discard();
    runtime.thread.resumeRun({
      parentId: state.messages.at(-1)?.id ?? null,
      stream: () => hold(pending.turnId, pending.stream, claim),
    });
  }, [pending, runtime, claim]);

  useEffect(() => {
    void load();
  }, [load, id]);

  /* And again whenever a turn starts or ends in this conversation.
   *
   * Not a query, because what a `turn` event calls for is not a refetch but a *decision*: whether
   * this pane should be reading the turn now running, and whether what is on screen is still the
   * whole of what happened. `load` answers both, which is why there is nothing else left here.
   *
   * The one skip is a pane already holding the stream, which is looking at the very turn being
   * announced. Everything else `load` works out for itself from what the server says. */
  useServerEvent(
    "turn",
    () => {
      if (owned.current) return;
      void load(true);
    },
    id,
  );

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
    // Never while a turn is running: widening the window rebuilds the thread, and rebuilding
    // it under a live run loses the reply. The pill says so rather than doing nothing silently.
    if (loadingEarlier || windowStart <= 0 || !id) return;
    if (runtime.thread.getState().isRunning) return;
    setLoadingEarlier(true);
    const older = await fetchConversation(id, { turns: WINDOW, before: windowStart }).catch(
      () => null,
    );
    setLoadingEarlier(false);
    if (!older || !older.timeline.length) return;

    /* The older page in front of what is *on screen*, not in front of what was fetched.
     *
     * It used to rebuild from `older.timeline + timeline`, and `timeline` is written only by
     * the open fetch — nothing appends a live turn to it, because the open is an imperative
     * `fetchQuery` rather than a subscription. So loading earlier turns deleted every message
     * produced during the session from the thread: not from disk, but from in front of you,
     * which is worse because it looks like loss. Taking the thread's own messages as the tail
     * keeps the session and still puts the older page above it. */
    const onScreen = runtime.thread.getState().messages as ThreadMessageLike[];
    const older_ = toThreadMessages(older.timeline);
    const grown = onScreen.length ? [...older_, ...onScreen] : older_;
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
          // How many messages were added *in front*, which is the only number that shifts an
          // index. This was `grown.length - messages.length`: a converted-message count minus
          // a count of DOM nodes, two different things that only agree when every message is
          // mounted — so with anything virtualised or not yet laid out it pinned the wrong
          // message and fought the scroll for 2.5 seconds.
          shift: older_.length,
          offset: messages[index].getBoundingClientRect().top - top,
        };
      }
    }
    setWindowStart(older.windowStart ?? 0);
    setResumed(grown);
  }, [id, loadingEarlier, runtime, windowStart]);

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
  }, [id, loading, resumed]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ClaimTheDrop active={active} />
      <PublishToTheWorkPanel active={active} />
      <KeepTheDraft uid={uid} conversationId={id} projectId={projectId} />
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
              {/* The thread stays mounted; the skeleton lies over it.
                  Swapping them was the scroll glitch on opening. Two things went wrong and
                  both come from the thread not being in the DOM yet. The effect below looks
                  for `[data-slot="aui_thread-viewport"]` inside this pane, so while the
                  skeleton was up it found nothing, returned early, and never attached the
                  scroll listener or ran the settle — so the position was read from a thread
                  that had not laid out. And the swap itself is a mount: the thread arrived at
                  its natural height and the library's scroll-to-bottom ran against a box that
                  was still growing, which is the jump you see.

                  Mounted from the start, the viewport exists before the messages do, the
                  effect attaches once, and the skeleton is just something drawn on top until
                  there is something better to look at. */}
              <div className="relative min-h-0 flex-1">
                <Thread conversationId={id} />
                {loading ? (
                  <div className="bg-background absolute inset-0 z-20">
                    <ThreadSkeleton />
                  </div>
                ) : null}
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
/**
 * Keeps this tab's unsent message somewhere that outlives the pane, and puts it back.
 *
 * Inside the provider for the same reason `ClaimTheDrop` is: `useComposerRuntime()` resolves
 * nowhere else. **Not gated on `active`**, which is the one structural difference from both of
 * its neighbours and the whole point of it — a draft has to be held for the chat you have just
 * switched away from, which is exactly the chat that is no longer active.
 *
 * Restore, then subscribe, in that order: the subscription fires with whatever the composer holds
 * now, and reversing the two would write an empty composer over the draft it was about to be
 * given.
 *
 * **Nothing is written on unmount.** The mirror is one-directional and always current, so
 * React's mount-before-unmount ordering — the hazard `clearActiveComposer` has to guard against —
 * has nothing here to clobber, and there is no cleanup for a crash or a window close to miss.
 */
function KeepTheDraft({
  uid,
  conversationId,
  projectId,
}: {
  uid?: string;
  conversationId: string;
  projectId: number | null;
}) {
  const composer = useComposerRuntime();
  const hold = useDrafts((state) => state.hold);
  const forget = useDrafts((state) => state.forget);
  useEffect(() => {
    if (!uid) return;
    /** Set by the cleanup below, read by the attachment loop, which is the one thing here that
     *  outlives the effect that started it. */
    let gone = false;
    const waiting = draftFor(uid);
    const now = composer.getState();
    /* Only into a composer with nothing in it.
     *
     * The guard is what makes this effect safe to re-run, and it re-runs twice in the ordinary
     * life of a chat: once when the stream names it, once if the project is changed. It is also
     * what stops a restore doubling attachments — `addAttachment` appends, it does not set. */
    if (waiting && !now.text.trim() && now.attachments.length === 0) {
      composer.setText(waiting.text);
      if (waiting.quote) composer.setQuote(waiting.quote);
      // Sequentially, because `addAttachment` is async and the order they come back in is the
      // order they were pasted in — which is the order their names refer to. And abandoned if
      // this effect is torn down part-way through, so a tab clicked twice quickly does not go on
      // adding files to a composer nobody is looking at any more.
      void (async () => {
        for (const file of waiting.attachments) {
          if (gone) return;
          await composer.addAttachment(file).catch(() => {});
        }
      })();
    }
    const save = () => {
      const state = composer.getState();
      /* A message already on its way out is not a draft — see `alreadyGoing`.
       *
       * `forget` and not a bare return: the keystrokes before Enter already wrote an entry, and
       * leaving that behind is the same bug with the whole message in it. A send that fails
       * restores the text and notifies again once the flag clears, so the draft comes back on its
       * own. */
      if (alreadyGoing(state)) {
        forget(uid);
        return;
      }
      hold(uid, {
        conversationId,
        projectId,
        text: state.text,
        quote: state.quote,
        // `file` is only on an attachment that has not been sent yet, which is all of these —
        // a composer's attachments are by definition unsent.
        attachments: state.attachments.flatMap((one) => (one.file ? [one.file] : [])),
      });
    };
    // Once immediately, which is also what re-stamps the conversation id when a chat that had
    // none is finally named. `hold` compares every field, so this is a write only if it says
    // something new.
    save();
    const stop = composer.subscribe(save);
    return () => {
      gone = true;
      stop();
      /* A ⌘⏎ message still waiting on the turn belongs to this tab too.
       *
       * It is the one piece of unsent text this subscription never sees: ⌘⏎ empties the composer
       * *before* handing the words to `holdUntilIdle`, so by the time they are waiting they are
       * not in the box and not in the store. And what they are waiting on is a `deliver` closure
       * over this composer's `send` — which stops working the moment this pane does. Left alone
       * the poll ran its twenty minutes and then delivered into nothing: not sent, not shown, not
       * held. Taken back here, it is simply a draft again, with the mark to say so. */
      const waiting = uid ? rescueHeld(conversationId) : "";
      if (waiting) {
        hold(uid, {
          conversationId,
          projectId,
          text: waiting,
          quote: undefined,
          attachments: [],
        });
      }
    };
  }, [composer, uid, conversationId, projectId, hold, forget]);
  return null;
}

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
  /* Depended on by value, not by identity.
   *
   * `latestUsage` builds a fresh object out of the last message on every render, and the thread
   * re-renders on every streamed chunk — so an effect keyed on the object ran clear-then-publish
   * hundreds of times a turn, each pair a store write and a re-render of the meter. The numbers
   * are what the meter draws, so the numbers are what the effect watches. */
  const share = usage?.context?.share ?? usage?.baseline?.share ?? -1;
  const window = usage?.context?.window ?? 0;
  const folded = !!usage?.folded;
  useEffect(() => {
    if (!active) return;
    publish({ usage, running });
    return clear;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, clear, publish, running, share, window, folded]);
  return null;
}
