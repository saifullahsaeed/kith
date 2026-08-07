import { NotebookPen } from "lucide-react";
import { Markdown } from "@/components/files";
import { ItemMenu } from "@/components/ui/item-menu";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { DeleteButton, EmptyState, PageHeader } from "./chrome";
import { groupByDay, matches, time } from "./format";
import type { Handlers } from "./types";

/* ── Journal (day-grouped reading column) ───────────────────────────────── */

export function Journal({
  snap,
  query,
  remove,
}: {
  snap: BrainSnapshot;
  query: string;
  remove: Handlers["remove"];
}) {
  const items = snap.journal.filter((j) => matches(query, j.entry));
  return (
    <>
      <PageHeader
        icon={<NotebookPen className="size-5" />}
        color="sky"
        title="Journal"
        count={items.length}
        subtitle="His private diary — how the days felt."
      />
      {items.length === 0 ? (
        <EmptyState icon={<NotebookPen className="size-5" />}>The journal is empty.</EmptyState>
      ) : (
        <div className="mx-auto max-w-2xl space-y-8">
          {groupByDay(items, (j) => j.created_at).map(([day, entries]) => (
            <div key={day}>
              <div className="mb-3 text-xs font-semibold text-muted-foreground">{day}</div>
              <div className="space-y-3">
                {entries.map((j) => (
                  <ItemMenu
                    key={j.id}
                    title={"Journal entry"}
                    copy={j.entry}
                    onDelete={() => remove("journal", j.id, j.entry)}
                  >
                    <div
                      key={j.id}
                      className="group relative rounded-xl border border-border/70 bg-card/50 p-4 shadow-sm transition-all hover:shadow-md"
                    >
                      <span className="absolute top-4 left-0 h-8 w-0.5 -translate-x-px rounded-full bg-sky-500/50" />
                      {/* His journal is Markdown too — headings, lists and links render. */}
                      <Markdown>{j.entry}</Markdown>
                      <div className="mt-2.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span className="tabular-nums">{time(j.created_at)}</span>
                        <div className="flex-1" />
                        <DeleteButton onClick={() => remove("journal", j.id, j.entry)} />
                      </div>
                    </div>
                  </ItemMenu>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ── Projects (the one home for work — open one to get at its tasks) ─────── */
