import { useState } from "react";
import { BellRing, Plus } from "lucide-react";
import { Markdown } from "@/components/files";
import { Button } from "@/components/ui/button";
import { ItemMenu } from "@/components/ui/item-menu";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { Composer, DeleteButton, EmptyState, PageHeader, SectionLabel } from "./chrome";
import { matches, when } from "./format";
import { CHIP, FIELD, INPUT } from "./types";
import type { Handlers } from "./types";

/* ── The task board — always scoped to one project (or the loose tray) ───── */

export function Reminders({
  snap,
  query,
  remove,
  create,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [note, setNote] = useState("");
  const [mins, setMins] = useState("30");
  const items = snap.reminders.filter((r) => matches(query, r.note, r.status));
  const pending = items.filter((r) => r.status === "pending");
  const past = items.filter((r) => r.status !== "pending");
  const add = () => {
    const minutes = Number(mins);
    if (!note.trim() || !Number.isFinite(minutes) || minutes <= 0) return;
    create("reminder", { note, in_minutes: minutes });
    setNote("");
  };
  return (
    <>
      <PageHeader
        icon={<BellRing className="size-5" />}
        color="orange"
        title="Reminders"
        count={items.length}
        subtitle="Nudges that fire once, at a set time."
      />
      <Composer onSubmit={add}>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Remind him to…"
          className={`${FIELD} flex-1`}
        />
        <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
          in
          <input
            type="number"
            min={1}
            value={mins}
            onChange={(e) => setMins(e.target.value)}
            className={`${INPUT} w-16`}
          />
          min
        </span>
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Set
        </Button>
      </Composer>
      {items.length === 0 ? (
        <EmptyState icon={<BellRing className="size-5" />}>No reminders.</EmptyState>
      ) : (
        <div className="space-y-6">
          {pending.length > 0 ? (
            <div>
              <SectionLabel hint={`${pending.length}`}>Upcoming</SectionLabel>
              <div className="space-y-2">
                {pending.map((r) => (
                  <ItemMenu
                    key={r.id}
                    title={"Reminder"}
                    copy={r.note}
                    onDelete={() => remove("reminder", r.id, r.note)}
                  >
                    <div
                      key={r.id}
                      className="group flex items-center gap-3 rounded-xl border border-orange-500/25 bg-orange-500/[0.05] p-4 shadow-sm"
                    >
                      <span
                        className={cn(
                          "flex size-9 shrink-0 items-center justify-center rounded-xl",
                          CHIP.orange,
                        )}
                      >
                        <BellRing className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="break-words text-sm">
                          <Markdown>{r.note}</Markdown>
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          {r.fires || "scheduled"} · {when(r.fire_at)}
                        </div>
                      </div>
                      <DeleteButton onClick={() => remove("reminder", r.id, r.note)} />
                    </div>
                  </ItemMenu>
                ))}
              </div>
            </div>
          ) : null}
          {past.length > 0 ? (
            <div>
              <SectionLabel hint={`${past.length}`}>Past</SectionLabel>
              <div className="space-y-2">
                {past.map((r) => (
                  <div
                    key={r.id}
                    className="group flex items-center gap-3 rounded-xl border border-border/70 bg-card/40 p-4 opacity-75 transition-opacity hover:opacity-100"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-muted/60 text-muted-foreground">
                      <BellRing className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="break-words text-sm">
                        <Markdown>{r.note}</Markdown>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {r.status} · {when(r.fire_at)}
                      </div>
                    </div>
                    <DeleteButton onClick={() => remove("reminder", r.id, r.note)} />
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}
