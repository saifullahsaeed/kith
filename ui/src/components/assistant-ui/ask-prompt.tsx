import { useThreadRuntime } from "@assistant-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, CornerDownLeft, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  answerQuestion,
  fetchOpenQuestion,
  type AskedQuestion,
  type OpenQuestion,
  type Reply,
} from "@/lib/backend/questions";
import { cn } from "@/lib/utils";

/** One step of the pager. Disabled rather than hidden, so the control does not move about
 *  under the cursor as you page. */
function Step({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="text-muted-foreground/70 hover:text-foreground flex size-5 items-center justify-center rounded transition-colors disabled:pointer-events-none disabled:opacity-25"
    >
      {children}
    </button>
  );
}

/** The answers, written the way the person would have typed them.
 *
 *  Only used for a recovered question, where the answer travels as a message rather than as a
 *  reply — so it has to carry its own question with it. A bare "Use each invoice date" arriving
 *  on its own is unreadable a day later, and unanswerable by a turn that has to work out what it
 *  was in aid of. */
function asText(open: OpenQuestion, replies: Reply[]): string {
  const blocks = open.questions.map((q, i) => {
    const reply = replies[i];
    const said = reply?.skipped
      ? "(you decide)"
      : [reply?.chosen?.join(", "), reply?.text].filter(Boolean).join(" — ") || "(no answer)";
    // Blockquote, and a blank line between the two. Both matter: user messages render as
    // markdown, where a single newline is not a line break — so the first version of this went
    // out as "What would you most like to make progress on right now? Something personal", one
    // run-on line in which the question and the answer are indistinguishable, and which reads
    // far more like the person asking than answering.
    return `> ${q.question}\n\n${said}`;
  });
  return blocks.join("\n\n");
}

/**
 * A question he is waiting on, where you would reply to him.
 *
 * The `ask` tool holds the turn open, which is what makes it worth having and also what makes
 * this component load-bearing: without it the turn sits on a card nobody drew until the
 * fifteen-minute deadline. That happened, on the first call, and it is the reason this is not
 * optional polish.
 *
 * Beside the composer rather than inline in the message, deliberately. It is a thing to answer,
 * so it belongs where answering happens — the same slot the permission prompt uses, for the
 * same reason. Inline it would also scroll away from you mid-turn while he is still talking.
 *
 * Polled rather than pushed. The id is minted server-side when the tool runs, after the
 * `tool_call` event has gone out, so there is nothing in the stream to key on; and polling
 * survives the case that matters most — coming back to a conversation whose question was
 * asked while you were somewhere else.
 *
 * Raising the card from the stream event instead was tried, on 2026-08-13, and reverted the
 * same evening. Calling the fetch synchronously from inside the adapter's event loop — the
 * `tool_call` for `ask` arriving — correlated exactly with the renderer going dead: last
 * request of any kind at 20:11:20 UTC, the ask at 20:11:24, and nothing at all for the eleven
 * minutes after. Machine awake, `backgroundThrottling` already false, and the same ask on the
 * previous build had left polling running. Whatever it does, it is not safe from there, and
 * the reason it looked necessary — a card that takes many seconds to appear — is still not
 * explained. Do not re-add this without understanding the stall first.
 */
export function AskPrompt({ conversationId }: { conversationId: string }) {
  // Optional, because drawing the card does not need a runtime and only the recovered-question
  // path does. Required, it threw "ThreadRuntime is not available" in every test that renders
  // this on its own — a component that cannot be mounted without the whole thread around it is
  // harder to test than it needs to be, for a dependency it uses on one branch.
  const runtime = useThreadRuntime({ optional: true });
  const [open, setOpen] = useState<OpenQuestion | null>(null);
  const [replies, setReplies] = useState<Reply[]>([]);
  const [at, setAt] = useState(0);
  /** How far you have got. Forward paging stops here so it never skips a question you have
   *  not seen, while still letting you return from one you went back to. */
  const [furthest, setFurthest] = useState(0);
  const [sending, setSending] = useState(false);

  // Which question the answers on screen belong to. A ref rather than reading `open` inside
  // the poll, because the two updates have to be decided together: setting state from inside
  // another setter's updater runs the reset twice under StrictMode, and what that looks like
  // is a selection quietly clearing itself under your cursor.
  const shown = useRef<string>("");

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchOpenQuestion(conversationId)
        .then((found) => {
          if (!alive || found?.id === shown.current) return;
          shown.current = found?.id ?? "";
          setOpen(found);
          // A different question: start again rather than carry answers across.
          setReplies((found?.questions ?? []).map(() => ({ chosen: [], text: "", skipped: false })));
          setAt(0);
          setFurthest(0);
        })
        .catch(() => {});
    load();
    // Brisk, because this is the one thing on screen the turn is actually waiting for — and
    // still not enough on its own. Chromium throttles an interval hard in a window that is not
    // focused: measured at fifty polls a minute with focus and twelve in three and a half
    // minutes without, so the card could take twenty seconds to appear and the turn looked hung.
    // Quitting and reopening the app fetched immediately, which is why that read as the fix.
    const timer = setInterval(load, 1_200);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [conversationId]);

  const send = useCallback(
    async (final: Reply[]) => {
      if (!open || sending) return;
      setSending(true);
      // A question recovered after a restart has nobody waiting on it — the turn that asked it
      // died with the process. Posting the answer to `/answer` would set an event no thread is
      // holding and the reply would go nowhere, which from this side looks exactly like the
      // card working. Sent as an ordinary message it starts a turn, and that turn reads the
      // conversation with the question in it, because the interrupted call was closed off on
      // the way back up. See `questions.recover_interrupted`.
      await answerQuestion(open.id, final);
      if (open.interrupted) {
        runtime?.append({ role: "user", content: [{ type: "text", text: asText(open, final) }] });
      }
      // Cleared either way: if the server no longer has it, the turn moved on without us and
      // leaving the card up would invite answering something nobody is waiting for.
      setOpen(null);
      setSending(false);
    },
    [open, sending, runtime],
  );

  if (!open || open.questions.length === 0) return null;

  const question: AskedQuestion = open.questions[at];
  const reply = replies[at] ?? { chosen: [], text: "", skipped: false };
  const last = at >= open.questions.length - 1;

  const update = (next: Reply): Reply[] => {
    const all = replies.slice();
    all[at] = next;
    setReplies(all);
    return all;
  };

  /** Move on, or finish. One question is answered by picking; several page forward.
   *
   *  "Last" is the last *question*, not the furthest you have reached — so answering question
   *  two after paging back to it goes to three rather than submitting the lot. */
  const advance = (next: Reply) => {
    const all = update(next);
    if (last) void send(all);
    else {
      setFurthest((was) => Math.max(was, at + 1));
      setAt(at + 1);
    }
  };

  const choose = (label: string) => {
    if (question.multiple) {
      // Picking is toggling here, and nothing is sent until "Done" — otherwise choosing a
      // second option would be impossible.
      const chosen = reply.chosen.includes(label)
        ? reply.chosen.filter((one) => one !== label)
        : [...reply.chosen, label];
      update({ ...reply, chosen });
      return;
    }
    advance({ ...reply, chosen: [label], skipped: false });
  };

  return (
    <div className="mx-auto mb-2 w-full max-w-(--thread-max-width) px-4">
      <div className="border-kith/30 bg-card/85 rounded-xl border p-3 shadow-sm backdrop-blur">
        <div className="mb-2.5 flex items-baseline gap-2">
          <p className="min-w-0 flex-1 text-sm font-medium">
            {question.question}
            {/* Said out loud, because a card that merely *permits* several looks identical to
                one that takes the first click as the answer. */}
            {question.multiple ? (
              <span className="text-muted-foreground ms-1.5 text-[11px] font-normal">
                pick any that apply
              </span>
            ) : null}
          </p>
          {/* Both ways, because a single-choice question answers itself the moment you click —
              so overshooting is one careless click away and there was no way back from it.
              Forward is only offered once you have been past, so it moves between answers you
              have already given rather than skipping ones you have not. */}
          {open.questions.length > 1 ? (
            <span className="flex shrink-0 items-center gap-0.5">
              <Step label="Previous question" onClick={() => setAt(at - 1)} disabled={at === 0}>
                <ChevronLeft className="size-3.5" />
              </Step>
              <span className="text-muted-foreground/60 font-mono text-[11px] tabular-nums">
                {at + 1} of {open.questions.length}
              </span>
              <Step
                label="Next question"
                onClick={() => setAt(at + 1)}
                disabled={at >= furthest || last}
              >
                <ChevronRight className="size-3.5" />
              </Step>
            </span>
          ) : null}
        </div>

        <ul className="flex flex-col">
          {question.options.map((option, i) => {
            const picked = reply.chosen.includes(option.label);
            return (
              <li key={option.label}>
                <button
                  type="button"
                  onClick={() => choose(option.label)}
                  className={cn(
                    "border-border/40 flex w-full items-baseline gap-2.5 border-b px-1.5 py-2 text-start transition-colors",
                    "hover:bg-accent/40",
                    picked && "bg-kith-soft/60",
                  )}
                >
                  {/* Square and ticked when several are allowed, numbered when one is: the
                      shape of the marker is the fastest way to say which kind of question
                      this is, and it says it before you have clicked anything. */}
                  <span
                    className={cn(
                      "flex size-5 shrink-0 items-center justify-center font-mono text-[10px]",
                      question.multiple ? "rounded-[5px] border" : "rounded-md",
                      picked
                        ? "bg-kith text-background border-kith"
                        : question.multiple
                          ? "border-border/70 text-transparent"
                          : "bg-muted text-muted-foreground",
                    )}
                  >
                    {question.multiple ? <Check className="size-3" /> : i + 1}
                  </span>
                  <span className="min-w-0 flex-1 text-xs">
                    {option.label}
                    {option.description ? (
                      <span className="text-muted-foreground block text-[11px] leading-snug">
                        {option.description}
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        <div className="mt-1.5 flex items-center gap-2">
          <Pencil className="text-muted-foreground/50 size-3.5 shrink-0" />
          {/* Controlled, and that is a fix rather than a preference. Uncontrolled with
              `defaultValue`, React keeps the same input element across a change of question —
              same position, same type — so what you typed for question one was sitting in the
              box for question two, already looking like your answer. Bound to the reply, the
              box shows what that question holds and nothing else, and paging back shows your
              words again instead of losing them. */}
          <input
            value={reply.text}
            onChange={(event) => update({ ...reply, text: event.target.value })}
            placeholder="Something else…"
            aria-label="Answer in your own words"
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              if (!reply.text.trim() && !reply.chosen.length) return;
              advance({ ...reply, text: reply.text.trim(), skipped: false });
            }}
            className="min-w-0 flex-1 bg-transparent py-1.5 text-xs outline-none"
          />
          {reply.chosen.length > 0 || reply.text.trim() ? (
            <Button
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => advance({ ...reply, text: reply.text.trim(), skipped: false })}
            >
              {last ? "Done" : "Next"}
              <CornerDownLeft className="size-3" />
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground h-7 text-xs"
              // Skipping is an answer: he is told to choose and say why, which is different
              // from being told what to choose.
              onClick={() => advance({ chosen: [], text: "", skipped: true })}
            >
              Skip
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
