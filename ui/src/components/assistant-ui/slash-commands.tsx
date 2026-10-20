import { useEffect, useMemo, useState } from "react";
import { useAuiState } from "@assistant-ui/react";

import type { ContextLedger } from "@/lib/backend/types";
import { foldNow, listSkills, stopTurn, type SkillSummary } from "@/lib/commands";
import { rememberSkills, type SlashCommand } from "@/lib/slash";

/**
 * The `/` commands, and the line that says what one did.
 *
 * Two kinds, and the difference is worth keeping straight. `/fold` and `/stop` *do* something
 * to the conversation and report back — they never become a message. `/design` and a skill are
 * the start of a request rather than the whole of one, so they stay in the box as a
 * word, with the cursor after it: "use the running-a-project skill" on its own is not an
 * instruction, and sending it immediately would make him guess what for. What those words mean
 * is spelled out when the message is sent — see `expandCommand` in `lib/slash.ts`.
 *
 * The skill list is read when the hook mounts rather than held, because he writes skills
 * mid-conversation and a cached list would not offer the one he just made.
 */
export function useSlashCommands(conversationId?: string) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [note, setNote] = useState("");
  // What the window looked like once the last `/fold` finished. Held here rather than left to
  // the meter because nothing else will report it: a reading is taken by a turn, and a fold is
  // not a turn — so between the fold and the next thing you send, this is the only measurement
  // of the conversation that actually exists now.
  const [reading, setReading] = useState<ContextLedger>();
  // The library's own id for the thread on screen. Commands act on the conversation being
  // looked at, and this hook sits inside the composer, which is the only place that is
  // unambiguous.
  const running = useAuiState((s) => s.thread.isRunning);

  useEffect(() => {
    let alive = true;
    void listSkills().then((found) => {
      rememberSkills(found.map((one) => one.name));
      if (alive) setSkills(found);
    });
    return () => {
      alive = false;
    };
  }, []);

  // Dropped the moment a turn starts, because that turn will measure the window itself and its
  // reading is the true one. Held only for the gap in between — which is exactly the moment
  // someone asks "did that do anything", and the moment the meter used to answer no.
  useEffect(() => {
    if (running) setReading(undefined);
  }, [running]);

  const say = (text: string) => {
    setNote(text);
    // Long enough to read, short enough that it is gone before it becomes furniture.
    setTimeout(() => setNote(""), 6000);
  };

  const id = conversationId ?? "";

  const commands = useMemo<SlashCommand[]>(
    () => [
      {
        name: "fold",
        kind: "action",
        description: "Summarise the older turns now, instead of waiting for the window to fill",
        run: () => {
          say("Folding…");
          void foldNow(id)
            .then((result) => {
              say(result.note);
              if (result.reading) setReading(result.reading);
            })
            // A fold that never reached the server must not leave "Folding…" on screen for good.
            .catch(() => say("Could not reach Kith to fold."));
        },
      },
      {
        name: "stop",
        kind: "action",
        description: running ? "Stop the turn running now" : "Nothing is running",
        run: () => {
          void stopTurn(id)
            .then((result) => say(result.note))
            .catch(() => say("Could not reach Kith to stop."));
        },
      },
      /* Ahead of the skill list and separate from it, because it is not the same offer.
       * `/designing-a-screen` would say "read the skill"; this says read it *and* make several
       * files *and* attach them, which is the difference between a board with options on it and
       * a paragraph about options. */
      {
        name: "design",
        kind: "prefix",
        description: "Mock up a few options as screens I can compare on the canvas",
      },
      ...skills.map(
        (skill): SlashCommand => ({
          name: skill.name,
          kind: "prefix",
          description: skill.description || "Open this skill before answering",
        }),
      ),
    ],
    // `say` and the setters are stable in effect; what changes the list is these.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, running, skills],
  );

  return { commands, note, reading };
}
