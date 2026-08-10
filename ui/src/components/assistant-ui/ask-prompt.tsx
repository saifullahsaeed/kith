import { useCallback, useEffect, useRef, useState } from "react";
import { CornerDownLeft, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  answerQuestion,
  fetchOpenQuestion,
  type AskedQuestion,
  type OpenQuestion,
  type Reply,
} from "@/lib/backend/questions";
import { cn } from "@/lib/utils";

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
 */
export function AskPrompt({ conversationId }: { conversationId: string }) {
  const [open, setOpen] = useState<OpenQuestion | null>(null);
  const [replies, setReplies] = useState<Reply[]>([]);
  const [at, setAt] = useState(0);
  const [sending, setSending] = useState(false);
  const typed = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchOpenQuestion(conversationId)
        .then((found) => {
          if (!alive) return;
          setOpen((was) => {
            if (found?.id === was?.id) return was;
            // A new question: start again rather than carry answers across.
            setReplies(
              (found?.questions ?? []).map(() => ({ chosen: [], text: "", skipped: false })),
            );
            setAt(0);
            return found;
          });
        })
        .catch(() => {});
    load();
    // Brisk, because this is the one thing on screen the turn is actually waiting for.
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
      const went = await answerQuestion(open.id, final);
      // Cleared either way: if the server no longer has it, the turn moved on without us and
      // leaving the card up would invite answering something nobody is waiting for.
      setOpen(null);
      setSending(false);
      if (!went) return;
    },
    [open, sending],
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

  /** Move on, or finish. One question is answered by picking; several page forward. */
  const advance = (next: Reply) => {
    const all = update(next);
    if (last) void send(all);
    else setAt(at + 1);
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
          <p className="min-w-0 flex-1 text-sm font-medium">{question.question}</p>
          {open.questions.length > 1 ? (
            <span className="text-muted-foreground/60 shrink-0 font-mono text-[11px] tabular-nums">
              {at + 1} of {open.questions.length}
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
                  <span
                    className={cn(
                      "flex size-5 shrink-0 items-center justify-center rounded-md font-mono text-[10px]",
                      picked ? "bg-kith text-background" : "bg-muted text-muted-foreground",
                    )}
                  >
                    {i + 1}
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
          <input
            ref={typed}
            defaultValue={reply.text}
            placeholder="Something else…"
            aria-label="Answer in your own words"
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              const text = event.currentTarget.value.trim();
              if (!text && !reply.chosen.length) return;
              advance({ ...reply, text, skipped: false });
            }}
            className="min-w-0 flex-1 bg-transparent py-1.5 text-xs outline-none"
          />
          {question.multiple && reply.chosen.length > 0 ? (
            <Button
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => advance({ ...reply, text: typed.current?.value.trim() ?? "" })}
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
