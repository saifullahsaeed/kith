import { useState } from "react";
import { Pause, Play, Plus, Repeat } from "lucide-react";
import { Markdown } from "@/components/files";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { Badge, Composer, DeleteButton, EmptyState, PageHeader } from "./chrome";
import { matches } from "./format";
import { CHIP, FIELD, INPUT } from "./types";
import type { Handlers } from "./types";

/* ── Schedules (standing jobs) ──────────────────────────────────────────── */

export function Schedules({
  snap,
  query,
  remove,
  create,
  update,
}: { snap: BrainSnapshot } & { query: string } & Handlers) {
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"daily" | "every">("daily");
  const [at, setAt] = useState("09:00");
  const [mins, setMins] = useState("60");
  const items = snap.schedules.filter((s) => matches(query, s.note, s.status));
  const add = () => {
    if (!note.trim()) return;
    if (mode === "every") {
      const m = Number(mins);
      if (!Number.isFinite(m) || m <= 0) return;
      create("schedule", { note, every_minutes: m });
    } else {
      create("schedule", { note, daily_at: at });
    }
    setNote("");
  };
  return (
    <>
      <PageHeader
        icon={<Repeat className="size-5" />}
        color="orange"
        title="Schedules"
        count={items.length}
        subtitle="Standing jobs he runs on a cadence."
      />
      <Composer onSubmit={add}>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="A standing job — e.g. brief me on my sources…"
          className={`${FIELD} min-w-48 flex-1`}
        />
        <Dropdown
          value={mode}
          onChange={(v) => setMode(v as "daily" | "every")}
          options={[
            { value: "daily", label: "daily at" },
            { value: "every", label: "every" },
          ]}
          className="w-28"
          ariaLabel="Cadence"
        />
        {mode === "daily" ? (
          <input type="time" value={at} onChange={(e) => setAt(e.target.value)} className={INPUT} />
        ) : (
          <span className="flex items-center gap-1 text-sm text-muted-foreground">
            <input
              type="number"
              min={1}
              value={mins}
              onChange={(e) => setMins(e.target.value)}
              className={`${INPUT} w-16`}
            />
            min
          </span>
        )}
        <Button size="sm" onClick={add}>
          <Plus className="size-4" />
          Add
        </Button>
      </Composer>
      {items.length === 0 ? (
        <EmptyState icon={<Repeat className="size-5" />}>
          No standing jobs. Add one and he'll run it on schedule.
        </EmptyState>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map((s) => {
            const active = s.status === "active";
            return (
              <div
                key={s.id}
                className={cn(
                  "group flex items-start gap-3 rounded-xl border bg-card/50 p-4 shadow-sm transition-all hover:shadow-md",
                  active ? "border-orange-500/25" : "border-border/70 opacity-75",
                )}
              >
                <span
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-xl",
                    active ? CHIP.orange : "bg-muted/60 text-muted-foreground",
                  )}
                >
                  <Repeat className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="break-words text-sm font-medium">
                    <Markdown>{s.note}</Markdown>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                    <Badge
                      className={
                        active ? "border-orange-500/25 bg-orange-500/10 text-orange-500" : ""
                      }
                    >
                      {s.daily_at ? `daily · ${s.daily_at}` : `every ${s.every_minutes}m`}
                    </Badge>
                    <span>{active ? <>next {s.fires || "soon"}</> : "paused"}</span>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground hover:text-foreground"
                  onClick={() => update("schedule", s.id, { status: active ? "paused" : "active" })}
                  aria-label={active ? "Pause" : "Resume"}
                >
                  {active ? <Pause className="size-4" /> : <Play className="size-4" />}
                </Button>
                <DeleteButton onClick={() => remove("schedule", s.id, s.note)} />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
