import { useEffect, useState } from "react";
import { useAuiState, useComposerRuntime, type Unstable_SlashCommand } from "@assistant-ui/react";

import type { ContextLedger } from "@/lib/backend/types";
import { foldNow, listSkills, stopTurn, type SkillSummary } from "@/lib/commands";

/**
 * The `/` commands, and the line that says what one did.
 *
 * Two kinds, and the difference is worth keeping straight. `/fold` and `/stop` *do* something
 * to the conversation and report back — they never become a message. `/skill` writes into the
 * composer and leaves the cursor with you, because choosing a skill is the start of a request
 * rather than the whole of one: "use the running-a-project skill" on its own is not an
 * instruction, and sending it immediately would make him guess what for.
 *
 * The skill list is read when the hook mounts rather than held, because he writes skills
 * mid-conversation and a cached list would not offer the one he just made.
 */
export function useSlashCommands(conversationId?: string) {
  const composer = useComposerRuntime();
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
    void listSkills().then((found) => alive && setSkills(found));
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

  const commands: Unstable_SlashCommand[] = [
    {
      id: "fold",
      label: "fold",
      description: "Summarise the older turns now, instead of waiting for the window to fill",
      icon: "Layers",
      execute: () => {
        say("Folding…");
        void foldNow(id).then((result) => {
          say(result.note);
          if (result.reading) setReading(result.reading);
        });
      },
    },
    {
      id: "stop",
      label: "stop",
      description: running ? "Stop the turn running now" : "Nothing is running",
      icon: "Square",
      execute: () => {
        void stopTurn(id).then((result) => say(result.note));
      },
    },
    ...skills.map(
      (skill): Unstable_SlashCommand => ({
        id: `skill:${skill.name}`,
        label: skill.name,
        description: skill.description || "Open this skill before answering",
        icon: "BookOpenText",
        // Written into the composer, not sent. See the note above.
        execute: () => {
          composer.setText(`Read the ${skill.name} skill and use it for this: `);
        },
      }),
    ),
  ];

  return { commands, note, reading };
}
