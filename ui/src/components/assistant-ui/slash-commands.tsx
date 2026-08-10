import { useEffect, useState } from "react";
import { useAuiState, useComposerRuntime, type Unstable_SlashCommand } from "@assistant-ui/react";

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
        void foldNow(id).then((result) => say(result.note));
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

  return { commands, note };
}
