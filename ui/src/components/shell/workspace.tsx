import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/thread";
import { CheckpointsProvider } from "@/components/assistant-ui/checkpoints-context";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader } from "@/components/shell/app-header";
import { WorkspaceFileViewer } from "@/components/files/workspace-file-viewer";

import { WorkPanel } from "@/components/chat/work-panel";
import { HistoryPanel } from "@/components/chat/history-panel";
import { ThreadSkeleton } from "@/components/chat/thread-skeleton";
import { LayoutView } from "@/components/shell/layout/layout-view";
import { useLayout } from "@/components/shell/layout/store";
import {
  hasTab as treeHasTab,
  tabKey as tabKeyOf,
  type TabRef,
} from "@/components/shell/layout/tree";
import { SessionBar } from "@/components/chat/session-bar";
import { DropZone } from "@/components/shell/drop-zone";
import { ErrorBoundary } from "@/components/shell/error-boundary";
import { useServerEvent } from "@/hooks/use-live";
import { useActivity } from "@/hooks/use-activity";
import { useMessages } from "@/hooks/use-messages";
import { AnyFileAttachmentAdapter } from "@/lib/attachments";
import { keys } from "@/lib/query-keys";
import {
  parseLocation,
  pathForMessages,
  pathForSettings,
  pathForTab,
  pathForTask,
} from "@/lib/router";
import {
  createBackendAdapter,
  resumeTurn,
  fetchConversation,
  fetchLiveTurns,
  setConversationProject,
  USAGE_PART,
  type ContextLedger,
  type StoredTurn,
  patchServerConfig,
  type ServerConfig,
} from "@/lib/backend";

/* The four screens that are not the chat, fetched when one is opened rather than before the
 * chat can paint.
 *
 * Every one of them is already rendered conditionally, so this costs nothing at the point of
 * use — it only stops them riding in the entry chunk, which is what the browser has to parse
 * before the first message appears. The Control Panel alone pulls in the whole board: the
 * roadmap graph, the file browser, the memory list.
 *
 * `WorkspaceFileViewer` above is deliberately not among them. It is mounted unconditionally
 * and renders nothing until a file is opened, so lazying it would trade a parse it already
 * does cheaply for a suspense boundary around the app's most common overlay. */
const ControlPanel = lazy(() =>
  import("@/components/control-panel").then((m) => ({ default: m.ControlPanel })),
);
const SettingsPage = lazy(() =>
  import("@/components/settings/settings-page").then((m) => ({ default: m.SettingsPage })),
);
const ContextDetailScreen = lazy(() =>
  import("@/components/chat/context-detail").then((m) => ({ default: m.ContextDetailScreen })),
);
const InboxPanel = lazy(() =>
  import("@/components/chat/inbox-panel").then((m) => ({ default: m.InboxPanel })),
);

/** Where the open conversation is remembered across a reload.
 *
 * Without it a refresh dropped you on a blank chat with your conversation one click away in
 * a panel that was also closed — so the app forgot what you were doing every time it
 * reloaded, which is the one thing a window is supposed to be good at. */
const LAST_CONVERSATION = "kith-conversation";

/** Turns mounted when a conversation opens, and added by each "load earlier".
 *
 * Forty rather than a round hundred: it is comfortably more than fits on a screen, so the thread
 * still reads as continuous history rather than a stub, while staying far below the point where
 * mounting and markdown-parsing the tail is what you are waiting for. */
const WINDOW = 40;

/** How long to hold the restored position against the thread settling after a remount. Long,
 *  because the settling is: on an 80-turn conversation `scrollHeight` went 25,070 → 48,106 and
 *  was still climbing at 900ms, which is where the first attempt gave up and left you near the
 *  top of a thread you had been reading the middle of. Any input from you ends it early. */
const RESTORE_MS = 2500;
/** How long to wait before reading the scroll position on a freshly opened thread. */
const SETTLE_MS = 600;

/** The ready-state app: chat runtime, header, and the activity ("Work") panel.
 * Split out so its hooks only run once the backend is reachable. */
export function Workspace({
  config,
  onSaveConfig,
  onConnectionSaved,
}: {
  config: ServerConfig;
  onSaveConfig: (config: ServerConfig) => void;
  /** Called after the provider/model is saved, so the header stops showing the old one. */
  onConnectionSaved: () => void;
}) {
  // Which conversation the chat is in. Held in a ref as well as state: the adapter reads
  // it fresh on every run, and a resumed conversation must not rebuild the runtime while a
  // stream is open.
  const cache = useQueryClient();
  const [conversationId, setConversationId] = useState("");
  const conversationRef = useRef("");
  conversationRef.current = conversationId;
  /* Messages to seed the thread with when resuming.
   *
   * This used to be paired with a `threadKey` bump on the `AssistantRuntimeProvider`, on the
   * stated grounds that remounting was "the only way to replace a local runtime's messages
   * wholesale". Half of that was true: `useLocalRuntime` does `useState(() => new
   * LocalRuntimeCore(opt, initialMessages))`, so `initialMessages` is read once at construction
   * and never again. But the runtime exposes `thread.reset(messages)` for exactly this, and using
   * it means a conversation switch no longer destroys and rebuilds the whole thread — which is
   * what made switching feel like a page load, dropped the composer draft, and reset the scroll. */
  const [resumed, setResumed] = useState<ThreadMessageLike[]>([]);
  /* What is loaded — a page of the conversation, not the conversation.
   *
   * Two problems met here, and only one of them used to be solved. Mounting was: a 473-turn
   * conversation handed to the runtime entire mounts every message and parses every character
   * of markdown before anything appears, and `content-visibility: auto` does not save you —
   * it skips layout and paint for off-screen messages, not React and not the parser. So a
   * forty-turn tail was mounted and the rest held in state. That also fixed where the scroll
   * lands: until a message is laid out the browser assumes the 200px of
   * `contain-intrinsic-size`, so 473 of them made the initial `scrollHeight` a ~95,000px guess
   * against a much larger real height, and "scroll to bottom" went to the bottom of that
   * fiction, which is near the top. Mount forty and the height is honest.
   *
   * What was not solved is that all 492 turns still had to *arrive* — 22.83 MB on the largest
   * real transcript, downloaded, parsed and kept in state so that "load earlier" could slice
   * the tail locally. The window is the same forty turns; it happens on the server now, and
   * `shown` is gone with it: everything loaded is everything rendered, and loading earlier
   * fetches the page before this one. */
  const [timeline, setTimeline] = useState<StoredTurn[]>([]);
  /** Where `timeline[0]` sits in the whole conversation. Zero once you have reached the top,
   *  and the `before` for the next page until then. */
  const [windowStart, setWindowStart] = useState(0);
  /* A conversation is being fetched and there is nothing to show for it yet.
   *
   * The gap this fills was the whole of what "opening a chat hangs" meant: `openConversation`
   * awaited the payload before it changed any state at all, so a click on a row left the
   * previous conversation on screen, unchanged, for as long as the fetch took. Nothing was
   * frozen and nothing was slow to draw — there was simply nothing saying it had begun. */
  const [opening, setOpening] = useState(false);
  /* Whether you are near the top of what is loaded — the only place "load earlier" means
   * anything. It used to be on screen permanently, including at the bottom of the conversation,
   * offering to fetch history in the one position where you have just arrived and are reading
   * forwards. */
  const [nearTop, setNearTop] = useState(false);
  /* Which message you were looking at, across the remount that "load earlier" performs.
   *
   * Not a scroll offset. Distance-from-the-bottom was the first attempt and it drifts by
   * thousands of pixels, because `content-visibility` placeholders are still measuring their
   * real height long after the frame budget any restore loop can reasonably hold — measured,
   * `scrollHeight` was still climbing past 2.5s. Nor a `data-message-id`: assistant-ui mints
   * fresh ones on mount, and none of the ids visible before a load exist after it.
   *
   * What does survive is *ordinal*. Loading earlier prepends a known number of messages, so the
   * message you were reading is the same message `shift` places further down the list. Pinning
   * an element is immune to anything settling above it, because its position is recomputed each
   * time rather than assumed. */
  const anchor = useRef<{
    index: number;
    shift: number;
    offset: number;
  } | null>(null);
  // What this session is working on. Held here rather than fetched inside the bar because
  // it changes from two directions — you set it, and so does he, by starting a project or
  // filing a task mid-turn.
  const [projectId, setProjectId] = useState<number | null>(null);

  // No config passed: the server reads its own settings, so there is nothing here that
  // can go stale. Memoised so the runtime is never recreated mid-stream.
  const adapter = useMemo(
    () =>
      createBackendAdapter({
        get: () => conversationRef.current,
        set: (id) => setConversationId((was) => was || id),
        // Read through the ref rather than closed over, like `get`: the adapter is built once
        // and the choice is made later. This is what carries "start a chat in this project"
        // into the turn that creates the conversation, so its prompt is assembled with the
        // project already known — see `pendingProject`.
        project: () => pendingProject.current,
      }),
    [],
  );
  // Always offered, and it takes anything. It used to appear only for models reporting
  // vision, and then only accept `image/*` — so a spreadsheet could not be attached at all,
  // and on a model without vision the paperclip simply vanished. Both were the wrong call:
  // he has a whole computer, so a file he cannot *see* is still a file he can open, and
  // whether to inline a picture or hand him a path is a decision the server makes next to
  // the model config rather than one the composer makes by hiding a button.
  const attachments = useMemo(() => new AnyFileAttachmentAdapter(), []);
  const runtime = useLocalRuntime(adapter, {
    initialMessages: resumed,
    ...(attachments ? { adapters: { attachments } } : {}),
  });

  /**
   * Ask him — here, in the thread — to check work he handed over.
   *
   * A chat message rather than a task comment, and that is the whole design of the `review` column: a tick
   * verifying its own output is marking its own homework, since it wrote the brief, chose the
   * requirements and supplied the evidence. Chat has the conversation the work came out of, forty
   * rounds, and a person in it.
   *
   * Appending to the thread rather than posting on the task, because the point is that you see the
   * answer and can argue with it. A comment on a task is somewhere you have to go and look.
   */
  /* Rejoin the turn already running in the conversation you just opened.

     The screen is a window onto a turn, not the thing running it. The turn lives on the server,
     on its own thread, and carries on whether or not anybody is watching — but until now the
     watching could only ever start at the beginning, so switching away mid-answer and coming
     back showed a message frozen where you left it, and the finished reply only appeared after
     a reload. Everything the turn said in between was reachable (`live_turns` kept it) and
     nothing asked for it.

     It matters more than a cosmetic catch-up now that he can block on a question and on a
     permission: a card you cannot see is a turn that looks hung, and the answer it is waiting
     for is one you have no way to give.

     Guarded on `isRunning`, because the thread is already streaming when *this* window started
     the turn — resuming then would put a second reader on the same events and render them
     twice. */
  const rejoin = useCallback(() => {
    if (!conversationId) return;
    const wanted = conversationId;
    void resumeTurn(wanted).then((attached) => {
      if (!attached) return;
      /* Two reasons to walk away, and both have to close the stream rather than drop it.
       *
       * The conversation moved while the fetch was in flight — a switch takes one round trip and
       * this took another, so by now the thread may hold someone else's messages, and resuming
       * into it would pour that conversation's turn into this one. Checked against the ref rather
       * than the captured value, because the ref is what the adapter reads too.
       *
       * Or the thread is already streaming, which is the ordinary case: this window started the
       * turn, `live_turns.begin` published `turn`, and the event came back to us. Reading it twice
       * would render every token twice.
       *
       * Either way `discard()`, not `return`. See `resumeTurn`: the generator has not started, so
       * letting it go leaves the response body open and a server thread writing into it. */
      if (conversationRef.current !== wanted) return attached.discard();
      const state = runtime.thread.getState();
      if (state.isRunning) return attached.discard();
      runtime.thread.resumeRun({
        parentId: state.messages.at(-1)?.id ?? null,
        stream: () => attached.stream,
      });
    });
  }, [conversationId, runtime]);

  /* Push the messages into the thread that is already mounted.
   *
   * The whole of what `threadKey` used to do, without the teardown. The runtime is shared across
   * conversations, so a stream still being read when the messages are swapped would append the
   * conversation you *left* into the one you just opened. Cancelling stops this window reading; it
   * does not stop the turn — see the note in `lib/backend/adapter.ts` — which is the point: the
   * work carries on and `rejoin` picks it up again when you come back.
   *
   * **And the reset waits for the cancel to land, which the first version of this did not.**
   * `cancelRun` aborts the controller and returns; the run is still unwinding, and its own
   * `finally` then calls `updateMessage({status: cancelled})` against a message id that `reset`
   * has already cleared out of the repository. Switching away from a running chat therefore
   * dropped the old turn's dying write into the conversation you had just opened — the thread
   * would sit there looking stuck, and when the abandoned turn finally ended the state settled and
   * the switch appeared to happen by itself, a minute after it was asked for. Both halves of
   * "I can't switch while one is running, and then it switches on its own" are that one race.
   *
   * So: cancel, wait for the thread to actually stop running, then replace the messages. Bounded,
   * because a run that never settles must not leave you looking at the wrong conversation for
   * ever — after two seconds the swap happens regardless, which is the old behaviour and no worse.
   */
  useEffect(() => {
    const thread = runtime.thread;
    if (!thread.getState().isRunning) {
      thread.reset(resumed);
      return;
    }

    let settled = false;
    let unsubscribe: (() => void) | undefined;
    let timer: number | undefined;

    const swap = () => {
      if (settled) return;
      settled = true;
      unsubscribe?.();
      if (timer !== undefined) window.clearTimeout(timer);
      thread.reset(resumed);
    };

    thread.cancelRun();
    // Subscribed before the check below, so a run that settles between the two is not missed.
    unsubscribe = thread.subscribe(() => {
      if (!thread.getState().isRunning) swap();
    });
    timer = window.setTimeout(swap, 2_000);
    if (!thread.getState().isRunning) swap();

    return () => {
      settled = true;
      unsubscribe?.();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [runtime, resumed]);

  /* And the composer goes with the conversation.
   *
   * Not smoothness — correctness. The remount used to clear the draft as a side effect of
   * destroying everything; without it, half a sentence typed in one chat follows you into the next
   * one and looks like something you wrote there. Per-conversation drafts would be better than
   * either, and are a feature rather than a fix: this is the behaviour that was already intended.
   *
   * Keyed on the conversation and not on `resumed`, so widening the window with "load earlier"
   * leaves what you were typing alone. */
  useEffect(() => {
    runtime.thread.composer.setText("");
  }, [runtime, conversationId]);

  useEffect(rejoin, [rejoin]);

  /* And again whenever a turn *starts* in this conversation, which is the half that was missing.
     The effect above only fires when you open a conversation, so a turn the server began on its own
     — a reminder firing, a background task finishing — streamed to nobody: the transcript grew on
     disk and the window found out when you reloaded it. That was the second Cmd-R.

     `resumeTurn` is already idempotent about this (it returns nothing when no turn is live, and the
     `isRunning` guard drops the case where this window started the turn itself), so an extra call is
     free and a missed event is the only thing that costs anything. */
  // `useServerEvent` rather than a query, because this is not data: there is nothing to refetch,
  // there is a stream to attach to. See hooks/use-live.ts — it is the one shape in the app that
  // stayed imperative, and deliberately so.
  useServerEvent("turn", rejoin, conversationId);

  /** Counts opens, so a slow one cannot land on top of a later fast one. */
  const openSeq = useRef(0);

  const openConversation = useCallback(
    async (id: string) => {
      /* Through the cache, so going back to a conversation you were just in is instant.
       *
       * This awaited a fresh `fetchConversation` every time, which meant a click on a row did
       * nothing at all until the round trip came back — and switching back and forth between two
       * chats paid for the same transcript over and over. `fetchQuery` hands back what is already
       * held when it is still fresh and fetches when it is not; a `turn` event invalidates
       * `["conversation"]`, so one whose turn finished while you were away is refetched rather
       * than restored without its reply. */
      /* Which click this was. Two rows clicked quickly are two fetches in flight, and without a
       * sequence they land in whichever order the network settles them — so the conversation you
       * end up in is the one that answered fastest, not the one you asked for last. */
      const mine = ++openSeq.current;

      /* Switch first, fetch second, and that order is the fix.
       *
       * Everything below the await used to be above nothing: the conversation id, the project,
       * the thread contents all changed only once the payload had landed, so the click had no
       * effect you could see until it was already over. Moving the id and an empty thread ahead
       * of the await means the app is *in* the conversation immediately, showing that it is
       * loading it — which is what "do not hang" actually asks for. */
      pendingProject.current = null;
      setConversationId(id);
      setTimeline([]);
      setResumed([]);
      setWindowStart(0);
      setOpening(true);
      remember(id);

      const detail = await cache
        .fetchQuery({ queryKey: keys.conversation(id), queryFn: () => fetchConversation(id) })
        .catch(() => null);
      // A later open has already claimed the screen; this one's payload is not wanted, and
      // `setOpening(false)` is not ours to call either — the newer open owns that flag now.
      if (openSeq.current !== mine) return;
      setOpening(false);
      if (!detail) return;

      setProjectId(detail.projectId ?? null);
      setTimeline(detail.timeline);
      setWindowStart(detail.windowStart ?? 0);
      // Already a page — the server windowed it. Slicing again here is what this used to do to
      // 492 turns it had just finished parsing.
      setResumed(toThreadMessages(detail.timeline));
    },
    [cache],
  );

  /** A page is being fetched onto the front of what you are reading. */
  const [loadingEarlier, setLoadingEarlier] = useState(false);

  /* Widen the window by another page.
   *
   * `reset` with the longer list rather than a prepend, because a local runtime has no prepend —
   * see the note on `resumed`.
   *
   * It fetches now. It used to slice, because the whole conversation was already in state —
   * which is exactly what made opening one cost 22.83 MB: the button's convenience was paid
   * for on every open, by everyone, including the openings where nobody ever scrolled up.
   */
  const loadEarlier = useCallback(async () => {
    if (loadingEarlier || windowStart <= 0 || !conversationId) return;
    setLoadingEarlier(true);
    const older = await fetchConversation(conversationId, {
      turns: WINDOW,
      before: windowStart,
    }).catch(() => null);
    setLoadingEarlier(false);
    if (!older || !older.timeline.length) return;

    const widened = [...older.timeline, ...timeline];
    // Remember which message you were reading before the thread is torn down; see `anchor`.
    const grown = toThreadMessages(widened);
    const viewport = document.querySelector<HTMLElement>(
      '[data-slot="aui_thread-viewport"]',
    );
    anchor.current = null;
    if (viewport) {
      const top = viewport.getBoundingClientRect().top;
      const messages = [
        ...viewport.querySelectorAll<HTMLElement>("[data-message-id]"),
      ];
      // The first message still on screen — the one you are actually reading, rather than the
      // one scrolled off above it.
      const index = messages.findIndex(
        (m) => m.getBoundingClientRect().bottom > top,
      );
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
  }, [conversationId, loadingEarlier, timeline, windowStart]);

  /* Watch the thread's own scroll box: whether you are near the top, and putting you back where
   * you were after "load earlier" tore the thread down and built a longer one.
   *
   * Both live here rather than in `Thread` because the button and the windowing state are here,
   * and the viewport is reachable by its slot.
   *
   * "Near the top" is a screenful rather than a pixel count. A fixed threshold does not survive
   * this thread: `content-visibility` placeholders measure their real height as you arrive, and
   * asking for `scrollTop = 0` settled at 531 once they had — under any tight constant, the
   * offer of older messages was hidden at the exact moment you had scrolled up to look for it.
   *
   * The restore holds a *message* at the place on screen it already occupied, on a frame loop,
   * because it is competing with the library's scroll-to-bottom and with that same settling.
   * Pinning an element rather than an offset is what makes it exact: everything above it can
   * change height and the answer is still recomputed from where the element actually is.
   */
  useEffect(() => {
    const viewport = document.querySelector<HTMLElement>(
      '[data-slot="aui_thread-viewport"]',
    );
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
      // A fresh conversation opens at the bottom; only read the position once the library's own
      // scroll has run, or the pill flashes on open.
      timer = window.setTimeout(look, SETTLE_MS);
    } else {
      const until = performance.now() + RESTORE_MS;
      const pin = () => {
        if (done) return;
        const messages =
          viewport.querySelectorAll<HTMLElement>("[data-message-id]");
        const mine = messages[held.index + held.shift];
        if (mine) {
          const drift =
            mine.getBoundingClientRect().top -
            viewport.getBoundingClientRect().top -
            held.offset;
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
  }, [conversationId, resumed]);

  /* A project chosen for a conversation that does not exist yet.
   *
   * "Start a chat in this project" is asked from the history panel, where the project is in
   * front of you — but a fresh chat has no id until the first turn comes back from the stream,
   * and the binding is written against an id. So the choice is held here and shown immediately
   * in the session bar, which is true: it is what the next turn will be bound to.
   *
   * It now rides *with* that turn as well, through the adapter above. Writing it afterwards was
   * a turn too late: the first message of a chat is the one that says what the work is, and it
   * was the one turn assembled with no project bound — so he got the full cross-project listing
   * and none of this project's memory, plan or tasks, on the exact turn that decided what to do
   * next. The write below is now a confirmation rather than the mechanism, and it stays because
   * it is also the path for picking a project in the session bar part-way through a chat. */
  const pendingProject = useRef<number | null>(null);

  const newConversation = useCallback((project: number | null = null) => {
    pendingProject.current = project;
    setConversationId("");
    setProjectId(project);
    setTimeline([]);
    setWindowStart(0);
    setOpening(false);
    setResumed([]);
    remember("");
  }, []);

  // Reopen where you were. A reload used to land on an empty chat with the history panel
  // closed, so the conversation you were mid-way through was two clicks away and looked
  // gone. Runs once: if the stored conversation has since been deleted, `openConversation`
  // finds nothing and this quietly stays a fresh chat.
  useEffect(() => {
    // Not when the URL already names one. A notification opens `/chat/<id>`, and restoring the
    // last conversation alongside it is two opens racing for the same screen — the effect below
    // asks for the one you were sent to, this one asks for the one you left, and whichever
    // answers second wins. The route is the more specific instruction, so it takes precedence.
    if (route.conversationId) return;
    let stored = "";
    try {
      stored = localStorage.getItem(LAST_CONVERSATION) || "";
    } catch {
      /* private mode, or no storage — a fresh chat is a fine answer */
    }
    if (stored) void openConversation(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openConversation]);

  // The session's own state, re-read whenever the conversation changes underneath us — a
  // chat started from an empty composer gets its id from the stream, not from us.
  const refreshSession = useCallback(() => {
    if (!conversationId) return;
    /* Through the cache, like `openConversation`.
     *
     * This is called on every completed step of a turn — `useEffect(refreshSession, [finished])`
     * below — and it was an uncached `fetchConversation`, so a turn that ran forty tool calls
     * refetched and re-parsed the whole transcript forty times. Six megabytes of JSONL on the
     * conversation this was found in, to read one nullable `projectId` off the top of it.
     *
     * `fetchQuery` serves it from the cache inside the 60s `staleTime` and dedupes with whatever
     * `openConversation` already fetched; a `turn` event invalidates the key, so the reading is
     * still current when it matters. */
    void cache
      .fetchQuery({
        queryKey: keys.conversation(conversationId),
        queryFn: () => fetchConversation(conversationId),
      })
      .then((detail) => setProjectId(detail.projectId ?? null))
      .catch(() => {});
  }, [cache, conversationId]);

  useEffect(() => {
    remember(conversationId);
    // Write the pending project before re-reading, not after: `refreshSession` would otherwise
    // fetch the server's null and clear the project you picked a second before the id existed.
    const wanted = pendingProject.current;
    if (conversationId && wanted !== null) {
      pendingProject.current = null;
      void setConversationProject(conversationId, wanted)
        .then(() => setProjectId(wanted))
        .catch(refreshSession);
      return;
    }
    refreshSession();
  }, [conversationId, refreshSession]);

  const activity = useActivity();
  const inbox = useMessages();
  // The Control Panel (and which tab/task is open) lives in the URL, so deep
  // links, refresh, and back/forward all work.
  const location = useLocation();
  const navigate = useNavigate();
  const route = parseLocation(location.pathname);
  const panelOpen = route.panelOpen;
  // A notification with nothing narrower to point at (a reach-out, a stall, a session
  // resting itself) links here — so it has to be a real URL, the same way the task panel
  // and settings already are, not state a click sets and a notification has no way to reach.
  const inboxOpen = route.inboxOpen;

  /** Closing a surface from inside it — its own header X — is closing its tab. Two ways to
   *  shut the same thing that disagreed would be worse than one. */
  const closeTab = useLayout((state) => state.close);
  const openSurface = useLayout((state) => state.open);
  const layoutTree = useLayout((state) => state.tree);
  /** Whether a surface is on screen anywhere, for the header's pressed states. Read from the
   *  tree rather than from a flag beside it, so the button cannot disagree with the layout. */
  const hasTab = useCallback((key: string) => treeHasTab(layoutTree, key), [layoutTree]);
  /* The chat tab, while there is only one of it.
   *
   * The tree keys a chat by its conversation, which is what will let two of them coexist. Until
   * then there is one chat tab carrying an empty id, meaning "whichever conversation this
   * session has open" — and `Workspace` still owns that, as `conversationId`. Writing the real
   * id into the ref here would be half of per-tab sessions: the tab would rename itself on every
   * open while one runtime and one timeline window still sat behind it, so two tabs would show
   * the same conversation under two names. The ref becomes authoritative when the session behind
   * it does. */
  const chatKey = tabKeyOf({ surface: "chat", conversationId: "" });
  /** The header's buttons are toggles: pressing one twice puts the surface away again. */
  const toggleSurface = useCallback(
    (surface: "conversations" | "work" | "board" | "settings") => {
      if (treeHasTab(layoutTree, surface)) closeTab(surface);
      else openSurface({ surface });
    },
    [closeTab, layoutTree, openSurface],
  );


  /* A route names what you are looking at; the layout decides where it goes.
   *
   * These five screens used to cover the app, so "open settings" and "put settings on screen"
   * were the same act. They are panes now, and a deep link — a notification, a refresh, a
   * back button — has to *dock* the thing it names rather than replace what is there. So the
   * URL still carries what it always carried and every one of those links still works; it
   * opens a tab instead of a screen.
   *
   * Focus-or-open rather than open: `openTab` finds an existing tab and brings it forward, so
   * a second notification about the board does not stack a second board. */
  useEffect(() => {
    if (route.settingsTab) openSurface({ surface: "settings" });
  }, [openSurface, route.settingsTab]);
  useEffect(() => {
    if (panelOpen) openSurface({ surface: "board" });
  }, [openSurface, panelOpen]);
  useEffect(() => {
    if (inboxOpen) openSurface({ surface: "inbox" });
  }, [openSurface, inboxOpen]);
  useEffect(() => {
    if (route.contextOpen) openSurface({ surface: "context" });
  }, [openSurface, route.contextOpen]);

  /* A notification that lands you in the chat it is about.

     A question he is waiting on lives in exactly one conversation, and the answer can only be
     given there — so "he needs you" that drops you into whichever chat you last had open is a
     notification that has not finished its job. `/chat/<id>` is the one route that could not be
     expressed before.

     Guarded on already being there, or every render would reopen it and throw away the thread
     you are reading. */
  useEffect(() => {
    const wanted = route.conversationId;
    if (!wanted || wanted === conversationId) return;
    void openConversation(wanted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.conversationId]);

  /* The three thresholds that used to live here are gone.
   *
   * `CHAT_FLOOR`, `WORK_YIELDS_BELOW` and `HISTORY_YIELDS_BELOW` encoded one intent — do not
   * squeeze prose to three words a line — as a hand-tuned order in which two named panels gave
   * way to a third, plus a `squeezed` flag held apart from `workOpen` so that narrowing the
   * window did not overwrite your choice, plus an overlay mode for the conversation list, plus
   * a pointer-driven divider that remembered its width in `localStorage`. All of it answered a
   * question the layout now answers structurally: every surface declares a `minWidth`, the
   * panel library enforces it, and there is no order of yielding to maintain because there is
   * no fixed set of three panels to order.
   *
   * See `layout/surfaces.ts`, where the same measured numbers survive as one field each. */

  // Clicking a desktop notification lands here. The shell dispatches this rather than
  // reloading the window, because a notification arriving mid-reply should not cost you the
  // reply — and `__kithRouter` is how it knows an in-page route is possible at all.
  useEffect(() => {
    (window as unknown as { __kithRouter?: boolean }).__kithRouter = true;
    const onNavigate = (event: Event) => {
      const path = (event as CustomEvent<string>).detail;
      if (typeof path === "string" && path.startsWith("/")) navigate(path);
    };
    window.addEventListener("kith:navigate", onNavigate);
    return () => {
      (window as unknown as { __kithRouter?: boolean }).__kithRouter = false;
      window.removeEventListener("kith:navigate", onNavigate);
    };
  }, [navigate]);

  /* Whether he is working, and where — which this could not honestly say until now.
   *
   * It was `const working = false`, with a comment explaining that the green wash and the session
   * bar were switched off rather than lying, waiting on "the reliability work, which gives them one
   * honest meaning: a session is busy if and only if a turn is live in it". That meaning exists now:
   * `live_turns.live()` is the set of conversations with a turn actually running, `turn` events
   * invalidate it the moment one starts or finishes, and the stream those events arrive on is
   * resumable — so a missed one costs a beat rather than a wrong screen.
   *
   * Both halves are wanted and they are different. `working` is about the conversation in front of
   * you, and drives the wash and the orb. `elsewhere` is the one this app was missing entirely: a
   * turn carrying on in a chat you are not looking at, which until now announced itself only as a
   * 6px dot inside a panel you had to have open to see. */
  const { data: live = [] } = useQuery({
    queryKey: keys.liveTurns(),
    queryFn: fetchLiveTurns,
  });
  const working = conversationId !== "" && live.includes(conversationId);
  const elsewhere = live.filter((id) => id !== conversationId);
  // He adopts a project by working on one, so what the session is bound to can change
  // part-way through a turn. One re-read per completed step or turn, which is the cheapest
  // signal that anything could have changed at all.
  const finished = activity.activity.filter(
    (item) => item.kind === "done",
  ).length;
  useEffect(refreshSession, [finished, refreshSession]);
  // The room glows green while he is working, and is otherwise his own amber.
  const wash = working ? "var(--roam)" : "var(--kith)";

  /* Which element a tab shows.
   *
   * The layout module is handed this rather than importing the surfaces itself, so it never
   * pulls the Thread or the board into its own chunk, and so every prop these components need
   * stays here with the state that produces it.
   *
   * One `ErrorBoundary` per surface, kept from the fixed layout: a pane that throws takes only
   * itself down, and the chat surviving a broken roadmap graph is the difference between "one
   * thing is wrong" and "Kith is down". */
  const renderSurface = useCallback(
    (ref: TabRef) => {
      switch (ref.surface) {
        case "conversations":
          return (
            <ErrorBoundary where="Conversations" compact>
              <HistoryPanel
                activeId={conversationId}
                onOpen={(id) => void openConversation(id)}
                onNew={(project) => newConversation(project ?? null)}
                onClose={() => closeTab("conversations")}
              />
            </ErrorBoundary>
          );

        case "chat":
          return (
            <div className="relative flex h-full min-h-0 flex-col">
          <SessionBar
            conversationId={conversationId}
            projectId={projectId}
            onProject={(next) => {
              setProjectId(next);
              // Picked before the conversation exists — the bar shows on an empty chat now,
              // and there is no id to write the binding against until the first turn comes
              // back. Held in the same place "New chat here" holds it, and written by the
              // same effect.
              if (!conversationRef.current) pendingProject.current = next;
            }}
          />
          {/* The thread gets its own box with a definite height rather than sitting
              straight in the column. Without one, the thread root's `h-full` resolved
              against the whole column — session bar included — so the moment a
              conversation existed the composer's bottom edge sat 37px past the window
              and the send button was clipped clean off it. An empty chat looked fine
              because the session bar draws nothing, which is what made it read as
              random rather than as a layout bug.

              A flex column, not a block, and that is the same bug a second time. `Thread`
              is `h-full`, so in block layout it takes the whole box *and* the "Load
              earlier" button above it takes its own height on top — the thread's bottom
              ends up past the box by exactly the height of that button. It only shows on
              a conversation long enough to be windowed, because that is the only time the
              button is rendered, so it reads as "big chats are broken" rather than as a
              layout bug. The bottom-most thing in the thread falls off first, which is
              the context meter under the composer.

              Anything added beside `Thread` in here has to go in the flow, not on top of
              it. */}
          <div className="relative flex min-h-0 flex-1 flex-col">
            <ErrorBoundary where="The conversation">
              <CheckpointsProvider
                conversationId={conversationId}
                // How many turns were dropped off the front by the window. Checkpoints are
                // tagged with their absolute turn index and matched against the *rendered*
                // message index, so without this the offer silently walks backwards through
                // the conversation as you window — "restore to here" on the first visible
                // message would target whatever happened 433 turns earlier. A destructive
                // action aiming at the wrong commit is the worst thing windowing could have
                // broken, so the offset travels with the checkpoints rather than being
                // recomputed anywhere that compares them.
                // Now read straight off the page the server handed back, rather than
                // inferred from two client-side lengths. Same number, one definition.
                turnOffset={windowStart}
              >
                {/* Floating, not stacked.
                    This was a full-width flex row in the flow, which made it a band across
                    the top of the conversation: it claimed its own height from the thread,
                    and the first message ran up underneath the thread's top fade to meet
                    it. Absolute takes it out of the flow entirely — the thread gets the
                    whole box back, and the pill hovers over the top of it the way a
                    "jump to latest" chip does at the other end.
                    `pointer-events-none` on the strip so the full-width row cannot
                    intercept anything; only the pill itself is clickable. */}
                {windowStart > 0 && nearTop ? (
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
                  {opening ? <ThreadSkeleton /> : <Thread conversationId={conversationId} />}
                </div>
              </CheckpointsProvider>
            </ErrorBoundary>
          </div>
            </div>
          );

        case "work":
          return (
            <ErrorBoundary where="Work" compact>
              <WorkPanel
                activity={activity}
                conversationId={conversationId}
                onClose={() => closeTab("work")}
              />
            </ErrorBoundary>
          );

        case "board":
          return (
            <ErrorBoundary where="The Control Panel">
              <Suspense fallback={<ScreenLoading />}>
                <ControlPanel
                  tab={route.tab}
                  openTask={route.taskId}
                  onSelectTab={(t) => navigate(pathForTab(t))}
                  onOpenTask={(id) => navigate(pathForTask(id))}
                  onClose={() => closeTab("board")}
                />
              </Suspense>
            </ErrorBoundary>
          );

        case "settings":
          return (
            <ErrorBoundary where="Settings">
              <Suspense fallback={<ScreenLoading />}>
                <SettingsPage
                  tab={route.settingsTab ?? "model"}
                  config={config}
                  onSelectTab={(t) => navigate(pathForSettings(t))}
                  onSaveConfig={onSaveConfig}
                  onConnectionSaved={onConnectionSaved}
                  onClose={() => closeTab("settings")}
                />
              </Suspense>
            </ErrorBoundary>
          );

        case "inbox":
          return (
            <Suspense fallback={<ScreenLoading />}>
              <InboxPanel inbox={inbox} onClose={() => closeTab("inbox")} />
            </Suspense>
          );

        case "context":
          return (
            <ErrorBoundary where="The context breakdown">
              <Suspense fallback={<ScreenLoading />}>
                <ContextDetailScreen
                  conversationId={conversationId}
                  onClose={() => closeTab("context")}
                />
              </Suspense>
            </ErrorBoundary>
          );
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      activity,
      closeTab,
      config,
      conversationId,
      inbox,
      loadingEarlier,
      nearTop,
      newConversation,
      opening,
      openConversation,
      projectId,
      route.settingsTab,
      route.tab,
      route.taskId,
      windowStart,
    ],
  );

  /** A chat tab is named by its conversation; everything else by the surface registry. */
  const titleForTab = useCallback(
    (ref: TabRef) => {
      if (ref.surface !== "chat" || !ref.conversationId) return undefined;
      /* Straight out of the query cache rather than a fetch of its own. Opening the
       * conversation already put its detail there, so naming its tab is free — and a tab that
       * had to request a title would put one request per tab on every reload. */
      const held = cache.getQueryData<{ title?: string }>(keys.conversation(ref.conversationId));
      return held?.title;
    },
    [cache],
  );

  return (
    <TooltipProvider>
      {/* No `key` any more. It was bumped on every conversation switch to force a remount, which
          is what made switching feel like a page load; the messages are replaced with
          `thread.reset` instead — see the note on `resumed`. */}
      <AssistantRuntimeProvider runtime={runtime}>
        <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
          {/* Ambient wash — leans green while he works, amber while he's here. */}
          <div
            className="kith-ambient"
            style={{ ["--wash" as string]: wash }}
          />
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            <AppHeader
              working={working}
              status={null}
              model={config.model}
              effort={config.effort}
              // Offered unless the provider has said otherwise: an unknown model is the
              // normal case right after a switch, and the transport retries a reasoning 400.
              supportsEffort={
                !config.capabilities?.known || config.capabilities.reasoning
              }
              onEffort={(effort) => {
                onSaveConfig({ ...config, effort });
                void patchServerConfig({ effort });
              }}
              historyOpen={hasTab("conversations")}
              onOpenHistory={() => toggleSurface("conversations")}
              onNewConversation={() => newConversation()}
              elsewhere={elsewhere}
              onGoToWorking={(id) => {
                // One: go straight to it, which is the whole point of knowing where. Several: open
                // the list, because picking is the question and the panel is where it is answered.
                if (id) void openConversation(id);
                else openSurface({ surface: "conversations" });
              }}
              unread={inbox.unread}
              workOpen={hasTab("work")}
              onOpenInbox={() => {
                inbox.enableNotifications();
                openSurface({ surface: "inbox" });
                navigate(pathForMessages());
              }}
              onOpenWork={() => toggleSurface("work")}
              onOpenPanel={() => {
                openSurface({ surface: "board" });
                navigate(pathForTab(route.tab ?? "overview"));
              }}
              onOpenSettings={() => navigate(pathForSettings())}
            />
            <LayoutView render={renderSurface} titleFor={titleForTab} />
          </div>
        </div>
        {/* Drop a file anywhere in the window and it lands on the composer. Disabled — but
            still swallowing the drop — while something is covering the thread, since attaching
            to a composer nobody can see is a file that has vanished. */}
        {/* Enabled only while a chat is on screen. It used to be disabled whenever one of the
            five takeover screens covered the thread; there are no takeovers now, so the
            question is the one it was always really asking — is there a composer for this file
            to land on. A drop with the chat tab closed would attach to something nobody can
            see, which is a file that has vanished. */}
        <DropZone enabled={hasTab(chatKey)} />
        {/* One viewer for the whole app — a path in a message, a deliverable, and the
            file browser all open this. Given this session's project, because the paths it is
            handed are mostly relative ones out of his prose and his tool results, and a
            relative path he wrote during a project turn is relative *to that project*. Without
            it the viewer asked the global workspace root and got "there's no
            .kith/work/task-76.md" for a file that was never missing. */}
        <WorkspaceFileViewer projectId={projectId} />
      </AssistantRuntimeProvider>
    </TooltipProvider>
  );
}

/** The moment between opening a screen and its chunk arriving.
 *
 * A full-bleed backdrop rather than a spinner in the corner: these screens cover the app, so
 * anything smaller reads as the click having missed. On a warm cache it is one frame. */
function ScreenLoading() {
  return (
    <div className="bg-background/80 fixed inset-0 z-40 backdrop-blur-[2px]" role="status">
      <span className="sr-only">Loading</span>
    </div>
  );
}

/** Keep (or clear) the conversation to reopen on the next load. */
function remember(conversationId: string): void {
  try {
    if (conversationId) localStorage.setItem(LAST_CONVERSATION, conversationId);
    else localStorage.removeItem(LAST_CONVERSATION);
  } catch {
    /* ignore */
  }
}

/**
 * A stored conversation, rebuilt as the thread saw it.
 *
 * Not just the words: the reasoning blocks he opened, the prose between tool rounds, and
 * each call with the result it got. Reopening a conversation should show you the one you
 * had — a paragraph where six rounds of work used to be is a summary, and no amount of
 * cleverness reconstructs the shape once it is gone.
 */
function toThreadMessages(timeline: StoredTurn[]): ThreadMessageLike[] {
  const out: unknown[] = [];
  // A running count of tool calls seen so far in this conversation, not the backend's own
  // id. `${turnIndex}-${part.id}` was the earlier fix and is still right for the ordinary
  // case, but it assumes the backend's per-turn ids are actually unique within whatever
  // `timeline()` groups as one turn — true for a turn that is one `stream_agent` call, and
  // false for older conversations where a reminder continued the same conversation_id
  // with no new user message in between: two separate turns, each restarting its own ids at
  // c1, land in the transcript with nothing to tell `timeline()` to split them, so "c9" can
  // appear twice *inside* one rendered turn. No amount of scoping by turn index fixes a
  // collision that happens within a single turn index — only something that can never repeat
  // does, so this counts instead of reading anything the backend assigned.
  let callSeq = 0;
  for (const turn of timeline) {
    const content: unknown[] = [];
    // Collected rather than pushed as they arrive. One of these is recorded per model request,
    // and pushing each as its own data part is what made a reopened conversation a column of
    // six-figure token counts — "328,422 tokens", "165,292 tokens", a dozen deep — threaded
    // through the reply. The live stream has always accumulated them into one; a resumed turn
    // has to read the same, or reopening a conversation does not show you the one you had.
    //
    // It also cost the tool grouping: a data part between two tool calls breaks the run, so
    // eight consecutive calls rendered as eight separate "1 tool call" rows instead of one
    // line saying what he touched.
    const rounds: { uncached: number; cached: number; out: number }[] = [];
    // One per turn at most, recorded when it ended — see the `context` branch in
    // `services/conversations`. Collected the same way the counts are, so it lands in the same
    // footer instead of somewhere in the middle of the reply.
    let context: ContextLedger | undefined;
    let baseline: ContextLedger | undefined;
    let folded = false;
    let retried = 0;
    for (const part of turn.parts) {
      if (part.kind === "text") content.push({ type: "text", text: part.text });
      else if (part.kind === "reasoning")
        content.push({ type: "reasoning", text: part.text });
      else if (part.kind === "tool") {
        content.push({
          type: "tool-call",
          // The backend resets its own tool-call ids to c1 at the start of every turn, so the
          // raw id repeats across nearly every message in a long conversation — 209 times for
          // "c9" alone in one real conversation, which is what actually crashed the thread on
          // reopening it: two different messages both offering a tool call keyed "c9" collided
          // in assistant-ui's own resource cache. A counter rather than the backend's id or
          // even `${turnIndex}-${part.id}`: those still collide on a turn that is really two
          // continuations glued together with no user message between them (older
          // conversations, from before this stopped happening unaskedsation on their own) —
          // both restart their own ids at c1, landing two "c9"s inside what `timeline()`
          // reads as one turn. This never repeats, by construction, regardless of what the
          // backend assigned or how the transcript is shaped.
          toolCallId: `call-${callSeq++}`,
          toolName: part.name,
          args: part.arguments,
          argsText: JSON.stringify(part.arguments),
          result: part.result,
        });
      } else if (part.kind === "context") {
        context = part.context;
        // `{}` — not absent — on a turn recorded before this field existed; `window` is
        // always present on a real reading, never on that placeholder. `turn-usage.tsx`
        // falls back to `context` when this is undefined.
        baseline = part.baseline?.window
          ? (part.baseline as ContextLedger)
          : undefined;
        folded = part.folded;
        retried = part.retried ?? 0;
      } else {
        rounds.push({
          uncached: part.uncached,
          cached: part.cached,
          out: part.out,
        });
      }
    }
    // Last, so the figure lands at the foot of the turn and nothing is split around it.
    // Token counts ride back as the same data part the live stream uses, so the footer reads
    // the same on a resumed turn as it did on a fresh one.
    if (rounds.length || context) {
      content.push({
        type: "data",
        name: USAGE_PART,
        data: { rounds, context, baseline, folded, retried },
      });
    }
    // `createdAt` from the transcript, never left to default. A `ThreadMessageLike` without one
    // is stamped with the moment it was converted, so every turn in a conversation reopened now
    // would read as having happened now — a clock that is wrong on exactly the messages it is
    // there to date. Omitted rather than guessed when the turn predates the field.
    const at = turn.at ? new Date(turn.at) : null;
    if (content.length) {
      out.push({
        role: turn.role,
        content,
        ...(at && !Number.isNaN(at.getTime()) ? { createdAt: at } : {}),
      });
    }
  }
  // One cast, at the boundary: the shapes above are the library's own, and its content
  // union narrows by role in a way that defeats inference through a map.
  return out as ThreadMessageLike[];
}
