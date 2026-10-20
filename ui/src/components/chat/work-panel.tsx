import { useQuery } from "@tanstack/react-query";

import { WorkingOn } from "@/components/assistant-ui/working-on";
import { AgentsCard } from "@/components/chat/agents-card";
import { BackgroundTasks } from "@/components/chat/background-tasks";
import { StandingWork } from "@/components/chat/standing-work";
import { fetchConversation } from "@/lib/backend";
import { ProjectRootProvider } from "@/lib/project-root";
import { keys } from "@/lib/query-keys";

/**
 * What chat cannot tell you, beside the chat — the companion column's Activity tab.
 *
 * This was a round-by-round feed of tool calls — "opened the skill running-a-project", "looked over
 * his tasks" — which is the same list the thread already renders, one panel away from where you read
 * it. Four hundred pixels of second copy, and a column of nothing whenever a conversation was quiet.
 *
 * Gone, and what is left is the things a conversation genuinely cannot say, each as a card:
 *
 * * **what he is working on**, and how far through its checklist;
 * * **the agents** in this chat's tree, as a map;
 * * **what is running in the background** while he does something else, and what will start on
 *   its own;
 * * **what is in his head**, by group, with the button that shrinks it.
 *
 * Each is its own file. This is the column they sit in.
 *
 * **No counter footer.** "31 steps · 2.1M tokens" used to sit under the cards: rounds counted off
 * the feed since the server started, across every conversation, under a panel about one of them. It
 * answered no question the cards above it leave open, so it went with the redesign.
 */
export function WorkPanel({
  conversationId = "",
}: {
  /** Which conversation is on screen. The sections about one conversation take it; background tasks
   *  and the lifetime token count are about the machine. */
  conversationId?: string;
}) {
  /* The project this conversation is in, so a path in a card reads `webapp/app.py` rather than the
   * forty characters of home folder in front of it.
   *
   * The panel sits *beside* the chat, not inside it, so the provider `ChatPane` sets up is not
   * above it. The same lookup that provider makes, then — the conversation's own record, which the
   * pane has already fetched under this key.
   *
   * A cache read and never a fetch (`enabled: false` still hands back what is cached, and follows
   * it). That record is the transcript — a megabyte on a long chat — and an observer that could
   * fetch would refetch all of it on every turn's invalidation to learn one number that does not
   * change. */
  const { data: detail } = useQuery({
    queryKey: keys.conversation(conversationId),
    queryFn: () => fetchConversation(conversationId),
    enabled: false,
  });

  return (
    // No width, no border and no background of its own: the column it sits in draws all three, and
    // a second set here fought the one beside it.
    <ProjectRootProvider projectId={detail?.projectId ?? null}>
      <div className="flex h-full min-h-0 w-full flex-col">
        {/* Everything scrolls, and `min-h-0` is the half that makes it work: a flex child defaults
            to `min-height: auto`, which refuses to shrink below its content, so `overflow-y-auto`
            on its own finds nothing to scroll and silently does nothing. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {/* The cards, 14px apart inside a 14px margin. `peer` so the line under it can say so
              when every card has nothing to show and the list renders empty. */}
          <div className="peer mx-auto flex w-full max-w-295 flex-col gap-3.5 p-3.5 empty:hidden">
            {/* What he is on right now, and how far through it. */}
            <WorkingOn conversationId={conversationId} />

            {/* The agents this chat's tree has, as a map. Nothing when there are none. */}
            <AgentsCard conversationId={conversationId} />

            {/* What is running while he does something else. Renders nothing when there is
                nothing — a header over an empty list is furniture. */}
            <BackgroundTasks conversationId={conversationId} />

            {/* And what is going to start on its own. The same question as the one above asked
                about a different clock. */}
            <StandingWork conversationId={conversationId} />

          </div>
          <p className="hidden px-6 py-10 text-center text-[12px] leading-relaxed text-muted-foreground peer-empty:block">
            Nothing is going on here right now. The task he is on, and anything he leaves running,
            show up in this tab while they last.
          </p>
        </div>
      </div>
    </ProjectRootProvider>
  );
}
